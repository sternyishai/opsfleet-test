"""Input and output schemas of every tool (ADR 0004, ADR 0007).

Two kinds of tool share this module:

- **Analysis tools**, bound to the ReAct sub-agent: ``get_schema``, ``search_golden_trios``,
  ``run_sql``, ``save_report``, ``list_my_reports``, ``get_my_report``.
- **Branch functions**, called only from fixed branches the router opened, never offered to a
  model: ``plan_deletion``, ``execute_deletion``, ``draft_persona``, ``publish_persona``,
  ``rollback_persona``.

The rule for every input: **no field names a User, an Author or a Brand scope.** Identity is
injected from graph state through ``agent.tools.context.TurnContext``, so no wording reaches
another User's data.

Every output is a discriminated union on ``status``. Error variants are values the model (or
the branch) reads, not exceptions; exceptions are reserved for the classified errors in
``agent.errors`` that end the step.
"""

from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from agent.domain.persona import Persona, PersonaGateResult, PersonaPath, PersonaVersion
from agent.domain.query import FilterLiteral, QueryResult, RejectReason, TableName
from agent.domain.reports import (
    DeletionFilter,
    DeletionOutcome,
    DeletionPlan,
    EntityType,
    ReportListItem,
    ReportQuery,
    ReportStructured,
    SavedReport,
)
from agent.domain.trios import TrioMatch
from agent.errors import MessageClass


class ToolError(BaseModel):
    """A permanent failure of this call, rendered as a catalog message. Not retried."""

    status: Literal["error"] = "error"
    message_class: MessageClass
    detail: str = Field(description="For the trace; never shown to the User.")


# --- get_schema ------------------------------------------------------------------------------


class GetSchemaInput(BaseModel):
    tables: list[TableName] | None = Field(default=None, description="None: all four tables.")


class ColumnSchema(BaseModel):
    name: str
    type: str
    description: str


class TableSchema(BaseModel):
    name: TableName
    description: str
    columns: list[ColumnSchema] = Field(
        description="Allowlisted columns only, as this User's scoped view projects them."
    )


class SchemaOutput(BaseModel):
    status: Literal["ok"] = "ok"
    tables: list[TableSchema]
    scope_note: str = Field(description='"You can analyze Nike and Adidas." or "all Brands".')
    data_as_of: date


# --- search_golden_trios ---------------------------------------------------------------------


class SearchTriosInput(BaseModel):
    query: str = Field(min_length=3, max_length=500)
    limit: int = Field(default=3, ge=1, le=5)


class SearchTriosOutput(BaseModel):
    status: Literal["ok"] = "ok"
    matches: list[TrioMatch] = Field(description="Wrapped as reference data in the prompt.")


# --- run_sql ---------------------------------------------------------------------------------


class RunSqlInput(BaseModel):
    sql: str = Field(min_length=1, max_length=20_000, description="One BigQuery SELECT.")


class RunSqlOk(BaseModel):
    status: Literal["ok"] = "ok"
    result: QueryResult
    attempt: int = Field(ge=1)


class RunSqlRetry(BaseModel):
    """Self-correctable: the model should rewrite the SQL. Shares the 2-retry budget."""

    status: Literal["retry"] = "retry"
    kind: Literal["guard_rejected", "dry_run_error", "over_bytes_cap", "runtime_error"]
    reason: RejectReason | None = Field(default=None, description="guard_rejected only.")
    error: str = Field(description="The error text, cleaned of service names.")
    hint: str | None = None
    relevant_schema: list[TableSchema] = Field(default_factory=list)
    retries_left: int = Field(ge=0)


class RunSqlEmpty(BaseModel):
    """0 rows. On the first empty result the probe ran; the model gets one more try."""

    status: Literal["empty"] = "empty"
    probe_hint: str | None = Field(
        description="\"state has 'CA', not 'California'; data ends on 3 Nov\". None if used up."
    )
    checked: list[FilterLiteral] = Field(description="What the probe checked.")
    final: bool = Field(description="True: the filters are valid, so empty is the answer.")


class RunSqlGaveUp(BaseModel):
    """The budget is spent. The model writes the graceful answer from what it has."""

    status: Literal["gave_up"] = "gave_up"
    message_class: MessageClass = MessageClass.SELF_CORRECTION_EXHAUSTED
    tried: list[str] = Field(description='Plain-language: "filtering by state", "by month".')


type RunSqlOutput = Annotated[
    RunSqlOk | RunSqlRetry | RunSqlEmpty | RunSqlGaveUp | ToolError,
    Field(discriminator="status"),
]


# --- save_report -----------------------------------------------------------------------------


class SaveReportInput(BaseModel):
    """Only when the User explicitly asked to save. Scope comes from state, not from here."""

    title: str = Field(max_length=200)
    question: str = Field(max_length=2000)
    sql: str = Field(description="The SQL that answered it, as the model wrote it.")
    markdown: str = Field(max_length=50_000, description="The answer as the User sees it.")
    structured: ReportStructured


class SaveReportOk(BaseModel):
    status: Literal["ok"] = "ok"
    report_id: UUID
    created: bool = Field(description="False: this turn already saved it (idempotent).")
    redactions: int = Field(ge=0, description="PII-filter hits removed before writing.")


class SaveReportNeedsQuery(BaseModel):
    """No ``run_sql`` succeeded this turn, so there is no guard scope to record. The model can
    fix it: run the query that answers the report in this turn, then save again."""

    status: Literal["needs_query"] = "needs_query"
    hint: str = "Run the query that answers this report with run_sql in this turn, then save."


type SaveReportOutput = Annotated[
    SaveReportOk | SaveReportNeedsQuery | ToolError, Field(discriminator="status")
]


class EntityMention(BaseModel):
    entity_type: EntityType
    value: str = Field(max_length=200, description="As written in the report.")


class ExtractedEntities(BaseModel):
    """Structured output of ``ModelStep.ENTITY_EXTRACTION`` at save time."""

    entities: list[EntityMention] = Field(default_factory=list, max_length=50)


# --- list_my_reports / get_my_report ---------------------------------------------------------


class ListReportsInput(ReportQuery):
    """Same fields as the repository query; the Author is injected."""


class ListReportsOutput(BaseModel):
    status: Literal["ok"] = "ok"
    reports: list[ReportListItem]
    total: int


class GetReportInput(BaseModel):
    report_id: UUID


class GetReportOk(BaseModel):
    status: Literal["ok"] = "ok"
    report: SavedReport
    label: str = Field(description='"data as of <saved date>"')


type GetReportOutput = Annotated[GetReportOk | ToolError, Field(discriminator="status")]
"""Not found and out of scope are ``ToolError`` with REPORT_NOT_FOUND / REPORT_OUT_OF_SCOPE.
Not found covers another User's report too: existence is never confirmed."""


# --- plan_deletion / execute_deletion (branch functions) -------------------------------------


class PlanDeletionInput(BaseModel):
    filter: DeletionFilter = Field(
        description="Structured output from the User's own message (MODEL_DELETION_FILTER)."
    )


class PlanDeletionOk(BaseModel):
    status: Literal["planned"] = "planned"
    plan: DeletionPlan


class PlanDeletionNothing(BaseModel):
    status: Literal["nothing_matched"] = "nothing_matched"
    message_class: MessageClass = MessageClass.NOTHING_MATCHED


type PlanDeletionOutput = Annotated[
    PlanDeletionOk | PlanDeletionNothing, Field(discriminator="status")
]


class ExecuteDeletionInput(BaseModel):
    plan_id: UUID
    confirmed_report_ids: list[UUID] = Field(min_length=1)


class ExecuteDeletionOk(BaseModel):
    status: Literal["deleted"] = "deleted"
    outcome: DeletionOutcome
    replayed: bool = Field(description="True: this plan was already executed; nothing changed.")


type ExecuteDeletionOutput = Annotated[ExecuteDeletionOk | ToolError, Field(discriminator="status")]
"""``ToolError(PLAN_EXPIRED)`` for a cancelled, superseded or foreign plan."""


# --- draft_persona / publish_persona / rollback_persona (branch functions) -------------------


class DraftPersonaInput(BaseModel):
    request: str = Field(max_length=2000, description="The CEO's words.")


class PersonaDraft(BaseModel):
    status: Literal["drafted"] = "drafted"
    persona: Persona
    changed_fields: list[str]
    summary: str = Field(description='"warm and encouraging, ending with 3 action items"')


class PublishPersonaInput(BaseModel):
    persona: Persona
    path: Literal[PersonaPath.CHAT, PersonaPath.DIRECT_EDIT]


class PersonaPublished(BaseModel):
    status: Literal["published"] = "published"
    active: PersonaVersion
    previous_version: int
    gate: PersonaGateResult


class PersonaRejected(BaseModel):
    status: Literal["rejected"] = "rejected"
    gate: PersonaGateResult
    current_version: int
    message_class: MessageClass = Field(
        description="PERSONA_GATE_FAILED, or MODEL_BUSY when the gate could not run."
    )


type PublishPersonaOutput = Annotated[
    PersonaPublished | PersonaRejected, Field(discriminator="status")
]


class RollbackPersonaInput(BaseModel):
    to_version: int | None = Field(default=None, ge=1, description="None: the previous version.")


class PersonaRolledBack(BaseModel):
    status: Literal["rolled_back"] = "rolled_back"
    active: PersonaVersion
    from_version: int


type RollbackPersonaOutput = Annotated[PersonaRolledBack | ToolError, Field(discriminator="status")]
"""``ToolError(PERSONA_NOTHING_TO_ROLL_BACK)`` when version 1 is active."""
