"""ChatService: the one inbound entry point every channel calls.

The CLI loop, the web adapter (ADR 0010) and, in production, the Channel Gateway all call
``handle``. It is the deep module in front of the graph: identity, Conversation creation,
resume after ``interrupt()``, the graph-boundary catch-all and the single exit are all behind
one method.
"""

from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID, uuid4

from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.types import Command

from agent.composition import Ports
from agent.contracts.channel import (
    AgentReply,
    ConfirmationRequest,
    ConfirmationResponse,
    InboundMessage,
)
from agent.core.catalog import render
from agent.core.outbound import Outbound
from agent.domain.profile import UserProfile
from agent.errors import AgentError, ErrorClass, MessageClass
from agent.graph.state import TURN_KEYS, AgentGraph, AgentState, TurnRuntime
from agent.ports.channel import ChannelAdapter
from agent.ports.tracer import AttrValue

_REFUSALS = frozenset(
    {
        MessageClass.OFF_TOPIC,
        MessageClass.PII_REQUEST,
        MessageClass.INJECTION,
        MessageClass.NO_ROLE,
        MessageClass.OUT_OF_SCOPE,
        MessageClass.PERSONA_FORBIDDEN,
    }
)
"""A refusal is the safety layer working, not a failed task: ``/stats`` counts it apart."""

_ANSWERS = frozenset({MessageClass.NOTHING_MATCHED})
"""Catalog messages that are the answer, not a failure: nothing matched the delete."""


def turn_outcome(paused: bool, failed: bool, message_class: MessageClass | None) -> str:
    """``outcome`` on the root span: ``answered``, ``confirmation`` (paused at a Deletion
    Plan), ``refused``, ``gave_up`` (a catalog message instead of an answer) or ``error``."""
    if failed:
        return "error"
    if paused:
        return "confirmation"
    if message_class is None or message_class in _ANSWERS:
        return "answered"
    return "refused" if message_class in _REFUSALS else "gave_up"


def profile_snapshot(profile: UserProfile | None) -> str:
    """The profile the answer was written with, as typed values and a note count only:
    note text is free text, so it stays out of the trace."""
    if profile is None:
        return ""
    values = [
        f"{name}={getattr(profile, name).value}"
        for name in ("format", "depth", "charts", "language")
        if getattr(profile, name) is not None
    ]
    values += [f"{name}={v.value}" for name, v in sorted(profile.attributes.items())]
    if profile.notes:
        values.append(f"notes={len(profile.notes)}")
    return " ".join(values)


class ChatService:
    """Interface:

    - ``handle`` runs one turn for ``message`` and sends everything it produces (progress
      events, a ``ConfirmationRequest``, the ``AgentReply``) to ``channel`` through a per-turn
      ``Outbound``, so every message passes the output PII filter.
    - ``InboundMessage.conversation_id = None`` starts a Conversation; the new ID is on every
      outbound message.
    - If the Conversation is paused at a Deletion Plan, the message (text or a
      ``ConfirmationResponse``) resumes it; ``parse_reply`` decides what happens. Only the
      plan's User can resume it; anyone else, and a ``ConfirmationResponse`` with nothing
      paused, gets ``PLAN_EXPIRED`` and changes nothing.
    - A pause ends the turn with the ``ConfirmationRequest`` the graph paused with.
    - The User comes from ``message.user_id``, which the driving adapter must have resolved
      (SSO, IAP, ``--as``, or the web prototype's dev picker). An unknown User gets ``NO_ROLE``.
    - **Never raises.** Every turn ends in a delivered message; an unclassified error becomes
      the ``UNCLASSIFIED`` catalog message with its reference ID. This is the graph-boundary
      catch-all: the Conversation's state up to the failed node is already checkpointed.
    - Blocking: returns when the turn has ended (a reply or a confirmation prompt was sent).
    """

    def __init__(self, ports: Ports, graph: AgentGraph) -> None:
        self._ports = ports
        self._graph = graph

    def handle(
        self, message: InboundMessage | ConfirmationResponse, channel: ChannelAdapter
    ) -> None:
        conversation_id = message.conversation_id or uuid4()
        outbound = Outbound(channel, self._ports.pii, self._ports.tracer)
        config: RunnableConfig = {"configurable": {"thread_id": str(conversation_id)}}
        with self._ports.tracer.turn(
            conversation_id=str(conversation_id),
            # A button press carries no channel: the Conversation's is read after the turn.
            channel=message.channel.value if isinstance(message, InboundMessage) else None,
            **{"user.id": message.user_id},
        ) as (trace_id, span):
            failure: AgentError | None = None
            paused = False
            turn_id = uuid4().hex
            try:
                result = self._graph.invoke(
                    self._turn_input(message, config, conversation_id, turn_id, trace_id),
                    config,
                    context=TurnRuntime(user_id=message.user_id, outbound=outbound),
                )
                for pause in result.get("__interrupt__", []):
                    paused = True
                    outbound.emit(
                        ConfirmationRequest.model_validate(
                            pause.value["request"]
                            | {
                                "conversation_id": conversation_id,
                                "trace_id": trace_id,
                                "sent_at": datetime.now(UTC),
                            }
                        )
                    )
            except AgentError as exc:
                failure = exc
                span.record_error(exc.error_class, exc)
                self._fail(outbound, exc.message_class, conversation_id, trace_id)
            except Exception as exc:
                failure = AgentError(type(exc).__name__)
                span.record_error(ErrorClass.UNCLASSIFIED, exc)
                self._fail(outbound, MessageClass.UNCLASSIFIED, conversation_id, trace_id)
            span.set(**self._turn_attributes(config, paused, failure))

    def _turn_attributes(
        self, config: RunnableConfig, paused: bool, failure: AgentError | None
    ) -> dict[str, AttrValue]:
        """The root span's contract (LLD 06 § Trace attributes), read from the Conversation's
        checkpoint, so a turn that failed half-way still says who asked and what was routed.
        Never raises: a trace that cannot be read is less important than the answer."""
        try:
            state: dict[str, Any] = dict(self._graph.get_state(config).values)
        except Exception:
            state = {}
        user = state.get("user")
        router = state.get("router")
        persona = state.get("persona")
        message_class = failure.message_class if failure else state.get("message_class")
        error_class = failure.error_class if failure else state.get("error_class")
        channel = state.get("channel")
        return {
            **({"channel": channel.value} if channel else {}),
            "user.role": user.role.value if user and user.role else None,
            "router.verdict": router.verdict.value if router else None,
            "router.action": router.action.value if router and router.action else None,
            "persona.version": persona.version if persona else None,
            "profile.snapshot": profile_snapshot(state.get("profile")),
            "retrieval.gap": bool(state.get("ran_sql")) and not state.get("trios"),
            "retrieval.degraded": bool(state.get("retrieval_degraded")),
            "outcome": turn_outcome(paused, failure is not None, message_class),
            "error_class": error_class.value if error_class else None,
            "message_class": message_class.value if message_class else None,
        }

    def _turn_input(
        self,
        message: InboundMessage | ConfirmationResponse,
        config: RunnableConfig,
        conversation_id: UUID,
        turn_id: str,
        trace_id: str,
    ) -> AgentState | Command:
        """A new turn, or the resume of a paused one. The resume value is plain JSON (it is
        checkpointed) and carries the new turn's IDs for the nodes after the pause."""
        snapshot = self._graph.get_state(config)
        plan = snapshot.values.get("open_plan") if snapshot.interrupts else None
        if plan is None:
            if isinstance(message, ConfirmationResponse):
                raise AgentError("no open plan", message_class=MessageClass.PLAN_EXPIRED)
            turn: AgentState = {
                "messages": [HumanMessage(content=message.text)],
                "conversation_id": conversation_id,
                "channel": message.channel,
                "turn_id": turn_id,
                "trace_id": trace_id,
            }
            # Cleared here as well as in load_context, so a turn that fails before
            # load_context never reports the previous turn's router or retrieval.
            return cast(AgentState, dict.fromkeys(TURN_KEYS - {"turn_id", "trace_id"}) | turn)
        if message.user_id != plan.author_id:
            raise AgentError("not the plan's User", message_class=MessageClass.PLAN_EXPIRED)
        reply: dict[str, Any] = (
            {"response": message.model_dump(mode="json")}
            if isinstance(message, ConfirmationResponse)
            else {"text": message.text}
        )
        return Command(resume=reply | {"turn_id": turn_id, "trace_id": trace_id})

    def _fail(
        self,
        outbound: Outbound,
        message_class: MessageClass,
        conversation_id: UUID,
        trace_id: str,
    ) -> None:
        persona = self._ports.personas.active().persona
        outbound.emit(
            AgentReply(
                conversation_id=conversation_id,
                trace_id=trace_id,
                sent_at=datetime.now(UTC),
                markdown=render(message_class, persona, ref=trace_id),
                message_class=message_class,
            )
        )
