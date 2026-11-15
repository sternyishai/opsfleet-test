"""The six analysis tools bound to the ReAct sub-agent (docs/lld/05-tools.md).

``get_schema`` and ``run_sql`` (happy path) are built; the rest are stubs until their slice.

Each is a plain function ``(input, ctx[, state]) -> output`` so tests call it directly with fake
ports. The graph wraps each one as a LangChain tool whose schema is only the ``*Input`` model;
``ctx`` and state values are injected and invisible to the model.
"""

from dataclasses import replace
from datetime import date
from typing import Literal

from agent.contracts.tools import (
    ColumnSchema,
    GetReportInput,
    GetReportOutput,
    GetSchemaInput,
    ListReportsInput,
    ListReportsOutput,
    RunSqlGaveUp,
    RunSqlInput,
    RunSqlOk,
    RunSqlOutput,
    RunSqlRetry,
    SaveReportInput,
    SaveReportOutput,
    SchemaOutput,
    SearchTriosInput,
    SearchTriosOutput,
    TableSchema,
)
from agent.domain.identity import BrandScope, Role
from agent.domain.query import GuardRejected, RejectReason
from agent.domain.warehouse import ALLOWLIST
from agent.errors import SelfCorrectableError
from agent.tools.context import SqlBudget, TurnContext


def get_schema(args: GetSchemaInput, ctx: TurnContext) -> SchemaOutput:
    """Allowlisted tables and columns with descriptions, as this User's scoped view projects
    them (no ``num_of_item`` for a Manager). No I/O beyond the bundled descriptions."""
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
    """Re-search for a sub-question. ``TrioRetriever.search(args.query, ctx.user.scope)``.
    A ``TransientError`` returns no matches; it never fails the step."""
    raise NotImplementedError


def run_sql(
    args: RunSqlInput, ctx: TurnContext, budget: SqlBudget
) -> tuple[RunSqlOutput, SqlBudget]:
    """Guard -> dry run -> execute, with the bounded self-correction loop (HLD 06).

    The loop's state is ``budget``; the function returns the next budget for graph state.
    The empty-result probe is code: filter literals from ``GuardAllowed.filters`` become small
    scoped probe queries that pass the same guard.

    Slice 1 builds guard -> execute and the shared retry budget. The dry run, the bytes cap,
    the probe and the plain-language ``tried`` list arrive with slice 3.
    """
    budget = replace(budget, attempts=budget.attempts + 1)
    decision = ctx.ports.guard.check(args.sql, ctx.user.scope, row_limit=ctx.limits.sql_row_limit)
    if isinstance(decision, GuardRejected):
        return _retry(
            "guard_rejected", decision.detail, ctx, budget, decision.reason, decision.hint
        )
    try:
        result = ctx.ports.warehouse.execute(
            decision.rewritten_sql,
            decision.params,
            max_bytes=ctx.limits.max_bytes_billed,
            max_rows=ctx.limits.sql_rows_to_model,
        )
    except SelfCorrectableError as exc:
        return _retry("runtime_error", exc.detail, ctx, budget)
    return RunSqlOk(result=result, attempt=budget.attempts), replace(
        budget, last_scope=decision.scope
    )


def _retry(
    kind: Literal["guard_rejected", "runtime_error"],
    error: str,
    ctx: TurnContext,
    budget: SqlBudget,
    reason: RejectReason | None = None,
    hint: str | None = None,
) -> tuple[RunSqlOutput, SqlBudget]:
    """One retry from the shared budget, or the graceful give-up once it is spent."""
    budget = replace(budget, retries_used=budget.retries_used + 1)
    retries_left = ctx.limits.sql_max_retries - budget.retries_used
    if retries_left < 0:
        return RunSqlGaveUp(tried=list(budget.tried)), budget
    out = RunSqlRetry(kind=kind, reason=reason, error=error, hint=hint, retries_left=retries_left)
    return out, budget


def save_report(args: SaveReportInput, ctx: TurnContext, budget: SqlBudget) -> SaveReportOutput:
    """Run the output PII filter on every text field, then ``ReportRepository.save`` with
    ``author_id = ctx.user.user_id`` and ``scope = budget.last_scope``. Entities are extracted
    at save time in the prototype (``ModelStep.ENTITY_EXTRACTION``). Refuses without a prior
    successful ``run_sql`` in the turn."""
    raise NotImplementedError


def list_my_reports(args: ListReportsInput, ctx: TurnContext) -> ListReportsOutput:
    """``ReportRepository.find`` for the session User; ``accessible`` set from
    ``ctx.user.scope.covers(report.scope)``."""
    raise NotImplementedError


def get_my_report(args: GetReportInput, ctx: TurnContext) -> GetReportOutput:
    """``ReportRepository.get`` for the session User; content withheld when out of scope."""
    raise NotImplementedError
