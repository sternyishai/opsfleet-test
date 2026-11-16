"""Every ``Views`` result is a way out (CODING_STANDARDS §2, LLD 07): no stored text reaches a
channel without the PII filter.

Slices 6, 7b and 8 each had a review find text leaving unfiltered (exception text in the
scorecard, Persona rejection reasons, the gate's ``detail`` in the browser); no test had
driven those paths. Here every store holds an email in every free-text field, every public
``Views`` method is called, and the email must not appear anywhere in what comes back. A new
view fails ``test_every_view_is_swept`` until it is added to ``SWEEP``.
"""

import inspect
from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import BaseModel

import agent.core.views as views_module
from agent.config import Limits
from agent.contracts.tools import ListReportsInput
from agent.core.views import Views
from agent.domain.identity import BrandScope, Permission, Role, UserContext, UserId
from agent.domain.persona import (
    GateCheck,
    GateCheckResult,
    Persona,
    PersonaGateResult,
    PersonaPath,
    PersonaVersion,
    Tone,
    TonePreset,
    Vocabulary,
)
from agent.domain.profile import ProfileNote, ProfileValue, Source, UserProfile
from agent.domain.reports import ReportStructured, SavedReport
from agent.domain.traces import SpanRecord
from agent.errors import MessageClass
from tests.fakes import FakeLLMFactory, InMemoryReportRepository, fake_ports

EMAIL = "jane.doe@example.com"
"""Planted in every free-text field; the filter must take it out on the way."""

CEO = UserContext(
    user_id="ceo",
    role=Role.EXECUTIVE,
    permissions=frozenset({Permission.PERSONA_EDITOR}),
    access_version=1,
)

KEEP = {"user_id", "author_id", "created_by", "actor_id", "trace_id", "span_id", "parent_id",
        "brands", "user.id", "turn_id", "name", "changed_fields"}  # fmt: skip
"""Identifiers and names the code sets (a span's name, a Persona's changed field names), not
text a User, the model or the warehouse wrote. Poisoning them would only make a lookup miss."""

NOW = datetime(2026, 11, 16, tzinfo=UTC)


def poisoned(value: Any, key: str | None = None) -> Any:
    """``value`` with the email added to every free-text string, at any depth."""
    if key in KEEP or isinstance(value, Enum):
        return value
    if isinstance(value, str):
        return f"{value} {EMAIL}"
    if isinstance(value, BaseModel):
        fields = {k: poisoned(getattr(value, k), k) for k in type(value).model_fields}
        return type(value).model_validate(fields)
    if isinstance(value, dict):
        return {k: poisoned(v, k if isinstance(k, str) else None) for k, v in value.items()}
    if isinstance(value, list):
        return [poisoned(v, key) for v in value]
    return value


PERSONA = poisoned(
    PersonaVersion(
        version=2,
        persona=Persona(
            tone=Tone(preset=TonePreset.WARM, notes="Encouraging."),
            report_sections=["Summary", "Action items"],
            emphasis="Revenue.",
            vocabulary=Vocabulary(prefer={"customers": "shoppers"}, avoid=["synergy"]),
            examples=["An example report."],
            messages={MessageClass.OUT_OF_SCOPE: "Not your Brand."},
        ),
        created_by="ceo",
        created_at=NOW,
        path=PersonaPath.DIRECT_EDIT,
        changed_fields=["tone"],
        is_active=True,
    )
)

REPORT = poisoned(
    SavedReport(
        report_id=uuid4(),
        author_id="ceo",
        conversation_id=uuid4(),
        turn_id="t1",
        created_at=NOW,
        title="Q1 revenue",
        question="What was Q1 revenue?",
        sql="SELECT 1",
        markdown="## Summary\nRevenue grew.",
        structured=ReportStructured(
            title="Q1 revenue", summary="Revenue grew.", insights=["Up 5%."],
            action_items=["Push Q2."], period="Q1 2026",
        ),
        scope=BrandScope(all_brands=True),
        data_as_of=date(2026, 11, 16),
    )
)

PROFILE = poisoned(
    UserProfile(
        user_id="ceo",
        language=ProfileValue[str](value="English", source=Source.EXPLICIT, confirmed_at=NOW),
        notes=[ProfileNote(note_id=uuid4(), text="Lead with margin.", source=Source.EXPLICIT,
                           created_at=NOW)],  # fmt: skip
    )
)

TRACE_ID = "abc123def456"
SPANS = [
    poisoned(
        SpanRecord(
            trace_id=TRACE_ID,
            span_id=span_id,
            parent_id=parent,
            name=name,
            start=NOW,
            duration_ms=10.0,
            attributes=attributes,
            error={"class": "permanent", "type": "ValueError", "detail": "bad value"},
        )
    )
    for span_id, parent, name, attributes in [
        ("s1", None, "turn", {"user.id": "ceo", "outcome": "answered", "router.intent": "x"}),
        ("s2", "s1", "router", {"router.intent": "analysis", "llm.model": "flash"}),
        ("s3", "s1", "query_guard", {"sql.written": "SELECT 1", "guard.reason": "none"}),
    ]
]


class PoisonedPersonas:
    def active(self) -> PersonaVersion:
        return PERSONA

    def history(self) -> list[PersonaVersion]:
        return [PERSONA]

    def publish(self, persona: Persona, **kwargs: Any) -> PersonaVersion:
        return PERSONA

    def record_rejection(self, persona: Persona, **kwargs: Any) -> None:
        return None

    def rollback(self, *, by: UserId, to_version: int | None = None) -> PersonaVersion:
        return PERSONA

    def events(self) -> list[Any]:
        return []


class PoisonedProfiles:
    def load(self, user_id: UserId) -> UserProfile:
        return PROFILE

    def save_standing(self, user_id: UserId, *args: Any, **kwargs: Any) -> UserProfile:
        return PROFILE

    def remove(self, user_id: UserId, note_id: UUID | None = None) -> UserProfile:
        return PROFILE


class PoisonedTracer:
    def read(self, since: datetime | None = None) -> list[SpanRecord]:
        return SPANS

    def __getattr__(self, name: str) -> Any:  # turn/span/current_trace_id: unused by views
        from tests.fakes import FakeTracer

        return getattr(FakeTracer(), name)


class RejectingGate:
    """The gate's verdict as it would come back on a rejection: a reason for the User, a
    ``detail`` for the trace, both carrying the email."""

    def __init__(self, *args: Any) -> None:
        pass

    def check(self, persona: Persona) -> PersonaGateResult:
        return PersonaGateResult(
            passed=False,
            checks=[
                GateCheckResult(
                    check=GateCheck(1),
                    passed=False,
                    reason=f"Too long, see {EMAIL}",
                    detail=f"model said {EMAIL}",
                )
            ],
        )


@pytest.fixture
def views(monkeypatch: pytest.MonkeyPatch) -> Iterator[Views]:
    monkeypatch.setattr(views_module, "PersonaQualityGate", RejectingGate)
    reports = InMemoryReportRepository()
    reports.reports[REPORT.report_id] = REPORT
    ports = fake_ports(
        CEO,
        llm=FakeLLMFactory(),
        reports=reports,
        personas=PoisonedPersonas(),  # pyright: ignore[reportArgumentType]
        profiles=PoisonedProfiles(),  # pyright: ignore[reportArgumentType]
        tracer=PoisonedTracer(),  # pyright: ignore[reportArgumentType]
    )
    yield Views(ports, Limits(_env_file=None))  # pyright: ignore[reportCallIssue]


SWEEP: dict[str, Callable[[Views], Any]] = {
    "reports": lambda v: v.reports("ceo", ListReportsInput()),
    "report": lambda v: v.report("ceo", REPORT.report_id),
    "profile": lambda v: v.profile("ceo"),
    "remove_preference": lambda v: v.remove_preference("ceo", uuid4()),
    "persona": lambda v: v.persona("ceo"),
    "persona_history": lambda v: v.persona_history("ceo"),
    "edit_persona": lambda v: v.edit_persona("ceo", {"emphasis": "Margin."}),
    "rollback_persona": lambda v: v.rollback_persona("ceo"),
    "trace": lambda v: v.trace("ceo", TRACE_ID),
    "stats": lambda v: v.stats("ceo", "30d"),
}


def dumped(result: Any) -> str:
    if isinstance(result, BaseModel):
        return result.model_dump_json()
    if isinstance(result, list):
        return "".join(dumped(r) for r in result)  # pyright: ignore[reportUnknownVariableType]
    return repr(result)


def test_the_fixture_really_carries_the_email() -> None:
    assert EMAIL in REPORT.markdown and EMAIL in PROFILE.notes[0].text
    assert EMAIL in PERSONA.persona.tone.notes and EMAIL in str(SPANS[2].attributes)


def test_every_view_is_swept() -> None:
    public = {n for n, _ in inspect.getmembers(Views, inspect.isfunction) if not n.startswith("_")}
    assert public == set(SWEEP)


@pytest.mark.parametrize("view", sorted(SWEEP))
def test_no_view_lets_stored_pii_out(views: Views, view: str) -> None:
    result = SWEEP[view](views)

    assert result is not None
    assert EMAIL not in dumped(result)
