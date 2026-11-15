"""Tracer writing one JSON line per span to ``traces/<date>.jsonl``. Minimal in slice 1: the
root span and plain child spans; timing details, the step tree and OTel export are slice 5.

Never fails a turn: a write error is swallowed (the trace is lost, the answer is not).
"""

import json
import secrets
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent.errors import ErrorClass
from agent.ports.tracer import AttrValue

_trace_id: ContextVar[str | None] = ContextVar("trace_id", default=None)


class _JsonlSpan:
    def __init__(self, name: str, attributes: dict[str, AttrValue]) -> None:
        self.name = name
        self.attributes = dict(attributes)
        self.error: dict[str, str] | None = None

    def set(self, **attributes: AttrValue) -> None:
        self.attributes.update(attributes)

    def record_error(self, error_class: ErrorClass, exc: BaseException) -> None:
        self.error = {"class": error_class.value, "type": type(exc).__name__, "detail": str(exc)}


class JsonlTracer:
    def __init__(self, traces_dir: Path) -> None:
        self._dir = traces_dir

    @contextmanager
    def turn(self, **attributes: AttrValue) -> Iterator[tuple[str, _JsonlSpan]]:
        trace_id = secrets.token_hex(6)
        token = _trace_id.set(trace_id)
        try:
            with self._span("turn", trace_id, attributes) as span:
                yield trace_id, span
        finally:
            _trace_id.reset(token)

    @contextmanager
    def span(self, name: str, **attributes: AttrValue) -> Iterator[_JsonlSpan]:
        with self._span(name, _trace_id.get(), attributes) as span:
            yield span

    def current_trace_id(self) -> str | None:
        return _trace_id.get()

    @contextmanager
    def _span(
        self, name: str, trace_id: str | None, attributes: dict[str, AttrValue]
    ) -> Iterator[_JsonlSpan]:
        span = _JsonlSpan(name, attributes)
        started = time.perf_counter()
        start = datetime.now(UTC)
        try:
            yield span
        finally:
            self._write(
                {
                    "trace_id": trace_id,
                    "name": name,
                    "start": start.isoformat(),
                    "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                    "attributes": span.attributes,
                    "error": span.error,
                }
            )

    def _write(self, record: dict[str, Any]) -> None:
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            path = self._dir / f"{datetime.now(UTC).date().isoformat()}.jsonl"
            with path.open("a") as f:
                f.write(json.dumps(record, default=str) + "\n")
        except OSError:
            pass
