"""ChatService: the one inbound entry point every channel calls.

The CLI loop, the web adapter (ADR 0010) and, in production, the Channel Gateway all call
``handle``. It is the deep module in front of the graph: identity, Conversation creation,
resume after ``interrupt()``, the graph-boundary catch-all and the single exit are all behind
one method.
"""

from datetime import UTC, datetime
from typing import Any
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
from agent.errors import AgentError, ErrorClass, MessageClass
from agent.graph.state import AgentGraph, AgentState, TurnRuntime
from agent.ports.channel import ChannelAdapter


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
        with self._ports.tracer.turn(
            conversation_id=str(conversation_id), **{"user.id": message.user_id}
        ) as (trace_id, span):
            try:
                config: RunnableConfig = {"configurable": {"thread_id": str(conversation_id)}}
                turn_id = uuid4().hex
                result = self._graph.invoke(
                    self._turn_input(message, config, conversation_id, turn_id, trace_id),
                    config,
                    context=TurnRuntime(user_id=message.user_id, outbound=outbound),
                )
                for paused in result.get("__interrupt__", []):
                    outbound.emit(
                        ConfirmationRequest.model_validate(
                            paused.value["request"]
                            | {
                                "conversation_id": conversation_id,
                                "trace_id": trace_id,
                                "sent_at": datetime.now(UTC),
                            }
                        )
                    )
            except AgentError as exc:
                span.record_error(exc.error_class, exc)
                self._fail(outbound, exc.message_class, conversation_id, trace_id)
            except Exception as exc:
                span.record_error(ErrorClass.UNCLASSIFIED, exc)
                self._fail(outbound, MessageClass.UNCLASSIFIED, conversation_id, trace_id)

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
            return {
                "messages": [HumanMessage(content=message.text)],
                "conversation_id": conversation_id,
                "channel": message.channel,
                "turn_id": turn_id,
                "trace_id": trace_id,
            }
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
