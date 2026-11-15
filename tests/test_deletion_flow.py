"""The confirmation flow through the whole graph (HLD 04, LLD 03 § Pause and resume).

Turns go through ``ChatService.handle`` like the CLI, with a fake model and the in-memory
repository (the repository itself is tested against both adapters in test_deletion.py).
"""

from datetime import UTC, datetime
from uuid import UUID

from langchain_core.messages import AIMessage

from agent.contracts.channel import AgentReply, ConfirmationRequest, ConfirmationResponse
from agent.contracts.router import Action, RouterDecision, Verdict
from agent.core.chat import ChatService
from agent.domain.reports import DeletionFilter
from agent.errors import MessageClass
from agent.ports.llm import ModelStep
from tests.fakes import (
    CollectingChannel,
    FakeLLMFactory,
    InMemoryReportRepository,
    analysis_decision,
    ask,
    chat_service,
    fake_ports,
)
from tests.test_deletion import HERE, save, titles
from tests.test_reports import CEO, MANAGER_A, MANAGER_B

DELETE = RouterDecision(verdict=Verdict.ALLOWED, action=Action.DELETE_REPORTS)
CK = DeletionFilter(entities=["Calvin Klein"])


def service(
    reports: InMemoryReportRepository,
    decisions: list[RouterDecision],
    *,
    f: DeletionFilter = CK,
    analyst: list[str] = (),  # type: ignore[assignment]
) -> tuple[ChatService, FakeLLMFactory]:
    llm = FakeLLMFactory(
        analyst=[AIMessage(content=a) for a in analyst],
        decision=decisions,
        structured={ModelStep.DELETION_FILTER: f},
    )
    svc, _ = chat_service(fake_ports(CEO, MANAGER_A, MANAGER_B, llm=llm, reports=reports))
    return svc, llm


def three_ck_reports() -> InMemoryReportRepository:
    reports = InMemoryReportRepository()
    for title in ("Q1 sales", "Churn drivers", "Top Customers"):
        save(reports, title, mentions=("Calvin Klein",))
    save(reports, "Tommy returns", mentions=("Tommy Hilfiger",))
    return reports


def say(svc: ChatService, text: str, user: str = "manager-a"):
    delivered, _ = ask(svc, user, text, HERE)
    return delivered


def test_a_delete_request_shows_the_plan_and_deletes_nothing() -> None:
    reports = three_ck_reports()
    svc, _ = service(reports, [DELETE])

    [prompt] = say(svc, "delete all reports mentioning Calvin Klein")

    assert isinstance(prompt, ConfirmationRequest)
    assert [(i.index, i.label.split(" (")[0]) for i in prompt.items] == [
        (1, "Q1 sales"),
        (2, "Churn drivers"),
        (3, "Top Customers"),
    ]
    assert prompt.items[0].reason == "mentions: Calvin Klein"
    assert (prompt.total, prompt.require_count) == (3, False)
    assert "yes except" in prompt.text
    assert len(titles(reports)) == 4


def test_yes_except_deletes_the_rest() -> None:
    reports = three_ck_reports()
    svc, _ = service(reports, [DELETE])
    say(svc, "delete all reports mentioning Calvin Klein")

    [reply] = say(svc, "yes except 2")

    assert isinstance(reply, AgentReply)
    assert "Deleted 2 reports" in reply.markdown
    assert 'Kept "Churn drivers"' in reply.markdown
    assert titles(reports) == ["Churn drivers", "Tommy returns"]


def test_no_deletes_nothing_and_a_later_yes_has_expired() -> None:
    reports = three_ck_reports()
    svc, _ = service(reports, [DELETE])
    say(svc, "delete all reports mentioning Calvin Klein")

    [declined] = say(svc, "no")
    [later] = say(svc, "yes")

    assert isinstance(declined, AgentReply) and "Nothing was deleted" in declined.markdown
    assert isinstance(later, AgentReply) and later.message_class is MessageClass.PLAN_EXPIRED
    assert len(titles(reports)) == 4


def test_a_new_question_cancels_the_plan_and_is_answered() -> None:
    reports = three_ck_reports()
    svc, llm = service(
        reports, [DELETE, analysis_decision("revenue in 2025")], analyst=["Revenue was $2."]
    )
    say(svc, "delete all reports mentioning Calvin Klein")

    [answer] = say(svc, "actually, what was revenue in 2025?")
    [later] = say(svc, "yes")

    assert isinstance(answer, AgentReply) and answer.markdown == "Revenue was $2."
    assert llm.steps.count(ModelStep.ROUTER) == 2  # the reply went through the input guard
    assert isinstance(later, AgentReply) and later.message_class is MessageClass.PLAN_EXPIRED
    assert len(titles(reports)) == 4


def test_an_ambiguous_reply_never_deletes() -> None:
    reports = three_ck_reports()
    svc, _ = service(reports, [DELETE, analysis_decision("sure")], analyst=["Okay."])
    say(svc, "delete all reports mentioning Calvin Klein")

    say(svc, "sure")

    assert len(titles(reports)) == 4


def test_a_large_plan_asks_once_for_the_count() -> None:
    reports = InMemoryReportRepository()
    for n in range(21):
        save(reports, f"report {n}")
    svc, _ = service(reports, [DELETE], f=DeletionFilter(all_reports=True))
    [first] = say(svc, "delete all my reports")

    [again] = say(svc, "yes")
    [reply] = say(svc, "yes, delete all 21")

    assert isinstance(first, ConfirmationRequest)
    assert (len(first.items), first.total, first.require_count) == (20, 21, True)
    assert isinstance(again, ConfirmationRequest) and "delete all 21" in again.text
    assert isinstance(reply, AgentReply) and "Deleted 21 reports" in reply.markdown
    assert titles(reports) == []


def test_a_second_bare_yes_on_a_large_plan_cancels() -> None:
    reports = InMemoryReportRepository()
    for n in range(21):
        save(reports, f"report {n}")
    svc, _ = service(reports, [DELETE], f=DeletionFilter(all_reports=True))
    say(svc, "delete all my reports")
    say(svc, "yes")

    [reply] = say(svc, "yes")

    assert isinstance(reply, AgentReply) and reply.message_class is MessageClass.PLAN_EXPIRED
    assert len(titles(reports)) == 21


def test_zero_matches_answers_without_a_prompt() -> None:
    reports = three_ck_reports()
    svc, _ = service(reports, [DELETE], f=DeletionFilter(entities=["Nike"]))

    [reply] = say(svc, "delete my Nike reports")

    assert isinstance(reply, AgentReply)
    assert reply.message_class is MessageClass.NOTHING_MATCHED


def test_the_deferred_request_runs_after_the_answer() -> None:
    reports = three_ck_reports()
    deferred = RouterDecision(
        verdict=Verdict.ALLOWED,
        action=Action.DELETE_REPORTS,
        deferred_request="what was revenue in 2025?",
    )
    svc, llm = service(reports, [deferred], analyst=["Revenue was $2."])
    [prompt] = say(svc, "delete my Calvin Klein reports and tell me 2025 revenue")

    deleted, answered = say(svc, "yes")

    assert isinstance(prompt, ConfirmationRequest) and "revenue in 2025" in prompt.text
    assert ModelStep.ANALYST not in llm.steps[: llm.steps.index(ModelStep.DELETION_FILTER)]
    assert isinstance(deleted, AgentReply) and "Deleted 3 reports" in deleted.markdown
    assert isinstance(answered, AgentReply) and answered.markdown == "Revenue was $2."


def test_the_plan_is_built_once_across_the_pause() -> None:
    reports = three_ck_reports()
    svc, llm = service(reports, [DELETE])
    say(svc, "delete all reports mentioning Calvin Klein")
    save(reports, "saved meanwhile", mentions=("Calvin Klein",))

    say(svc, "yes")

    assert llm.steps.count(ModelStep.DELETION_FILTER) == 1
    assert titles(reports) == ["Tommy returns", "saved meanwhile"]


def press(svc: ChatService, prompt: ConfirmationRequest, user: str, **kw):
    channel = CollectingChannel()
    fields = {
        "user_id": user,
        "conversation_id": HERE,
        "request_id": prompt.request_id,
        "approved": True,
    } | kw
    svc.handle(ConfirmationResponse.model_validate(fields), channel)
    return channel.delivered


def test_buttons_confirm_like_text() -> None:
    reports = three_ck_reports()
    svc, _ = service(reports, [DELETE])
    [prompt] = say(svc, "delete all reports mentioning Calvin Klein")
    assert isinstance(prompt, ConfirmationRequest)

    [reply] = press(svc, prompt, "manager-a", selected_indices=[1, 3])

    assert isinstance(reply, AgentReply) and "Deleted 2 reports" in reply.markdown
    assert titles(reports) == ["Churn drivers", "Tommy returns"]


def test_a_button_from_another_user_deletes_nothing() -> None:
    reports = three_ck_reports()
    svc, _ = service(reports, [DELETE])
    [prompt] = say(svc, "delete all reports mentioning Calvin Klein")
    assert isinstance(prompt, ConfirmationRequest)

    [reply] = press(svc, prompt, "manager-b")

    assert isinstance(reply, AgentReply) and reply.message_class is MessageClass.PLAN_EXPIRED
    assert len(titles(reports)) == 4


def test_a_button_without_an_open_plan_has_expired() -> None:
    reports = three_ck_reports()
    svc, _ = service(reports, [DELETE])
    fake = ConfirmationRequest(
        conversation_id=HERE,
        trace_id="t",
        sent_at=datetime.now(UTC),
        request_id=UUID(int=7),
        text="",
        items=[],
        total=1,
        require_count=False,
    )

    [reply] = press(svc, fake, "manager-a")

    assert isinstance(reply, AgentReply) and reply.message_class is MessageClass.PLAN_EXPIRED


def test_a_one_report_plan_offers_only_yes_or_no() -> None:
    reports = InMemoryReportRepository()
    save(reports, "Q1 sales", mentions=("Calvin Klein",))
    svc, _ = service(reports, [DELETE])

    [prompt] = say(svc, "delete all reports mentioning Calvin Klein")

    assert isinstance(prompt, ConfirmationRequest)
    assert "except" not in prompt.text
    assert "yes or no" in prompt.text


def test_the_except_example_names_an_item_in_the_plan() -> None:
    reports = InMemoryReportRepository()
    for title in ("Q1 sales", "Q2 sales"):
        save(reports, title, mentions=("Calvin Klein",))
    svc, _ = service(reports, [DELETE])

    [prompt] = say(svc, "delete all reports mentioning Calvin Klein")

    assert isinstance(prompt, ConfirmationRequest)
    assert '"yes except 2"' in prompt.text
