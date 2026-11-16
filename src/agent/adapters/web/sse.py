"""The web ``ChannelAdapter``: one per ``POST /api/messages``, feeding that request's SSE
stream (LLD 07 § The chat stream)."""

import threading
from collections import deque

from agent.contracts.channel import OutboundMessage
from agent.ports.tracer import Tracer


class SseChannel:
    """A buffer between the turn's worker thread (``deliver``) and the stream.

    The stream waits with ``wait``, which takes nothing, and then takes with ``take``, which
    never blocks. So a wait cancelled by a disconnect loses no message: whatever is still
    buffered at ``close`` is traced as dropped. After ``close`` (the browser went away)
    ``deliver`` drops each message and records it on a ``channel.dropped`` span; it never
    raises, so the turn runs to its end and is checkpointed, and the reply is in the
    Conversation next time."""

    def __init__(self, tracer: Tracer) -> None:
        self._tracer = tracer
        self._buffer: deque[OutboundMessage] = deque()
        self._ready = threading.Condition()
        self._ended = False
        self._closed = False

    def deliver(self, message: OutboundMessage) -> None:
        with self._ready:
            if not self._closed:
                self._buffer.append(message)
                self._ready.notify_all()
                return
        self._dropped(message)

    def wait(self, timeout_s: float) -> None:
        """Until a message is buffered, the turn has ended, or ``timeout_s`` passes."""
        with self._ready:
            self._ready.wait_for(lambda: self._buffer or self._ended, timeout_s)

    def take(self) -> OutboundMessage | None:
        """The next buffered message, or None when there is none yet."""
        with self._ready:
            return self._buffer.popleft() if self._buffer else None

    @property
    def finished(self) -> bool:
        """The turn has ended and every message has been taken."""
        with self._ready:
            return self._ended and not self._buffer

    def end(self) -> None:
        """Called by the worker when ``handle`` returns, so a turn that sent nothing (the
        output filter failed closed) still ends the stream."""
        with self._ready:
            self._ended = True
            self._ready.notify_all()

    def close(self) -> None:
        with self._ready:
            self._closed = True
            unsent = list(self._buffer)
            self._buffer.clear()
        for message in unsent:
            self._dropped(message)

    def _dropped(self, message: OutboundMessage) -> None:
        with self._tracer.span("channel.dropped", kind=message.kind, channel="web"):
            pass
