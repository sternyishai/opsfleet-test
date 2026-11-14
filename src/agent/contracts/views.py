"""Results of the model-free commands, shared by every channel (docs/lld/07-web.md).

The CLI renders them as text (``/reports``, ``/profile``, ``/trace``, ``/stats``); the web
adapter returns them as JSON. Report views reuse the tool outputs, so the agent, the CLI and
the UI all see the same shape.
"""

from datetime import datetime

from pydantic import BaseModel, Field


class SpanNode(BaseModel):
    """One line of the ``/trace`` step tree."""

    name: str = Field(description='Node or tool name, e.g. "run_sql #2".')
    duration_ms: int
    summary: str = Field(description='"guard: rewritten (scope: 2 Brands)  rows: 12"')
    children: list["SpanNode"] = Field(default_factory=list)


class TraceView(BaseModel):
    trace_id: str
    started_at: datetime
    action: str | None
    duration_ms: int
    tokens: int
    outcome: str
    root: list[SpanNode]


class StatsView(BaseModel):
    """The metric families of HLD 08, computed from trace attributes only."""

    since: datetime
    turns: int
    users: int
    families: dict[str, dict[str, float | int | str]] = Field(
        description='{"quality": {"task_success": 0.91, ...}, "safety": {...}, ...}'
    )
