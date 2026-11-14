"""HTTP bodies for the optional web channel (ADR 0010, docs/lld/07-web.md).

Like tool inputs, these never carry the User: the web adapter resolves identity from the
request (the dev picker header in the prototype, IAP in production) and builds the
``InboundMessage`` / ``ConfirmationResponse`` itself. Responses are ``OutboundMessage`` events
on a Server-Sent Events stream, so the UI needs no extra reply types.
"""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from agent.domain.identity import Role, UserId


class WebText(BaseModel):
    kind: Literal["text"] = "text"
    conversation_id: UUID | None = Field(default=None, description="None starts a Conversation.")
    text: str = Field(min_length=1, max_length=4000)


class WebConfirmation(BaseModel):
    """The Deletion Plan's checkboxes and buttons."""

    kind: Literal["confirmation"] = "confirmation"
    conversation_id: UUID
    request_id: UUID
    approved: bool
    selected_indices: list[int] | None = None


type WebRequest = Annotated[WebText | WebConfirmation, Field(discriminator="kind")]


class DevUser(BaseModel):
    """One seeded User for the prototype's identity picker. Never served in production."""

    user_id: UserId
    role: Role | None
    brands: list[str]
