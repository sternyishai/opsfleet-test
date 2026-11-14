"""The input guard + intent router output (HLD 01 § Agent graph, HLD 03, ADR 0008).

One Flash-Lite call (``MODEL_ROUTER``) with a locked prompt returns a ``RouterDecision`` through
structured output. Cheap deterministic rules (length, known injection phrases, encoded
payloads) run before the call and can block without it.
"""

from enum import StrEnum
from typing import Self

from pydantic import BaseModel, Field, model_validator

from agent.domain.profile import PreferenceStatement


class Verdict(StrEnum):
    ALLOWED = "allowed"
    OFF_TOPIC = "off_topic"
    PII_REQUEST = "pii_request"
    INJECTION = "injection"


class Action(StrEnum):
    """The turn's single action. ``deliver`` joins this list with the first Delivery adapter
    (ADR 0009); until then there are four."""

    ANALYSIS = "analysis"  # questions, report requests, list/open own reports, schema questions
    DELETE_REPORTS = "delete_reports"
    CHANGE_PERSONA = "change_persona"
    PREFERENCE_ONLY = "preference_only"


class PersonaOp(StrEnum):
    CHANGE = "change"  # "make the reports warmer"
    UNDO = "undo"  # "undo the tone change"


class RouterDecision(BaseModel):
    verdict: Verdict
    action: Action | None = Field(description="None when the verdict blocks the turn.")
    search_question: str | None = Field(
        default=None,
        max_length=500,
        description=(
            "analysis only: a standalone question for retrieve_trios that carries earlier "
            'context ("and NY?" -> "why are NY users underspending compared to CA?").'
        ),
    )
    persona_op: PersonaOp | None = Field(default=None, description="change_persona only.")
    preferences: list[PreferenceStatement] = Field(
        default_factory=list,
        max_length=10,
        description="Any message may carry these, alongside any action.",
    )
    deferred_request: str | None = Field(
        default=None,
        max_length=2000,
        description=(
            "When a delete or Persona change is mixed with another request, the other request, "
            "run after the User answers (HLD 01). Its text is also its Trio search question."
        ),
    )

    @model_validator(mode="after")
    def _shape(self) -> Self:
        blocked = self.verdict is not Verdict.ALLOWED
        if blocked != (self.action is None):
            raise ValueError("an action is set exactly when the verdict is allowed")
        if (self.action is Action.ANALYSIS) != (self.search_question is not None):
            raise ValueError("search_question is set exactly for analysis")
        if (self.action is Action.CHANGE_PERSONA) != (self.persona_op is not None):
            raise ValueError("persona_op is set exactly for change_persona")
        if self.deferred_request and self.action not in (
            Action.DELETE_REPORTS,
            Action.CHANGE_PERSONA,
        ):
            raise ValueError("only a delete or a Persona change defers another request")
        if self.action is Action.PREFERENCE_ONLY and not self.preferences:
            raise ValueError("preference_only needs at least one preference")
        return self
