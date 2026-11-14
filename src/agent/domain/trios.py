"""Golden Trios in their stored form (HLD 02, ADR 0005).

Stored Trios teach method, not figures: measured results and identifiers are removed from all
three parts; numbers survive only as declared ``parameters``.
"""

from enum import StrEnum

from pydantic import BaseModel, Field

from agent.domain.identity import BrandScope


class TrioStatus(StrEnum):
    ACTIVE = "active"  # the only status that is retrieved
    SUPERSEDED = "superseded"
    RETIRED = "retired"  # SQL no longer compiles (schema drift)


class QuestionType(StrEnum):
    COMPARISON = "comparison"
    TREND = "trend"
    CHURN = "churn"
    COHORT = "cohort"
    SEGMENTATION = "segmentation"
    OTHER = "other"


class TrioParameter(BaseModel):
    """A definition value that is part of the method, e.g. ``churn_window_days = 90``."""

    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    value: float | int | str
    unit: str | None = None


class Trio(BaseModel):
    trio_id: str
    version: int = Field(ge=1)
    question: str = Field(description="Embedded. Brand names replaced by a placeholder.")
    paraphrases: list[str] = Field(default_factory=list, description="Also embedded.")
    sql: str = Field(description="Payload. Brand literals replaced by a query parameter.")
    method_summary: str = Field(description="Payload. How the analyst reasoned, no results.")
    parameters: list[TrioParameter] = Field(default_factory=list)
    scope: BrandScope = Field(
        description="Brands the Trio's data covers; all_brands when tagging was not confident."
    )
    tables_used: list[str]
    question_type: QuestionType
    status: TrioStatus
    source_uri: str | None = None
    approved_by: str | None = None

    @property
    def ref(self) -> str:
        """``trio_id@version``, as recorded in traces."""
        return f"{self.trio_id}@{self.version}"


class TrioMatch(BaseModel):
    """What the analysis model sees: a delimited reference block, never instructions."""

    ref: str = Field(description="trio_id@version")
    score: float = Field(ge=0, le=1)
    question: str
    method_summary: str
    sql: str
    parameters: list[TrioParameter]
    tables_used: list[str]


class TrioSearchResult(BaseModel):
    matches: list[TrioMatch] = Field(description="Above the threshold, best first.")
    dropped_below_threshold: int = Field(ge=0, description="For the trace.")
