"""The Deletion Plan: plan_deletion and execute_deletion (LLD 05), run against both
``ReportRepository`` adapters. The confirmation flow through the graph is in
``tests/test_deletion_flow.py``."""

import json
import sqlite3
from uuid import UUID, uuid4

import pytest

from agent.adapters.reports_sqlite import SqliteReportRepository
from agent.contracts.tools import (
    ExecuteDeletionInput,
    ExecuteDeletionOk,
    ListReportsInput,
    PlanDeletionInput,
    PlanDeletionNothing,
    PlanDeletionOk,
    SaveReportOk,
    ToolError,
)
from agent.domain.reports import DeletionFilter, DeletionPlan
from agent.errors import MessageClass
from agent.ports.llm import ModelStep
from agent.ports.reports import ReportRepository
from agent.tools.analysis import list_my_reports, save_report
from agent.tools.branches import execute_deletion, plan_deletion
from agent.tools.context import TurnContext
from tests.fakes import FakeLLMFactory
from tests.test_reports import (
    CEO,
    MANAGER_A,
    ctx_for,
    entities,
    queried,
    report_input,
)

HERE = UUID("00000000-0000-0000-0000-00000000aaaa")
THERE = UUID("00000000-0000-0000-0000-00000000bbbb")


def save(
    reports: ReportRepository,
    title: str,
    *,
    mentions: tuple[str, ...] = (),
    markdown: str = "Revenue held up.",
    user=MANAGER_A,
    conversation_id: UUID = HERE,
) -> UUID:
    """Save one report whose extracted entities are ``mentions``."""
    llm = FakeLLMFactory(structured={ModelStep.ENTITY_EXTRACTION: entities(*mentions)})
    ctx = ctx_for(user, reports, llm=llm, conversation_id=conversation_id, turn_id=uuid4().hex)
    args = report_input(title=title, question="How did sales go?", markdown=markdown)
    out = save_report(args, ctx, queried(ctx))
    assert isinstance(out, SaveReportOk)
    return out.report_id


def ctx(reports: ReportRepository, user=MANAGER_A, conversation_id: UUID = HERE) -> TurnContext:
    return ctx_for(user, reports, conversation_id=conversation_id)


def planned(reports: ReportRepository, f: DeletionFilter, **kw) -> DeletionPlan:
    out = plan_deletion(PlanDeletionInput(filter=f), ctx(reports, **kw))
    assert isinstance(out, PlanDeletionOk), out
    return out.plan


def titles(reports: ReportRepository, user=MANAGER_A) -> list[str]:
    listed = list_my_reports(ListReportsInput(limit=50), ctx(reports, user))
    return sorted(r.title for r in listed.reports)


def confirm(
    reports: ReportRepository, plan: DeletionPlan, ids: list[UUID] | None = None, **kw
) -> ExecuteDeletionOk | ToolError:
    args = ExecuteDeletionInput(
        plan_id=plan.plan_id, confirmed_report_ids=ids or [i.report_id for i in plan.items]
    )
    return execute_deletion(args, ctx(reports, **kw), plan)


# --- plan_deletion ---------------------------------------------------------------------------


def test_this_conversation_is_an_exact_match(reports: ReportRepository) -> None:
    save(reports, "made here")
    save(reports, "made elsewhere", conversation_id=THERE)

    plan = planned(reports, DeletionFilter(this_conversation=True))

    assert [(i.index, i.title, i.reason) for i in plan.items] == [
        (1, "made here", "made in this conversation")
    ]
    assert plan.conversation_id == HERE
    assert plan.author_id == "manager-a"


@pytest.mark.parametrize("asked", ["Calvin Klein", "calvin KLEIN", "CK", "calvin-klein"])
def test_entities_match_across_case_and_aliases(reports: ReportRepository, asked: str) -> None:
    save(reports, "Q1 sales", mentions=("Calvin Klein",))
    save(reports, "Tommy returns", mentions=("Tommy Hilfiger",))

    plan = planned(reports, DeletionFilter(entities=[asked]))

    assert [(i.title, i.reason) for i in plan.items] == [("Q1 sales", f"mentions: {asked}")]


def test_full_text_is_the_fallback_when_no_entities_were_extracted(
    reports: ReportRepository,
) -> None:
    save(reports, "Q1 sales", markdown="Calvin Klein led in Q1.")  # extraction found nothing
    save(reports, "Q2 sales", markdown="Tommy Hilfiger led in Q2.")

    plan = planned(reports, DeletionFilter(entities=["calvin klein"]))

    assert [i.title for i in plan.items] == ["Q1 sales"]


def test_any_listed_entity_matches_and_the_reason_names_it(reports: ReportRepository) -> None:
    save(reports, "both", mentions=("Calvin Klein", "Tommy Hilfiger"))
    save(reports, "tommy only", mentions=("Tommy Hilfiger",))
    save(reports, "neither")

    plan = planned(reports, DeletionFilter(entities=["Calvin Klein", "Tommy Hilfiger"]))

    assert [(i.title, i.reason) for i in plan.items] == [
        ("both", "mentions: Calvin Klein, Tommy Hilfiger"),
        ("tommy only", "mentions: Tommy Hilfiger"),
    ]


def test_criteria_combine_with_and(reports: ReportRepository) -> None:
    save(reports, "churn here", mentions=("Calvin Klein",), markdown="Churn rose.")
    save(
        reports,
        "churn there",
        mentions=("Calvin Klein",),
        markdown="Churn rose.",
        conversation_id=THERE,
    )
    save(reports, "sales here", mentions=("Calvin Klein",))

    plan = planned(reports, DeletionFilter(this_conversation=True, entities=["CK"], topic="churn"))

    assert [(i.title, i.reason) for i in plan.items] == [
        ("churn here", 'made in this conversation; mentions: CK; about "churn"')
    ]


def test_another_authors_reports_are_never_matched(reports: ReportRepository) -> None:
    save(reports, "ceo report", mentions=("Calvin Klein",), user=CEO)

    out = plan_deletion(PlanDeletionInput(filter=DeletionFilter(all_reports=True)), ctx(reports))

    assert isinstance(out, PlanDeletionNothing)


def test_zero_matches_is_nothing_matched(reports: ReportRepository) -> None:
    save(reports, "Q1 sales", mentions=("Calvin Klein",))

    out = plan_deletion(PlanDeletionInput(filter=DeletionFilter(entities=["Nike"])), ctx(reports))

    assert isinstance(out, PlanDeletionNothing)
    assert out.message_class is MessageClass.NOTHING_MATCHED


def test_full_text_operators_in_a_topic_are_plain_words(reports: ReportRepository) -> None:
    save(reports, "Q1 sales", markdown="Revenue held up.")

    out = plan_deletion(
        PlanDeletionInput(filter=DeletionFilter(topic='revenue OR "x" NEAR(*)')), ctx(reports)
    )

    assert isinstance(out, PlanDeletionNothing)  # no "x", "near": every word must appear


# --- execute_deletion ------------------------------------------------------------------------


def test_partial_confirmation_deletes_only_the_confirmed(reports: ReportRepository) -> None:
    for title in ("one", "two", "three"):
        save(reports, title)
    plan = planned(reports, DeletionFilter(all_reports=True))
    one, two, three = plan.items

    out = confirm(reports, plan, [one.report_id, three.report_id])

    assert isinstance(out, ExecuteDeletionOk)
    assert [i.title for i in out.outcome.deleted] == ["one", "three"]
    assert [i.title for i in out.outcome.kept] == ["two"]
    assert out.outcome.already_gone == []
    assert out.outcome.audit_id is not None
    assert titles(reports) == ["two"]


def test_reports_saved_after_planning_are_never_deleted(reports: ReportRepository) -> None:
    save(reports, "old", mentions=("Calvin Klein",))
    plan = planned(reports, DeletionFilter(entities=["Calvin Klein"]))
    save(reports, "new", mentions=("Calvin Klein",))

    confirm(reports, plan)

    assert titles(reports) == ["new"]


def test_replaying_a_plan_deletes_nothing(reports: ReportRepository) -> None:
    save(reports, "one")
    plan = planned(reports, DeletionFilter(all_reports=True))
    first = confirm(reports, plan)

    again = confirm(reports, plan)

    assert isinstance(first, ExecuteDeletionOk) and isinstance(again, ExecuteDeletionOk)
    assert (first.replayed, again.replayed) == (False, True)
    assert again.outcome.audit_id == first.outcome.audit_id
    assert [i.title for i in again.outcome.deleted] == ["one"]


def test_a_replayed_plan_id_deletes_nothing_at_the_repository(reports: ReportRepository) -> None:
    keep = save(reports, "keep")
    gone = save(reports, "gone")
    plan_id, f = uuid4(), DeletionFilter(all_reports=True)
    reports.delete_with_audit("manager-a", plan_id, [gone], {gone: "gone"}, f)

    receipt = reports.delete_with_audit("manager-a", plan_id, [keep], {keep: "keep"}, f)

    assert receipt.replayed
    assert titles(reports) == ["keep"]


def test_a_report_deleted_in_the_meantime_is_already_gone(reports: ReportRepository) -> None:
    save(reports, "one")
    save(reports, "two")
    plan = planned(reports, DeletionFilter(all_reports=True))
    first = planned(reports, DeletionFilter(all_reports=True))
    confirm(reports, first, [first.items[0].report_id])  # e.g. from another channel

    out = confirm(reports, plan)

    assert isinstance(out, ExecuteDeletionOk)
    assert [i.title for i in out.outcome.already_gone] == ["one"]
    assert [i.title for i in out.outcome.deleted] == ["two"]


def test_nothing_left_to_delete_writes_no_audit(reports: ReportRepository) -> None:
    save(reports, "one")
    plan = planned(reports, DeletionFilter(all_reports=True))
    confirm(reports, planned(reports, DeletionFilter(all_reports=True)))

    out = confirm(reports, plan)

    assert isinstance(out, ExecuteDeletionOk)
    assert out.outcome.deleted == [] and out.outcome.audit_id is None


def test_ownership_is_checked_again_at_the_repository(reports: ReportRepository) -> None:
    theirs = save(reports, "ceo report", user=CEO)

    receipt = reports.delete_with_audit(
        "manager-a", uuid4(), [theirs], {theirs: "ceo report"}, DeletionFilter(all_reports=True)
    )

    assert receipt.already_gone == [theirs] and receipt.audit is None
    assert titles(reports, CEO) == ["ceo report"]


def expired(out: ExecuteDeletionOk | ToolError) -> bool:
    return isinstance(out, ToolError) and out.message_class is MessageClass.PLAN_EXPIRED


def test_a_plan_that_is_not_the_open_one_has_expired(reports: ReportRepository) -> None:
    save(reports, "one")
    plan = planned(reports, DeletionFilter(all_reports=True))
    newer = planned(reports, DeletionFilter(all_reports=True))
    ids = [i.report_id for i in plan.items]

    no_plan = execute_deletion(
        ExecuteDeletionInput(plan_id=plan.plan_id, confirmed_report_ids=ids), ctx(reports), None
    )
    replaced = execute_deletion(
        ExecuteDeletionInput(plan_id=plan.plan_id, confirmed_report_ids=ids), ctx(reports), newer
    )

    assert expired(no_plan) and expired(replaced)
    assert titles(reports) == ["one"]


def test_a_plan_is_bound_to_its_user_and_conversation(reports: ReportRepository) -> None:
    save(reports, "one")
    plan = planned(reports, DeletionFilter(all_reports=True))

    assert expired(confirm(reports, plan, user=CEO))
    assert expired(confirm(reports, plan, conversation_id=THERE))
    assert expired(confirm(reports, plan, [uuid4()]))  # not in the plan
    assert titles(reports) == ["one"]


def test_the_audit_row_holds_metadata_only(tmp_path, reports: ReportRepository) -> None:
    if not isinstance(reports, SqliteReportRepository):
        pytest.skip("reads the stored row")
    save(reports, "Q1 sales", markdown="Secret figure 46,399.46 for Calvin Klein.")
    plan = planned(reports, DeletionFilter(entities=["Calvin Klein"]))
    confirm(reports, plan)

    db = sqlite3.connect(tmp_path / "app.db")
    db.row_factory = sqlite3.Row
    [row] = db.execute("SELECT * FROM deletion_audit").fetchall()

    stored = json.dumps(dict(row))
    assert "46,399.46" not in stored and "Revenue" not in stored
    assert json.loads(row["titles"]) == ["Q1 sales"]
    assert json.loads(row["report_ids"]) == [str(plan.items[0].report_id)]
    assert row["author_id"] == "manager-a"
