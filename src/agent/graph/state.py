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

from typing import Annotated, TypedDict
from uuid import UUID

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages

from agent.contracts.channel import Channel
from agent.contracts.router import RouterDecision
from agent.contracts.tools import PersonaDraft
from agent.domain.identity import BrandScope, UserContext
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
    user: UserContext
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
