"""ChannelAdapter for the CLI: prints each outbound message to stdout."""

import sys
from typing import TextIO
from uuid import UUID

from agent.contracts.channel import AgentReply, ConfirmationRequest, OutboundMessage, ProgressEvent


class StdoutChannel:
    """Also remembers the Conversation the last message belonged to, so the CLI can send the
    next message into it (``ChatService`` creates the ID on the first turn)."""

    def __init__(self, stream: TextIO = sys.stdout) -> None:
        self._stream = stream
        self.conversation_id: UUID | None = None

    def deliver(self, message: OutboundMessage) -> None:
        self.conversation_id = message.conversation_id
        match message:
            case ProgressEvent():
                text = f"… {message.text}"
            case ConfirmationRequest():
                lines = [f"  {i.index}. {i.label}" for i in message.items]
                text = "\n".join([message.text, *lines])
            case AgentReply():
                text = message.markdown
        print(f"{text}\n", file=self._stream, flush=True)
