"""UserDirectory over SQLite, seeded from ``seed/users.yaml`` (prototype, LLD 06)."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import yaml

from agent.db import ddl
from agent.domain.identity import Permission, Role, UserContext, UserId
from agent.errors import MessageClass, TransientError


class SqliteUserDirectory:
    """Loads the seed on construction, then reads the identity tables fresh on every ``load``.

    The seed is synced, not replaced: unchanged Brand Assignments are left alone, so a restart
    does not bump ``access_version`` (the trigger in the DDL bumps it on every row change).
    """

    def __init__(self, db_path: Path, *, seed: Path) -> None:
        self._db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name = 'users'").fetchone():
                db.executescript(ddl("sqlite"))
            _sync_seed(db, yaml.safe_load(seed.read_text())["users"])

    def load(self, user_id: UserId) -> UserContext | None:
        try:
            with self._connect() as db:
                row = db.execute(
                    "SELECT role, access_version FROM users WHERE user_id = ? AND active = 1",
                    (user_id,),
                ).fetchone()
                if row is None:
                    return None
                permissions = db.execute(
                    "SELECT permission FROM user_permissions WHERE user_id = ?", (user_id,)
                ).fetchall()
                brands = db.execute(
                    "SELECT brand FROM brand_assignments WHERE user_id = ?", (user_id,)
                ).fetchall()
        except sqlite3.Error as exc:
            raise TransientError(
                type(exc).__name__, dependency="app_db", message_class=MessageClass.UNAVAILABLE
            ) from exc
        role, access_version = row
        return UserContext(
            user_id=user_id,
            role=Role(role) if role else None,
            permissions=frozenset(Permission(p) for (p,) in permissions),
            brands=frozenset(b for (b,) in brands),
            access_version=access_version,
        )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """One connection per call, committed and closed: ``load`` runs every turn."""
        db = sqlite3.connect(self._db_path)
        try:
            db.execute("PRAGMA foreign_keys = ON")
            with db:
                yield db
        finally:
            db.close()


def _sync_seed(db: sqlite3.Connection, users: list[dict]) -> None:
    for user in users:
        user_id = user["user_id"]
        db.execute(
            "INSERT INTO users (user_id, role) VALUES (?, ?) "
            "ON CONFLICT (user_id) DO UPDATE SET role = excluded.role",
            (user_id, user.get("role")),
        )
        _sync_set(db, "user_permissions", "permission", user_id, set(user.get("permissions", [])))
        _sync_set(db, "brand_assignments", "brand", user_id, set(user.get("brands", [])))


def _sync_set(
    db: sqlite3.Connection, table: str, column: str, user_id: UserId, wanted: set[str]
) -> None:
    have = {v for (v,) in db.execute(f"SELECT {column} FROM {table} WHERE user_id = ?", (user_id,))}
    for value in have - wanted:
        db.execute(f"DELETE FROM {table} WHERE user_id = ? AND {column} = ?", (user_id, value))
    for value in wanted - have:
        db.execute(f"INSERT INTO {table} (user_id, {column}) VALUES (?, ?)", (user_id, value))
