"""``/trace`` and ``/stats`` from finished spans (HLD 08 § In the prototype).

Pure functions over ``SpanRecord``s, so the step tree and every metric are worked out from
the trace attribute names alone (LLD 06 § Trace attributes) and are testable without files.
``Views`` reads the spans through the ``Tracer`` port and checks who may see them.
"""

import math
import re
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime, timedelta

from agent.contracts.views import SpanNode, StatsView, TraceView
from agent.domain.traces import AttrValue, SpanRecord

type Prices = Mapping[str, tuple[float, float]]
"""USD per million tokens (input, output) by model ID."""

_SQL_RAN = ("ok", "empty")
"""``sql.status`` of a statement the warehouse executed."""

_INPUT_GUARD = ("off_topic", "pii_request", "injection")


# --- /trace ---------------------------------------------------------------------------------


def last_turn(spans: Iterable[SpanRecord], user_id: str) -> str | None:
    """The trace ID of the User's latest turn."""
    roots = [s for s in spans if _is_root(s) and s.attributes.get("user.id") == user_id]
    return max(roots, key=lambda s: s.start).trace_id if roots else None


def build_trace(spans: Iterable[SpanRecord], trace_id: str, user_id: str) -> TraceView | None:
    """The step tree of one turn, or None when it is not this User's (or does not exist):
    another User's turn looks the same as a missing one."""
    mine = [s for s in spans if s.trace_id == trace_id]
    root = next((s for s in mine if _is_root(s)), None)
    if root is None or root.attributes.get("user.id") != user_id:
        return None
    children: dict[str, list[SpanRecord]] = defaultdict(list)
    for s in sorted(mine, key=lambda s: s.start):
        if s.parent_id:
            children[s.parent_id].append(s)

    def node(s: SpanRecord) -> SpanNode:
        return SpanNode(
            name=_name(s),
            duration_ms=round(s.duration_ms),
            summary=_summary(s),
            children=[node(c) for c in children[s.span_id]],
        )

    action = root.attributes.get("router.action")
    return TraceView(
        trace_id=trace_id,
        started_at=root.start,
        action=str(action) if action else None,
        duration_ms=round(root.duration_ms),
        tokens=sum(_tokens(s) for s in mine),
        outcome=str(root.attributes.get("outcome") or "unknown"),
        root=[node(c) for c in children[root.span_id]],
    )


def _name(s: SpanRecord) -> str:
    a = s.attributes
    if s.name == "run_sql" and a.get("attempt"):
        return f"run_sql #{a['attempt']}"
    if s.name == "llm":
        return f"llm {a.get('llm.step') or ''}".strip()
    return s.name


def _summary(s: SpanRecord) -> str:
    """One line per span: what a debugger looks at first. Never a row or a SQL text."""
    a = s.attributes
    describe = _SUMMARIES.get(s.name, _generic)
    parts = [p for p in [describe(a)] if p]
    if s.error:
        parts.append(f"error: {s.error.get('class')}")
    return "  ".join(parts)


def _llm(a: Mapping[str, AttrValue]) -> str:
    model = a.get("llm.model") or "no model answered"
    parts = [
        str(model),
        f"{_int(a.get('llm.tokens.prompt')) + _int(a.get('llm.tokens.completion')):,} tokens",
    ]
    if a.get("llm.fallback_used"):
        parts.append("fallback")
    if a.get("llm.errors"):
        parts.append(f"errors: {', '.join(map(str, a['llm.errors'] or []))}")  # type: ignore[arg-type]
    return "  ".join(parts)


def _run_sql(a: Mapping[str, AttrValue]) -> str:
    parts = [str(a.get("sql.status") or "")]
    if a.get("sql.retry_kind"):
        parts.append(str(a["sql.retry_kind"]))
    if a.get("dry_run.bytes") is not None:
        parts.append(f"dry run: {_mb(a['dry_run.bytes'])}")
    if a.get("row_count") is not None:
        parts.append(f"rows: {a['row_count']}")
    if a.get("sql.gave_up"):
        parts.append(f"gave up: {a['sql.gave_up']}")
    return "  ".join(p for p in parts if p)


def _guard(a: Mapping[str, AttrValue]) -> str:
    decision = a.get("guard.decision")
    if decision == "rejected":
        return f"guard: rejected ({a.get('guard.reason')})"
    scope = a.get("guard.scope_brands")
    scope_text = "all Brands" if scope == "all" else f"{scope} Brands"
    return (
        f"guard: allowed{', rewritten' if a.get('guard.rewritten') else ''} (scope: {scope_text})"
    )


def _generic(a: Mapping[str, AttrValue]) -> str:
    shown = [
        f"{k}={_value(v)}"
        for k, v in a.items()
        if v not in (None, [], "") and k not in ("trace_id", "conversation_id", "user.id")
    ]
    return "  ".join(shown[:4])


_SUMMARIES: dict[str, Callable[[Mapping[str, AttrValue]], str]] = {
    "llm": _llm,
    "run_sql": _run_sql,
    "query_guard": _guard,
    "empty_result_probe": lambda a: (
        f"probe: checked {a.get('probe.checked')}, final: {a.get('probe.final')}"
    ),  # noqa: E501
    "outbound": lambda a: f"{a.get('kind')}  PII hits: {len(_list(a.get('pii.hits')))}",
}


# --- /stats ---------------------------------------------------------------------------------

_SINCE = re.compile(r"^(\d+)([mhd])$")
_UNITS = {"m": "minutes", "h": "hours", "d": "days"}


def parse_since(since: str, now: datetime) -> datetime:
    """``"7d"``, ``"24h"`` or ``"30m"`` before ``now``. Anything else is a ``ValueError``."""
    match = _SINCE.match(since.strip())
    if not match:
        raise ValueError(f"since must look like 7d, 24h or 30m, not {since!r}")
    return now - timedelta(**{_UNITS[match[2]]: int(match[1])})


def compute_stats(spans: Iterable[SpanRecord], since: datetime, prices: Prices) -> StatsView:
    """The metric families of HLD 08, from the spans of the turns that started at or after
    ``since``. Rates are 0–1 under keys ending in ``_rate``; a rate with nothing to count
    is ``"n/a"``."""
    spans = [s for s in spans if s.start >= since]
    roots = [s for s in spans if _is_root(s)]
    by_trace: dict[str | None, list[SpanRecord]] = defaultdict(list)
    for s in sorted(spans, key=lambda s: s.start):
        if not _is_root(s):
            by_trace[s.trace_id].append(s)
    turns = [_Turn(r, by_trace[r.trace_id]) for r in roots]
    in_window = [s for t in turns for s in t.spans]
    return StatsView(
        since=since,
        turns=len(turns),
        users=len({str(t.attr("user.id")) for t in turns}),
        families={
            "quality": _quality(turns),
            "safety": _safety(turns, in_window),
            "cost": _cost(turns, in_window, prices),
            "reliability": _reliability(turns, in_window),
            "knowledge": _knowledge(turns),
            "deletion": _deletion(in_window),
        },
    )


class _Turn:
    def __init__(self, root: SpanRecord, spans: list[SpanRecord]) -> None:
        self.root = root
        self.spans = spans
        self.sql = [s for s in spans if s.name == "run_sql" and s.attributes.get("sql.status")]

    def attr(self, name: str) -> AttrValue:
        return self.root.attributes.get(name)


def _quality(turns: list[_Turn]) -> dict[str, float | int | str]:
    tasks = [t for t in turns if t.attr("outcome") not in ("refused", "confirmation")]
    with_sql = [t for t in turns if t.sql]
    corrected = [t for t in with_sql if any(_status(s) == "retry" for s in t.sql)]
    executed = [s for t in with_sql for s in t.sql if _status(s) in _SQL_RAN]
    retries = sum(_status(s) == "retry" for t in with_sql for s in t.sql)
    return {
        "task_success_rate": _rate(sum(t.attr("outcome") == "answered" for t in tasks), len(tasks)),
        "first_try_sql_rate": _rate(
            sum(_status(t.sql[0]) in _SQL_RAN for t in with_sql), len(with_sql)
        ),
        "self_correction_saved": sum(_status(t.sql[-1]) in _SQL_RAN for t in corrected),
        "self_correction_needed": len(corrected),
        "empty_result_rate": _rate(sum(_status(s) == "empty" for s in executed), len(executed)),
        "retries_per_question": _rate(retries, len(with_sql)),
    }


def _safety(turns: list[_Turn], spans: list[SpanRecord]) -> dict[str, float | int | str]:
    ended_with = Counter(str(t.attr("message_class")) for t in turns)
    rejected = Counter(
        str(s.attributes.get("guard.reason"))
        for s in spans
        if s.name == "query_guard" and s.attributes.get("guard.decision") == "rejected"
    )
    out: dict[str, float | int | str] = {
        "input_guard_blocked": sum(ended_with[c] for c in _INPUT_GUARD),
        **{f"input_guard.{c}": ended_with[c] for c in _INPUT_GUARD if ended_with[c]},
        "query_guard_rejected": rejected.total(),
        **{f"query_guard.{reason}": n for reason, n in sorted(rejected.items())},
        "out_of_scope_refusals": ended_with["out_of_scope"],
        "pii_filter_hits": sum(
            len(_list(s.attributes.get("pii.hits"))) for s in spans if s.name == "outbound"
        ),
        "persona_changes": sum(s.name in ("publish_persona", "rollback_persona") for s in spans),
    }
    return out


def _cost(
    turns: list[_Turn], spans: list[SpanRecord], prices: Prices
) -> dict[str, float | int | str]:
    llm = [s for s in spans if s.name == "llm"]
    answered = sum(t.attr("outcome") == "answered" for t in turns)
    usd: float | str = "n/a"
    if prices and answered:
        usd = sum(_usd(s, prices) for s in llm) / answered
    return {
        "tokens_per_turn": _rate(sum(_tokens(s) for s in llm), len(turns)),
        "bigquery_bytes_per_turn": _rate(
            sum(_int(s.attributes.get("bytes_billed")) for s in spans if s.name == "run_sql"),
            len(turns),
        ),
        "usd_per_successful_answer": usd,
    }


def _reliability(turns: list[_Turn], spans: list[SpanRecord]) -> dict[str, float | int | str]:
    seconds = sorted(t.root.duration_ms / 1000 for t in turns)
    llm = [s for s in spans if s.name == "llm"]
    return {
        "p50_s": _percentile(seconds, 50),
        "p95_s": _percentile(seconds, 95),
        "fallbacks": sum(bool(s.attributes.get("llm.fallback_used")) for s in llm),
        "circuit_opens": sum(s.attributes.get("breaker.state") == "open" for s in llm),
        "model_failures": sum(
            "llm.model" in s.attributes and s.attributes["llm.model"] is None for s in llm
        ),
        "unclassified": sum(t.attr("error_class") == "unclassified" for t in turns),
    }


def _knowledge(turns: list[_Turn]) -> dict[str, float | int | str]:
    with_sql = [t for t in turns if t.sql]
    return {
        "retrieval_health_rate": _rate(
            sum(not t.attr("retrieval.gap") for t in with_sql), len(with_sql)
        ),
        "retrieval_degraded": sum(bool(t.attr("retrieval.degraded")) for t in turns),
    }


def _deletion(spans: list[SpanRecord]) -> dict[str, float | int | str]:
    # The node and the tool share a name; only the tool's span carries these attributes.
    plans = [s for s in spans if s.name == "plan_deletion" and "plan.size" in s.attributes]
    outcomes = Counter(
        str(s.attributes.get("outcome"))
        for s in spans
        if s.name in ("execute_deletion", "deletion_reply")
        and s.attributes.get("outcome")
        and not s.attributes.get("replayed")  # a replay reports the first outcome again
    )
    sizes = [_int(s.attributes.get("plan.size")) for s in plans if s.attributes.get("plan_id")]
    return {
        "plans": len(sizes),
        "zero_match": sum(_int(s.attributes.get("plan.size")) == 0 for s in plans),
        "confirmed": outcomes["confirmed"],
        "partial": outcomes["partial"],
        "cancelled": outcomes["cancelled"],
        "expired": outcomes["expired"],
        "mean_plan_size": _rate(sum(sizes), len(sizes)),
    }


# --- helpers --------------------------------------------------------------------------------


def _is_root(s: SpanRecord) -> bool:
    return s.name == "turn" and s.parent_id is None


def _status(s: SpanRecord) -> AttrValue:
    return s.attributes.get("sql.status")


def _tokens(s: SpanRecord) -> int:
    if s.name != "llm":
        return 0
    return _int(s.attributes.get("llm.tokens.prompt")) + _int(
        s.attributes.get("llm.tokens.completion")
    )


def _usd(s: SpanRecord, prices: Prices) -> float:
    price = prices.get(str(s.attributes.get("llm.model")))
    if price is None:
        return 0.0
    prompt = _int(s.attributes.get("llm.tokens.prompt"))
    completion = _int(s.attributes.get("llm.tokens.completion"))
    return (prompt * price[0] + completion * price[1]) / 1_000_000


def _rate(part: float, whole: int) -> float | str:
    return part / whole if whole else "n/a"


def _percentile(sorted_values: list[float], p: int) -> float | str:
    """Nearest rank: the smallest value with at least ``p`` percent of values at or below."""
    if not sorted_values:
        return "n/a"
    return sorted_values[max(0, math.ceil(p / 100 * len(sorted_values)) - 1)]


def _int(value: AttrValue) -> int:
    return int(value) if isinstance(value, int | float) and not isinstance(value, bool) else 0


def _list(value: AttrValue) -> list[str] | list[int]:
    return value if isinstance(value, list) else []


def _mb(value: AttrValue) -> str:
    return f"{_int(value) / 1_000_000:,.0f} MB"


def _value(value: AttrValue) -> str:
    return ",".join(map(str, value)) if isinstance(value, list) else str(value)
