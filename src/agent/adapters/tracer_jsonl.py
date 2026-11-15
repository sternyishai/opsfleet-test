"""Tracer writing one JSON line per finished span to ``traces/<date>.jsonl`` (HLD 08).

Each line is a ``SpanRecord``: the turn's ``trace_id``, the span's own ``span_id`` and its
``parent_id``, so ``/trace`` can rebuild the step tree from the files alone. The current span
is context-local: a tool opens a child of whatever node span it runs in, with no handle.

Never fails a turn: a write error is swallowed (the trace is lost, the answer is not), and
so is any error from ``export``, the optional second sink (Phoenix).
"""

import secrets
import time
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager, nullcontext
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from agent.errors import ErrorClass
from agent.ports.tracer import AttrValue, SpanRecord

type Export = Callable[[SpanRecord], None]
type Activate = Callable[[str | None, str], AbstractContextManager[None]]
"""Makes a span current in another tracing system while it runs (Phoenix full content)."""

_trace_id: ContextVar[str | None] = ContextVar("trace_id", default=None)
_span_id: ContextVar[str | None] = ContextVar("span_id", default=None)


class _JsonlSpan:
    def __init__(self, name: str, attributes: dict[str, AttrValue]) -> None:
        self.name = name
        self.attributes = dict(attributes)
        self.error: dict[str, str] | None = None

    def set(self, **attributes: AttrValue) -> None:
        self.attributes.update(attributes)

    def record_error(self, error_class: ErrorClass, exc: BaseException) -> None:
        # The class and type only: an exception's text, classified or not, can wrap a
        # provider message that quotes a value from the data.
        self.error = {"class": error_class.value, "type": type(exc).__name__}


class JsonlTracer:
    def __init__(
        self, traces_dir: Path, export: Export | None = None, activate: Activate | None = None
    ) -> None:
        self._dir = traces_dir
        self._export = export
        self._activate = activate

    @contextmanager
    def turn(self, **attributes: AttrValue) -> Iterator[tuple[str, _JsonlSpan]]:
        trace_id = secrets.token_hex(6)
        token = _trace_id.set(trace_id)
        try:
            with self._span("turn", trace_id, {"trace_id": trace_id, **attributes}) as span:
                yield trace_id, span
        finally:
            _trace_id.reset(token)

    @contextmanager
    def span(self, name: str, **attributes: AttrValue) -> Iterator[_JsonlSpan]:
        with self._span(name, _trace_id.get(), attributes) as span:
            yield span

    def current_trace_id(self) -> str | None:
        return _trace_id.get()

    def read(self, since: datetime | None = None) -> list[SpanRecord]:
        try:
            files = sorted(self._dir.glob("*.jsonl"))
        except OSError:
            return []
        first_day = since.date().isoformat() if since else ""
        found: list[SpanRecord] = []
        for path in files:
            if path.stem < first_day:  # files are named by the UTC day their spans ended
                continue
            found.extend(r for r in _records(path) if since is None or r.start >= since)
        return sorted(found, key=lambda r: r.start)

    @contextmanager
    def _span(
        self, name: str, trace_id: str | None, attributes: dict[str, AttrValue]
    ) -> Iterator[_JsonlSpan]:
        span = _JsonlSpan(name, attributes)
        span_id = secrets.token_hex(8)
        parent_id = _span_id.get()
        token = _span_id.set(span_id)
        started = time.perf_counter()
        start = datetime.now(UTC)
        active = self._activate(trace_id, span_id) if self._activate else nullcontext()
        try:
            with active:
                yield span
        finally:
            _span_id.reset(token)
            self._finish(
                SpanRecord(
                    trace_id=trace_id,
                    span_id=span_id,
                    parent_id=parent_id,
                    name=name,
                    start=start,
                    duration_ms=round((time.perf_counter() - started) * 1000, 1),
                    attributes=span.attributes,
                    error=span.error,
                )
            )

    def _finish(self, record: SpanRecord) -> None:
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            path = self._dir / f"{datetime.now(UTC).date().isoformat()}.jsonl"
            with path.open("a") as f:
                f.write(record.model_dump_json() + "\n")
        except (OSError, ValueError):
            pass
        if self._export is not None:
            try:
                self._export(record)
            except Exception:
                pass  # an optional viewer being down never reaches the turn


def _records(path: Path) -> Iterator[SpanRecord]:
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return
    for line in lines:
        try:
            yield SpanRecord.model_validate_json(line)
        except ValidationError:
            continue  # a torn line, or a record from before span IDs existed
