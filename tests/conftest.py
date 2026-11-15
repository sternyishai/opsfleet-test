"""Checkpoints are deserialized strictly in tests, so a state type missing from
``CHECKPOINT_TYPES`` fails here instead of warning in production. The ``reports`` fixture
runs a test against both ``ReportRepository`` adapters."""

import os
from pathlib import Path

import pytest

os.environ.setdefault("LANGGRAPH_STRICT_MSGPACK", "true")

from agent.ports.reports import ReportRepository  # noqa: E402  # after the env is set

SEED_USERS = """\
users:
  - {user_id: ceo, role: executive}
  - {user_id: manager-a, role: manager, brands: [Calvin Klein, Tommy Hilfiger]}
  - {user_id: manager-b, role: manager, brands: [Carhartt]}
"""


@pytest.fixture(params=["sqlite", "fake"])
def reports(request: pytest.FixtureRequest, tmp_path: Path) -> ReportRepository:
    """Every repository test runs against the SQLite adapter and the in-memory fake."""
    if request.param == "fake":
        from tests.fakes import InMemoryReportRepository

        return InMemoryReportRepository()
    from agent.adapters.reports_sqlite import SqliteReportRepository
    from agent.adapters.users_sqlite import SqliteUserDirectory

    seed = tmp_path / "users.yaml"
    seed.write_text(SEED_USERS)
    db = tmp_path / "app.db"
    SqliteUserDirectory(db, seed=seed)  # the users the reports' Author key points at
    return SqliteReportRepository(db)


@pytest.fixture(autouse=True)
def spans_hold_no_pii(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every value any test puts on a span passes the real PII filter (HLD 03: never PII in
    a trace). A span is a way out, like a reply: this holds it to the same check in every
    test, so a new attribute that can carry a Customer's email fails where it is added."""
    from agent.adapters.pii_regex import RegexPiiFilter
    from agent.adapters.tracer_jsonl import JsonlTracer, _JsonlSpan
    from tests.fakes import FakeSpan, FakeTracer

    pii = RegexPiiFilter()

    def check(name: str, attributes: dict[str, object]) -> None:
        for key, value in attributes.items():
            for text in value if isinstance(value, list) else [value]:
                if isinstance(text, str) and pii.scan(text).hits:
                    pytest.fail(f"PII in span {name!r} attribute {key!r}: {text[:80]!r}")

    for span_class in (_JsonlSpan, FakeSpan):
        original = span_class.set

        def checked_set(self, __original=original, **attributes):  # type: ignore[no-untyped-def]
            check(self.name, attributes)
            __original(self, **attributes)

        monkeypatch.setattr(span_class, "set", checked_set)

    for tracer_class in (JsonlTracer, FakeTracer):
        for opener in ("span", "turn"):
            original_open = getattr(tracer_class, opener)

            def checked_open(self, *args, __original=original_open, __opener=opener, **attributes):  # type: ignore[no-untyped-def]  # noqa: E501
                check(args[0] if args else __opener, attributes)
                return __original(self, *args, **attributes)

            monkeypatch.setattr(tracer_class, opener, checked_open)
