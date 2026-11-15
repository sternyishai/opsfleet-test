# State keys are total=False for LangGraph, but load_context sets every turn key and
# ChatService sets the conversation keys before any node below reads them.
# pyright: reportTypedDictNotRequiredAccess=false
"""The agent graph: fixed shell, fixed Trio retrieval, ReAct analysis core (ADR 0004, ADR 0008).

The edges are the reference diagram in docs/hld/01-architecture.md § Agent graph. Node names
here are the span names in traces.

Wired so far: the analysis path ``load_context`` -> ``input_guard`` -> ``router`` ->
``retrieve_trios`` -> ``analysis`` -> ``after`` -> ``output_filter`` -> ``respond``, with
``refuse`` for a missing role or a blocked verdict; and the deletion branch ``router`` ->
``plan_deletion`` -> ``confirm_deletion`` (paused) -> ``execute_deletion`` -> ``after``, where
a deferred request goes back to ``retrieve_trios``. The other nodes join with their slice.
"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

from agent.composition import Ports
from agent.config import Limits
from agent.contracts.channel import AgentReply
from agent.contracts.router import Action, RouterDecision, Verdict
from agent.core.catalog import render
from agent.core.confirmation import looks_like_reply
from agent.core.input_rules import screen
from agent.domain.profile import UserProfile
from agent.errors import ErrorClass, MessageClass, TransientError
from agent.graph.analysis import analysis
from agent.graph.deletion import DeletionNodes
from agent.graph.prompts import router_messages
from agent.graph.state import TURN_KEYS, AgentGraph, AgentState, TurnRuntime
from agent.ports.llm import ModelStep


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


def build_graph(ports: Ports, limits: Limits, checkpointer: BaseCheckpointSaver) -> AgentGraph:
    """Wire the nodes above onto ``AgentState`` and compile with the checkpointer
    (``thread_id = conversation_id``).

    The graph-boundary catch-all is ``ChatService.handle``: an exception from any node ends
    the turn there, as its catalog message, with the state up to that node checkpointed.
    """
    nodes = _Nodes(ports, limits)
    g = StateGraph(AgentState, context_schema=TurnRuntime)
    g.add_node(Node.LOAD_CONTEXT, nodes.load_context)
    g.add_node(Node.INPUT_GUARD, nodes.input_guard)
    g.add_node(Node.ROUTER, _passthrough)
    g.add_node(Node.RETRIEVE_TRIOS, nodes.retrieve_trios)

    def run_analysis(state: AgentState) -> dict[str, Any]:
        return analysis(state, ports, limits)

    g.add_node(Node.ANALYSIS, run_analysis)
    deletion = DeletionNodes(
        ports,
        limits,
        execute=Node.EXECUTE_DELETION,
        after=Node.AFTER,
        restart=Node.LOAD_CONTEXT,
    )
    g.add_node(Node.PLAN_DELETION, deletion.plan_deletion)
    g.add_node(Node.CONFIRM_DELETION, deletion.confirm_deletion, destinations=deletion.confirm_to)
    g.add_node(Node.EXECUTE_DELETION, deletion.execute_deletion)
    g.add_node(Node.REFUSE, nodes.refuse)
    g.add_node(Node.AFTER, nodes.after)
    g.add_node(Node.OUTPUT_FILTER, nodes.output_filter)
    g.add_node(Node.RESPOND, nodes.respond)

    g.add_edge(START, Node.LOAD_CONTEXT)
    g.add_conditional_edges(Node.LOAD_CONTEXT, _refused_or(Node.INPUT_GUARD))
    g.add_conditional_edges(Node.INPUT_GUARD, _refused_or(Node.ROUTER))
    # Persona and preference-only actions are analysis until their branches exist (slice 7).
    g.add_conditional_edges(Node.ROUTER, _by_action)
    g.add_edge(Node.RETRIEVE_TRIOS, Node.ANALYSIS)
    g.add_edge(Node.ANALYSIS, Node.AFTER)
    g.add_conditional_edges(Node.PLAN_DELETION, _planned)
    g.add_edge(Node.EXECUTE_DELETION, Node.AFTER)
    g.add_edge(Node.REFUSE, Node.AFTER)
    g.add_conditional_edges(Node.AFTER, _deferred)
    g.add_edge(Node.OUTPUT_FILTER, Node.RESPOND)
    g.add_edge(Node.RESPOND, END)
    return g.compile(checkpointer=checkpointer)


_BLOCKED: dict[Verdict, MessageClass] = {
    Verdict.OFF_TOPIC: MessageClass.OFF_TOPIC,
    Verdict.PII_REQUEST: MessageClass.PII_REQUEST,
    Verdict.INJECTION: MessageClass.INJECTION,
}

_TURN_DEFAULTS: dict[str, Any] = {
    **dict.fromkeys(TURN_KEYS - {"turn_id", "trace_id"}),
    "trios": [],
    "retrieval_degraded": False,
    "analysis_steps": 0,
    "sql_attempts": 0,
    "sql_retries": 0,
    "empty_diagnoses": 0,
    "ran_sql": False,
    "result_summaries": [],
    "saved_report_ids": [],
}
"""What ``load_context`` resets every turn. ``turn_id`` and ``trace_id`` come in with the turn."""


def _passthrough(state: AgentState) -> dict[str, Any]:
    return {}


def _by_action(state: AgentState) -> str:
    router = state["router"]
    deleting = router is not None and router.action is Action.DELETE_REPORTS
    return Node.PLAN_DELETION if deleting else Node.RETRIEVE_TRIOS


def _planned(state: AgentState) -> str:
    return Node.CONFIRM_DELETION if state.get("open_plan") else Node.AFTER


def _deferred(state: AgentState) -> str:
    """``after`` turned a deferred request into this turn's next question."""
    return (
        Node.RETRIEVE_TRIOS
        if isinstance(state["messages"][-1], HumanMessage)
        else Node.OUTPUT_FILTER
    )


def _refused_or(next_node: Node):
    def route(state: AgentState) -> str:
        return Node.REFUSE if state.get("message_class") else next_node

    return route


def _last_text(state: AgentState) -> str:
    return state["messages"][-1].text


class _Nodes:
    def __init__(self, ports: Ports, limits: Limits) -> None:
        self._ports = ports
        self._limits = limits

    def load_context(self, state: AgentState, runtime: Runtime[TurnRuntime]) -> dict[str, Any]:
        user_id = runtime.context.user_id
        user = self._ports.users.load(user_id)
        profiles = self._ports.profiles
        update = {
            **_TURN_DEFAULTS,
            "user": user,
            "persona": self._ports.personas.active(),
            "profile": profiles.load(user_id) if profiles else UserProfile(user_id=user_id),
        }
        if user is None or user.role is None:  # unknown, inactive or no role: default deny
            update |= {"error_class": ErrorClass.PERMANENT, "message_class": MessageClass.NO_ROLE}
        else:
            update["access_version_seen"] = user.access_version
        return update

    def input_guard(self, state: AgentState) -> dict[str, Any]:
        """Deterministic rules first, then the router call, which is also the guard's
        classifier. Fails closed: if the call raises, the turn ends at the catch-all and no
        analysis runs."""
        messages = state["messages"]
        if state.get("last_plan_id") and looks_like_reply(_last_text(state)):
            # No plan is open (a paused one would have been resumed): never guess which.
            return {"error_class": ErrorClass.PERMANENT, "message_class": MessageClass.PLAN_EXPIRED}
        if verdict := screen(_last_text(state), max_chars=self._limits.input_max_chars):
            return {"error_class": ErrorClass.PERMANENT, "message_class": _BLOCKED[verdict]}
        # json_schema: Gemini's native schema mode resolves the nested $defs of RouterDecision.
        model = self._ports.llm.chat(ModelStep.ROUTER).with_structured_output(
            RouterDecision, method="json_schema"
        )
        decision = model.invoke(
            router_messages(messages[:-1], _last_text(state), turns=self._limits.verbatim_turns)
        )
        assert isinstance(decision, RouterDecision)
        update: dict[str, Any] = {"router": decision, "deferred_request": decision.deferred_request}
        if decision.verdict in _BLOCKED:
            update |= {
                "error_class": ErrorClass.PERMANENT,
                "message_class": _BLOCKED[decision.verdict],
            }
        return update

    def retrieve_trios(self, state: AgentState) -> dict[str, Any]:
        user = state["user"]
        assert user is not None
        router = state["router"]
        question = (router.search_question if router else None) or _last_text(state)
        try:
            result = self._ports.trios.search(question, user.scope, limit=self._limits.trio_top_k)
        except TransientError:
            return {"trios": [], "retrieval_degraded": True}
        return {"trios": result.matches}

    def refuse(self, state: AgentState) -> dict[str, Any]:
        message_class = state["message_class"]
        assert message_class is not None
        text = render(message_class, state["persona"].persona, ref=state["trace_id"])
        return {"messages": [AIMessage(content=text)]}

    def after(self, state: AgentState, runtime: Runtime[TurnRuntime]) -> dict[str, Any]:
        """A deferred request runs once the delete is answered: the branch's reply is sent
        now, and the request becomes the next question (``_deferred`` routes it to
        ``retrieve_trios``). It runs at most once: it is cleared here."""
        deferred = state.get("deferred_request")
        if not deferred:
            return {}
        runtime.context.outbound.emit(self._reply(state))
        return {"deferred_request": None, "messages": [HumanMessage(content=deferred)]}

    def output_filter(self, state: AgentState) -> dict[str, Any]:
        """Filter the reply before it is checkpointed, so history holds what the User saw."""
        last = state["messages"][-1]
        scanned = self._ports.pii.scan(last.text)
        return {"messages": [AIMessage(content=scanned.text, id=last.id)]}

    def respond(self, state: AgentState, runtime: Runtime[TurnRuntime]) -> dict[str, Any]:
        runtime.context.outbound.emit(self._reply(state))
        return {}

    def _reply(self, state: AgentState) -> AgentReply:
        return AgentReply(
            conversation_id=state["conversation_id"],
            trace_id=state["trace_id"],
            sent_at=datetime.now(UTC),
            markdown=_last_text(state),
            message_class=state.get("message_class"),
        )
