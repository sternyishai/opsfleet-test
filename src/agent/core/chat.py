"""ChatService: the one inbound entry point every channel calls. Stub.

The CLI loop, the web adapter (ADR 0010) and, in production, the Channel Gateway all call
``handle``. It is the deep module in front of the graph: identity, Conversation creation,
resume after ``interrupt()``, the graph-boundary catch-all and the single exit are all behind
one method.
"""

from agent.composition import Ports
from agent.contracts.channel import ConfirmationResponse, InboundMessage
from agent.ports.channel import ChannelAdapter


class ChatService:
    """Interface:

    - ``handle`` runs one turn for ``message`` and sends everything it produces (progress
      events, a ``ConfirmationRequest``, the ``AgentReply``) to ``channel`` through a per-turn
      ``Outbound``, so every message passes the output PII filter.
    - ``InboundMessage.conversation_id = None`` starts a Conversation; the new ID is on every
      outbound message.
    - If the Conversation is paused at a Deletion Plan, the message (text or a
      ``ConfirmationResponse``) resumes it; ``parse_reply`` decides what happens.
    - The User comes from ``message.user_id``, which the driving adapter must have resolved
      (SSO, IAP, ``--as``, or the web prototype's dev picker). An unknown User gets ``NO_ROLE``.
    - **Never raises.** Every turn ends in a delivered message; an unclassified error becomes
      the ``UNCLASSIFIED`` catalog message with its reference ID.
    - Blocking: returns when the turn has ended (a reply or a confirmation prompt was sent).
    """

    def __init__(self, ports: Ports) -> None:
        self._ports = ports

    def handle(
        self, message: InboundMessage | ConfirmationResponse, channel: ChannelAdapter
    ) -> None:
        raise NotImplementedError("slice 1: walking skeleton")
