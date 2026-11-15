"""The CLI loop and ChatService: every turn ends in a delivered message, and a failed turn
never ends the session (HLD 06, LLD 07)."""

from collections.abc import Callable

from langchain_core.messages import AIMessage

from agent.cli import run_chat
from agent.config import Limits
from agent.contracts.channel import AgentReply, InboundMessage
from agent.core.chat import ChatService
from agent.core.views import Views
from agent.domain.identity import Role, UserContext
from agent.errors import MessageClass
from agent.ports.channel import ChannelAdapter
from tests.fakes import (
    CollectingChannel,
    FakeLLMFactory,
    InMemoryReportRepository,
    ask,
    chat_service,
    fake_ports,
)

CEO = UserContext(user_id="ceo", role=Role.EXECUTIVE, access_version=1)


class ExplodingOnce(ChatService):
    """A ChatService whose first turn raises, as if its own catch-all had a bug."""

    def __init__(self) -> None:
        self.texts: list[str] = []

    def handle(self, message: InboundMessage, channel: ChannelAdapter) -> None:  # type: ignore[override]
        self.texts.append(message.text)
        if len(self.texts) == 1:
            raise RuntimeError("boom")


def typed(*lines: str) -> Callable[[str], str]:
    """Stands in for ``input``: returns ``lines`` in order, then EOF (Ctrl-D)."""
    remaining = iter(lines)

    def read(_prompt: str) -> str:
        try:
            return next(remaining)
        except StopIteration:
            raise EOFError from None

    return read


def test_a_failing_turn_does_not_end_the_loop() -> None:
    service = ExplodingOnce()

    views = Views(fake_ports(CEO), Limits(_env_file=None))  # pyright: ignore[reportCallIssue]
    run_chat(service, views, "ceo", CollectingChannel(), typed("first question", "second question"))

    assert service.texts == ["first question", "second question"]


def test_a_model_outage_becomes_the_unclassified_message_with_a_reference() -> None:
    ports = fake_ports(CEO, llm=FakeLLMFactory(fail=RuntimeError("provider exploded")))
    service, _ = chat_service(ports)

    [reply], _ = ask(service, "ceo", "What was total revenue in 2025?")

    assert isinstance(reply, AgentReply)
    assert reply.message_class is MessageClass.UNCLASSIFIED
    assert reply.trace_id in reply.markdown
    assert "provider exploded" not in reply.markdown


def test_the_next_turn_works_after_a_failed_one() -> None:
    llm = FakeLLMFactory(analyst=[AIMessage(content="Revenue was $2.")])
    service, _ = chat_service(fake_ports(CEO, llm=llm))
    llm.fail = RuntimeError("down")
    ask(service, "ceo", "first")
    llm.fail = None

    [reply], _ = ask(service, "ceo", "second")

    assert isinstance(reply, AgentReply)
    assert reply.markdown == "Revenue was $2."


def test_an_unknown_user_gets_no_role_and_no_model_call() -> None:
    llm = FakeLLMFactory(fail=AssertionError("no model call for an unknown User"))
    service, _ = chat_service(fake_ports(CEO, llm=llm))

    [reply], _ = ask(service, "stranger", "What was total revenue in 2025?")

    assert isinstance(reply, AgentReply)
    assert reply.message_class is MessageClass.NO_ROLE


def test_a_user_with_no_role_gets_no_role() -> None:
    nobody = UserContext(user_id="nobody", role=None, access_version=1)
    service, _ = chat_service(fake_ports(nobody))

    [reply], _ = ask(service, "nobody", "What was total revenue in 2025?")

    assert isinstance(reply, AgentReply)
    assert reply.message_class is MessageClass.NO_ROLE


def test_slash_reports_lists_the_users_reports_without_a_model_call() -> None:
    from tests.test_reports import MANAGER_A, ctx_for, saved

    reports = InMemoryReportRepository()
    saved(ctx_for(MANAGER_A, reports))
    llm = FakeLLMFactory(fail=AssertionError("/reports needs no model"))
    ports = fake_ports(CEO, MANAGER_A, llm=llm, reports=reports)
    service, _ = chat_service(ports)
    printed: list[str] = []

    run_chat(
        service,
        Views(ports, Limits(_env_file=None)),  # pyright: ignore[reportCallIssue]
        "manager-a",
        CollectingChannel(),
        typed("/reports"),
        printed.append,
    )

    [text] = printed
    assert "1. Calvin Klein 2025 revenue" in text
    assert llm.steps == []


def test_slash_reports_with_none_says_so() -> None:
    ports = fake_ports(CEO)
    service, _ = chat_service(ports)
    printed: list[str] = []

    run_chat(
        service,
        Views(ports, Limits(_env_file=None)),  # pyright: ignore[reportCallIssue]
        "ceo",
        CollectingChannel(),
        typed("/reports"),
        printed.append,
    )

    assert printed == ["You have no saved reports yet."]
