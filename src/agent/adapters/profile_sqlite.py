"""ProfileStore over SQLite (prototype, HLD 05 § In the prototype; LLD 06).

The same database file as the users: ``user_profiles.user_id`` is a foreign key into
``users``. One row per User; each typed value and the notes list are JSON columns, so the row
is the ``UserProfile`` as stored.
"""

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from agent.domain.identity import UserId
from agent.domain.profile import PreferenceScope, PreferenceStatement, UserProfile, merged
from agent.errors import MessageClass, TransientError

_TYPED = ("format", "depth", "charts", "language")
_COLUMNS = (*_TYPED, "attributes", "notes")


class SqliteProfileStore:
    """One connection per call, committed and closed, like the other SQLite adapters. Any
    read or write that fails raises ``TransientError(UNAVAILABLE)``; the graph traces it and
    goes on, because a profile is never on the critical path."""

    def __init__(self, db_path: Path, *, max_notes: int = UserProfile.MAX_NOTES) -> None:
        self._db_path = db_path
        self._max_notes = max_notes

    def load(self, user_id: UserId) -> UserProfile:
        with self._connect() as db:
            return _read(db, user_id)

    def save_standing(self, user_id: UserId, statements: list[PreferenceStatement]) -> UserProfile:
        if any(s.scope is not PreferenceScope.STANDING for s in statements):
            raise ValueError("only standing preferences are saved")
        with self._connect() as db:
            profile = merged(
                _read(db, user_id), statements, at=datetime.now(UTC), max_notes=self._max_notes
            )
            _write(db, profile)
        return profile

    def remove(self, user_id: UserId, note_id: UUID | None = None) -> UserProfile:
        with self._connect() as db:
            if note_id is None:
                db.execute("DELETE FROM user_profiles WHERE user_id = ?", (user_id,))
                return UserProfile(user_id=user_id)
            current = _read(db, user_id)
            profile = current.model_copy(
                update={"notes": [n for n in current.notes if n.note_id != note_id]}
            )
            _write(db, profile)
        return profile

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        try:
            db = sqlite3.connect(self._db_path)
            try:
                db.execute("PRAGMA foreign_keys = ON")
                with db:
                    yield db
            finally:
                db.close()
        except sqlite3.Error as exc:
            raise TransientError(
                type(exc).__name__, dependency="app_db", message_class=MessageClass.UNAVAILABLE
            ) from exc


def _read(db: sqlite3.Connection, user_id: UserId) -> UserProfile:
    row = db.execute(
        f"SELECT {', '.join(_COLUMNS)} FROM user_profiles WHERE user_id = ?", (user_id,)
    ).fetchone()
    if row is None:
        return UserProfile(user_id=user_id)
    stored: dict[str, Any] = {c: json.loads(v) for c, v in zip(_COLUMNS, row, strict=True) if v}
    return UserProfile.model_validate({"user_id": user_id, **stored})


def _write(db: sqlite3.Connection, profile: UserProfile) -> None:
    data = profile.model_dump(mode="json")
    values = [json.dumps(data[c]) if data[c] is not None else None for c in _COLUMNS]
    db.execute(
        f"INSERT INTO user_profiles (user_id, {', '.join(_COLUMNS)}) "
        f"VALUES (?, {', '.join('?' * len(_COLUMNS))}) "
        f"ON CONFLICT (user_id) DO UPDATE SET "
        f"{', '.join(f'{c} = excluded.{c}' for c in _COLUMNS)}, "
        "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')",
        (profile.user_id, *values),
    )
