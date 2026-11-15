"""The single outbound exit (ADR 0003 layer 3).

The only code that hands anything to a ``ChannelAdapter`` (and, later, a ``Delivery``
adapter). Progress events, confirmation prompts, catalog messages and final answers all leave
through ``emit``, so the output PII filter is a property of the way out, not a node the graph
happens to pass through.
"""

import logging

from agent.contracts.channel import (
    AgentReply,
    ConfirmationRequest,
    OutboundMessage,
    ProgressEvent,
)
from agent.errors import ErrorClass
from agent.ports.channel import ChannelAdapter
from agent.ports.pii import PiiFilter, PiiType
from agent.ports.tracer import Span, Tracer

_log = logging.getLogger(__name__)


class Outbound:
    """Interface:

    - ``emit`` scans every text field of the message (``templated=True`` for
      ``ProgressEvent``), replaces it with the redacted text, records hit *types* on a span
      and raises the PII alert on any hit, then calls ``ChannelAdapter.deliver``.
    - Never raises: a filter or channel failure is traced; the filter falls back to regex
      inside the adapter and is never skipped. If the filter itself fails, nothing is sent
      (fail closed).
    - Returns the message as sent, so the ``output_filter`` node can checkpoint filtered text.
    - Built by ``ChatService.handle`` once per turn, around that turn's ``ChannelAdapter``.
    """

    def __init__(self, channel: ChannelAdapter, pii: PiiFilter, tracer: Tracer) -> None:
        self._channel = channel
        self._pii = pii
        self._tracer = tracer

    def emit(self, message: OutboundMessage) -> OutboundMessage:
        with self._tracer.span("outbound", kind=message.kind) as span:
            try:
                sent = self._filtered(message, span)
            except Exception as exc:
                span.record_error(ErrorClass.UNCLASSIFIED, exc)
                return message
            try:
                self._channel.deliver(sent)
            except Exception as exc:
                span.record_error(ErrorClass.UNCLASSIFIED, exc)
            return sent

    def _filtered(self, message: OutboundMessage, span: Span) -> OutboundMessage:
        hits: list[PiiType] = []

        def scan(text: str, *, templated: bool = False) -> str:
            result = self._pii.scan(text, templated=templated)
            hits.extend(result.hits)
            return result.text

        match message:
            case ProgressEvent():
                sent: OutboundMessage = message.model_copy(
                    update={"text": scan(message.text, templated=True)}
                )
            case ConfirmationRequest():
                items = [
                    item.model_copy(
                        update={
                            "label": scan(item.label),
                            "reason": scan(item.reason) if item.reason else None,
                        }
                    )
                    for item in message.items
                ]
                sent = message.model_copy(update={"text": scan(message.text), "items": items})
            case AgentReply():
                sent = message.model_copy(update={"markdown": scan(message.markdown)})
        span.set(**{"pii.hits": sorted({h.value for h in hits})})
        if hits:
            _log.warning("output PII filter hit: %s", sorted({h.value for h in hits}))
        return sent
