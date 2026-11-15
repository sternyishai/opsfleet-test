"""Saved Reports, Deletion Plans and the deletion audit (HLD 04)."""

import re
from collections.abc import Callable
from datetime import date, datetime
from enum import StrEnum
from typing import ClassVar, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent.domain.identity import BrandScope, UserId

type ReportId = UUID
type ConversationId = UUID


class ReportStructured(BaseModel):
    """The structured part of a Saved Report (``saved_reports.structured``)."""

    title: str = Field(max_length=200)
    summary: str = Field(
        max_length=1000,
        description="Short summary, written by the analysis model at save time (ADR 0009).",
    )
    insights: list[str] = Field(default_factory=list, max_length=20)
    action_items: list[str] = Field(default_factory=list, max_length=20)
    period: str | None = Field(default=None, description='Free text, e.g. "Q1 2026".')

    def map_text(self, fn: Callable[[str], str]) -> "ReportStructured":
        """Every text field passed through ``fn``: the PII filter on write and on view."""
        return ReportStructured(
            title=fn(self.title),
            summary=fn(self.summary),
            insights=[fn(i) for i in self.insights],
            action_items=[fn(a) for a in self.action_items],
            period=fn(self.period) if self.period else None,
        )


class SavedReport(BaseModel):
    """A question, the SQL that answered it and the answer, saved on the User's explicit request.

    Private to its Author. Every field has already passed the output PII filter (ADR 0003).
    """

    model_config = ConfigDict(frozen=True)

    report_id: ReportId
    author_id: UserId
    conversation_id: ConversationId
    turn_id: str = Field(description="With conversation_id, the idempotency key of save_report.")
    created_at: datetime
    title: str
    question: str
    sql: str = Field(description="The SQL the model wrote, before the guard's scope rewrite.")
    markdown: str = Field(description="The exact text the User saw.")
    structured: ReportStructured
    scope: BrandScope = Field(description="Brands the data covers, from the query guard.")
    data_as_of: date = Field(description='Shown as "data as of <date>" when the report is opened.')


class EntityType(StrEnum):
    BRAND = "brand"
    CATEGORY = "category"
    PRODUCT = "product"
    CUSTOMER = "customer"  # a user_id, never a name


ENTITY_ALIASES: dict[str, str] = {
    "ck": "calvin klein",
    "tommy": "tommy hilfiger",
    "tnf": "the north face",
    "north face": "the north face",
    "levi strauss": "levis",
}
"""Other names for the same entity, already normalized. Small and hand-kept in the prototype."""


def normalize_entity(value: str) -> str:
    """The match key for an entity: case-folded, apostrophes dropped, other punctuation and
    runs of whitespace as one space, then aliases resolved ("Levi's" and "LEVIS" -> "levis")."""
    folded = re.sub(r"['’]", "", value.casefold())
    key = " ".join(re.sub(r"[^\w]+", " ", folded).split())
    return ENTITY_ALIASES.get(key, key)


class ReportEntity(BaseModel):
    """One entity named in a report, extracted at save time (prototype) or by a job (production)."""

    report_id: ReportId
    entity_type: EntityType
    value: str = Field(description="As written in the report.")
    normalized: str = Field(description="Case- and alias-normalized; the match key.")


class ReportListItem(BaseModel):
    """One row of ``list_my_reports``. Content is withheld when the scope check fails."""

    report_id: ReportId
    title: str
    created_at: datetime
    conversation_id: ConversationId
    accessible: bool = Field(
        description="False when the report covers a Brand the User no longer has (HLD 04)."
    )
    scope: BrandScope = Field(
        exclude=True,
        description="The report's scope, for the access check. Never serialized, so an "
        "inaccessible report shows its title only.",
    )


# --- deletion -------------------------------------------------------------------------------


class DateRange(BaseModel):
    start: date | None = None
    end: date | None = None  # inclusive


class ReportQuery(BaseModel):
    """Search over the User's own reports (``list_my_reports``). Never names an Author."""

    text: str | None = Field(default=None, max_length=200, description="Full-text match.")
    entities: list[str] = Field(default_factory=list, max_length=10)
    created: DateRange | None = None
    this_conversation: bool = False
    limit: int = Field(default=20, ge=1, le=50)


class DeletionFilter(BaseModel):
    """What the model is allowed to produce from a delete request. It never names an Author.

    Exactly one of ``all_reports``, ``this_conversation`` or a content criterion must be set;
    content criteria (entities, topic, created) combine with AND.
    """

    all_reports: bool = False
    this_conversation: bool = Field(
        default=False, description="Code substitutes the current conversation_id."
    )
    entities: list[str] = Field(default_factory=list, max_length=20)
    topic: str | None = Field(default=None, max_length=200, description="Full-text match.")
    created: DateRange | None = None

    @model_validator(mode="after")
    def _one_kind(self) -> Self:
        # A term with no letter or digit would be dropped by the full-text match, widening
        # the filter to every report; refuse it instead.
        for term in [*self.entities, *([self.topic] if self.topic is not None else [])]:
            if not any(ch.isalnum() for ch in term):
                raise ValueError(f"{term!r} has no words to match")
        content = bool(self.entities or self.topic or self.created)
        if self.all_reports and (self.this_conversation or content):
            raise ValueError("all_reports cannot be combined with other criteria")
        if not (self.all_reports or self.this_conversation or content):
            raise ValueError("an empty filter matches nothing; ask the User to be specific")
        return self


def match_reason(filter: DeletionFilter, entity_hits: list[str]) -> str:
    """Why a report matched, in the User's words: ``entity_hits`` are the filter entities it
    mentions. One wording for every ``ReportRepository`` adapter."""
    if filter.all_reports:
        return "all your reports"
    parts: list[str] = []
    if filter.this_conversation:
        parts.append("made in this conversation")
    if entity_hits:
        parts.append(f"mentions: {', '.join(entity_hits)}")
    if filter.topic:
        parts.append(f'about "{filter.topic}"')
    if filter.created:
        start, end = filter.created.start, filter.created.end
        if start and end:
            parts.append(f"made {start.isoformat()} to {end.isoformat()}")
        elif start:
            parts.append(f"made on or after {start.isoformat()}")
        elif end:
            parts.append(f"made on or before {end.isoformat()}")
    return "; ".join(parts)


class DeletionPlanItem(BaseModel):
    index: int = Field(ge=1, description='The number the User sees, used in "yes except 2,3".')
    report_id: ReportId
    title: str
    created_at: datetime
    reason: str = Field(description='"mentions: Nike, Adidas" or "made in this conversation".')


class DeletionPlan(BaseModel):
    """The exact reports a delete request matched. Stores IDs, never the filter (HLD 04)."""

    model_config = ConfigDict(frozen=True)

    plan_id: UUID
    author_id: UserId = Field(description="Only this User may confirm it.")
    conversation_id: ConversationId = Field(description="Only in this Conversation.")
    created_at: datetime
    filter: DeletionFilter = Field(
        description="Kept for the trace and the audit, not for deleting."
    )
    items: list[DeletionPlanItem] = Field(description="All matches, in display order.")

    LARGE_PLAN: ClassVar[int] = 20  # more than this: show 20, ask for the count to confirm all

    @property
    def total(self) -> int:
        return len(self.items)

    @property
    def is_large(self) -> bool:
        return self.total > self.LARGE_PLAN


class DeletionOutcome(BaseModel):
    plan_id: UUID
    deleted: list[DeletionPlanItem]
    kept: list[DeletionPlanItem] = Field(description='Dropped by "yes except …".')
    already_gone: list[DeletionPlanItem] = Field(
        description="Confirmed, but deleted in the meantime (e.g. from another channel)."
    )
    audit_id: UUID | None = Field(description="None when nothing was deleted.")


class DeletionAuditRecord(BaseModel):
    """Metadata only, never report content."""

    audit_id: UUID
    plan_id: UUID
    author_id: UserId
    deleted_at: datetime
    report_ids: list[ReportId]
    titles: list[str]

    @property
    def count(self) -> int:
        return len(self.report_ids)


class DeletionReceipt(BaseModel):
    """What ``ReportRepository.delete_with_audit`` did."""

    audit: DeletionAuditRecord | None = Field(description="None when nothing was left to delete.")
    already_gone: list[ReportId] = Field(description="Confirmed, but no longer the Author's.")
    replayed: bool = Field(description="True: this plan was executed before; nothing changed.")
