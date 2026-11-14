"""The single outbound exit (ADR 0003 layer 3). Stub.

The only code that hands anything to a ``ChannelAdapter`` (and, later, a ``Delivery``
adapter). Progress events, confirmation prompts, catalog messages and final answers all leave
through ``emit``, so the output PII filter is a property of the way out, not a node the graph
happens to pass through.
"""

from agent.contracts.channel import OutboundMessage
from agent.ports.channel import ChannelAdapter
from agent.ports.pii import PiiFilter
from agent.ports.tracer import Tracer


class Outbound:
    """Interface:

    - ``emit`` scans every text field of the message (``templated=True`` for
      ``ProgressEvent``), replaces it with the redacted text, records hit *types* on a span
      and raises the PII alert on any hit, then calls ``ChannelAdapter.deliver``.
    - Never raises: a filter or channel failure is traced; the filter falls back to regex
      inside the adapter and is never skipped.
    - Returns the message as sent, so the ``output_filter`` node can checkpoint filtered text.
    - Built by ``ChatService.handle`` once per turn, around that turn's ``ChannelAdapter``.
    """

    def __init__(self, channel: ChannelAdapter, pii: PiiFilter, tracer: Tracer) -> None:
        self._channel = channel
        self._pii = pii
        self._tracer = tracer

    def emit(self, message: OutboundMessage) -> OutboundMessage:
        raise NotImplementedError
