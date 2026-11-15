"""The confirmation grammar (LLD 04 § Confirmation grammar): one row of the table per test.

Pure code: an ambiguous reply must never become a deletion.
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from agent.contracts.channel import ConfirmationResponse
from agent.core.confirmation import (
    ConfirmAll,
    ConfirmExcept,
    Decline,
    NeedsCount,
    NotAnAnswer,
    parse_reply,
)
from agent.domain.reports import DeletionFilter, DeletionPlan, DeletionPlanItem


def plan(size: int) -> DeletionPlan:
    now = datetime(2026, 11, 15, tzinfo=UTC)
    return DeletionPlan(
        plan_id=uuid4(),
        author_id="manager-a",
        conversation_id=uuid4(),
        created_at=now,
        filter=DeletionFilter(all_reports=True),
        items=[
            DeletionPlanItem(
                index=i, report_id=uuid4(), title=f"r{i}", created_at=now, reason="all"
            )
            for i in range(1, size + 1)
        ],
    )


SMALL = plan(3)
LARGE = plan(37)


@pytest.mark.parametrize("reply", ["yes", "YES", "  Yes  ", "yes."])
def test_yes_confirms_a_small_plan(reply: str) -> None:
    assert parse_reply(reply, SMALL) == ConfirmAll()


def test_bare_yes_on_a_large_plan_asks_for_the_count() -> None:
    assert parse_reply("yes", LARGE) == NeedsCount()


@pytest.mark.parametrize(
    "reply", ["yes, delete all 37", "Yes delete all 37", "yes, delete all 37."]
)
def test_yes_with_the_exact_count_confirms_a_large_plan(reply: str) -> None:
    assert parse_reply(reply, LARGE) == ConfirmAll()


@pytest.mark.parametrize("reply", ["yes, delete all 36", "yes, delete all 38", "yes, delete all"])
def test_a_wrong_count_is_not_an_answer(reply: str) -> None:
    assert parse_reply(reply, LARGE) == NotAnAnswer()


@pytest.mark.parametrize(
    "reply", ["yes except 2,3", "yes except 2, 3", "Yes, except 3 2", "yes except 2 and 3"]
)
def test_yes_except_keeps_the_listed_items(reply: str) -> None:
    assert parse_reply(reply, SMALL) == ConfirmExcept(excluded=[2, 3])


def test_yes_except_works_on_a_large_plan() -> None:
    assert parse_reply("yes except 5", LARGE) == ConfirmExcept(excluded=[5])


@pytest.mark.parametrize(
    "reply", ["yes except 4", "yes except 0", "yes except 1,2,3", "yes except"]
)
def test_yes_except_outside_the_plan_or_excluding_everything_is_not_an_answer(reply: str) -> None:
    assert parse_reply(reply, SMALL) == NotAnAnswer()


@pytest.mark.parametrize("reply", ["no", "NO", " no "])
def test_no_declines(reply: str) -> None:
    assert parse_reply(reply, SMALL) == Decline()


@pytest.mark.parametrize(
    "reply",
    ["sure", "y", "yes please", "ok", "yeah", "no wait, yes", "what was revenue in 2025?", ""],
)
def test_anything_else_is_not_an_answer(reply: str) -> None:
    assert parse_reply(reply, SMALL) == NotAnAnswer()


def button(
    p: DeletionPlan, *, approved: bool, selected: list[int] | None = None, **kw
) -> ConfirmationResponse:
    fields = {
        "user_id": p.author_id,
        "conversation_id": p.conversation_id,
        "request_id": p.plan_id,
        "approved": approved,
        "selected_indices": selected,
    } | kw
    return ConfirmationResponse.model_validate(fields)


def test_buttons_reach_the_same_decisions() -> None:
    assert parse_reply(button(SMALL, approved=True), SMALL) == ConfirmAll()
    assert parse_reply(button(SMALL, approved=False), SMALL) == Decline()
    assert parse_reply(button(SMALL, approved=True, selected=[1]), SMALL) == ConfirmExcept(
        excluded=[2, 3]
    )
    assert parse_reply(button(LARGE, approved=True), LARGE) == NeedsCount()


def test_a_button_for_another_request_or_user_is_not_an_answer() -> None:
    assert parse_reply(button(SMALL, approved=True, request_id=uuid4()), SMALL) == NotAnAnswer()
    assert parse_reply(button(SMALL, approved=True, user_id="manager-b"), SMALL) == NotAnAnswer()
    assert parse_reply(button(SMALL, approved=True, selected=[]), SMALL) == NotAnAnswer()
    assert parse_reply(button(SMALL, approved=True, selected=[9]), SMALL) == NotAnAnswer()


@pytest.mark.parametrize(
    "reply",
    [
        "yes except ２",  # fullwidth digit
        "yes except ٢",  # Arabic-Indic digit
        "yes\nexcept 2",
        "yes except 1,1",
        "yes except 02",
        "yes\n",
    ],
)
def test_only_plain_ascii_answers_count(reply: str) -> None:
    decision = parse_reply(reply, SMALL)
    assert decision == NotAnAnswer() or (reply == "yes\n" and decision == ConfirmAll())


@pytest.mark.parametrize("reply", ["yes, delete all ３７", "yes, delete all 037"])
def test_the_count_is_plain_digits(reply: str) -> None:
    assert parse_reply(reply, LARGE) == NotAnAnswer()


def test_selecting_every_box_on_a_large_plan_still_needs_the_count() -> None:
    every = list(range(1, LARGE.total + 1))
    assert parse_reply(button(LARGE, approved=True, selected=every), LARGE) == NeedsCount()
