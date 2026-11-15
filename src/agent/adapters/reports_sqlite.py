"""ReportRepository over SQLite + FTS5 (prototype, HLD 04, LLD 06).

The same database file as ``SqliteUserDirectory``: a report's Author is a foreign key into
``users``. Matching narrows in SQL (Author, Conversation, dates, full text) and resolves
entities per filter term, so each plan item can say which terms it mentions.
"""

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from agent.contracts.channel import Channel
from agent.db import ddl
from agent.domain.identity import BrandScope, UserId
from agent.domain.reports import (
    ConversationId,
    DateRange,
    DeletionAuditRecord,
    DeletionFilter,
    DeletionPlanItem,
    DeletionReceipt,
    ReportEntity,
    ReportId,
    ReportListItem,
    ReportQuery,
    ReportStructured,
    SavedReport,
    match_reason,
    normalize_entity,
)
from agent.errors import AgentError, MessageClass, TransientError

_COLUMNS = (
    "report_id, author_id, conversation_id, turn_id, created_at, title, question, sql, "
    "markdown, structured, scope_all, scope_brands, data_as_of"
)


class SqliteReportRepository:
    """One connection per call, committed and closed, like ``SqliteUserDirectory``."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name = 'users'").fetchone():
                db.executescript(ddl("sqlite"))

    def save(
        self, report: SavedReport, entities: list[ReportEntity], *, channel: Channel
    ) -> tuple[SavedReport, bool]:
        with self._connect() as db:
            db.execute(
                "INSERT INTO conversations (conversation_id, user_id, channel) VALUES (?, ?, ?) "
                "ON CONFLICT (conversation_id) DO NOTHING",
                (str(report.conversation_id), report.author_id, channel.value),
            )
            inserted = db.execute(
                f"INSERT INTO saved_reports ({_COLUMNS}) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (conversation_id, turn_id) DO NOTHING",
                _row(report),
            ).rowcount
            if inserted:
                db.executemany(
                    "INSERT INTO report_entities (report_id, entity_type, value, normalized) "
                    "VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING",
                    [
                        (str(e.report_id), e.entity_type.value, e.value, e.normalized)
                        for e in entities
                    ],
                )
                return report, True
            row = db.execute(
                f"SELECT {_COLUMNS} FROM saved_reports WHERE conversation_id = ? AND turn_id = ?",
                (str(report.conversation_id), report.turn_id),
            ).fetchone()
        return _report(row), False

    def find(
        self, author_id: UserId, query: ReportQuery, *, conversation_id: ConversationId
    ) -> tuple[list[ReportListItem], int]:
        criteria = _Criteria(
            this_conversation=query.this_conversation,
            entities=query.entities,
            text=query.text,
            created=query.created,
        )
        with self._connect() as db:
            rows = _matching(db, author_id, criteria, conversation_id)
        rows.sort(key=lambda r: r[0]["created_at"], reverse=True)
        items = [
            ReportListItem(
                report_id=UUID(row["report_id"]),
                title=row["title"],
                created_at=_time(row["created_at"]),
                conversation_id=UUID(row["conversation_id"]),
                accessible=True,
                scope=_scope(row),
            )
            for row, _ in rows[: query.limit]
        ]
        return items, len(rows)

    def get(self, author_id: UserId, report_id: ReportId) -> SavedReport | None:
        with self._connect() as db:
            row = db.execute(
                f"SELECT {_COLUMNS} FROM saved_reports WHERE report_id = ? AND author_id = ?",
                (str(report_id), author_id),
            ).fetchone()
        return _report(row) if row else None

    def match(
        self, author_id: UserId, filter: DeletionFilter, *, conversation_id: ConversationId
    ) -> list[DeletionPlanItem]:
        criteria = _Criteria(
            this_conversation=filter.this_conversation,
            entities=filter.entities,
            text=filter.topic,
            created=filter.created,
        )
        with self._connect() as db:
            rows = _matching(db, author_id, criteria, conversation_id)
        rows.sort(key=lambda r: r[0]["created_at"])
        return [
            DeletionPlanItem(
                index=i,
                report_id=UUID(row["report_id"]),
                title=row["title"],
                created_at=_time(row["created_at"]),
                reason=match_reason(filter, hits),
            )
            for i, (row, hits) in enumerate(rows, start=1)
        ]

    def delete_with_audit(
        self,
        author_id: UserId,
        plan_id: UUID,
        report_ids: list[ReportId],
        titles: dict[ReportId, str],
        filter: DeletionFilter,
    ) -> DeletionReceipt:
        with self._connect() as db:
            done = db.execute(
                "SELECT audit_id, plan_id, author_id, deleted_at, report_ids, titles "
                "FROM deletion_audit WHERE plan_id = ? AND author_id = ?",
                (str(plan_id), author_id),
            ).fetchone()
            if done:
                return DeletionReceipt(audit=_audit(done), already_gone=[], replayed=True)
            deleted: list[ReportId] = []
            for report_id in report_ids:  # the Author is checked again, in the transaction
                gone = db.execute(
                    "DELETE FROM saved_reports WHERE report_id = ? AND author_id = ?",
                    (str(report_id), author_id),
                ).rowcount
                if gone:
                    deleted.append(report_id)
            audit = None
            if deleted:
                audit = DeletionAuditRecord(
                    audit_id=uuid4(),
                    plan_id=plan_id,
                    author_id=author_id,
                    deleted_at=datetime.now(UTC),
                    report_ids=deleted,
                    titles=[titles[i] for i in deleted],
                )
                db.execute(
                    "INSERT INTO deletion_audit "
                    "(audit_id, plan_id, author_id, deleted_at, report_ids, titles, filter) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(audit.audit_id),
                        str(plan_id),
                        author_id,
                        audit.deleted_at.isoformat(),
                        json.dumps([str(i) for i in deleted]),
                        json.dumps(audit.titles),
                        filter.model_dump_json(),
                    ),
                )
        already_gone = [i for i in report_ids if i not in deleted]
        return DeletionReceipt(audit=audit, already_gone=already_gone, replayed=False)

    # Eval grading only (``agent.evals.runner.TrialStore``): these read across Authors, which
    # is why they are not on the ``ReportRepository`` port.
    def all_report_ids(self) -> frozenset[ReportId]:
        with self._connect() as db:
            return frozenset(UUID(r[0]) for r in db.execute("SELECT report_id FROM saved_reports"))

    def audit_rows(self) -> int:
        with self._connect() as db:
            return db.execute("SELECT COUNT(*) FROM deletion_audit").fetchone()[0]

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Committed on success, rolled back on any error; SQLite errors are classified."""
        try:
            db = sqlite3.connect(self._db_path)
        except sqlite3.Error as exc:
            raise _classified(exc) from exc
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys = ON")
            with db:
                yield db
        except sqlite3.Error as exc:
            raise _classified(exc) from exc
        finally:
            db.close()


@dataclass(frozen=True, slots=True)
class _Criteria:
    """What ``find`` and ``match`` share: a ``DeletionFilter`` with ``topic`` as ``text``."""

    this_conversation: bool
    entities: list[str]
    text: str | None
    created: DateRange | None


def _matching(
    db: sqlite3.Connection,
    author_id: UserId,
    c: _Criteria,
    conversation_id: ConversationId,
) -> list[tuple[sqlite3.Row, list[str]]]:
    """The Author's reports that meet every criterion, each with the entities it mentions."""
    where = ["r.author_id = ?"]
    params: list[Any] = [author_id]
    if c.this_conversation:
        where.append("r.conversation_id = ?")
        params.append(str(conversation_id))
    if c.created and c.created.start:
        where.append("substr(r.created_at, 1, 10) >= ?")
        params.append(c.created.start.isoformat())
    if c.created and c.created.end:
        where.append("substr(r.created_at, 1, 10) <= ?")
        params.append(c.created.end.isoformat())
    if c.text:
        words = _fts_words(c.text)
        if not words:  # nothing to match on: match nothing, never everything
            return []
        where.append(_IN_FTS)
        params.append(words)
    rows = db.execute(
        f"SELECT {_COLUMNS} FROM saved_reports r WHERE {' AND '.join(where)}", params
    ).fetchall()
    if not c.entities:
        return [(row, []) for row in rows]
    ids = [row["report_id"] for row in rows]
    hits: dict[str, list[str]] = {i: [] for i in ids}
    for term in c.entities:
        for report_id in _mentioning(db, ids, term):
            hits[report_id].append(term)
    return [(row, hits[row["report_id"]]) for row in rows if hits[row["report_id"]]]


_IN_FTS = "r.rowid IN (SELECT rowid FROM saved_reports_fts WHERE saved_reports_fts MATCH ?)"


def _mentioning(db: sqlite3.Connection, ids: list[str], term: str) -> set[str]:
    """Of ``ids``, the reports with ``term`` as an extracted entity or, the fallback, as a
    full-text phrase."""
    if not ids:
        return set()
    marks = ", ".join("?" * len(ids))
    found = {
        r[0]
        for r in db.execute(
            "SELECT report_id FROM report_entities "
            f"WHERE normalized = ? AND report_id IN ({marks})",
            [normalize_entity(term), *ids],
        )
    }
    if phrase := _fts_phrase(term):
        found |= {
            r[0]
            for r in db.execute(
                f"SELECT r.report_id FROM saved_reports r WHERE {_IN_FTS} "
                f"AND r.report_id IN ({marks})",
                [phrase, *ids],
            )
        }
    return found


def _fts_words(text: str) -> str:
    """Every word must appear. Each is quoted, so no FTS5 operator in User text is live."""
    return " ".join(q for w in text.split() if (q := _quoted(w)) != '""')


def _fts_phrase(text: str) -> str:
    quoted = _quoted(text)
    return "" if quoted == '""' else quoted


def _quoted(text: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in text).strip()
    return '"' + " ".join(cleaned.split()) + '"'


def _row(r: SavedReport) -> tuple[Any, ...]:
    return (
        str(r.report_id),
        r.author_id,
        str(r.conversation_id),
        r.turn_id,
        r.created_at.astimezone(UTC).isoformat(),
        r.title,
        r.question,
        r.sql,
        r.markdown,
        r.structured.model_dump_json(),
        int(r.scope.all_brands),
        json.dumps(sorted(r.scope.brands)),
        r.data_as_of.isoformat(),
    )


def _report(row: sqlite3.Row) -> SavedReport:
    return SavedReport(
        report_id=UUID(row["report_id"]),
        author_id=row["author_id"],
        conversation_id=UUID(row["conversation_id"]),
        turn_id=row["turn_id"],
        created_at=_time(row["created_at"]),
        title=row["title"],
        question=row["question"],
        sql=row["sql"],
        markdown=row["markdown"],
        structured=ReportStructured.model_validate_json(row["structured"]),
        scope=_scope(row),
        data_as_of=row["data_as_of"],
    )


def _scope(row: sqlite3.Row) -> BrandScope:
    if row["scope_all"]:
        return BrandScope(all_brands=True)
    return BrandScope(brands=frozenset(json.loads(row["scope_brands"])))


def _audit(row: sqlite3.Row) -> DeletionAuditRecord:
    return DeletionAuditRecord(
        audit_id=UUID(row["audit_id"]),
        plan_id=UUID(row["plan_id"]),
        author_id=row["author_id"],
        deleted_at=_time(row["deleted_at"]),
        report_ids=[UUID(i) for i in json.loads(row["report_ids"])],
        titles=json.loads(row["titles"]),
    )


def _time(text: str) -> datetime:
    """Stored as ISO 8601 UTC; the DDL default writes a ``Z`` suffix."""
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _classified(exc: sqlite3.Error) -> AgentError:
    """A locked or unreachable file is transient; a broken constraint is a bug."""
    if isinstance(exc, sqlite3.IntegrityError):
        return AgentError(f"app_db integrity: {exc}")
    return TransientError(
        type(exc).__name__, dependency="app_db", message_class=MessageClass.UNAVAILABLE
    )
