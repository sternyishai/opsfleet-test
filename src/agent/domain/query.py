"""Query guard decisions and query results (HLD 03 § Query guard, HLD 06 § SQL self-correction)."""

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from agent.domain.identity import BrandScope

type TableName = Literal["orders", "order_items", "products", "users"]
"""The allowlist. Other ``thelook`` tables are rejected by name."""


class RejectReason(StrEnum):
    UNPARSEABLE = "unparseable"  # fail closed
    NOT_SINGLE_SELECT = "not_single_select"  # DML, DDL, scripting, multiple statements
    TABLE_NOT_ALLOWED = "table_not_allowed"
    COLUMN_NOT_ALLOWED = "column_not_allowed"  # PII columns, other tables' columns
    ORDER_LEVEL_FIELD = "order_level_field"  # orders.num_of_item for a Manager
    BRAND_OUT_OF_SCOPE = "brand_out_of_scope"  # rejected, never silently filtered
    NO_ROLE = "no_role"  # default deny


class FilterLiteral(BaseModel):
    """A literal the SQL filters on, read from the AST. Input to the empty-result probe."""

    table: TableName
    column: str
    operator: str = Field(description='"=", "IN", "LIKE", ">=", ...')
    value: str


class GuardAllowed(BaseModel):
    decision: Literal["allowed"] = "allowed"
    rewritten_sql: str = Field(
        description="Every table replaced by a scoped subquery; LIMIT forced."
    )
    params: dict[str, Any] = Field(description="Bound query parameters, e.g. {'brands': [...]}.")
    scope: BrandScope = Field(description="What the rewritten query can touch; saved with reports.")
    tables: list[TableName]
    filters: list[FilterLiteral]
    rewritten: bool = Field(description="False only when the rewrite was a no-op projection.")


class GuardRejected(BaseModel):
    decision: Literal["rejected"] = "rejected"
    reason: RejectReason
    detail: str = Field(description="For the model's retry and the trace.")
    hint: str | None = Field(
        default=None,
        description='e.g. "basket size: COUNT(*) per order_id on order_items".',
    )
    out_of_scope_brands: list[str] = Field(default_factory=list)


type GuardDecision = Annotated[GuardAllowed | GuardRejected, Field(discriminator="decision")]


class DryRunResult(BaseModel):
    bytes_processed: int = Field(ge=0)
    over_cap: bool


class QueryResult(BaseModel):
    """Rows from one executed statement. Rows go to the model for this step only; the
    message history and the checkpoint keep a ``ResultSummary`` (HLD 01 § Conversation state)."""

    columns: list[str]
    rows: list[dict[str, Any]] = Field(description="Capped at settings.sql_rows_to_model.")
    row_count: int = Field(ge=0, description="Before the cap.")
    truncated: bool
    bytes_billed: int = Field(ge=0)


class ResultSummary(BaseModel):
    """The compact form that survives the turn: columns, row count, key figures."""

    columns: list[str]
    row_count: int
    key_figures: dict[str, str] = Field(default_factory=dict, max_length=20)
