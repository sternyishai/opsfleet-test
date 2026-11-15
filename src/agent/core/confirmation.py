"""Reading the User's reply to a Deletion Plan (HLD 04 § The confirmation flow).

Pure code, no model: an ambiguous reply never deletes anything.
"""

import re
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
    - A ``ConfirmationResponse`` for another ``request_id`` or User -> ``NotAnAnswer``
      (expired). Its ``selected_indices`` become the excluded rest; ``None`` means all.
    - Case, surrounding whitespace and one trailing full stop are ignored; nothing else is
      guessed ("y", "sure" and "yes please" are not answers).
    """
    if isinstance(reply, ConfirmationResponse):
        return _parse_button(reply, plan)
    text = _normalized(reply)
    if text == "no":
        return Decline()
    if text == "yes":
        return NeedsCount() if plan.is_large else ConfirmAll()
    if match := _DELETE_ALL.fullmatch(text):
        return ConfirmAll() if int(match["count"]) == plan.total else NotAnAnswer()
    if match := _EXCEPT.fullmatch(text):
        return _except({int(n) for n in re.findall(r"\d+", match["indices"])}, plan)
    return NotAnAnswer()


def looks_like_reply(text: str) -> bool:
    """True for text shaped like an answer to a plan (``yes``, ``no``, ``yes except 2``,
    ``yes, delete all 37``), whatever the numbers. Used when no plan is open."""
    text = _normalized(text)
    return text in ("yes", "no") or bool(_DELETE_ALL.fullmatch(text) or _EXCEPT.fullmatch(text))


def _normalized(text: str) -> str:
    return text.strip().lower().removesuffix(".").strip()


_DELETE_ALL = re.compile(r"yes,?\s+delete\s+all\s+(?P<count>\d+)")
_EXCEPT = re.compile(r"yes,?\s+except\s+(?P<indices>\d+(?:\s*(?:,|\band\b|\s)\s*\d+)*)")


def _except(excluded: set[int], plan: DeletionPlan) -> ConfirmationDecision:
    """Every index must be in the plan, and at least one item must be left to delete."""
    if not excluded <= set(range(1, plan.total + 1)) or len(excluded) == plan.total:
        return NotAnAnswer()
    return ConfirmExcept(excluded=sorted(excluded))


def _parse_button(response: ConfirmationResponse, plan: DeletionPlan) -> ConfirmationDecision:
    if response.request_id != plan.plan_id or response.user_id != plan.author_id:
        return NotAnAnswer()
    if not response.approved:
        return Decline()
    if response.selected_indices is None:
        return NeedsCount() if plan.is_large else ConfirmAll()
    selected = set(response.selected_indices)
    everything = set(range(1, plan.total + 1))
    if not selected or not selected <= everything:
        return NotAnAnswer()
    if selected == everything:
        return ConfirmAll()
    return _except(everything - selected, plan)
