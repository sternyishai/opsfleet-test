"""HTTP bodies for the optional web channel (ADR 0010, docs/lld/07-web.md).

Like tool inputs, these never carry the User: the web adapter resolves identity from the
request (the dev picker header in the prototype, IAP in production) and builds the
``InboundMessage`` / ``ConfirmationResponse`` itself. Responses are ``OutboundMessage`` events
on a Server-Sent Events stream, so the UI needs no extra reply types.
"""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from agent.domain.identity import Role, UserId
from agent.errors import MessageClass


class WebText(BaseModel):
    model_config = ConfigDict(extra="forbid")  # a body that names a User is refused (422)

    kind: Literal["text"] = "text"
    conversation_id: UUID | None = Field(default=None, description="None starts a Conversation.")
    text: str = Field(min_length=1, max_length=4000)


class WebConfirmation(BaseModel):
    """The Deletion Plan's checkboxes and buttons."""

    model_config = ConfigDict(extra="forbid")

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
    brands: list[str] = Field(description="Brand Assignments; empty for an Executive (all).")


class ApiError(BaseModel):
    """The body of every refused request. A refused view carries its catalog message, as the
    CLI prints it; a request with no identity, a missing turn or a bad window has none."""

    message_class: MessageClass | None = None
    message: str
