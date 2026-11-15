"""The six analysis tools bound to the ReAct sub-agent (docs/lld/05-tools.md).

``get_schema`` and ``run_sql`` live here; the Saved Report tools live in
``agent.tools.reports`` and are re-exported, so this module names all six.

Each is a plain function ``(input, ctx[, state]) -> output`` so tests call it directly with fake
ports. The graph wraps each one as a LangChain tool whose schema is only the ``*Input`` model;
``ctx`` and state values are injected and invisible to the model.
"""

from dataclasses import replace
from datetime import date
from typing import Literal

from agent.contracts.tools import (
    ColumnSchema,
    GetSchemaInput,
    RunSqlEmpty,
    RunSqlGaveUp,
    RunSqlInput,
    RunSqlOk,
    RunSqlOutput,
    RunSqlRetry,
    SchemaOutput,
    SearchTriosInput,
    SearchTriosOutput,
    TableSchema,
    ToolError,
)
from agent.domain.identity import BrandScope, Role
from agent.domain.query import (
    FilterLiteral,
    GuardAllowed,
    GuardDecision,
    GuardRejected,
    QueryResult,
    RejectReason,
    TableName,
)
from agent.domain.trios import TrioSearchResult
from agent.domain.warehouse import ALLOWLIST
from agent.errors import MessageClass, SelfCorrectableError, TransientError
from agent.ports.tracer import AttrValue, Span, Tracer
from agent.ports.trios import TrioRetriever
from agent.tools.context import SqlBudget, TurnContext
from agent.tools.probe import diagnose
from agent.tools.reports import get_my_report, list_my_reports, save_report

__all__ = [
    "get_my_report",
    "get_schema",
    "list_my_reports",
    "run_sql",
    "save_report",
    "search_golden_trios",
    "search_trios",
]

_NARROW_HINT = "Narrow the date range or aggregate earlier."

type RetryKind = Literal["guard_rejected", "dry_run_error", "over_bytes_cap", "runtime_error"]


def get_schema(args: GetSchemaInput, ctx: TurnContext) -> SchemaOutput:
    """Allowlisted tables and columns with descriptions, as this User's scoped view projects
    them (no ``num_of_item`` for a Manager). No I/O beyond the bundled descriptions."""
    role = ctx.user.role
    with ctx.ports.tracer.span(
        "get_schema", tables=[str(t) for t in args.tables or []], role=role.value if role else None
    ):
        return _schema_output(args, ctx)


def _schema_output(args: GetSchemaInput, ctx: TurnContext) -> SchemaOutput:
    wanted = set(args.tables) if args.tables else None
    hide_order_level = ctx.user.role is not Role.EXECUTIVE
    tables = [
        TableSchema(
            name=spec.name,
            description=spec.description,
            columns=[
                ColumnSchema(name=c.name, type=c.type, description=c.description)
                for c in spec.columns
                if not (c.order_level and hide_order_level)
            ],
        )
        for spec in ALLOWLIST
        if wanted is None or spec.name in wanted
    ]
    # thelook is regenerated daily (and runs a few days past today), so "as of" is today.
    return SchemaOutput(
        tables=tables, scope_note=scope_note(ctx.user.scope), data_as_of=date.today()
    )


def scope_note(scope: BrandScope) -> str:
    if scope.all_brands:
        return "You can analyze all Brands."
    if not scope.brands:
        return "You have no Brands to analyze."
    *rest, last = sorted(scope.brands)
    return f"You can analyze {', '.join(rest) + ' and ' if rest else ''}{last}."


def search_golden_trios(args: SearchTriosInput, ctx: TurnContext) -> SearchTriosOutput:
    """Re-search for a sub-question, in the User's own scope (never one from the model).
    A ``TransientError`` returns no matches; it never fails the step."""
    result = search_trios(
        ctx.ports.trios,
        ctx.ports.tracer,
        args.query,
        ctx.user.scope,
        limit=args.limit,
        span_name="search_golden_trios",
        # The model wrote it, but from the User's words: filtered like the SQL.
        attributes={"query": _traceable(args.query, ctx)},
    )
    return SearchTriosOutput(matches=result.matches if result else [])


def search_trios(
    trios: TrioRetriever,
    tracer: Tracer,
    query: str,
    scope: BrandScope,
    *,
    limit: int,
    span_name: str,
    attributes: dict[str, AttrValue] | None = None,
) -> TrioSearchResult | None:
    """One traced search, shared by the tool and the ``retrieve_trios`` node: ``None`` when
    the retriever is down (``TransientError``), which callers treat as no matches."""
    with tracer.span(span_name, **(attributes or {})) as span:
        try:
            result = trios.search(query, scope, limit=limit)
        except TransientError:
            span.set(**{"trios.degraded": True, "trios.refs": [], "trios.scores": []})
            return None
        span.set(
            **{
                "trios.degraded": False,
                "trios.refs": [m.ref for m in result.matches],
                # Strings: a trace attribute list holds str or int, and two places is enough.
                "trios.scores": [f"{m.score:.2f}" for m in result.matches],
                "trios.dropped": result.dropped_below_threshold,
            }
        )
    return result


def run_sql(
    args: RunSqlInput, ctx: TurnContext, budget: SqlBudget
) -> tuple[RunSqlOutput, SqlBudget]:
    """Guard -> dry run -> execute, with the bounded self-correction loop (HLD 06).

    The loop's state is ``budget``; the function returns the next budget for graph state, so
    the loop is testable without a graph. Guard rejections, dry-run errors, the bytes cap and
    runtime errors share one retry budget. The empty-result probe is code: filter literals
    from ``GuardAllowed.filters`` become small probe queries that pass the same guard and dry
    run (``agent.tools.probe``). A ``TransientError`` (BigQuery down) is not caught: it ends
    the turn at the graph boundary as ``DATA_SOURCE_DOWN``.
    """
    with ctx.ports.tracer.span("run_sql") as span:
        out, budget = _run_sql(args, ctx, budget, span)
        span.set(**_trace(out, budget, ctx))
    return out, budget


def _trace(out: RunSqlOutput, budget: SqlBudget, ctx: TurnContext) -> dict[str, AttrValue]:
    """LLD 05 § run_sql trace attributes. Never rows: counts and the error text only."""
    ok = isinstance(out, RunSqlOk)
    retry = isinstance(out, RunSqlRetry)
    return {
        "attempt": budget.attempts,
        "sql.status": out.status,
        "sql.retry_kind": out.kind if retry else None,
        # Which plain message the User got; the technical reason is in sql.retry_kind / error
        # on the call before.
        "sql.gave_up": out.message_class.value if isinstance(out, RunSqlGaveUp) else None,
        # The warehouse's error can quote a literal from the SQL: filtered like the SQL.
        "error": _traceable(out.error, ctx) if retry else None,
        "row_count": out.result.row_count if ok else 0 if isinstance(out, RunSqlEmpty) else None,
        "bytes_billed": out.result.bytes_billed if ok else None,
    }


def _run_sql(
    args: RunSqlInput, ctx: TurnContext, budget: SqlBudget, span: Span
) -> tuple[RunSqlOutput, SqlBudget]:
    budget = replace(budget, attempts=budget.attempts + 1)
    decision = _guarded(args.sql, ctx)
    if isinstance(decision, GuardRejected):
        hint = decision.hint
        if decision.reason is RejectReason.BRAND_OUT_OF_SCOPE:
            if budget.refused_brand:  # told once which Brands they can analyze: stop here
                refused = ToolError(message_class=MessageClass.OUT_OF_SCOPE, detail=decision.detail)
                return refused, budget
            budget = replace(budget, refused_brand=True)
            hint = scope_note(ctx.user.scope)  # the Brands they *can* analyze
        return _retry("guard_rejected", decision.detail, ctx, budget, decision.reason, hint)
    budget = replace(budget, tried=_tried(budget.tried, decision.filters))
    warehouse = ctx.ports.warehouse
    schema = _schema(decision.tables, ctx)
    try:
        dry = warehouse.dry_run(
            decision.rewritten_sql, decision.params, max_bytes=ctx.limits.max_bytes_billed
        )
    except SelfCorrectableError as exc:
        return _retry("dry_run_error", exc.detail, ctx, budget, schema=schema)
    span.set(**{"dry_run.bytes": dry.bytes_processed})
    if dry.over_cap:
        error = f"the query would scan {dry.bytes_processed:,} bytes, over the cap"
        return _retry("over_bytes_cap", error, ctx, budget, hint=_NARROW_HINT, schema=schema)
    try:
        result = warehouse.execute(
            decision.rewritten_sql,
            decision.params,
            max_bytes=ctx.limits.max_bytes_billed,
            max_rows=ctx.limits.sql_rows_to_model,
        )
    except SelfCorrectableError as exc:
        return _retry("runtime_error", exc.detail, ctx, budget, schema=schema)
    budget = replace(budget, last_scope=decision.scope)
    if result.row_count == 0:
        return _empty(decision.filters, ctx, budget)
    return RunSqlOk(result=result, attempt=budget.attempts), budget


def _empty(
    filters: list[FilterLiteral], ctx: TurnContext, budget: SqlBudget
) -> tuple[RunSqlOutput, SqlBudget]:
    """The first empty result is diagnosed; after that, empty is the answer."""
    if budget.diagnoses_used >= ctx.limits.empty_result_diagnoses:
        return RunSqlEmpty(probe_hint=None, checked=filters, final=True), budget
    budget = replace(budget, diagnoses_used=budget.diagnoses_used + 1)
    with ctx.ports.tracer.span("empty_result_probe") as span:
        diagnosis = diagnose(
            filters, lambda sql: _probe(sql, ctx), max_columns=ctx.limits.probe_max_columns
        )
        # Counts only: the hint quotes warehouse values, and rows never enter a trace.
        span.set(**{"probe.checked": len(diagnosis.checked), "probe.final": diagnosis.final})
    out = RunSqlEmpty(probe_hint=diagnosis.hint, checked=diagnosis.checked, final=diagnosis.final)
    return out, budget


def _probe(sql: str, ctx: TurnContext) -> QueryResult | None:
    """One probe statement through the same guard and dry run as the question's query."""
    decision = _guarded(sql, ctx)
    if isinstance(decision, GuardRejected):
        return None
    warehouse, limits = ctx.ports.warehouse, ctx.limits
    try:
        dry = warehouse.dry_run(
            decision.rewritten_sql, decision.params, max_bytes=limits.max_bytes_billed
        )
        if dry.over_cap:
            return None
        return warehouse.execute(
            decision.rewritten_sql,
            decision.params,
            max_bytes=limits.max_bytes_billed,
            max_rows=limits.probe_values,
        )
    except SelfCorrectableError:
        return None


def _guarded(sql: str, ctx: TurnContext) -> GuardDecision:
    """``QueryGuard.check``, with its decision on a span. The guard itself is pure."""
    scope = ctx.user.scope
    with ctx.ports.tracer.span("query_guard") as span:
        decision = ctx.ports.guard.check(sql, scope, row_limit=ctx.limits.sql_row_limit)
        allowed = isinstance(decision, GuardAllowed)
        span.set(
            **{
                "guard.decision": decision.decision,
                "guard.reason": None if allowed else decision.reason.value,
                "guard.rewritten": allowed and decision.rewritten,
                "guard.tables": list(decision.tables) if allowed else [],
                "guard.scope_brands": "all" if scope.all_brands else len(scope.brands),
                # The SQL before and after the guard, through the PII filter: a literal in a
                # WHERE clause can quote a Customer's email the User typed.
                "sql.written": _traceable(sql, ctx),
                "sql.rewritten": _traceable(decision.rewritten_sql, ctx) if allowed else None,
            }
        )
    return decision


def _traceable(sql: str, ctx: TurnContext) -> str:
    return ctx.ports.pii.scan(sql).text


def _tried(tried: tuple[str, ...], filters: list[FilterLiteral]) -> tuple[str, ...]:
    """What the graceful give-up tells the User it tried: "filtering by state"."""
    return tuple(dict.fromkeys([*tried, *(f"filtering by {f.column}" for f in filters)]))


def _schema(tables: list[TableName], ctx: TurnContext) -> list[TableSchema]:
    """The tables a failed statement read, as ``get_schema`` shows them to this User."""
    return _schema_output(GetSchemaInput(tables=tables), ctx).tables  # not a tool call: no span


def _retry(
    kind: RetryKind,
    error: str,
    ctx: TurnContext,
    budget: SqlBudget,
    reason: RejectReason | None = None,
    hint: str | None = None,
    *,
    schema: list[TableSchema] | None = None,
) -> tuple[RunSqlOutput, SqlBudget]:
    """One retry from the shared budget, or the graceful give-up once it is spent."""
    budget = replace(budget, retries_used=budget.retries_used + 1)
    retries_left = ctx.limits.sql_max_retries - budget.retries_used
    if retries_left < 0:
        too_large = kind == "over_bytes_cap"
        message_class = (
            MessageClass.QUERY_TOO_LARGE if too_large else MessageClass.SELF_CORRECTION_EXHAUSTED
        )
        return RunSqlGaveUp(message_class=message_class, tried=list(budget.tried)), budget
    out = RunSqlRetry(
        kind=kind,
        reason=reason,
        error=error,
        hint=hint,
        relevant_schema=schema or [],
        retries_left=retries_left,
    )
    return out, budget
