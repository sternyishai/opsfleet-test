"""ReportRepository: Saved Reports, matching and the delete-with-audit transaction (HLD 04)."""

from typing import Protocol
from uuid import UUID

from agent.domain.identity import UserId
from agent.domain.reports import (
    ConversationId,
    DeletionAuditRecord,
    DeletionFilter,
    DeletionPlanItem,
    ReportEntity,
    ReportId,
    ReportListItem,
    ReportQuery,
    SavedReport,
)


class ReportRepository(Protocol):
    """Interface:

    - **Every method takes ``author_id`` and is restricted to it.** There is no method that
      reads across Authors. Callers pass ``TurnContext.user.user_id``, never a model value.
    - ``get`` of another Author's report returns ``None``, the same as a missing one.
    - ``save`` is idempotent on ``(conversation_id, turn_id)``: a second call returns the
      first report and ``created=False``. It writes the entity rows in the same transaction.
    - ``match`` resolves a ``DeletionFilter`` to plan items with reasons: exact
      ``conversation_id``; entity match on normalized values with full-text fallback; date
      range on ``created_at``. It never deletes.
    - ``delete_with_audit`` is one transaction: delete the confirmed IDs that still belong to
      ``author_id``, cascade their entity rows, delete pending Trio Candidates derived from
      them (production), insert one audit row. Idempotent on ``plan_id``: a replay returns the
      first audit record and deletes nothing.
    - Raises ``TransientError(dependency="app_db", UNAVAILABLE)`` when the store is down.
    - Prototype: SQLite + FTS5. Production: Postgres + ``tsvector``.
    """

    def save(self, report: SavedReport, entities: list[ReportEntity]) -> tuple[SavedReport, bool]:
        """Return the stored report and whether it was created by this call."""
        ...

    def find(
        self, author_id: UserId, query: ReportQuery, *, conversation_id: ConversationId
    ) -> tuple[list[ReportListItem], int]:
        """Newest first, with the total count. ``accessible`` is left True here; the tool
        sets it from the User's current scope."""
        ...

    def get(self, author_id: UserId, report_id: ReportId) -> SavedReport | None: ...

    def match(
        self, author_id: UserId, filter: DeletionFilter, *, conversation_id: ConversationId
    ) -> list[DeletionPlanItem]: ...

    def delete_with_audit(
        self,
        author_id: UserId,
        plan_id: UUID,
        report_ids: list[ReportId],
        titles: dict[ReportId, str],
    ) -> tuple[DeletionAuditRecord | None, list[ReportId]]:
        """Return the audit record (None if nothing was left to delete) and the IDs that
        were already gone."""
        ...
