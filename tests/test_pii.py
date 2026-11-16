"""The output PII filter (HLD 03 § PII, LLD 06 § PiiFilter): each detector, the redaction
format, no false hits on the figures an answer is made of, and every ``OutboundMessage`` kind
through the single exit ``Outbound.emit``."""

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage

from agent.adapters.pii_regex import RegexPiiFilter
from agent.contracts.channel import (
    AgentReply,
    ConfirmationItem,
    ConfirmationRequest,
    OutboundMessage,
    ProgressEvent,
)
from agent.core.outbound import Outbound
from agent.domain.identity import Role, UserContext
from agent.ports.pii import PiiType
from tests.fakes import (
    CollectingChannel,
    FakeLLMFactory,
    FakeTracer,
    ask,
    chat_service,
    fake_ports,
)

PII = RegexPiiFilter()
CEO = UserContext(user_id="ceo", role=Role.EXECUTIVE, access_version=1)


@pytest.mark.parametrize(
    ("text", "kind", "redacted"),
    [
        (
            "Write to jane.doe+promo@example.co.uk today",
            PiiType.EMAIL,
            "Write to [REDACTED:email] today",
        ),
        ("Call (415) 555-0132 now", PiiType.PHONE, "Call [REDACTED:phone] now"),
        ("Call 415-555-0132", PiiType.PHONE, "Call [REDACTED:phone]"),
        ("Call +44 20 7946 0958", PiiType.PHONE, "Call [REDACTED:phone]"),
        (
            "Lives at 1019 Mitchell Street, apt 2",
            PiiType.STREET_ADDRESS,
            "Lives at [REDACTED:street_address], apt 2",
        ),
        ("Ship to 77 North Oak Ave.", PiiType.STREET_ADDRESS, "Ship to [REDACTED:street_address]"),
        ("ZIP 94107-1234", PiiType.POSTAL_CODE, "ZIP [REDACTED:postal_code]"),
        ("postal code 94107", PiiType.POSTAL_CODE, "postal code [REDACTED:postal_code]"),
        (
            "San Francisco, CA 94107",
            PiiType.POSTAL_CODE,
            "San Francisco, CA [REDACTED:postal_code]",
        ),
        ("Boise, ID 83702", PiiType.POSTAL_CODE, "Boise, ID [REDACTED:postal_code]"),
        ("St. Louis,MO 63101", PiiType.POSTAL_CODE, "St. Louis,MO [REDACTED:postal_code]"),
        ("Lakewood, NJ 8701", PiiType.POSTAL_CODE, "Lakewood, NJ [REDACTED:postal_code]"),
        ("Boise, ID 48213-1234", PiiType.POSTAL_CODE, "Boise, ID [REDACTED:postal_code]"),
        ("London SW1A 1AA", PiiType.POSTAL_CODE, "London [REDACTED:postal_code]"),
        ("Toronto M5V 3L9", PiiType.POSTAL_CODE, "Toronto [REDACTED:postal_code]"),
        ("at 37.774929, -122.419418", PiiType.COORDINATES, "at [REDACTED:coordinates]"),
        ("latitude: 37.7749", PiiType.COORDINATES, "[REDACTED:coordinates]"),
        ("POINT(-122.4194 37.7749)", PiiType.COORDINATES, "[REDACTED:coordinates]"),
    ],
)
def test_each_detector_redacts_with_its_type(text: str, kind: PiiType, redacted: str) -> None:
    scan = PII.scan(text)

    assert scan.text == redacted
    assert scan.hits == [kind]
    assert scan.detectors == "regex"


@pytest.mark.parametrize(
    "text",
    [
        "Revenue in 2025 was $2,358,149.49, up 12.5% on 2024.",
        "Calvin Klein 2025: $46,399.46 over 1,204 orders (avg basket 1.43 items).",
        "Top Customers: user_id 48213 spent $1,020.50; user_id 99871 spent $987.10.",
        "Data as of 2026-11-15; orders between 2025-01-01 and 2025-12-31.",
        "Return rate 0.1234 in Q3, ratio 12.3456, 7.8901.",
        "SKU EBD58B8A3F1D72F4206201DA62FB1204 in Jeans, distribution center 3.",
        "California 12,345 Customers; Texas 9,876.",
        "Customer ID 48213 spent $1,020.50.",
        "Top Customers: ID 48213, ID 99871, ID 10442.",
        "Customer 1: $1,020.50, ID 48213; Customer 2: 14 orders, ID 99871.",
        "ID 48213 returned 3 items (OK 12345 of them were Jeans, OR 23456 in Tops).",
        "Calvin Klein, ID 48213 spent $1,020.50; Levi's, ID 8370 spent $987.10.",
    ],
)
def test_analysis_figures_are_not_hits(text: str) -> None:
    scan = PII.scan(text)

    assert scan.text == text
    assert scan.hits == []


def test_every_hit_is_counted_and_types_only_are_returned() -> None:
    scan = PII.scan("a@b.com and c@d.org, call 415-555-0132")

    assert scan.hits == [PiiType.EMAIL, PiiType.EMAIL, PiiType.PHONE]
    assert "a@b.com" not in str(scan.hits)


def test_templated_text_passes_the_same_regex_detectors() -> None:
    assert PII.scan("Querying x@y.com…", templated=True).text == "Querying [REDACTED:email]…"


# Every OutboundMessage kind through the single exit

PLANTED = "jane.doe@example.com"
REDACTED = "[REDACTED:email]"


def _base() -> dict[str, Any]:
    return {"conversation_id": uuid4(), "trace_id": "t", "sent_at": datetime.now(UTC)}


MESSAGES: list[OutboundMessage] = [
    ProgressEvent(**_base(), template="querying", text=f"Querying {PLANTED}"),
    ConfirmationRequest(
        **_base(),
        request_id=uuid4(),
        text=f"Delete these? {PLANTED}",
        items=[ConfirmationItem(index=1, label=f"Report for {PLANTED}", reason=f"by {PLANTED}")],
        total=1,
        require_count=False,
    ),
    AgentReply(**_base(), markdown=f"Top Customer: {PLANTED}"),
]


@pytest.mark.parametrize("message", MESSAGES, ids=lambda m: m.kind)
def test_a_planted_email_is_redacted_in_every_message_kind(message: OutboundMessage) -> None:
    channel = CollectingChannel()
    tracer = FakeTracer()

    sent = Outbound(channel, RegexPiiFilter(), tracer).emit(message)

    assert channel.delivered == [sent]
    assert PLANTED not in sent.model_dump_json()
    assert REDACTED in sent.model_dump_json()
    [span] = [s for s in tracer.spans if s.name == "outbound"]
    assert span.attributes["pii.hits"] == ["email"]


class _BrokenFilter:
    def scan(self, text: str, *, templated: bool = False) -> object:
        raise RuntimeError("detector crashed")


def test_nothing_is_sent_when_the_filter_fails() -> None:
    channel = CollectingChannel()

    Outbound(channel, _BrokenFilter(), FakeTracer()).emit(MESSAGES[2])  # pyright: ignore[reportArgumentType]

    assert channel.delivered == []


def test_the_checkpoint_holds_the_filtered_reply() -> None:
    llm = FakeLLMFactory(analyst=[AIMessage(content=f"Your top Customer is {PLANTED}.")])
    service, graph = chat_service(fake_ports(CEO, llm=llm))

    [reply], config = ask(service, "ceo", "Who is my top Customer?")

    assert isinstance(reply, AgentReply)
    assert reply.markdown == f"Your top Customer is {REDACTED}."
    stored = graph.get_state(config).values["messages"][-1].text
    assert stored == f"Your top Customer is {REDACTED}."
