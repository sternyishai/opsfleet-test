"""run_sql: the guard -> execute path, with a fake QueryRunner and the real guard (LLD 05)."""

from agent.contracts.tools import RunSqlInput, RunSqlOk, RunSqlRetry
from agent.domain.identity import Role, UserContext
from agent.domain.query import QueryResult, RejectReason, ResultSummary
from agent.tools.analysis import run_sql
from agent.tools.context import SqlBudget
from tests.fakes import FakeQueryRunner, fake_ports, turn_context

CEO = UserContext(user_id="ceo", role=Role.EXECUTIVE, access_version=1)
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
    assert sql == "SELECT brand FROM products LIMIT 1000"
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
