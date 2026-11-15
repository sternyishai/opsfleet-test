"""QueryGuard on sqlglot (ADR 0011). Minimal in slice 1; completed in slice 2.

Built now: parse (fail closed), exactly one ``SELECT``, the table allowlist by name, the
no-role deny and the forced ``LIMIT``. Not yet: column allowlist, Brand detection, the scope
rewrite and parameter binding. Until then the query can touch every Brand, and the decision
says so (``scope = all Brands``).
"""

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from agent.domain.identity import BrandScope
from agent.domain.query import GuardAllowed, GuardDecision, GuardRejected, RejectReason, TableName
from agent.domain.warehouse import ALLOWLIST

_TABLES: dict[str, TableName] = {spec.name: spec.name for spec in ALLOWLIST}
_DATASET = "thelook_ecommerce"
_PROJECT = "bigquery-public-data"


class SqlglotQueryGuard:
    def __init__(self, dialect: str = "bigquery") -> None:
        self._dialect = dialect

    def check(self, sql: str, scope: BrandScope, *, row_limit: int) -> GuardDecision:
        if not scope.all_brands and not scope.brands:
            return GuardRejected(reason=RejectReason.NO_ROLE, detail="no Brands to analyze")
        try:
            statements = [s for s in sqlglot.parse(sql, read=self._dialect) if s is not None]
        except SqlglotError as exc:
            return GuardRejected(reason=RejectReason.UNPARSEABLE, detail=str(exc)[:500])
        if len(statements) != 1 or not isinstance(statements[0], exp.Select):
            return GuardRejected(
                reason=RejectReason.NOT_SINGLE_SELECT, detail="exactly one SELECT is allowed"
            )
        select = statements[0]
        tables = _tables(select)
        if isinstance(tables, GuardRejected):
            return tables
        limited = _force_limit(select, row_limit)
        rewritten_sql = limited.sql(dialect=self._dialect)
        return GuardAllowed(
            rewritten_sql=rewritten_sql,
            params={},
            scope=BrandScope(all_brands=True),  # no scope rewrite until slice 2
            tables=tables,
            filters=[],
            rewritten=limited is not select,
        )


def _tables(select: exp.Select) -> list[TableName] | GuardRejected:
    """Every table read, by allowlisted name. CTE names are not tables."""
    ctes = {cte.alias_or_name for cte in select.find_all(exp.CTE)}
    found: list[TableName] = []
    for table in select.find_all(exp.Table):
        if not table.db and table.name in ctes:
            continue
        name = _TABLES.get(table.name)
        foreign = table.db not in ("", _DATASET) or table.catalog not in ("", _PROJECT)
        if name is None or foreign:
            return GuardRejected(
                reason=RejectReason.TABLE_NOT_ALLOWED,
                detail=f"table {table.sql()} is not allowed",
                hint="use only orders, order_items, products and users",
            )
        if name not in found:
            found.append(name)
    return found


def _force_limit(select: exp.Select, row_limit: int) -> exp.Select:
    """The outer query gets ``LIMIT row_limit`` unless it already has a smaller literal one."""
    limit = select.args.get("limit")
    current = limit.expression if isinstance(limit, exp.Limit) else None
    if isinstance(current, exp.Literal) and current.is_int and int(current.this) <= row_limit:
        return select
    return select.limit(row_limit, copy=True)
