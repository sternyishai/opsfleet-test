"""PersonaStore over SQLite (prototype, HLD 09 § In the prototype; LLD 06).

The same database file as the users: ``created_by`` and ``actor_id`` are foreign keys into
``users``. The bundled ``persona.yaml`` is inserted as version 1 on first run, and it is also
what ``active`` serves on a cold start when the store cannot be read at all.
"""

import json
import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from agent.adapters.persona_file import FilePersonaStore
from agent.domain.identity import UserId
from agent.domain.persona import (
    Persona,
    PersonaEvent,
    PersonaGateResult,
    PersonaPath,
    PersonaVersion,
)
from agent.errors import MessageClass, TransientError

_log = logging.getLogger(__name__)

_VERSION_COLUMNS = "version, persona, created_by, created_at, path, changed_fields, is_active"


class SqlitePersonaStore:
    """One connection per call, committed and closed, like the other SQLite adapters.

    ``active`` is read on every turn (the prototype has no 60 s cache) and remembers the last
    version it read, so a store that stops answering keeps serving it."""

    def __init__(self, db_path: Path, *, seed: Path) -> None:
        self._db_path = db_path
        self._default = FilePersonaStore(seed).active()
        self._last: PersonaVersion | None = None
        try:
            with self._connect() as db:
                if not db.execute("SELECT 1 FROM persona_versions LIMIT 1").fetchone():
                    db.execute(
                        "INSERT INTO persona_versions (version, persona, path, is_active) "
                        "VALUES (?, ?, 'seed', 1)",
                        (self._default.version, self._default.persona.model_dump_json()),
                    )
        except sqlite3.Error as exc:  # served from the bundled default until it answers
            _log.warning("the Persona store cannot be read (%s)", type(exc).__name__)

    def active(self) -> PersonaVersion:
        try:
            with self._connect() as db:
                row = db.execute(
                    f"SELECT {_VERSION_COLUMNS} FROM persona_versions WHERE is_active = 1"
                ).fetchone()
        except sqlite3.Error as exc:
            _log.warning("the Persona store cannot be read (%s)", type(exc).__name__)
            row = None
        if row is not None:
            self._last = _version(row)
        return self._last or self._default

    def history(self) -> list[PersonaVersion]:
        with self._write() as db:
            rows = db.execute(
                f"SELECT {_VERSION_COLUMNS} FROM persona_versions ORDER BY version"
            ).fetchall()
        return [_version(r) for r in rows]

    def publish(
        self,
        persona: Persona,
        *,
        by: UserId,
        path: PersonaPath,
        changed_fields: list[str],
        gate: PersonaGateResult,
    ) -> PersonaVersion:
        if not gate.passed:
            raise ValueError("only a Persona that passed the Quality Gate can be published")
        with self._write() as db:
            current = _active_version(db)
            (latest,) = db.execute("SELECT MAX(version) FROM persona_versions").fetchone()
            version = latest + 1
            db.execute("UPDATE persona_versions SET is_active = 0 WHERE is_active = 1")
            db.execute(
                "INSERT INTO persona_versions (version, persona, created_by, path, "
                "changed_fields, gate_result, is_active) VALUES (?, ?, ?, ?, ?, ?, 1)",
                (
                    version,
                    persona.model_dump_json(),
                    by,
                    path.value,
                    json.dumps(changed_fields),
                    gate.model_dump_json(),
                ),
            )
            _record(db, "published", by, path, current, version, gate=gate)
            row = db.execute(
                f"SELECT {_VERSION_COLUMNS} FROM persona_versions WHERE version = ?", (version,)
            ).fetchone()
        return _version(row)

    def record_rejection(
        self, persona: Persona, *, by: UserId, path: PersonaPath, gate: PersonaGateResult
    ) -> None:
        with self._write() as db:
            _record(
                db, "rejected", by, path, _active_version(db), None, gate=gate, candidate=persona
            )

    def rollback(self, *, by: UserId, to_version: int | None = None) -> PersonaVersion:
        with self._write() as db:
            current = _active_version(db)
            if to_version is None:
                trail = _undo_trail(db)
                if len(trail) < 2:
                    raise LookupError("nothing to roll back to")
                to_version = trail[-2]
            if (
                to_version == current
                or not db.execute(
                    "SELECT 1 FROM persona_versions WHERE version = ?", (to_version,)
                ).fetchone()
            ):
                raise LookupError(f"version {to_version} cannot be rolled back to")
            db.execute("UPDATE persona_versions SET is_active = 0 WHERE is_active = 1")
            db.execute("UPDATE persona_versions SET is_active = 1 WHERE version = ?", (to_version,))
            _record(db, "rolled_back", by, None, current, to_version)
            row = db.execute(
                f"SELECT {_VERSION_COLUMNS} FROM persona_versions WHERE version = ?",
                (to_version,),
            ).fetchone()
        return _version(row)

    def events(self) -> list[PersonaEvent]:
        with self._write() as db:
            rows = db.execute(
                "SELECT kind, actor_id, occurred_at, path, from_version, to_version "
                "FROM persona_events ORDER BY occurred_at, rowid"
            ).fetchall()
        return [
            PersonaEvent(
                kind=kind,
                actor_id=actor,
                occurred_at=datetime.fromisoformat(at),
                path=PersonaPath(path) if path else None,
                from_version=from_v,
                to_version=to_v,
            )
            for kind, actor, at, path, from_v, to_v in rows
        ]

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        """Everything but ``active``: a store that cannot be read or written raises
        ``TransientError``, so nothing is published by half."""
        try:
            with self._connect() as db:
                yield db
        except sqlite3.Error as exc:
            raise TransientError(
                type(exc).__name__, dependency="app_db", message_class=MessageClass.UNAVAILABLE
            ) from exc

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self._db_path)
        try:
            db.execute("PRAGMA foreign_keys = ON")
            with db:
                yield db
        finally:
            db.close()


def _active_version(db: sqlite3.Connection) -> int:
    (version,) = db.execute("SELECT version FROM persona_versions WHERE is_active = 1").fetchone()
    return version


def _undo_trail(db: sqlite3.Connection) -> list[int]:
    """The versions that were made active, in order, with every undo taken off the end: its
    last item is the active version, and the one before it is what an undo goes back to."""
    trail = [1]
    for kind, to_version in db.execute(
        "SELECT kind, to_version FROM persona_events WHERE kind != 'rejected' "
        "ORDER BY occurred_at, rowid"
    ):
        if kind == "rolled_back" and len(trail) > 1 and trail[-2] == to_version:
            trail.pop()
        else:
            trail.append(to_version)
    return trail


def _record(
    db: sqlite3.Connection,
    kind: str,
    by: UserId,
    path: PersonaPath | None,
    from_version: int | None,
    to_version: int | None,
    *,
    gate: PersonaGateResult | None = None,
    candidate: Persona | None = None,
) -> None:
    db.execute(
        "INSERT INTO persona_events (event_id, actor_id, kind, path, from_version, to_version, "
        "gate_result, candidate) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            str(uuid4()),
            by,
            kind,
            path.value if path else None,
            from_version,
            to_version,
            gate.model_dump_json() if gate else None,
            # A rejected direct edit may hold values the gate refused (an unknown preset).
            candidate.model_dump_json(warnings=False) if candidate else None,
        ),
    )


def _version(row: tuple) -> PersonaVersion:
    version, persona, created_by, created_at, path, changed_fields, is_active = row
    return PersonaVersion(
        version=version,
        persona=Persona.model_validate_json(persona),
        created_by=created_by,
        created_at=datetime.fromisoformat(created_at),
        path=PersonaPath(path),
        changed_fields=json.loads(changed_fields),
        is_active=bool(is_active),
    )
