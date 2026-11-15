"""Saved Reports: save_report, list_my_reports, get_my_report and the report Views (LLD 05).

Every test runs against both ``ReportRepository`` adapters (the ``reports`` fixture). Scope
always comes from a real ``run_sql`` through the real query guard, never from a hand-built
budget, so "scope copied from the guard" is tested end to end.
"""

from datetime import UTC, date, datetime
from uuid import UUID, uuid4

import pytest
from langchain_core.messages import AIMessage

from agent.config import Limits
from agent.contracts.channel import AgentReply
from agent.contracts.tools import (
    EntityMention,
    ExtractedEntities,
    GetReportInput,
    GetReportOk,
    ListReportsInput,
    RunSqlInput,
    RunSqlOk,
    SaveReportInput,
    SaveReportNeedsQuery,
    SaveReportOk,
    ToolError,
)
from agent.core.views import Views
from agent.domain.identity import BrandScope, Role, UserContext
from agent.domain.reports import EntityType, ReportStructured
from agent.errors import MessageClass, PermanentError
from agent.ports.llm import ModelStep
from agent.ports.reports import ReportRepository
from agent.tools.analysis import get_my_report, list_my_reports, run_sql, save_report
from agent.tools.context import SqlBudget, TurnContext
from tests.fakes import FakeLLMFactory, ask, chat_service, fake_ports, turn_context

CEO = UserContext(user_id="ceo", role=Role.EXECUTIVE, access_version=1)
MANAGER_A = UserContext(
    user_id="manager-a",
    role=Role.MANAGER,
    brands=frozenset({"Calvin Klein", "Tommy Hilfiger"}),
    access_version=1,
)
MANAGER_B = UserContext(
    user_id="manager-b", role=Role.MANAGER, brands=frozenset({"Carhartt"}), access_version=1
)

BRAND_SQL = (
    "SELECT p.brand, SUM(oi.sale_price) AS revenue FROM order_items oi "
    "JOIN products p ON p.id = oi.product_id GROUP BY p.brand"
)


def entities(*values: str) -> ExtractedEntities:
    return ExtractedEntities(
        entities=[EntityMention(entity_type=EntityType.BRAND, value=v) for v in values]
    )


def ctx_for(
    user: UserContext,
    reports: ReportRepository,
    *,
    llm: FakeLLMFactory | None = None,
    conversation_id: UUID | None = None,
    turn_id: str = "t1",
) -> TurnContext:
    ports = fake_ports(CEO, MANAGER_A, MANAGER_B, llm=llm, reports=reports)
    return turn_context(user, ports, conversation_id=conversation_id, turn_id=turn_id)


def queried(ctx: TurnContext) -> SqlBudget:
    """A successful run_sql this turn, through the real guard."""
    out, budget = run_sql(RunSqlInput(sql=BRAND_SQL), ctx, SqlBudget())
    assert isinstance(out, RunSqlOk)
    return budget


def report_input(
    markdown: str = "Calvin Klein revenue was $46,399.46 in 2025.", **kw
) -> SaveReportInput:
    fields = {
        "title": "Calvin Klein 2025 revenue",
        "question": "What was Calvin Klein revenue in 2025?",
        "sql": BRAND_SQL,
        "markdown": markdown,
        "structured": ReportStructured(
            title="Calvin Klein 2025 revenue", summary="Revenue held up.", insights=["Q4 led."]
        ),
    } | kw
    return SaveReportInput.model_validate(fields)


def saved(ctx: TurnContext, args: SaveReportInput | None = None) -> SaveReportOk:
    out = save_report(args or report_input(), ctx, queried(ctx))
    assert isinstance(out, SaveReportOk), out
    return out


# --- save_report ----------------------------------------------------------------------------


def test_saving_twice_in_one_turn_returns_the_first_report(reports: ReportRepository) -> None:
    ctx = ctx_for(MANAGER_A, reports)

    first = saved(ctx)
    second = saved(ctx, report_input(markdown="A different answer."))

    assert second.report_id == first.report_id
    assert (first.created, second.created) == (True, False)
    listed = list_my_reports(ListReportsInput(), ctx)
    assert listed.total == 1


def test_another_turn_saves_another_report(reports: ReportRepository) -> None:
    cid = uuid4()
    saved(ctx_for(MANAGER_A, reports, conversation_id=cid, turn_id="t1"))
    saved(ctx_for(MANAGER_A, reports, conversation_id=cid, turn_id="t2"))

    assert list_my_reports(ListReportsInput(), ctx_for(MANAGER_A, reports)).total == 2


def test_the_scope_is_the_guards_not_the_models(reports: ReportRepository) -> None:
    ctx = ctx_for(MANAGER_A, reports)

    out = saved(ctx)

    got = get_my_report(GetReportInput(report_id=out.report_id), ctx)
    assert isinstance(got, GetReportOk)
    assert got.report.scope == BrandScope(brands=frozenset({"Calvin Klein", "Tommy Hilfiger"}))
    assert got.report.author_id == "manager-a"


def test_an_executive_report_covers_all_brands(reports: ReportRepository) -> None:
    ctx = ctx_for(CEO, reports)

    got = get_my_report(GetReportInput(report_id=saved(ctx).report_id), ctx)

    assert isinstance(got, GetReportOk)
    assert got.report.scope == BrandScope(all_brands=True)


def test_no_query_this_turn_saves_nothing(reports: ReportRepository) -> None:
    ctx = ctx_for(MANAGER_A, reports)

    out = save_report(report_input(), ctx, SqlBudget())

    assert isinstance(out, SaveReportNeedsQuery)
    assert list_my_reports(ListReportsInput(), ctx).total == 0


def test_pii_is_redacted_before_the_write(reports: ReportRepository) -> None:
    ctx = ctx_for(MANAGER_A, reports)
    args = report_input(
        markdown="Top Customer 4521 (jane.doe@example.com) spent $1,204.",
        structured=ReportStructured(
            title="Top Customers",
            summary="Call 415-555-0134 for details.",
            action_items=["Email jane.doe@example.com"],
        ),
    )

    out = saved(ctx, args)

    assert out.redactions == 3
    got = get_my_report(GetReportInput(report_id=out.report_id), ctx)
    assert isinstance(got, GetReportOk)
    stored = got.report.model_dump_json()
    assert "jane.doe@example.com" not in stored
    assert "415-555-0134" not in stored
    assert "[REDACTED:email]" in got.report.markdown
    assert "4521" in got.report.markdown  # a Customer ID is not PII


def test_entities_are_extracted_with_their_own_model_step(reports: ReportRepository) -> None:
    llm = FakeLLMFactory(structured={ModelStep.ENTITY_EXTRACTION: entities("Calvin Klein")})
    ctx = ctx_for(MANAGER_A, reports, llm=llm)

    saved(ctx, report_input(markdown="Revenue was up."))  # the text never says the Brand

    assert ModelStep.ENTITY_EXTRACTION in llm.steps
    found = list_my_reports(ListReportsInput(entities=["calvin klein"]), ctx)
    assert found.total == 1


def test_a_failed_extraction_still_saves_the_report(reports: ReportRepository) -> None:
    # The fake raises TransientError for an unscripted extraction, like a model outage.
    ctx = ctx_for(MANAGER_A, reports, llm=FakeLLMFactory())

    out = saved(ctx)

    assert out.created


# --- list_my_reports / get_my_report --------------------------------------------------------


def test_another_authors_reports_are_never_listed(reports: ReportRepository) -> None:
    saved(ctx_for(MANAGER_A, reports))

    listed = list_my_reports(ListReportsInput(), ctx_for(CEO, reports))

    assert listed.total == 0
    assert listed.reports == []


def test_another_authors_report_is_not_found_never_forbidden(reports: ReportRepository) -> None:
    out = saved(ctx_for(MANAGER_A, reports))

    foreign = get_my_report(GetReportInput(report_id=out.report_id), ctx_for(CEO, reports))
    missing = get_my_report(GetReportInput(report_id=uuid4()), ctx_for(CEO, reports))

    assert isinstance(foreign, ToolError)
    assert foreign.message_class is MessageClass.REPORT_NOT_FOUND
    assert foreign.model_dump(exclude={"detail"}) == missing.model_dump(exclude={"detail"})


def test_a_report_outside_the_current_scope_is_listed_by_title_only(
    reports: ReportRepository,
) -> None:
    out = saved(ctx_for(MANAGER_A, reports))
    lost_ck = MANAGER_A.model_copy(
        update={"brands": frozenset({"Calvin Klein"}), "access_version": 2}
    )
    ctx = ctx_for(lost_ck, reports)

    listed = list_my_reports(ListReportsInput(), ctx)
    opened = get_my_report(GetReportInput(report_id=out.report_id), ctx)

    [item] = listed.reports
    assert not item.accessible
    assert item.title == "Calvin Klein 2025 revenue"
    assert "scope" not in item.model_dump()  # the Brands it covers are not shown
    assert isinstance(opened, ToolError)
    assert opened.message_class is MessageClass.REPORT_OUT_OF_SCOPE


def test_an_opened_report_is_labelled_with_its_date(reports: ReportRepository) -> None:
    ctx = ctx_for(MANAGER_A, reports)

    got = get_my_report(GetReportInput(report_id=saved(ctx).report_id), ctx)

    assert isinstance(got, GetReportOk)
    assert got.label == f"data as of {date.today().isoformat()}"


def test_list_filters_by_conversation_and_text(reports: ReportRepository) -> None:
    here, there = uuid4(), uuid4()
    saved(ctx_for(MANAGER_A, reports, conversation_id=here))
    saved(
        ctx_for(MANAGER_A, reports, conversation_id=there),
        report_input(title="Tommy Hilfiger returns", markdown="Returns rose in March."),
    )
    ctx = ctx_for(MANAGER_A, reports, conversation_id=here)

    in_this = list_my_reports(ListReportsInput(this_conversation=True), ctx)
    by_text = list_my_reports(ListReportsInput(text="returns"), ctx)

    assert [r.title for r in in_this.reports] == ["Calvin Klein 2025 revenue"]
    assert [r.title for r in by_text.reports] == ["Tommy Hilfiger returns"]


def test_list_is_newest_first_and_capped(reports: ReportRepository) -> None:
    cid = uuid4()
    for n in range(3):
        saved(
            ctx_for(MANAGER_A, reports, conversation_id=cid, turn_id=f"t{n}"),
            report_input(title=f"report {n}"),
        )

    listed = list_my_reports(ListReportsInput(limit=2), ctx_for(MANAGER_A, reports))

    assert listed.total == 3
    assert [r.title for r in listed.reports] == ["report 2", "report 1"]


# --- Views (the model-free /reports) ---------------------------------------------------------


def views_for(reports: ReportRepository) -> Views:
    ports = fake_ports(CEO, MANAGER_A, MANAGER_B, reports=reports)
    return Views(ports, Limits(_env_file=None))  # pyright: ignore[reportCallIssue]


def test_views_show_only_the_users_own_reports(reports: ReportRepository) -> None:
    out = saved(ctx_for(MANAGER_A, reports))
    views = views_for(reports)

    mine = views.reports("manager-a", ListReportsInput())
    theirs = views.reports("manager-b", ListReportsInput())
    opened = views.report("manager-a", out.report_id)
    foreign = views.report("manager-b", out.report_id)

    assert [r.report_id for r in mine.reports] == [out.report_id]
    assert theirs.reports == []
    assert isinstance(opened, GetReportOk)
    assert isinstance(foreign, ToolError)
    assert foreign.message_class is MessageClass.REPORT_NOT_FOUND


def test_views_refuse_an_unknown_user(reports: ReportRepository) -> None:
    with pytest.raises(PermanentError) as err:
        views_for(reports).reports("stranger", ListReportsInput())

    assert err.value.message_class is MessageClass.NO_ROLE


def test_the_saved_time_round_trips_as_utc(reports: ReportRepository) -> None:
    ctx = ctx_for(MANAGER_A, reports)
    before = datetime.now(UTC)
    saved(ctx)

    [item] = list_my_reports(ListReportsInput(), ctx).reports

    assert item.created_at.tzinfo is not None
    assert before <= item.created_at <= datetime.now(UTC)


# --- through a whole turn --------------------------------------------------------------------


def call(name: str, args: dict, call_id: str) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])


def test_the_analyst_saves_a_report_in_a_turn(reports: ReportRepository) -> None:
    args = report_input().model_dump(mode="json")
    llm = FakeLLMFactory(
        analyst=[
            call("run_sql", {"sql": BRAND_SQL}, "c1"),
            call("save_report", args, "c2"),
            AIMessage(content="Saved as “Calvin Klein 2025 revenue”."),
        ]
    )
    svc, graph = chat_service(fake_ports(CEO, MANAGER_A, MANAGER_B, llm=llm, reports=reports))

    [reply], config = ask(svc, "manager-a", "save this as a report")

    assert isinstance(reply, AgentReply) and reply.message_class is None
    [item] = list_my_reports(ListReportsInput(), ctx_for(MANAGER_A, reports)).reports
    assert item.title == "Calvin Klein 2025 revenue"
    assert graph.get_state(config).values["saved_report_ids"] == [item.report_id]


def test_the_analyst_lists_and_opens_reports(reports: ReportRepository) -> None:
    out = saved(ctx_for(MANAGER_A, reports))
    llm = FakeLLMFactory(
        analyst=[
            call("list_my_reports", {}, "c1"),
            call("get_my_report", {"report_id": str(out.report_id)}, "c2"),
            AIMessage(content="Here it is."),
        ]
    )
    svc, _ = chat_service(fake_ports(CEO, MANAGER_A, MANAGER_B, llm=llm, reports=reports))

    [reply], _ = ask(svc, "manager-a", "open my Calvin Klein report")

    assert isinstance(reply, AgentReply) and reply.markdown == "Here it is."
    tool_results = [m.content for m in llm.analyst.seen[-1] if m.type == "tool"]
    assert "data as of" in tool_results[-1]
