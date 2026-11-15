"""Tracer: one trace per turn, one span per node and tool call (HLD 08)."""

from contextlib import AbstractContextManager
from datetime import datetime
from typing import Protocol

from agent.domain.traces import AttrValue, SpanRecord
from agent.errors import ErrorClass

__all__ = ["AttrValue", "Span", "SpanRecord", "Tracer"]


class Span(Protocol):
    def set(self, **attributes: AttrValue) -> None:
        """Attribute names follow docs/lld/06-ports.md § Trace attributes."""
        ...

    def record_error(self, error_class: ErrorClass, exc: BaseException) -> None: ...


class Tracer(Protocol):
    """Interface:

    - ``turn`` opens the root span and returns it with its ``trace_id``, which is also the
      reference ID shown to the User.
    - ``span`` opens a child of the current span (context-local, so tools need no handle).
    - **Never blocks or fails a turn.** Export is asynchronous and batched; on overflow spans
      are dropped. Adapters: JSONL in ``./traces/`` (always on in the prototype), plus
      OpenTelemetry/OpenInference to Phoenix when it is running.
    - **Never receives result rows or PII.** Callers pass result summaries; output-filter hits
      are recorded as types only.
    - ``read`` returns the finished spans started at or after ``since`` (all of them when
      ``None``), oldest first, for ``Views.trace`` and ``Views.stats``. A span is readable once
      it has ended, so a turn's root span appears after its children. Unreadable records are
      skipped; ``read`` never raises.
    """

    def turn(self, **attributes: AttrValue) -> AbstractContextManager[tuple[str, Span]]: ...

    def span(self, name: str, **attributes: AttrValue) -> AbstractContextManager[Span]: ...

    def current_trace_id(self) -> str | None: ...

    def read(self, since: datetime | None = None) -> list[SpanRecord]: ...
