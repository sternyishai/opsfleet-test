"""Reading the User's reply to a Deletion Plan (HLD 04 § The confirmation flow). Stub.

Pure code, no model: an ambiguous reply never deletes anything.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, Field

from agent.contracts.channel import ConfirmationResponse
from agent.domain.reports import DeletionPlan


class ConfirmAll(BaseModel):
    kind: Literal["confirm_all"] = "confirm_all"


class ConfirmExcept(BaseModel):
    kind: Literal["confirm_except"] = "confirm_except"
    excluded: list[int] = Field(min_length=1, description="1-based plan indices.")


class Decline(BaseModel):
    kind: Literal["decline"] = "decline"


class NeedsCount(BaseModel):
    """A bare "yes" to a large plan: re-prompt once for the count instead of cancelling."""

    kind: Literal["needs_count"] = "needs_count"


class NotAnAnswer(BaseModel):
    """Anything else: cancel the plan and re-enter the graph at the input guard."""

    kind: Literal["not_an_answer"] = "not_an_answer"


type ConfirmationDecision = Annotated[
    ConfirmAll | ConfirmExcept | Decline | NeedsCount | NotAnAnswer, Field(discriminator="kind")
]


def parse_reply(reply: str | ConfirmationResponse, plan: DeletionPlan) -> ConfirmationDecision:
    """Interface:

    - ``yes`` -> ``ConfirmAll``; ``no`` -> ``Decline``; ``yes except 2,3`` -> ``ConfirmExcept``.
    - Large plan (``plan.is_large``): only ``yes, delete all <total>`` with the exact count is
      ``ConfirmAll``; a bare ``yes`` is ``NeedsCount``.
    - Indices outside the plan, excluding every item, or any other text -> ``NotAnAnswer``.
    - A ``ConfirmationResponse`` for another ``request_id`` -> ``NotAnAnswer`` (expired).
    - Case and surrounding whitespace are ignored; nothing else is guessed.
    """
    raise NotImplementedError
