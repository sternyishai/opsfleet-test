"""The query guard safety suite (HLD 03 § Query guard, LLD 06 § QueryGuard). Gated at 100%.

Always the real ``SqlglotQueryGuard``. The scope rewrite is asserted on the AST of the
rewritten SQL, against the filters HLD 03's table spells out, never on strings.
"""

import re
from pathlib import Path

import pytest
import sqlglot
import yaml
from sqlglot import exp

from agent.adapters.query_guard import SqlglotQueryGuard
from agent.domain.identity import BrandScope
from agent.domain.query import FilterLiteral, GuardAllowed, GuardRejected, RejectReason

GUARD = SqlglotQueryGuard(dialect="bigquery")
EXECUTIVE = BrandScope(all_brands=True)
MANAGER = BrandScope(brands=frozenset({"Calvin Klein", "Tommy Hilfiger"}))
LIMIT = 1000


def allowed(sql: str, scope: BrandScope = MANAGER) -> GuardAllowed:
    decision = GUARD.check(sql, scope, row_limit=LIMIT)
    assert isinstance(decision, GuardAllowed), decision
    return decision


def rejected(sql: str, scope: BrandScope = MANAGER) -> GuardRejected:
    decision = GUARD.check(sql, scope, row_limit=LIMIT)
    assert isinstance(decision, GuardRejected), decision
    return decision


def parse(sql: str) -> exp.Expr:
    return sqlglot.parse_one(sql, read="bigquery")


# HLD 03 § Scope rewrite: what a Manager sees of each table.
_SCOPED_PRODUCTS = "SELECT id FROM products WHERE brand IN UNNEST(@brands)"
_SCOPED_ITEMS = f"SELECT {{col}} FROM order_items WHERE product_id IN ({_SCOPED_PRODUCTS})"
MANAGER_FILTER = {
    "products": parse("SELECT 1 FROM t WHERE brand IN UNNEST(@brands)").args["where"],
    "order_items": parse(f"SELECT 1 FROM t WHERE product_id IN ({_SCOPED_PRODUCTS})").args["where"],
    "orders": parse(
        f"SELECT 1 FROM t WHERE order_id IN ({_SCOPED_ITEMS.format(col='order_id')})"
    ).args["where"],
    "users": parse(f"SELECT 1 FROM t WHERE id IN ({_SCOPED_ITEMS.format(col='user_id')})").args[
        "where"
    ],
}
# The allowlisted columns, written out from HLD 03 and the thelook schema, not read from code.
COLUMNS = {
    "orders": [
        "order_id",
        "user_id",
        "status",
        "gender",
        "created_at",
        "returned_at",
        "shipped_at",
        "delivered_at",
        "num_of_item",
    ],
    "order_items": [
        "id",
        "order_id",
        "user_id",
        "product_id",
        "status",
        "created_at",
        "shipped_at",
        "delivered_at",
        "returned_at",
        "sale_price",
    ],
    "products": [
        "id",
        "cost",
        "category",
        "name",
        "brand",
        "retail_price",
        "department",
        "sku",
        "distribution_center_id",
    ],
    "users": ["id", "age", "gender", "state", "country", "traffic_source", "created_at"],
}
PII_COLUMNS = [
    "first_name",
    "last_name",
    "email",
    "street_address",
    "postal_code",
    "latitude",
    "longitude",
]


def base_reads(decision: GuardAllowed) -> list[exp.Select]:
    """Every SELECT in the rewritten query that reads a warehouse table directly."""
    tree = parse(decision.rewritten_sql)
    reads = []
    for table in tree.find_all(exp.Table):
        if table.name not in COLUMNS:
            continue
        select = table.parent_select
        assert select is not None
        from_ = select.args["from_"] if "from_" in select.args else select.args.get("from")
        assert from_ is not None and from_.this is table, "a raw table is joined, not scoped"
        assert not select.args.get("joins"), "a scoped read joins nothing"
        reads.append(select)
    return reads


def projected(select: exp.Select) -> list[str]:
    return [e.alias_or_name for e in select.expressions]


# 1. One read-only statement


def test_a_single_select_is_allowed() -> None:
    decision = allowed("SELECT SUM(sale_price) AS revenue FROM order_items")

    assert decision.tables == ["order_items"]
    assert decision.rewritten


def test_ctes_are_allowed() -> None:
    decision = allowed(
        "WITH r AS (SELECT order_id, SUM(sale_price) AS total FROM order_items GROUP BY 1) "
        "SELECT AVG(total) FROM r"
    )

    assert decision.tables == ["order_items"]


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM orders WHERE TRUE",
        "UPDATE orders SET status = 'Cancelled' WHERE TRUE",
        "INSERT INTO orders (order_id) VALUES (1)",
        "MERGE orders t USING orders s ON t.order_id = s.order_id WHEN MATCHED THEN DELETE",
        "DROP TABLE orders",
        "CREATE TABLE x AS SELECT * FROM orders",
        "TRUNCATE TABLE orders",
        "EXECUTE IMMEDIATE 'DELETE FROM orders WHERE TRUE'",
        "DECLARE x INT64 DEFAULT 1",
        "BEGIN SELECT 1; END",
        "SELECT 1 FROM orders; SELECT 2 FROM orders",
        "SELECT 1 FROM orders; DROP TABLE orders",
    ],
)
def test_anything_but_one_select_is_rejected(sql: str) -> None:
    assert rejected(sql).reason in (RejectReason.NOT_SINGLE_SELECT, RejectReason.UNPARSEABLE)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM EXTERNAL_QUERY('conn', 'SELECT * FROM secrets')",
        "SELECT SESSION_USER() AS who FROM orders",
        "SELECT @@project_id AS p FROM orders",
        "SELECT * FROM orders WHERE brand IN UNNEST(@brands)",
    ],
)
def test_unknown_functions_and_parameters_are_rejected(sql: str) -> None:
    assert rejected(sql, EXECUTIVE).reason is RejectReason.UNSUPPORTED_SYNTAX


@pytest.mark.parametrize("sql", ["", "SELEC brand FROM products", "SELECT FROM WHERE (", "))"])
def test_unparseable_input_is_rejected_not_raised(sql: str) -> None:
    decision = GUARD.check(sql, MANAGER, row_limit=LIMIT)

    assert isinstance(decision, GuardRejected)
    assert decision.reason in (RejectReason.UNPARSEABLE, RejectReason.NOT_SINGLE_SELECT)


def test_pathologically_nested_sql_is_rejected_not_raised() -> None:
    sql = "(" * 300 + "SELECT 1" + ")" * 300

    assert rejected(sql).reason is RejectReason.UNPARSEABLE


HOSTILE = [
    "(" * 5000 + "SELECT 1" + ")" * 5000,
    "SELECT " + " + ".join(["1"] * 5000) + " FROM orders",
    "SELECT " + "CONCAT(" * 500 + "'x'" + ")" * 500 + " FROM orders",
    "SELECT 1 FROM orders WHERE " + " AND ".join(["TRUE"] * 5000),
    "WITH " + ", ".join(f"c{i} AS (SELECT 1 AS x)" for i in range(500)) + " SELECT 1 FROM c0",
    "SELECT 'unterminated FROM orders",
    "SELECT `unterminated FROM orders",
    "SELECT 1 FROM orders /* unterminated",
    "\x00\xff\ufffd" * 100,
    "SELECT \u202e1 FROM orders",
    ";" * 1000,
    "SELECT * FROM orders " + "JOIN orders USING (order_id) " * 300,
]


@pytest.mark.parametrize("sql", HOSTILE, ids=range(len(HOSTILE)))
@pytest.mark.parametrize("scope", [EXECUTIVE, MANAGER], ids=["executive", "manager"])
def test_the_guard_never_raises_on_hostile_input(sql: str, scope: BrandScope) -> None:
    """The port's contract is a decision for any string; an exception would escape run_sql."""
    decision = GUARD.check(sql, scope, row_limit=LIMIT)

    assert isinstance(decision, GuardAllowed | GuardRejected)


def test_no_role_rejects_everything() -> None:
    assert rejected("SELECT 1 FROM orders", BrandScope()).reason is RejectReason.NO_ROLE


# 2. Allowlist


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM inventory_items",
        "SELECT * FROM events",
        "SELECT * FROM distribution_centers",
        "SELECT * FROM thelook_ecommerce.INFORMATION_SCHEMA.TABLES",
        "SELECT * FROM `other-project.thelook_ecommerce.orders`",
        "SELECT * FROM other_dataset.orders",
    ],
)
def test_tables_outside_the_allowlist_are_rejected(sql: str) -> None:
    assert rejected(sql, EXECUTIVE).reason is RejectReason.TABLE_NOT_ALLOWED


def test_qualified_names_of_allowlisted_tables_are_allowed() -> None:
    decision = allowed(
        "SELECT COUNT(*) FROM `bigquery-public-data.thelook_ecommerce.orders`", EXECUTIVE
    )

    assert decision.tables == ["orders"]


def test_a_cte_may_not_shadow_a_warehouse_table() -> None:
    decision = rejected("WITH products AS (SELECT 1 AS id) SELECT * FROM products")

    assert decision.reason is RejectReason.TABLE_NOT_ALLOWED


@pytest.mark.parametrize("column", PII_COLUMNS)
@pytest.mark.parametrize("scope", [EXECUTIVE, MANAGER], ids=["executive", "manager"])
def test_each_pii_column_is_rejected_by_name(column: str, scope: BrandScope) -> None:
    decision = rejected(f"SELECT u.id, u.{column.upper()} FROM users u", scope)

    assert decision.reason is RejectReason.COLUMN_NOT_ALLOWED
    assert column in decision.detail


def test_a_pii_column_in_a_filter_is_rejected_too() -> None:
    decision = rejected("SELECT id FROM users WHERE email LIKE '%@gmail.com'", EXECUTIVE)

    assert decision.reason is RejectReason.COLUMN_NOT_ALLOWED


# 3. Out-of-scope Brands are rejected, not filtered


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT SUM(sale_price) FROM order_items oi JOIN products p ON p.id = oi.product_id "
        "WHERE p.brand = 'Nike'",
        "SELECT * FROM products WHERE 'Nike' = brand",
        "SELECT * FROM products WHERE brand IN ('Calvin Klein', 'Nike')",
        "SELECT * FROM products WHERE LOWER(brand) = 'nike'",
        "SELECT * FROM products WHERE brand LIKE 'Nik%'",
        "WITH b AS (SELECT * FROM products WHERE products.brand = 'Nike') SELECT * FROM b",
        "SELECT * FROM products WHERE brand IN UNNEST(['Nike'])",
        "SELECT * FROM products WHERE 'Nike' IN (brand)",
        "SELECT * FROM products WHERE CAST(brand AS STRING) = 'Nike'",
        "SELECT * FROM products WHERE brand IS NOT DISTINCT FROM 'Nike'",
        "SELECT * FROM products WHERE STARTS_WITH(brand, 'Nik')",
        "SELECT * FROM products WHERE REGEXP_CONTAINS(brand, 'Nike')",
    ],
)
def test_a_brand_outside_scope_is_rejected_with_the_brand(sql: str) -> None:
    decision = rejected(sql)

    assert decision.reason is RejectReason.BRAND_OUT_OF_SCOPE
    assert [b.lower() for b in decision.out_of_scope_brands] in (["nike"], ["nik%"], ["nik"])


def test_brand_matching_ignores_case_as_stored_in_products() -> None:
    """thelook stores 'adidas' and 'PUMA': a Manager's literal in another case is still theirs,
    and another Manager's Brand in any case is still out of scope."""
    puma = BrandScope(brands=frozenset({"PUMA"}))

    allowed("SELECT * FROM products WHERE brand = 'Puma'", puma)
    allowed("SELECT * FROM products WHERE UPPER(brand) = 'PUMA'", puma)
    assert rejected("SELECT * FROM products WHERE brand = 'ADIDAS'", puma).out_of_scope_brands == [
        "ADIDAS"
    ]


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM products WHERE STARTS_WITH(brand, 'Calvin')",
        "SELECT * FROM products WHERE REGEXP_CONTAINS(brand, '(?i)tommy')",
        "SELECT * FROM products WHERE brand IN UNNEST(['Calvin Klein'])",
        "SELECT * FROM products WHERE brand LIKE '%Klein'",
    ],
)
def test_patterns_matching_an_in_scope_brand_are_allowed(sql: str) -> None:
    allowed(sql)


def test_in_scope_brand_literals_are_allowed() -> None:
    decision = allowed(
        "SELECT brand, COUNT(*) FROM products WHERE brand IN ('Calvin Klein', 'tommy hilfiger') "
        "GROUP BY brand"
    )

    assert decision.tables == ["products"]


def test_an_executive_may_name_any_brand() -> None:
    allowed("SELECT * FROM products WHERE brand = 'Nike'", EXECUTIVE)


# 4. Scope rewrite


@pytest.mark.parametrize("table", list(COLUMNS))
def test_manager_reads_of_each_table_keep_only_their_brands(table: str) -> None:
    decision = allowed(f"SELECT COUNT(*) AS n FROM {table}")

    reads = base_reads(decision)
    assert reads, "the table is still read"
    for select in reads:
        assert select.args["where"] == MANAGER_FILTER[select.args["from_"].this.name]


@pytest.mark.parametrize("table", list(COLUMNS))
def test_executive_reads_project_the_allowlist_and_filter_nothing(table: str) -> None:
    decision = allowed(f"SELECT * FROM {table}", EXECUTIVE)

    [select] = base_reads(decision)
    assert select.args.get("where") is None
    assert projected(select) == COLUMNS[table]
    assert decision.params == {}


@pytest.mark.parametrize("table", list(COLUMNS))
def test_manager_outer_reads_project_the_allowlist_without_order_level_fields(
    table: str,
) -> None:
    decision = allowed(f"SELECT * FROM {table}")

    outer = base_reads(decision)[0]  # the model's own read, before the filters it carries
    assert projected(outer) == [c for c in COLUMNS[table] if c != "num_of_item"]


def test_the_hld_example_is_scoped_in_every_table_and_subquery() -> None:
    decision = allowed(
        "WITH spend AS ("
        "  SELECT u.state, SUM(oi.sale_price) AS revenue"
        "  FROM order_items oi JOIN users u ON u.id = oi.user_id"
        "  WHERE oi.order_id IN (SELECT order_id FROM orders WHERE status = 'Complete')"
        "  GROUP BY u.state)"
        " SELECT * FROM spend ORDER BY revenue DESC"
    )

    reads = base_reads(decision)
    assert {s.args["from_"].this.name for s in reads} == set(COLUMNS)
    for select in reads:
        assert select.args["where"] == MANAGER_FILTER[select.args["from_"].this.name]
    assert decision.tables == ["order_items", "users", "orders"]


def test_aliases_still_resolve_after_the_rewrite() -> None:
    decision = allowed(
        "SELECT order_items.status, COUNT(*) FROM order_items GROUP BY order_items.status"
    )

    tree = parse(decision.rewritten_sql)
    [subquery] = [s for s in tree.find_all(exp.Subquery) if s.alias == "order_items"]
    assert subquery.parent_select is tree


def test_brands_are_bound_as_a_parameter_never_written_into_sql() -> None:
    decision = allowed("SELECT SUM(sale_price) FROM order_items")

    assert decision.params == {"brands": ["Calvin Klein", "Tommy Hilfiger"]}
    assert "Calvin" not in decision.rewritten_sql
    assert "Tommy" not in decision.rewritten_sql
    assert decision.scope == MANAGER


def test_executive_scope_is_all_brands() -> None:
    assert allowed("SELECT 1 AS one FROM orders", EXECUTIVE).scope == EXECUTIVE


# 4b. Order-level fields


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT AVG(num_of_item) FROM orders",
        "SELECT o.order_id FROM orders o WHERE o.num_of_item > 2",
        "SELECT order_id FROM orders ORDER BY NUM_OF_ITEM",
    ],
)
def test_num_of_item_is_rejected_for_managers_with_a_hint(sql: str) -> None:
    decision = rejected(sql)

    assert decision.reason is RejectReason.ORDER_LEVEL_FIELD
    assert decision.hint is not None
    assert "order_items" in decision.hint


def test_num_of_item_is_allowed_for_executives() -> None:
    decision = allowed("SELECT AVG(num_of_item) AS basket FROM orders", EXECUTIVE)

    [select] = base_reads(decision)
    assert "num_of_item" in projected(select)


# 5. Forced LIMIT


def test_a_limit_is_forced_on_the_outer_query() -> None:
    tree = parse(allowed("SELECT brand FROM products").rewritten_sql)

    assert tree.args["limit"].expression == exp.Literal.number(LIMIT)


def test_a_larger_limit_is_lowered_and_a_smaller_one_kept() -> None:
    big = parse(allowed("SELECT brand FROM products LIMIT 50000").rewritten_sql)
    small = parse(allowed("SELECT brand FROM products LIMIT 10").rewritten_sql)

    assert big.args["limit"].expression == exp.Literal.number(LIMIT)
    assert small.args["limit"].expression == exp.Literal.number(10)


def test_a_union_gets_one_outer_limit() -> None:
    sql = "SELECT brand FROM products UNION ALL SELECT category FROM products"
    tree = parse(allowed(sql, EXECUTIVE).rewritten_sql)

    assert tree.args["limit"].expression == exp.Literal.number(LIMIT)


# The real SQL the agent is taught with must pass


def _seed_trio_sql() -> list[tuple[str, str]]:
    found = []
    for path in sorted(Path("seed/trios").glob("*.yaml")):
        sql = yaml.safe_load(path.read_text())["sql"]
        # The model inlines a Trio's parameters and placeholders; run_sql binds none.
        sql = sql.replace("IN UNNEST(@brands)", "IN ('Calvin Klein')")
        sql = re.sub(r"@\w+", "DATE '2025-01-01'" if "period" in sql else "30", sql)
        found.append((path.stem, sql))
    return found


@pytest.mark.parametrize(("trio", "sql"), _seed_trio_sql())
def test_every_seed_trio_passes_for_both_roles(trio: str, sql: str) -> None:
    allowed(sql, EXECUTIVE)
    allowed(sql, MANAGER)


# --- filter literals for the empty-result probe (slice 3, HLD 06 § SQL self-correction) -------


def test_an_equality_filter_is_read_with_its_table() -> None:
    sql = "SELECT COUNT(*) FROM users WHERE state = 'California'"

    assert allowed(sql).filters == [
        FilterLiteral(table="users", column="state", operator="=", value="California")
    ]


def test_aliases_resolve_and_in_lists_give_one_literal_per_value() -> None:
    sql = (
        "SELECT SUM(oi.sale_price) FROM order_items AS oi JOIN products AS p "
        "ON oi.product_id = p.id WHERE p.category IN ('Jeans', 'Tops') AND oi.status = 'Complete'"
    )

    assert allowed(sql, EXECUTIVE).filters == [
        FilterLiteral(table="products", column="category", operator="IN", value="Jeans"),
        FilterLiteral(table="products", column="category", operator="IN", value="Tops"),
        FilterLiteral(table="order_items", column="status", operator="=", value="Complete"),
    ]


def test_date_bounds_are_read_through_date_wrappers() -> None:
    sql = (
        "SELECT COUNT(*) FROM orders WHERE DATE(created_at) >= '2025-01-01' "
        "AND created_at < TIMESTAMP('2026-01-01')"
    )

    assert allowed(sql, EXECUTIVE).filters == [
        FilterLiteral(table="orders", column="created_at", operator=">=", value="2025-01-01"),
        FilterLiteral(table="orders", column="created_at", operator="<", value="2026-01-01"),
    ]


def test_a_column_two_tables_share_is_skipped_when_unqualified() -> None:
    sql = (
        "SELECT COUNT(*) FROM orders AS o JOIN order_items AS oi ON o.order_id = oi.order_id "
        "WHERE status = 'Returned'"
    )

    assert allowed(sql, EXECUTIVE).filters == []
