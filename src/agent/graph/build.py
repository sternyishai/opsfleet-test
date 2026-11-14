"""The agent graph: fixed shell, fixed Trio retrieval, ReAct analysis core (ADR 0004, ADR 0008).

The edges are the reference diagram in docs/hld/01-architecture.md § Agent graph. Node names
here are the span names in traces. Stub: ``build_graph`` is implemented in slice 1.
"""

from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langgraph.checkpoint.base import BaseCheckpointSaver
    from langgraph.graph.state import CompiledStateGraph

    from agent.composition import Ports
    from agent.config import Limits


class Node(StrEnum):
    LOAD_CONTEXT = "load_context"  # UserDirectory, ProfileStore, PersonaStore; resets turn keys
    INPUT_GUARD = "input_guard"  # rules, then the router call; blocks -> REFUSE
    ROUTER = "router"  # same model call as the guard; split for spans only
    APPLY_PREFERENCES = "apply_preferences"  # merge into this turn's profile
    SAVE_PREFERENCES = "save_preferences"  # parallel; ProfileStore.save_standing
    RETRIEVE_TRIOS = "retrieve_trios"  # fixed step on every analysis turn
    ANALYSIS = "analysis"  # ReAct sub-agent with the six analysis tools
    PLAN_DELETION = "plan_deletion"
    CONFIRM_DELETION = "confirm_deletion"  # interrupt(); resumes with the User's reply
    EXECUTE_DELETION = "execute_deletion"
    PERSONA_PERMISSION = "persona_permission"  # persona_editor, checked in code
    DRAFT_PERSONA = "draft_persona"
    PERSONA_GATE = "persona_gate"
    PUBLISH_PERSONA = "publish_persona"
    ROLLBACK_PERSONA = "rollback_persona"
    ACKNOWLEDGE = "acknowledge"  # preference-only turns
    REFUSE = "refuse"  # catalog message
    AFTER = "after"  # deferred request? -> RETRIEVE_TRIOS, else -> OUTPUT_FILTER
    OUTPUT_FILTER = "output_filter"  # so checkpoints hold filtered text
    RESPOND = "respond"  # Outbound.emit(AgentReply)


ANALYSIS_TOOLS: tuple[str, ...] = (
    "get_schema",
    "search_golden_trios",
    "run_sql",
    "save_report",
    "list_my_reports",
    "get_my_report",
)
"""The only tools bound to a model. Branch functions are never in this list."""


def build_graph(
    ports: "Ports", limits: "Limits", checkpointer: "BaseCheckpointSaver"
) -> "CompiledStateGraph":
    """Wire the nodes above onto ``AgentState`` and compile with the checkpointer.

    Every node is wrapped by the graph-boundary catch-all: an ``AgentError`` becomes its
    catalog message; anything else becomes ``UNCLASSIFIED`` with the reference ID, traced and
    alerted. The Conversation state is preserved either way.
    """
    raise NotImplementedError("slice 1: walking skeleton")
