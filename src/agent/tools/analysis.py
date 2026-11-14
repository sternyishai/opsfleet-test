"""The six analysis tools bound to the ReAct sub-agent (docs/lld/05-tools.md). Stubs.

Each is a plain function ``(input, ctx[, state]) -> output`` so tests call it directly with fake
ports. The graph wraps each one as a LangChain tool whose schema is only the ``*Input`` model;
``ctx`` and state values are injected and invisible to the model.
"""

from agent.contracts.tools import (
    GetReportInput,
    GetReportOutput,
    GetSchemaInput,
    ListReportsInput,
    ListReportsOutput,
    RunSqlInput,
    RunSqlOutput,
    SaveReportInput,
    SaveReportOutput,
    SchemaOutput,
    SearchTriosInput,
    SearchTriosOutput,
)
from agent.tools.context import SqlBudget, TurnContext


def get_schema(args: GetSchemaInput, ctx: TurnContext) -> SchemaOutput:
    """Allowlisted tables and columns with descriptions, as this User's scoped view projects
    them (no ``num_of_item`` for a Manager). No I/O beyond the bundled descriptions."""
    raise NotImplementedError


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
    """
    raise NotImplementedError


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
