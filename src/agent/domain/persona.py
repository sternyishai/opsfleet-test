"""The Persona, its versions and the Persona Quality Gate result (HLD 09).

A structured form, not a free-form prompt, so every field can be checked. The Persona shapes
*how* the agent speaks, never *what* it says.
"""

from datetime import datetime
from enum import IntEnum, StrEnum
from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator

from agent.domain.identity import UserId
from agent.errors import MessageClass


class TonePreset(StrEnum):
    DIRECT = "direct"
    WARM = "warm"
    FORMAL = "formal"


class Tone(BaseModel):
    preset: TonePreset
    notes: str = Field(default="", max_length=500)


class Vocabulary(BaseModel):
    prefer: dict[str, str] = Field(
        default_factory=dict, description='Replace key with value: {"users": "Customers"}.'
    )
    avoid: list[str] = Field(default_factory=list, max_length=30)


class Persona(BaseModel):
    """The editable layer of the prompt. Locked layers (safety rules, tool and output contracts)
    live in code and cannot be overridden by anything here."""

    tone: Tone
    report_sections: list[str] = Field(min_length=1, max_length=8)
    emphasis: str = Field(default="", max_length=500)
    vocabulary: Vocabulary = Field(default_factory=Vocabulary)
    examples: list[str] = Field(default_factory=list, max_length=2)
    messages: dict[MessageClass, str] = Field(
        default_factory=dict,
        description="Catalog wording overrides. Missing classes use the bundled wording.",
    )


class PersonaPath(StrEnum):
    SEED = "seed"  # persona.yaml, version 1, also the cold-start default
    CHAT = "chat"  # the CEO asked in chat; a model drafted the fields
    DIRECT_EDIT = "direct_edit"  # /persona edit, or the Admin UI form


class PersonaVersion(BaseModel):
    version: int = Field(ge=1)
    persona: Persona
    created_by: UserId | None = Field(description="None for the seed.")
    created_at: datetime
    path: PersonaPath
    changed_fields: list[str] = Field(default_factory=list)
    is_active: bool


class PersonaEvent(BaseModel):
    """One row of the Persona's audit: who, when, which path and what happened (HLD 09)."""

    kind: Literal["published", "rejected", "rolled_back"]
    actor_id: UserId
    occurred_at: datetime
    path: PersonaPath | None = Field(description="None for a rollback.")
    from_version: int | None
    to_version: int | None = Field(description="None for a rejection.")


class GateCheck(IntEnum):
    """The five checks, in the order they run. Cheap code checks first."""

    FIELD_LIMITS = 1
    INJECTION_SCAN = 2
    SAFETY_HOLDS = 3  # red-team cases through the answer-writing step
    SECTIONS_PRESENT = 4
    FIGURES_UNCHANGED = 5


class GateCheckResult(BaseModel):
    check: GateCheck
    passed: bool
    reason: str | None = Field(default=None, description="Plain words, with a hint, on failure.")
    detail: str | None = Field(default=None, description="For the trace only.")


class PersonaGateResult(BaseModel):
    """Pass or fail, and the result and reason of each check that ran.

    Checks stop at the first failure, so a failed result may list fewer than five checks.
    """

    passed: bool
    checks: list[GateCheckResult]
    could_not_run: bool = Field(
        default=False,
        description="The gate could not run (e.g. model down): fail closed.",
    )

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.passed and (self.could_not_run or len(self.checks) != len(GateCheck)):
            raise ValueError("a passing result has run all five checks")
        if self.passed != all(c.passed for c in self.checks):
            raise ValueError("passed must equal all(check.passed)")
        return self

    @property
    def first_failure(self) -> GateCheckResult | None:
        return next((c for c in self.checks if not c.passed), None)
