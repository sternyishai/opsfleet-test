"""One finished span, as the ``Tracer`` writes it and reads it back (HLD 08, LLD 06).

``/trace`` and ``/stats`` are built from these records only, so the attribute names on them
are a contract (docs/lld/06-ports.md § Trace attributes).
"""

from datetime import datetime

from pydantic import BaseModel

type AttrValue = str | int | float | bool | list[str] | list[int] | None


class SpanRecord(BaseModel):
    trace_id: str | None
    """The turn's ID, which is also the reference ID shown to the User. None outside a turn."""
    span_id: str
    parent_id: str | None
    """None for the root span (``turn``) and for spans opened outside a turn."""
    name: str
    start: datetime
    duration_ms: float
    attributes: dict[str, AttrValue]
    error: dict[str, str] | None = None
    """``{"class": ErrorClass, "type": exception type, "detail": ...}``; never row values."""
