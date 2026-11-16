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
a deferred request goes back to ``retrieve_trios``; and the Persona branch ``router`` ->
``persona_permission`` -> ``draft_persona`` -> ``persona_gate`` -> ``publish_persona``, or
-> ``rollback_persona``, then ``after``. Between ``router`` and every branch sits
``apply_preferences``; when the router found standing preferences, ``save_preferences`` runs
beside it and ends there, and a preference-only turn goes to ``acknowledge``.
"""

import functools
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.errors import GraphBubbleUp
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

from agent.composition import Ports
from agent.config import Limits
from agent.contracts.channel import AgentReply
from agent.contracts.router import Action, RouterDecision, Verdict
from agent.core.catalog import render
from agent.core.confirmation import looks_like_reply
from agent.core.input_rules import screen
from agent.domain.identity import UserId
from agent.domain.profile import (
    PreferenceField,
    PreferenceScope,
    PreferenceStatement,
    UserProfile,
    is_option,
    merged,
)
from agent.errors import AgentError, ErrorClass, MessageClass
from agent.graph.analysis import analysis
from agent.graph.deletion import DeletionNodes
from agent.graph.persona import PersonaNodes
from agent.graph.prompts import router_messages
from agent.graph.state import TURN_KEYS, AgentGraph, AgentState, TurnRuntime
from agent.ports.llm import LLMFactory, ModelStep
from agent.ports.tracer import Tracer
from agent.tools.analysis import traced_trio_search


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
    PERSONA_GATE = "persona_gate"  # the five checks; a few model calls, no BigQuery
    PUBLISH_PERSONA = "publish_persona"  # publish, or record_rejection
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

    def add(node: Node, action: Callable[..., Any], **kwargs: Any) -> None:
        g.add_node(node, _traced(node, action, ports.tracer), **kwargs)

    add(Node.LOAD_CONTEXT, nodes.load_context)
    add(Node.INPUT_GUARD, nodes.input_guard)
    add(Node.ROUTER, _passthrough)
    add(Node.APPLY_PREFERENCES, nodes.apply_preferences)
    add(Node.SAVE_PREFERENCES, nodes.save_preferences)
    add(Node.ACKNOWLEDGE, nodes.acknowledge)
    add(Node.RETRIEVE_TRIOS, nodes.retrieve_trios)

    def run_analysis(state: AgentState) -> dict[str, Any]:
        return analysis(state, ports, limits)

    add(Node.ANALYSIS, run_analysis)
    deletion = DeletionNodes(
        ports,
        limits,
        execute=Node.EXECUTE_DELETION,
        after=Node.AFTER,
        restart=Node.LOAD_CONTEXT,
    )
    add(Node.PLAN_DELETION, deletion.plan_deletion)
    add(Node.CONFIRM_DELETION, deletion.confirm_deletion, destinations=deletion.confirm_to)
    add(Node.EXECUTE_DELETION, deletion.execute_deletion)
    persona = PersonaNodes(
        ports,
        limits,
        refuse=Node.REFUSE,
        draft=Node.DRAFT_PERSONA,
        rollback=Node.ROLLBACK_PERSONA,
    )
    add(Node.PERSONA_PERMISSION, persona.persona_permission)
    add(Node.DRAFT_PERSONA, persona.draft_persona)
    add(Node.PERSONA_GATE, persona.persona_gate)
    add(Node.PUBLISH_PERSONA, persona.publish_persona)
    add(Node.ROLLBACK_PERSONA, persona.rollback_persona)
    add(Node.REFUSE, nodes.refuse)
    add(Node.AFTER, nodes.after)
    add(Node.OUTPUT_FILTER, nodes.output_filter)
    add(Node.RESPOND, nodes.respond)

    g.add_edge(START, Node.LOAD_CONTEXT)
    g.add_conditional_edges(Node.LOAD_CONTEXT, _refused_or(Node.INPUT_GUARD))
    g.add_conditional_edges(Node.INPUT_GUARD, _refused_or(Node.ROUTER))
    g.add_conditional_edges(
        Node.ROUTER, _with_saving, [Node.APPLY_PREFERENCES, Node.SAVE_PREFERENCES]
    )
    g.add_edge(Node.SAVE_PREFERENCES, END)
    g.add_conditional_edges(Node.APPLY_PREFERENCES, _by_action)
    g.add_edge(Node.ACKNOWLEDGE, Node.AFTER)
    g.add_edge(Node.RETRIEVE_TRIOS, Node.ANALYSIS)
    g.add_edge(Node.ANALYSIS, Node.AFTER)
    g.add_conditional_edges(Node.PLAN_DELETION, _planned)
    g.add_edge(Node.EXECUTE_DELETION, Node.AFTER)
    g.add_conditional_edges(Node.PERSONA_PERMISSION, persona.permission_to)
    g.add_conditional_edges(Node.DRAFT_PERSONA, persona.drafted_to(Node.PERSONA_GATE, Node.AFTER))
    g.add_edge(Node.PERSONA_GATE, Node.PUBLISH_PERSONA)
    g.add_edge(Node.PUBLISH_PERSONA, Node.AFTER)
    g.add_edge(Node.ROLLBACK_PERSONA, Node.AFTER)
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


def _traced(node: Node, action: Callable[..., Any], tracer: Tracer) -> Callable[..., Any]:
    """``action`` inside a span named after its node. ``wraps`` keeps the signature, which
    LangGraph reads to decide whether to pass ``runtime``. A pause (``interrupt()``) is not an
    error; any other exception is recorded on the node's span, then ends the turn as before."""

    @functools.wraps(action)
    def run(*args: Any, **kwargs: Any) -> Any:
        with tracer.span(node.value) as span:
            try:
                return action(*args, **kwargs)
            except GraphBubbleUp:
                raise
            except AgentError as exc:
                span.record_error(exc.error_class, exc)
                raise
            except Exception as exc:
                span.record_error(ErrorClass.UNCLASSIFIED, exc)
                raise

    return run


def _passthrough(state: AgentState) -> dict[str, Any]:
    return {}


def _with_saving(state: AgentState) -> list[str]:
    """``save_preferences`` beside ``apply_preferences`` only when there is something to
    save: off the critical path, and its own branch ends at once (HLD 01 § Agent graph)."""
    router = state["router"]
    if router is not None and _standing(router.preferences):
        return [Node.APPLY_PREFERENCES, Node.SAVE_PREFERENCES]
    return [Node.APPLY_PREFERENCES]


def _by_action(state: AgentState) -> str:
    router = state["router"]
    action = router.action if router is not None else None
    if action is Action.DELETE_REPORTS:
        return Node.PLAN_DELETION
    if action is Action.CHANGE_PERSONA:
        return Node.PERSONA_PERMISSION
    if action is Action.PREFERENCE_ONLY:
        return Node.ACKNOWLEDGE
    return Node.RETRIEVE_TRIOS


def _standing(statements: list[PreferenceStatement]) -> list[PreferenceStatement]:
    return [s for s in statements if s.scope is PreferenceScope.STANDING]


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


def _error_class(exc: Exception) -> ErrorClass:
    return exc.error_class if isinstance(exc, AgentError) else ErrorClass.UNCLASSIFIED


_FIELD_WORDS: dict[PreferenceField, str] = {
    PreferenceField.FORMAT: "format",
    PreferenceField.DEPTH: "level of detail",
    PreferenceField.CHARTS: "charts",
    PreferenceField.LANGUAGE: "language",
}


def _acknowledgement(statements: list[PreferenceStatement]) -> str:
    """What a preference-only turn says: what was understood, and for how long. Written
    from the screened statements, not by a model, so it names nothing that was not applied.
    It runs beside the save and cannot see it fail; a failed save is on the trace."""
    lines = []
    for s in statements:
        what = (
            f'"{s.value}"'
            if s.field is PreferenceField.NOTE
            else f"{_FIELD_WORDS[s.field]}: {s.value.replace('_', ' ')}"
        )
        when = "from now on" if s.scope is PreferenceScope.STANDING else "this time only"
        lines.append(f"- {what}, {when}")
    tail = (
        "\nSee or remove your saved preferences any time with /profile."
        if _standing(statements)
        else ""
    )
    return "Noted:\n" + "\n".join(lines) + tail


def _last_text(state: AgentState) -> str:
    return state["messages"][-1].text


def route(
    llm: LLMFactory, history: Sequence[AnyMessage], text: str, *, turns: int
) -> RouterDecision:
    """The router call (``ModelStep.ROUTER``) on ``text`` with ``history`` as context: the
    input guard node's model step, also run on its own by the router step eval."""
    # json_schema: Gemini's native schema mode resolves the nested $defs of RouterDecision.
    model = llm.chat(ModelStep.ROUTER).with_structured_output(RouterDecision, method="json_schema")
    decision = model.invoke(router_messages(list(history), text, turns=turns))
    assert isinstance(decision, RouterDecision)
    return decision


class _Nodes:
    def __init__(self, ports: Ports, limits: Limits) -> None:
        self._ports = ports
        self._limits = limits

    def load_context(self, state: AgentState, runtime: Runtime[TurnRuntime]) -> dict[str, Any]:
        user_id = runtime.context.user_id
        user = self._ports.users.load(user_id)
        update = {
            **_TURN_DEFAULTS,
            "user": user,
            "persona": self._ports.personas.active(),
            "profile": self._stored_profile(user_id),
        }
        if user is None or user.role is None:  # unknown, inactive or no role: default deny
            update |= {"error_class": ErrorClass.PERMANENT, "message_class": MessageClass.NO_ROLE}
        else:
            update["access_version_seen"] = user.access_version
        return update

    def _stored_profile(self, user_id: UserId) -> UserProfile:
        """A profile only shapes presentation, so a store that cannot be read means an empty
        one for this turn, traced on its own span, never a failed turn."""
        profiles = self._ports.profiles
        if profiles is None:
            return UserProfile(user_id=user_id)
        try:
            return profiles.load(user_id)
        except Exception as exc:
            with self._ports.tracer.span("profile_load") as span:
                span.record_error(_error_class(exc), exc)
            return UserProfile(user_id=user_id)

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
        decision = route(
            self._ports.llm, messages[:-1], _last_text(state), turns=self._limits.verbatim_turns
        )
        update: dict[str, Any] = {"router": decision, "deferred_request": decision.deferred_request}
        if decision.verdict in _BLOCKED:
            update |= {
                "error_class": ErrorClass.PERMANENT,
                "message_class": _BLOCKED[decision.verdict],
            }
        return update

    def apply_preferences(self, state: AgentState) -> dict[str, Any]:
        """This turn's profile: the stored one, then standing, then this-turn statements, so
        "this time, prose" beats "always tables" said in the same message (HLD 05)."""
        router = state["router"]
        statements = self._safe(router.preferences if router else [])
        if not statements:
            return {}
        ordered = sorted(statements, key=lambda s: s.scope is PreferenceScope.THIS_TURN)
        profile = merged(
            state["profile"],
            ordered,
            at=datetime.now(UTC),
            max_notes=self._limits.profile_max_notes,
        )
        return {"profile": profile}

    def save_preferences(self, state: AgentState) -> dict[str, Any]:
        """The parallel branch. Writes no state (it runs beside ``apply_preferences``), and a
        failure is recorded on its span and swallowed: the answer does not wait on it."""
        router = state["router"]
        profiles = self._ports.profiles
        statements = self._safe(_standing(router.preferences if router else []))
        if profiles is None or not statements:
            return {}
        user = state["user"]
        assert user is not None
        with self._ports.tracer.span("profile_save") as span:
            try:
                profiles.save_standing(user.user_id, statements)
            except Exception as exc:
                span.record_error(_error_class(exc), exc)
                return {}
            span.set(
                **{
                    "profile.saved": len(statements),
                    "profile.fields": sorted({s.field.value for s in statements}),
                }
            )
        return {}

    def acknowledge(self, state: AgentState) -> dict[str, Any]:
        """A preference-only turn. If the guard's rules took out every statement, nothing
        was understood that may be kept: the turn is refused like any injection."""
        router = state["router"]
        assert router is not None
        statements = self._safe(router.preferences)
        if not statements:
            message_class = MessageClass.INJECTION
            text = render(message_class, state["persona"].persona, ref=state["trace_id"])
            return {
                "error_class": ErrorClass.PERMANENT,
                "message_class": message_class,
                "messages": [AIMessage(content=text)],
            }
        return {"messages": [AIMessage(content=_acknowledgement(statements))]}

    def _safe(self, statements: list[PreferenceStatement]) -> list[PreferenceStatement]:
        """The statements that may be applied, saved and acknowledged: the input guard's rules
        again on each value (the model wrote it; HLD 05 § What is stored), and only values
        that are one of the field's options, so the acknowledgement names nothing the merge
        would skip."""
        max_chars = self._limits.input_max_chars
        return [
            s for s in statements if is_option(s) and screen(s.value, max_chars=max_chars) is None
        ]

    def retrieve_trios(self, state: AgentState) -> dict[str, Any]:
        user = state["user"]
        assert user is not None
        router = state["router"]
        question = (router.search_question if router else None) or _last_text(state)
        result = traced_trio_search(
            self._ports.trios,
            self._ports.tracer,
            question,
            user.scope,
            limit=self._limits.trio_top_k,
            span_name="trio_search",
        )
        if result is None:
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
