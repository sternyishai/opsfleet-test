"""run_sql: the guard -> execute path, with a fake QueryRunner and the real guard (LLD 05)."""

import pytest
from langchain_core.messages import AIMessage

from agent.contracts.channel import AgentReply
from agent.contracts.tools import (
    RunSqlEmpty,
    RunSqlGaveUp,
    RunSqlInput,
    RunSqlOk,
    RunSqlRetry,
    ToolError,
)
from agent.core.catalog import BUNDLED
from agent.domain.identity import Role, UserContext
from agent.domain.query import (
    DryRunResult,
    FilterLiteral,
    QueryResult,
    RejectReason,
    ResultSummary,
)
from agent.errors import MessageClass, SelfCorrectableError, TransientError
from agent.tools.analysis import run_sql
from agent.tools.context import SqlBudget, TurnContext
from tests.fakes import (
    FakeLLMFactory,
    FakeQueryRunner,
    FakeTracer,
    ask,
    chat_service,
    fake_ports,
    rows,
    turn_context,
)

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
    assert allowed.pop("sql.written") == "SELECT COUNT(*) FROM orders"
    assert "@brands" in allowed.pop("sql.rewritten")  # scoped by parameter, never by name
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
    assert rejected["sql.written"] == "DROP TABLE orders"
    assert rejected["sql.rewritten"] is None


# --- self-correction (slice 3) ---------------------------------------------------------------


FREE = DryRunResult(bytes_processed=1024, over_cap=False)


def _ask(ctx: TurnContext, budget: SqlBudget, sql: str):
    return run_sql(RunSqlInput(sql=sql), ctx, budget)


def test_a_syntax_error_is_a_retry_with_the_schema_then_the_fix_runs() -> None:
    runner = FakeQueryRunner(
        REVENUE,
        dry_runs=[SelfCorrectableError("Unrecognized name: sale_prise at [1:12]"), FREE],
    )
    ctx = turn_context(CEO, fake_ports(CEO, warehouse=runner))

    first, budget = _ask(ctx, SqlBudget(), "SELECT SUM(sale_prise) FROM order_items")
    second, budget = _ask(ctx, budget, "SELECT SUM(sale_price) FROM order_items")

    assert isinstance(first, RunSqlRetry)
    assert first.kind == "dry_run_error"
    assert first.error == "Unrecognized name: sale_prise at [1:12]"
    assert first.retries_left == 1
    assert [t.name for t in first.relevant_schema] == ["order_items"]
    assert "sale_price" in [c.name for c in first.relevant_schema[0].columns]
    assert isinstance(second, RunSqlOk)
    assert second.attempt == 2
    assert len(runner.executed) == 1  # the broken statement never ran


def test_a_scan_over_the_bytes_cap_is_a_retry_with_a_narrowing_hint_and_never_runs() -> None:
    huge = DryRunResult(bytes_processed=5 * 1024**3, over_cap=True)
    runner = FakeQueryRunner(REVENUE, dry_runs=[huge])
    ctx = turn_context(CEO, fake_ports(CEO, warehouse=runner))

    out, budget = _ask(ctx, SqlBudget(), "SELECT * FROM order_items")

    assert isinstance(out, RunSqlRetry)
    assert out.kind == "over_bytes_cap"
    assert out.hint == "Narrow the date range or aggregate earlier."
    assert out.retries_left == 1
    assert runner.executed == []
    assert budget.retries_used == 1


def test_every_retry_kind_draws_on_one_budget_of_two_then_it_gives_up() -> None:
    runner = FakeQueryRunner(
        outcomes=[SelfCorrectableError("division by zero")],
        dry_runs=[SelfCorrectableError("Unrecognized name: x"), FREE],
    )
    ctx = turn_context(CEO, fake_ports(CEO, warehouse=runner))

    guard, budget = _ask(ctx, SqlBudget(), "DROP TABLE orders")
    dry, budget = _ask(ctx, budget, "SELECT x FROM orders")
    runtime, budget = _ask(
        ctx, budget, "SELECT 1 / 0 FROM orders WHERE status = 'Complete' AND gender = 'F'"
    )

    assert isinstance(guard, RunSqlRetry) and guard.retries_left == 1
    assert isinstance(dry, RunSqlRetry) and dry.retries_left == 0
    assert isinstance(runtime, RunSqlGaveUp)
    assert runtime.message_class is MessageClass.SELF_CORRECTION_EXHAUSTED
    assert runtime.tried == ["filtering by status", "filtering by gender"]
    assert budget.attempts == 3


def test_giving_up_after_the_bytes_cap_says_the_question_is_too_large() -> None:
    huge = DryRunResult(bytes_processed=5 * 1024**3, over_cap=True)
    ctx = turn_context(CEO, fake_ports(CEO, warehouse=FakeQueryRunner(dry_runs=[huge])))

    _, budget = _ask(ctx, SqlBudget(), "SELECT * FROM order_items")
    _, budget = _ask(ctx, budget, "SELECT * FROM order_items")
    out, budget = _ask(ctx, budget, "SELECT * FROM order_items")

    assert isinstance(out, RunSqlGaveUp)
    assert out.message_class is MessageClass.QUERY_TOO_LARGE
    tracer = ctx.ports.tracer
    assert isinstance(tracer, FakeTracer)
    last = [s.attributes for s in tracer.spans if s.name == "run_sql"][-1]
    assert last["sql.gave_up"] == "query_too_large"


def test_insisting_on_an_out_of_scope_brand_after_the_retry_ends_with_out_of_scope() -> None:
    runner = FakeQueryRunner(REVENUE)
    ctx = turn_context(MANAGER, fake_ports(MANAGER, warehouse=runner))

    first, budget = _ask(ctx, SqlBudget(), "SELECT COUNT(*) FROM products WHERE brand = 'Nike'")
    second, budget = _ask(ctx, budget, "SELECT COUNT(*) FROM products WHERE LOWER(brand) = 'nike'")

    assert isinstance(first, RunSqlRetry)
    assert isinstance(second, ToolError)
    assert second.message_class is MessageClass.OUT_OF_SCOPE
    assert runner.executed == []


# --- the empty-result probe -------------------------------------------------------------------

EMPTY = rows()
STATES = rows({"state": "CA", "n": 90}, {"state": "TX", "n": 70}, {"state": "NY", "n": 60})
ORDER_DATES = rows({"earliest": "2019-01-06 08:00:00", "latest": "2026-11-03 21:14:00"})


def _probes(sql: str) -> QueryResult | None:
    """The warehouse's answer to a probe query; None for the question's own queries."""
    if "GROUP BY state" in sql:
        return STATES
    if "MIN(created_at)" in sql:
        return ORDER_DATES
    return None


def test_a_wrong_literal_gets_a_probe_hint_and_the_corrected_query_succeeds() -> None:
    runner = FakeQueryRunner(outcomes=[EMPTY, rows({"n": 42})], probes=_probes)
    ctx = turn_context(CEO, fake_ports(CEO, warehouse=runner))

    first, budget = _ask(
        ctx, SqlBudget(), "SELECT COUNT(*) AS n FROM users WHERE state = 'California'"
    )
    second, budget = _ask(ctx, budget, "SELECT COUNT(*) AS n FROM users WHERE state = 'CA'")

    assert isinstance(first, RunSqlEmpty)
    assert first.probe_hint == "state has 'CA', not 'California'"
    assert first.checked == [
        FilterLiteral(table="users", column="state", operator="=", value="California")
    ]
    assert first.final is False
    assert isinstance(second, RunSqlOk)
    assert budget.diagnoses_used == 1
    assert budget.retries_used == 0  # an empty result is not a retry


def test_empty_with_valid_filters_is_the_answer() -> None:
    runner = FakeQueryRunner(outcomes=[EMPTY], probes=_probes)
    ctx = turn_context(CEO, fake_ports(CEO, warehouse=runner))

    out, _ = _ask(
        ctx,
        SqlBudget(),
        "SELECT COUNT(*) AS n FROM orders AS o JOIN users AS u ON o.user_id = u.id "
        "WHERE u.state = 'TX' AND o.created_at >= '2026-10-01'",
    )

    assert isinstance(out, RunSqlEmpty)
    assert out.final is True
    assert out.probe_hint == "created_at runs from 2019-01-06 to 2026-11-03"


def test_a_date_past_the_last_load_is_a_hint_not_the_answer() -> None:
    runner = FakeQueryRunner(outcomes=[EMPTY], probes=_probes)
    ctx = turn_context(CEO, fake_ports(CEO, warehouse=runner))

    out, _ = _ask(ctx, SqlBudget(), "SELECT COUNT(*) FROM orders WHERE created_at >= '2026-11-10'")

    assert isinstance(out, RunSqlEmpty)
    assert out.final is False
    assert out.probe_hint == "created_at runs from 2019-01-06 to 2026-11-03"


def test_the_second_empty_result_is_final_and_runs_no_probe() -> None:
    runner = FakeQueryRunner(outcomes=[EMPTY], probes=_probes)
    ctx = turn_context(CEO, fake_ports(CEO, warehouse=runner))
    sql = "SELECT COUNT(*) AS n FROM users WHERE state = 'California'"

    _, budget = _ask(ctx, SqlBudget(), sql)
    executed = len(runner.executed)
    second, budget = _ask(ctx, budget, sql)

    assert isinstance(second, RunSqlEmpty)
    assert second.final is True
    assert second.probe_hint is None
    assert len(runner.executed) == executed + 1  # the query itself, no probe


def test_a_probe_passes_the_guard_and_the_dry_run_like_any_query() -> None:
    runner = FakeQueryRunner(outcomes=[EMPTY], probes=_probes)
    ctx = turn_context(MANAGER, fake_ports(MANAGER, warehouse=runner))

    _ask(ctx, SqlBudget(), "SELECT COUNT(*) FROM users WHERE state = 'California'")

    [probe] = [sql for sql, *_ in runner.executed if "GROUP BY state" in sql]
    assert "@brands" in probe  # scoped to the Manager's Brands
    assert probe in runner.dry_ran


def test_bigquery_down_is_not_a_retry_it_ends_the_turn_as_data_source_down() -> None:
    down = TransientError(
        "ServiceUnavailable", dependency="bigquery", message_class=MessageClass.DATA_SOURCE_DOWN
    )
    ctx = turn_context(CEO, fake_ports(CEO, warehouse=FakeQueryRunner(error=down)))

    with pytest.raises(TransientError) as raised:
        _ask(ctx, SqlBudget(), "SELECT COUNT(*) FROM orders")

    assert raised.value.message_class is MessageClass.DATA_SOURCE_DOWN


# --- through a whole turn ---------------------------------------------------------------------


def _calls_run_sql(sql: str, call_id: str) -> AIMessage:
    return AIMessage(
        content="", tool_calls=[{"name": "run_sql", "args": {"sql": sql}, "id": call_id}]
    )


def test_a_turn_that_insists_on_another_brand_ends_with_the_out_of_scope_message() -> None:
    llm = FakeLLMFactory(
        analyst=[
            _calls_run_sql("SELECT COUNT(*) FROM products WHERE brand = 'Nike'", "c1"),
            _calls_run_sql("SELECT COUNT(*) FROM products WHERE brand = 'NIKE'", "c2"),
            AIMessage(content="Nike sold 12,000 units."),  # never reached
        ]
    )
    runner = FakeQueryRunner(REVENUE)
    service, _ = chat_service(fake_ports(MANAGER, llm=llm, warehouse=runner))

    [reply], _ = ask(service, "manager-a", "How many Nike products are there?")

    assert isinstance(reply, AgentReply)
    assert reply.message_class is MessageClass.OUT_OF_SCOPE
    assert reply.markdown == BUNDLED[MessageClass.OUT_OF_SCOPE]
    assert runner.executed == []


def test_bigquery_down_mid_turn_ends_with_the_data_source_message_and_its_reference() -> None:
    down = TransientError(
        "ServiceUnavailable", dependency="bigquery", message_class=MessageClass.DATA_SOURCE_DOWN
    )
    llm = FakeLLMFactory(analyst=[_calls_run_sql("SELECT COUNT(*) FROM orders", "c1")])
    service, _ = chat_service(fake_ports(CEO, llm=llm, warehouse=FakeQueryRunner(error=down)))

    [reply], _ = ask(service, "ceo", "How many orders?")

    assert isinstance(reply, AgentReply)
    assert reply.message_class is MessageClass.DATA_SOURCE_DOWN
    assert f"(ref: {reply.trace_id})" in reply.markdown


def test_each_call_leaves_its_outcome_on_a_run_sql_span() -> None:
    huge = DryRunResult(bytes_processed=5 * 1024**3, over_cap=True)
    ports = fake_ports(CEO, warehouse=FakeQueryRunner(REVENUE, dry_runs=[huge, FREE]))
    ctx = turn_context(CEO, ports)

    _, budget = _ask(ctx, SqlBudget(), "SELECT * FROM order_items")
    _ask(ctx, budget, "SELECT SUM(sale_price) AS revenue FROM order_items")

    tracer = ports.tracer
    assert isinstance(tracer, FakeTracer)
    over, ok = [s.attributes for s in tracer.spans if s.name == "run_sql"]
    assert over["sql.status"] == "retry"
    assert over["sql.retry_kind"] == "over_bytes_cap"
    assert over["dry_run.bytes"] == 5 * 1024**3
    assert ok == {
        "dry_run.bytes": 1024,
        "attempt": 2,
        "sql.status": "ok",
        "sql.retry_kind": None,
        "sql.gave_up": None,
        "error": None,
        "row_count": 1,
        "bytes_billed": 1024,
    }
