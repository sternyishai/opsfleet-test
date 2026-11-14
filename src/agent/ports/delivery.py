"""Delivery: outbound destinations for the ``deliver`` branch (ADR 0009). **Not wired.**

Designed, not built: the branch and its router action are added together with the first
adapter. The Protocol is here so the seam is concrete when that happens. Only
``agent.core.outbound.Outbound`` may call it, after the output PII filter.
"""

from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel

from agent.domain.identity import UserId
from agent.domain.reports import SavedReport


class Destination(StrEnum):
    EMAIL = "email"
    SLACK = "slack"
    PUSH = "push"


class DeliveryOutcome(BaseModel):
    destination: Destination
    sent: bool
    duplicate: bool  # the idempotency key was already sent
    detail: str | None = None


class Delivery(Protocol):
    """One adapter per destination. The recipient is resolved from ``author_id`` by the adapter,
    never passed in. ``idempotency_key`` is (report ID, report version, destination)."""

    destination: Destination

    def render(self, report: SavedReport) -> str:
        """Template per destination; no model call, no side effects."""
        ...

    def send(
        self, author_id: UserId, rendered: str, *, idempotency_key: str
    ) -> DeliveryOutcome: ...
