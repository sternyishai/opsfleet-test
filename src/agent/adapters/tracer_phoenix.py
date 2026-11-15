"""The second sink of ``JsonlTracer``: each finished span to Arize Phoenix as an
OpenTelemetry span with OpenInference attributes (HLD 08 § In the prototype).

Spans are rebuilt from the ``SpanRecord`` the JSONL tracer wrote, with the same trace and
span IDs, so the reference ID a User quotes finds the trace in Phoenix too. Export is the OTel
SDK's ``BatchSpanProcessor``: a bounded queue drained by a background thread, which drops
spans when full and never blocks a turn. Phoenix is used only when it answers at start-up.
"""

import atexit
import logging
import socket
from collections.abc import Mapping
from urllib.parse import urlsplit

from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import SpanContext, Status, StatusCode, TraceFlags

from agent.ports.tracer import AttrValue, SpanRecord

_RESOURCE = Resource.create({"service.name": "retail-analysis-assistant"})
_TOOLS = frozenset(
    {
        "get_schema",
        "search_golden_trios",
        "run_sql",
        "save_report",
        "list_my_reports",
        "get_my_report",
        "plan_deletion",
        "execute_deletion",
    }
)
_GUARDRAILS = frozenset({"query_guard", "outbound", "input_guard", "output_filter"})


class PhoenixExporter:
    """``export(record)`` queues one span for Phoenix. Build it with ``connect``."""

    def __init__(self, processor: BatchSpanProcessor) -> None:
        self._processor = processor

    @classmethod
    def connect(cls, endpoint: str, timeout_s: float = 0.3) -> "PhoenixExporter | None":
        """An exporter if something listens at ``endpoint`` (``http://host:port``), else
        None: with Phoenix down, the prototype runs on JSONL alone."""
        url = urlsplit(endpoint)
        try:
            with socket.create_connection((url.hostname or "", url.port or 80), timeout_s):
                pass
        except OSError:
            return None
        # A Phoenix that stops later fails each batch in the background thread; say so once
        # at ERROR, not as a warning on every batch in the middle of the chat.
        logging.getLogger("opentelemetry.exporter.otlp.proto.http").setLevel(logging.ERROR)
        logging.getLogger("opentelemetry.sdk.trace.export").setLevel(logging.ERROR)
        processor = BatchSpanProcessor(
            OTLPSpanExporter(endpoint=f"{endpoint.rstrip('/')}/v1/traces", timeout=5)
        )
        atexit.register(processor.force_flush, 2000)  # the last turn's spans, on quit
        return cls(processor)

    def __call__(self, record: SpanRecord) -> None:
        self._processor.on_end(to_otel(record))


def to_otel(record: SpanRecord) -> ReadableSpan:
    """One ``SpanRecord`` as a finished OTel span. Our 12-hex trace ID and 16-hex span IDs
    are read as the integers OTel uses, so they match the JSONL IDs."""
    trace_id = int(record.trace_id or "0", 16) or 1  # a span outside a turn still exports
    start = int(record.start.timestamp() * 1e9)
    error = record.error
    return ReadableSpan(
        name=record.name,
        context=_context(trace_id, record.span_id),
        parent=_context(trace_id, record.parent_id) if record.parent_id else None,
        resource=_RESOURCE,
        attributes=_attributes(record),
        status=Status(StatusCode.ERROR, error["class"]) if error else Status(StatusCode.OK),
        start_time=start,
        end_time=start + int(record.duration_ms * 1e6),
    )


def _context(trace_id: int, span_id: str) -> SpanContext:
    return SpanContext(
        trace_id=trace_id,
        span_id=int(span_id, 16),
        is_remote=False,
        trace_flags=TraceFlags(TraceFlags.SAMPLED),
    )


def _attributes(record: SpanRecord) -> dict[str, str | int | float | bool | list[str] | list[int]]:
    """Our attributes as they are (OTel has no None, so those are dropped), plus the
    OpenInference names Phoenix's views are built on."""
    ours: Mapping[str, AttrValue] = record.attributes
    out = {k: v for k, v in ours.items() if v is not None}
    out["openinference.span.kind"] = _kind(record.name)
    if record.name == "turn":
        if conversation := ours.get("conversation_id"):
            out["session.id"] = str(conversation)
    elif record.name == "llm":
        if model := ours.get("llm.model"):
            out["llm.model_name"] = str(model)
        prompt, completion = ours.get("llm.tokens.prompt"), ours.get("llm.tokens.completion")
        if isinstance(prompt, int) and isinstance(completion, int):
            out["llm.token_count.prompt"] = prompt
            out["llm.token_count.completion"] = completion
            out["llm.token_count.total"] = prompt + completion
    elif record.name in _TOOLS:
        out["tool.name"] = record.name
    if record.error:
        out["exception.type"] = record.error.get("type", "")
    return out


def _kind(name: str) -> str:
    if name == "turn":
        return "AGENT"
    if name == "llm":
        return "LLM"
    if name in _TOOLS:
        return "TOOL"
    if name in _GUARDRAILS:
        return "GUARDRAIL"
    return "CHAIN"
