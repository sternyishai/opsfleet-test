"""The LangGraph state (docs/lld/03-graph-state.md).

One ``AgentState`` per Conversation (``thread_id = conversation_id``), checkpointed after
every node. Fields are grouped by lifetime:

- **Conversation**: survive across turns (messages, running summary, access version seen,
  open Deletion Plan, deferred request).
- **Turn**: reset by ``load_context`` at the start of every turn.

Values are Pydantic models so they round-trip through the checkpointer's serializer. Nothing
here holds result rows: tools return rows to the model for one step, and the history keeps a
``ResultSummary``.
"""

import types
from dataclasses import dataclass
from enum import Enum
from typing import Annotated, Any, TypedDict, Union, get_args, get_origin, get_type_hints
from uuid import UUID

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel

from agent.contracts.channel import Channel
from agent.contracts.router import RouterDecision
from agent.contracts.tools import PersonaDraft
from agent.core.outbound import Outbound
from agent.domain.identity import BrandScope, UserContext, UserId
from agent.domain.persona import PersonaGateResult, PersonaVersion
from agent.domain.profile import UserProfile
from agent.domain.query import ResultSummary
from agent.domain.reports import DeletionOutcome, DeletionPlan
from agent.domain.trios import TrioMatch
from agent.errors import ErrorClass, MessageClass


class ConversationState(TypedDict, total=False):
    conversation_id: UUID
    channel: Channel
    messages: Annotated[list[AnyMessage], add_messages]
    running_summary: str
    """Older turns folded into one summary; the last ``verbatim_turns`` stay verbatim."""
    access_version_seen: int
    """``UserContext.access_version`` at the previous turn. A change drops earlier results."""
    open_plan: DeletionPlan | None
    """The latest open Deletion Plan. A new plan replaces (cancels) it; executing clears it."""
    deferred_request: str | None
    """Kept while a delete or Persona change runs; consumed by the ``after`` edge."""


class TurnState(TypedDict, total=False):
    turn_id: str
    trace_id: str
    user: UserContext | None
    """None when the User is unknown or inactive: the turn is refused with ``NO_ROLE``."""
    persona: PersonaVersion
    profile: UserProfile
    """This turn's profile: stored profile + this-turn and standing preferences merged."""
    router: RouterDecision
    # analysis
    trios: list[TrioMatch]
    retrieval_degraded: bool
    analysis_steps: int
    sql_attempts: int
    sql_retries: int
    empty_diagnoses: int
    ran_sql: bool
    """With ``trios == []``, marks a retrieval gap."""
    last_query_scope: BrandScope | None
    """Scope of the last successful ``run_sql``; ``save_report`` records it."""
    result_summaries: list[ResultSummary]
    saved_report_ids: list[UUID]
    # deletion branch
    deletion_outcome: DeletionOutcome | None
    # Persona branch
    persona_draft: PersonaDraft | None
    persona_gate: PersonaGateResult | None
    # outcome
    error_class: ErrorClass | None
    message_class: MessageClass | None


class AgentState(ConversationState, TurnState, total=False):
    """The full graph state."""


TURN_KEYS: frozenset[str] = frozenset(TurnState.__annotations__)
"""Keys ``load_context`` resets at the start of each turn."""


@dataclass(frozen=True, slots=True)
class TurnRuntime:
    """Per-turn values the nodes need that must never be checkpointed: passed as LangGraph's
    run ``context`` by ``ChatService.handle``, not through state."""

    user_id: UserId
    """Resolved by the driving adapter; ``load_context`` turns it into ``user``."""
    outbound: Outbound
    """This turn's single exit; ``respond`` emits through it."""


def _model_types(hint: Any, found: set[type]) -> None:
    if isinstance(hint, type) and issubclass(hint, Enum):
        found.add(hint)
        return
    if isinstance(hint, type) and issubclass(hint, BaseModel):
        if hint not in found:
            found.add(hint)
            for field in hint.model_fields.values():
                _model_types(field.annotation, found)
        return
    if get_origin(hint) in (Union, types.UnionType) or get_args(hint):
        for arg in get_args(hint):
            _model_types(arg, found)
    elif hasattr(hint, "__value__"):  # a ``type X = ...`` alias
        _model_types(hint.__value__, found)


def _checkpoint_types() -> tuple[tuple[str, str], ...]:
    found: set[type] = set()
    for hint in get_type_hints(AgentState, include_extras=True).values():
        _model_types(hint, found)
    ours = {(t.__module__, t.__name__) for t in found if t.__module__.startswith("agent.")}
    return tuple(sorted(ours))


CHECKPOINT_TYPES: tuple[tuple[str, str], ...] = _checkpoint_types()
"""Every one of our types that can appear in a checkpoint, for the serializer's allowlist.
Derived from ``AgentState`` so a new field cannot be forgotten."""


type AgentGraph = CompiledStateGraph[AgentState, TurnRuntime, AgentState, AgentState]
"""What ``build_graph`` returns and ``ChatService`` runs."""
