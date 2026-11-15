"""QueryGuard on sqlglot (ADR 0011): the core guarantee of Req 2 (HLD 03 § Query guard).

The order of the checks is the order of HLD 03: one read-only statement, then node types the
guard knows (an allowlist over the tree, since sqlglot is lenient), the table and column
allowlists, out-of-scope Brand literals, and finally the rewrite: every warehouse table is
replaced by a scoped subquery that projects the allowlist and, for a Manager, keeps only rows
related to their Brands, bound as ``@brands``. A ``LIMIT`` is forced last.

The scoped subqueries are built from constant SQL templates; no Brand and nothing the model
wrote is ever formatted into them.
"""

import re
from collections.abc import Callable

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from agent.domain.identity import BrandScope
from agent.domain.query import (
    FilterLiteral,
    GuardAllowed,
    GuardDecision,
    GuardRejected,
    RejectReason,
    TableName,
)
from agent.domain.warehouse import ALLOWLIST, HIDDEN_COLUMNS

_SPECS = {spec.name: spec for spec in ALLOWLIST}
_DATASET = "thelook_ecommerce"
_PROJECT = "bigquery-public-data"

_ALLOWED_NODES: tuple[type[exp.Expr], ...] = (
    exp.Select,
    exp.SetOperation,
    exp.Subquery,
    exp.With,
    exp.CTE,
    exp.From,
    exp.Join,
    exp.Where,
    exp.Group,
    exp.Having,
    exp.Qualify,
    exp.Order,
    exp.Ordered,
    exp.Limit,
    exp.Offset,
    exp.Window,
    exp.WindowSpec,
    exp.Distinct,
    exp.Table,
    exp.TableAlias,
    exp.Alias,
    exp.Column,
    exp.Identifier,
    exp.Star,
    exp.Literal,
    exp.Boolean,
    exp.Null,
    exp.Var,
    exp.DataType,
    exp.DataTypeParam,
    exp.Interval,
    exp.Tuple,
    exp.Paren,
    exp.Neg,
    exp.Not,
    exp.Binary,  # comparisons, arithmetic, AND / OR, LIKE
    exp.Predicate,  # IN, BETWEEN, IS, EXISTS
    exp.Bracket,
    exp.Dot,
)
"""Node types a read-only analysis query is made of. Anything else is rejected."""

_DENIED_FUNCTIONS: tuple[type[exp.Func], ...] = (
    exp.Anonymous,  # unknown to sqlglot: EXTERNAL_QUERY, ML.*, UDFs
    exp.SessionUser,
    exp.CurrentUser,
)
"""Functions that read the environment or leave the warehouse. ``CURRENT_*`` reads of the
session (role, schema, account, ...) are denied below except the four clock functions."""

_CLOCK = (exp.CurrentDate, exp.CurrentDatetime, exp.CurrentTime, exp.CurrentTimestamp)

_BRAND_WRAPPERS = (exp.Lower, exp.Upper, exp.Trim, exp.Initcap, exp.Cast, exp.TryCast)

_ORDER_LEVEL = {c.name for spec in ALLOWLIST for c in spec.columns if c.order_level}

_BRANDS_IN_SCOPE = "SELECT id FROM products WHERE brand IN UNNEST(@brands)"
_MANAGER_FILTER: dict[TableName, str] = {
    "products": "brand IN UNNEST(@brands)",
    "order_items": f"product_id IN ({_BRANDS_IN_SCOPE})",
    "orders": (
        f"order_id IN (SELECT order_id FROM order_items WHERE product_id IN ({_BRANDS_IN_SCOPE}))"
    ),
    "users": (f"id IN (SELECT user_id FROM order_items WHERE product_id IN ({_BRANDS_IN_SCOPE}))"),
}
"""HLD 03's table of what a Manager sees, one filter per table."""

_BASKET_HINT = (
    "orders.num_of_item counts other Brands' items too; for basket size use COUNT(*) per "
    "order_id on order_items"
)


class SqlglotQueryGuard:
    def __init__(self, dialect: str = "bigquery") -> None:
        self._dialect = dialect

    def check(self, sql: str, scope: BrandScope, *, row_limit: int) -> GuardDecision:
        if not scope.all_brands and not scope.brands:
            return GuardRejected(reason=RejectReason.NO_ROLE, detail="no Brands to analyze")
        try:
            return self._decide(sql, scope, row_limit)
        except SqlglotError as exc:
            return GuardRejected(reason=RejectReason.UNPARSEABLE, detail=str(exc)[:500])
        except Exception as exc:  # e.g. RecursionError on deep nesting: fail closed, never raise
            return GuardRejected(
                reason=RejectReason.UNPARSEABLE, detail=f"could not verify: {type(exc).__name__}"
            )

    def _decide(self, sql: str, scope: BrandScope, row_limit: int) -> GuardDecision:
        statements = [s for s in sqlglot.parse(sql, read=self._dialect) if s is not None]
        if len(statements) != 1 or not isinstance(statements[0], exp.Select | exp.SetOperation):
            return GuardRejected(
                reason=RejectReason.NOT_SINGLE_SELECT, detail="exactly one SELECT is allowed"
            )
        query: exp.Query = statements[0]
        if rejection := _unsupported(query):
            return rejection
        tables = _tables(query)
        if isinstance(tables, GuardRejected):
            return tables
        if rejection := _columns(query, scope) or _out_of_scope(query, scope):
            return rejection
        rewritten = _force_limit(_scoped(query, scope, self._dialect), row_limit)
        return GuardAllowed(
            rewritten_sql=rewritten.sql(dialect=self._dialect),
            params={} if scope.all_brands else {"brands": sorted(scope.brands)},
            scope=scope,
            tables=tables,
            filters=_filters(query, tables),
            rewritten=True,
        )


def _unsupported(query: exp.Expr) -> GuardRejected | None:
    for node in query.walk():
        if isinstance(node, exp.Parameter | exp.SessionParameter | exp.Placeholder):
            return GuardRejected(
                reason=RejectReason.UNSUPPORTED_SYNTAX,
                detail=f"query parameters are not supported: {node.sql()}",
                hint="write values inline; Brand scope is applied for you",
            )
        if isinstance(node, exp.Func):
            # By class name on purpose: sqlglot keeps adding CURRENT_* session reads
            # (role, account, warehouse, ...), and a new one is denied until listed in _CLOCK.
            denied = isinstance(node, _DENIED_FUNCTIONS) or (
                type(node).__name__.startswith("Current") and not isinstance(node, _CLOCK)
            )
            if denied:
                return GuardRejected(
                    reason=RejectReason.UNSUPPORTED_SYNTAX,
                    detail=f"function {node.sql()[:80]} is not allowed",
                    hint="use standard BigQuery functions on the four tables",
                )
            continue
        if not isinstance(node, _ALLOWED_NODES):
            return GuardRejected(
                reason=RejectReason.UNSUPPORTED_SYNTAX,
                detail=f"{type(node).__name__} is not allowed: {node.sql()[:80]}",
            )
    return None


def _cte_names(query: exp.Expr) -> set[str]:
    return {cte.alias_or_name.lower() for cte in query.find_all(exp.CTE)}


def _warehouse_tables(query: exp.Expr) -> list[exp.Table]:
    """Table references that read the warehouse, not a CTE. Valid once ``_tables`` passed."""
    ctes = _cte_names(query)
    return [t for t in query.find_all(exp.Table) if t.db or t.name.lower() not in ctes]


def _tables(query: exp.Expr) -> list[TableName] | GuardRejected:
    """Every table read, by allowlisted name. A CTE may not shadow a warehouse table, so the
    scoped subqueries' own table names always mean the warehouse."""
    ctes = _cte_names(query)
    if shadowed := ctes & _SPECS.keys():
        return GuardRejected(
            reason=RejectReason.TABLE_NOT_ALLOWED,
            detail=f"a CTE may not be named {', '.join(sorted(shadowed))}",
            hint="rename the CTE",
        )
    found: list[TableName] = []
    for table in _warehouse_tables(query):
        spec = _SPECS.get(table.name.lower())
        foreign = table.db not in ("", _DATASET) or table.catalog not in ("", _PROJECT)
        if spec is None or foreign:
            return GuardRejected(
                reason=RejectReason.TABLE_NOT_ALLOWED,
                detail=f"table {table.sql()} is not allowed",
                hint="use only orders, order_items, products and users",
            )
        if spec.name not in found:
            found.append(spec.name)
    return found


def _columns(query: exp.Expr, scope: BrandScope) -> GuardRejected | None:
    for column in query.find_all(exp.Column):
        name = column.name.lower()
        if name in HIDDEN_COLUMNS:
            return GuardRejected(
                reason=RejectReason.COLUMN_NOT_ALLOWED,
                detail=f"column {name} is not allowed",
                hint="Customers are analyzed by id, age, gender, state, country, traffic_source",
            )
        if name in _ORDER_LEVEL and not scope.all_brands:
            return GuardRejected(
                reason=RejectReason.ORDER_LEVEL_FIELD,
                detail=f"column {name} is not in your scoped view of orders",
                hint=_BASKET_HINT,
            )
    return None


def _out_of_scope(query: exp.Expr, scope: BrandScope) -> GuardRejected | None:
    """Brand literals the SQL compares ``brand`` with, outside a Manager's scope. Compared
    without case: thelook stores 'adidas' and 'PUMA', and LOWER(brand) is common. ``run_sql``
    words the hint, from the User's scope."""
    if scope.all_brands:
        return None
    if opaque := _opaque_brand_comparison(query):
        return GuardRejected(
            reason=RejectReason.BRAND_OUT_OF_SCOPE,
            detail=f"brand is compared with an expression: {opaque[:80]}",
            hint="compare brand only with Brand names written as plain text",
        )
    outside: list[str] = []
    for value, match in _brand_literals(query):
        if not any(match(value, brand) for brand in scope.brands) and value not in outside:
            outside.append(value)
    if not outside:
        return None
    return GuardRejected(
        reason=RejectReason.BRAND_OUT_OF_SCOPE,
        detail=f"outside your Brands: {', '.join(outside)}",
        out_of_scope_brands=outside,
    )


type _BrandMatch = Callable[[str, str], bool]
"""``(literal, brand) -> True`` when the comparison can select that in-scope Brand."""


def _equals(value: str, brand: str) -> bool:
    return value.lower() == brand.lower()


def _like(value: str, brand: str) -> bool:
    parts = (".*" if ch == "%" else "." if ch == "_" else re.escape(ch) for ch in value)
    return re.fullmatch("".join(parts), brand, re.IGNORECASE | re.DOTALL) is not None


def _starts_with(value: str, brand: str) -> bool:
    return brand.lower().startswith(value.lower())


def _regex(value: str, brand: str) -> bool:
    try:
        return re.search(value, brand) is not None
    except re.error:  # not a pattern Python reads: it names no Brand we can confirm
        return False


_PLAIN = (exp.Literal, exp.Column, exp.Null)
"""What ``brand`` may be compared with: a value the guard can read, or another column."""


def _opaque_brand_comparison(query: exp.Expr) -> str | None:
    """A comparison of ``brand`` with anything the guard cannot read as a literal
    (``LOWER('NIKE')``, ``CONCAT(...)``, a subquery). The scoped rewrite would still return no
    other Brand's rows, but the refusal is what tells the User why, so it fails closed."""
    pairs = (exp.EQ, exp.NEQ, exp.NullSafeEQ, exp.NullSafeNEQ, exp.Like, exp.ILike)
    for node in query.find_all(*pairs, exp.StartsWith, exp.RegexpLike, exp.In):
        if isinstance(node, exp.In):
            if _is_brand(node.this):
                unnest = node.args.get("unnest")
                arrays = unnest.expressions if unnest else []
                plain = (
                    node.args.get("query") is None
                    and all(isinstance(e, _PLAIN) for e in node.expressions)
                    and all(
                        isinstance(a, exp.Array)
                        and all(isinstance(v, _PLAIN) for v in a.expressions)
                        for a in arrays
                    )
                )
            else:
                brand_listed = any(_is_brand(e) for e in node.expressions)
                plain = not brand_listed or isinstance(node.this, _PLAIN)
        else:
            left, right = node.this, node.expression
            plain = not (
                (_is_brand(left) and not isinstance(right, _PLAIN))
                or (_is_brand(right) and not isinstance(left, _PLAIN))
            )
        if not plain:
            return node.sql()
    return None


def _brand_literals(query: exp.Expr) -> list[tuple[str, _BrandMatch]]:
    """Each string compared with a ``brand`` column, with how the comparison matches."""
    found: list[tuple[str, _BrandMatch]] = []
    pairs = (exp.EQ, exp.NEQ, exp.NullSafeEQ, exp.NullSafeNEQ, exp.Like, exp.ILike)
    for node in query.find_all(*pairs, exp.StartsWith, exp.RegexpLike, exp.In):
        if isinstance(node, exp.In):
            found += _in_literals(node)
            continue
        match: _BrandMatch = (
            _like
            if isinstance(node, exp.Like | exp.ILike)
            else _starts_with
            if isinstance(node, exp.StartsWith)
            else _regex
            if isinstance(node, exp.RegexpLike)
            else _equals
        )
        left, right = node.this, node.expression
        if _is_brand(left) and _is_string(right):
            found.append((right.this, match))
        elif _is_brand(right) and _is_string(left) and match is _equals:
            found.append((left.this, match))
    return found


def _in_literals(node: exp.In) -> list[tuple[str, _BrandMatch]]:
    """``brand IN ('a', ...)``, ``brand IN UNNEST(['a', ...])`` and ``'a' IN (brand, ...)``."""
    if _is_brand(node.this):
        unnest = node.args.get("unnest")
        values = unnest.find_all(exp.Literal) if unnest else node.expressions
        return [(v.this, _equals) for v in values if _is_string(v)]
    if _is_string(node.this) and any(_is_brand(e) for e in node.expressions):
        return [(node.this.this, _equals)]
    return []


def _is_brand(node: exp.Expr) -> bool:
    while isinstance(node, _BRAND_WRAPPERS):
        node = node.this
    return isinstance(node, exp.Column) and node.name.lower() == "brand"


def _is_string(node: exp.Expr) -> bool:
    return isinstance(node, exp.Literal) and node.is_string


_OPERATORS: dict[type[exp.Expr], str] = {
    exp.EQ: "=",
    exp.NEQ: "!=",
    exp.GT: ">",
    exp.GTE: ">=",
    exp.LT: "<",
    exp.LTE: "<=",
    exp.Like: "LIKE",
    exp.ILike: "LIKE",
}
_FLIPPED = {">": "<", ">=": "<=", "<": ">", "<=": ">="}
_VALUE_WRAPPERS = (exp.Cast, exp.TryCast, exp.Timestamp, exp.Date, exp.TsOrDsToDate)
_COLUMN_WRAPPERS = (*_BRAND_WRAPPERS, exp.Date, exp.TsOrDsToDate, exp.Timestamp)


def _filters(query: exp.Expr, tables: list[TableName]) -> list[FilterLiteral]:
    """Every ``column <op> literal`` in the SQL, in source order, for the empty-result probe.

    A column is kept only when its table is certain: through its qualifier (table name or
    alias), or, unqualified, when exactly one table the query reads has that column. Anything
    else (CTE columns, expressions, ambiguous names) is skipped: the probe checks less, it
    never checks the wrong table.
    """
    resolve = _column_resolver(query, tables)
    found: list[FilterLiteral] = []
    for node in query.find_all(*_OPERATORS, exp.In, exp.Between, bfs=False):
        if isinstance(node, exp.In):
            pairs = [(node.this, "IN", value) for value in node.expressions]
        elif isinstance(node, exp.Between):
            pairs = [(node.this, ">=", node.args["low"]), (node.this, "<=", node.args["high"])]
        else:
            op = _OPERATORS[type(node)]
            pairs = [(node.this, op, node.expression)]
            if op in _FLIPPED or op in ("=", "!="):  # '2025-01-01' <= created_at
                pairs.append((node.expression, _FLIPPED.get(op, op), node.this))
        if node.find_ancestor(exp.Not):  # NOT state = 'X' names no value the rows hold
            continue
        for column_side, op, value_side in pairs:
            column, value = _unwrap(column_side, _COLUMN_WRAPPERS), _literal(value_side)
            if not isinstance(column, exp.Column) or value is None:
                continue
            if table := resolve(column):
                found.append(
                    FilterLiteral(table=table, column=column.name.lower(), operator=op, value=value)
                )
    return found


def _column_resolver(
    query: exp.Expr, tables: list[TableName]
) -> Callable[[exp.Column], TableName | None]:
    """Resolve conservatively, without scope analysis: a qualifier must name exactly one
    warehouse table in the whole query, and an unqualified column is resolved only when no
    CTE or derived table could define a column of the same name."""
    bound: dict[str, list[str]] = {}
    for node in query.find_all(exp.Table, exp.Subquery, exp.CTE):
        name = node.alias_or_name.lower()
        if name:
            kind = node.name.lower() if isinstance(node, exp.Table) else "<derived>"
            bound.setdefault(name, []).append(kind)
    derived = any("<derived>" in kinds for kinds in bound.values())

    def resolve(column: exp.Column) -> TableName | None:
        name = column.name.lower()
        if column.table:
            kinds = bound.get(column.table.lower(), [])
            spec = _SPECS.get(kinds[0]) if len(kinds) == 1 else None
            has = spec is not None and any(c.name == name for c in spec.columns)
            return spec.name if spec is not None and has else None
        if derived:
            return None
        owners: list[TableName] = [
            t for t in tables if any(c.name == name for c in _SPECS[t].columns)
        ]
        return owners[0] if len(owners) == 1 else None

    return resolve


def _unwrap(node: exp.Expr, wrappers: tuple[type[exp.Expr], ...]) -> exp.Expr:
    while isinstance(node, wrappers):
        node = node.this
    return node


def _literal(node: exp.Expr) -> str | None:
    node = _unwrap(node, _VALUE_WRAPPERS)
    return str(node.this) if isinstance(node, exp.Literal) else None


def _scoped(query: exp.Query, scope: BrandScope, dialect: str) -> exp.Query:
    """Replace every warehouse table with its scoped subquery, under the same alias."""
    query = query.copy()
    for table in _warehouse_tables(query):
        spec = _SPECS[table.name.lower()]
        alias = table.args.get("alias")
        table.replace(
            exp.Subquery(
                this=_scoped_read(spec.name, scope, dialect),
                alias=alias.copy() if alias else exp.TableAlias(this=exp.to_identifier(table.name)),
            )
        )
    return query


def _scoped_read(table: TableName, scope: BrandScope, dialect: str) -> exp.Select:
    columns = [c.name for c in _SPECS[table].columns if scope.all_brands or not c.order_level]
    read = exp.select(*columns).from_(table)
    if scope.all_brands:
        return read
    return read.where(sqlglot.parse_one(_MANAGER_FILTER[table], read=dialect))


def _force_limit(query: exp.Query, row_limit: int) -> exp.Query:
    """The outer query gets ``LIMIT row_limit`` unless it already has a smaller literal one."""
    limit = query.args.get("limit")
    current = limit.expression if isinstance(limit, exp.Limit) else None
    if isinstance(current, exp.Literal) and current.is_int and int(current.this) <= row_limit:
        return query
    return query.limit(row_limit, copy=False)
