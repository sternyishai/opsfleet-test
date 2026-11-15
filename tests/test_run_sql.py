"""run_sql: the guard -> execute path, with a fake QueryRunner and the real guard (LLD 05)."""

from agent.contracts.tools import RunSqlInput, RunSqlOk, RunSqlRetry
from agent.domain.identity import Role, UserContext
from agent.domain.query import QueryResult, RejectReason, ResultSummary
from agent.tools.analysis import run_sql
from agent.tools.context import SqlBudget
from tests.fakes import FakeQueryRunner, FakeTracer, fake_ports, turn_context

CEO = UserContext(user_id="ceo", role=Role.EXECUTIVE, access_version=1)
MANAGER = UserContext(
    user_id="manager-a",
    role=Role.MANAGER,
    brands=frozenset({"Calvin Klein", "Tommy Hilfiger"}),
    access_version=1,
)
REVENUE = QueryResult(
    columns=["revenue"],
    rows=[{"revenue": 1234567.89}],
    row_count=1,
    truncated=False,
    bytes_billed=1024,
)


def test_happy_path_returns_ok_and_a_summary() -> None:
    runner = FakeQueryRunner(REVENUE)
    ctx = turn_context(CEO, fake_ports(CEO, warehouse=runner))

    out, budget = run_sql(
        RunSqlInput(sql="SELECT SUM(sale_price) AS revenue FROM order_items"), ctx, SqlBudget()
    )

    assert isinstance(out, RunSqlOk)
    assert out.result == REVENUE
    assert out.attempt == 1
    assert budget.attempts == 1
    assert ResultSummary.of(out.result) == ResultSummary(
        columns=["revenue"], row_count=1, key_figures={"revenue": "1234567.89"}
    )


def test_runner_gets_the_guarded_sql_and_the_limits() -> None:
    runner = FakeQueryRunner(REVENUE)
    ctx = turn_context(CEO, fake_ports(CEO, warehouse=runner))

    run_sql(RunSqlInput(sql="SELECT brand FROM products"), ctx, SqlBudget())

    [(sql, params, max_bytes, max_rows)] = runner.executed
    assert sql == (
        "SELECT brand FROM (SELECT id, cost, category, name, brand, retail_price, department, "
        "sku, distribution_center_id FROM products) AS products LIMIT 1000"
    )
    assert params == {}
    assert max_bytes == 2 * 1024**3
    assert max_rows == 200


def test_a_rejected_statement_never_reaches_the_warehouse() -> None:
    runner = FakeQueryRunner(REVENUE)
    ctx = turn_context(CEO, fake_ports(CEO, warehouse=runner))

    out, _ = run_sql(RunSqlInput(sql="DELETE FROM orders WHERE TRUE"), ctx, SqlBudget())

    assert isinstance(out, RunSqlRetry)
    assert out.reason is RejectReason.NOT_SINGLE_SELECT
    assert runner.executed == []


def test_summary_keeps_no_figures_for_multi_row_results() -> None:
    rows = QueryResult(
        columns=["brand", "revenue"],
        rows=[{"brand": "A", "revenue": 1}, {"brand": "B", "revenue": 2}],
        row_count=2,
        truncated=False,
        bytes_billed=1,
    )

    assert ResultSummary.of(rows) == ResultSummary(columns=["brand", "revenue"], row_count=2)


def test_a_manager_query_binds_their_brands_as_a_parameter() -> None:
    runner = FakeQueryRunner(REVENUE)
    ctx = turn_context(MANAGER, fake_ports(MANAGER, warehouse=runner))

    out, budget = run_sql(
        RunSqlInput(sql="SELECT SUM(sale_price) FROM order_items"), ctx, SqlBudget()
    )

    assert isinstance(out, RunSqlOk)
    [(sql, params, _, _)] = runner.executed
    assert params == {"brands": ["Calvin Klein", "Tommy Hilfiger"]}
    assert "@brands" in sql and "Calvin" not in sql
    assert budget.last_scope == MANAGER.scope


def test_an_out_of_scope_brand_is_a_retry_naming_the_brands_the_user_can_analyze() -> None:
    runner = FakeQueryRunner(REVENUE)
    ctx = turn_context(MANAGER, fake_ports(MANAGER, warehouse=runner))

    out, _ = run_sql(
        RunSqlInput(sql="SELECT COUNT(*) FROM products WHERE brand = 'Nike'"), ctx, SqlBudget()
    )

    assert isinstance(out, RunSqlRetry)
    assert out.reason is RejectReason.BRAND_OUT_OF_SCOPE
    assert out.hint == "You can analyze Calvin Klein and Tommy Hilfiger."
    assert "Nike" in out.error
    assert runner.executed == []


def test_guard_decisions_are_trace_attributes() -> None:
    ports = fake_ports(MANAGER)
    ctx = turn_context(MANAGER, ports)

    run_sql(RunSqlInput(sql="SELECT COUNT(*) FROM orders"), ctx, SqlBudget())
    run_sql(RunSqlInput(sql="DROP TABLE orders"), ctx, SqlBudget())

    tracer = ports.tracer
    assert isinstance(tracer, FakeTracer)
    allowed, rejected = [s.attributes for s in tracer.spans if s.name == "query_guard"]
    assert allowed == {
        "guard.decision": "allowed",
        "guard.reason": None,
        "guard.rewritten": True,
        "guard.tables": ["orders"],
        "guard.scope_brands": 2,
    }
    assert rejected["guard.decision"] == "rejected"
    assert rejected["guard.reason"] == "not_single_select"
    assert rejected["guard.tables"] == []
