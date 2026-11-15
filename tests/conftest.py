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
