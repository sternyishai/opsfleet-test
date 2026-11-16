"""The web ``ChannelAdapter``: one per ``POST /api/messages``, feeding that request's SSE
stream (LLD 07 § The chat stream)."""

import queue
import threading

from agent.contracts.channel import OutboundMessage
from agent.ports.tracer import Tracer


class SseChannel:
    """A queue between the turn's worker thread (``deliver``) and the stream (``next``).

    After ``close`` (the browser went away) ``deliver`` drops each message and records it on a
    ``channel.dropped`` span instead; it never raises, so the turn runs to its end and is
    checkpointed, and the reply is in the Conversation next time. Messages queued but never
    streamed when the stream closes are traced as dropped too."""

    def __init__(self, tracer: Tracer) -> None:
        self._tracer = tracer
        self._queue: queue.Queue[OutboundMessage | None] = queue.Queue()
        self._lock = threading.Lock()
        self._closed = False

    def deliver(self, message: OutboundMessage) -> None:
        with self._lock:
            if not self._closed:
                self._queue.put(message)
                return
        self._dropped(message)

    def next(self) -> OutboundMessage | None:
        """The next message, waiting for it; None once the turn has ended (``end``)."""
        return self._queue.get()

    def end(self) -> None:
        """Called by the worker when ``handle`` returns, so a turn that sent nothing (the
        output filter failed closed) still ends the stream."""
        self._queue.put(None)

    def close(self) -> None:
        with self._lock:
            self._closed = True
        while True:
            try:
                message = self._queue.get_nowait()
            except queue.Empty:
                return
            if message is not None:
                self._dropped(message)

    def _dropped(self, message: OutboundMessage) -> None:
        with self._tracer.span("channel.dropped", kind=message.kind, channel="web"):
            pass
