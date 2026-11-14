"""Messages between a channel and the Chat API (HLD 01 § How components communicate).

Inbound: ``InboundMessage`` (a question, or a reply to a confirmation prompt), and
``ConfirmationResponse`` when a channel has buttons. Outbound: ``ProgressEvent``,
``ConfirmationRequest`` and ``AgentReply``, all ``OutboundMessage``. Every outbound message
leaves through ``agent.core.outbound.Outbound``, which runs the output PII filter first.
"""

from datetime import date, datetime
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from agent.domain.identity import UserId
from agent.errors import MessageClass


class Channel(StrEnum):
    CLI = "cli"
    SLACK = "slack"
    TEAMS = "teams"
    WEB = "web"


class InboundMessage(BaseModel):
    """One User message. Identity is resolved by the channel (SSO; ``--as`` in the CLI)."""

    user_id: UserId
    channel: Channel
    conversation_id: UUID | None = Field(
        description="None starts a new Conversation; the Chat API creates the UUID."
    )
    text: str = Field(min_length=1, max_length=4000)
    received_at: datetime


class ConfirmationResponse(BaseModel):
    """A structured reply from a channel with buttons (Slack, Teams, web).

    The CLI sends plain text instead, which ``agent.core.confirmation.parse_reply`` reads.
    Both resolve to the same ``ConfirmationDecision``.
    """

    user_id: UserId
    conversation_id: UUID
    request_id: UUID = Field(description="The plan or send it answers; stale IDs are expired.")
    approved: bool
    selected_indices: list[int] | None = Field(
        default=None, description="Checked boxes; None means all items of the request."
    )


class _Outbound(BaseModel):
    conversation_id: UUID
    trace_id: str = Field(description="Also the reference ID shown with errors.")
    sent_at: datetime


class ProgressEvent(_Outbound):
    """A templated phrase plus tool and table names, never free model text.

    Passes the regex detectors only, so it adds no Sensitive Data Protection call (HLD 03).
    """

    kind: Literal["progress"] = "progress"
    template: str = Field(description='Catalog key, e.g. "comparing_by".')
    text: str = Field(max_length=200, description='Rendered: "Comparing spend by state…"')


class ConfirmationItem(BaseModel):
    index: int = Field(ge=1)
    label: str
    reason: str | None = None


class ConfirmationRequest(_Outbound):
    """The graph is paused at ``interrupt()``. Today only the Deletion Plan uses it."""

    kind: Literal["confirmation"] = "confirmation"
    request_id: UUID = Field(description="The Deletion Plan ID.")
    text: str = Field(description="Intro and how to answer.")
    items: list[ConfirmationItem] = Field(max_length=20, description="At most the first 20.")
    total: int = Field(ge=1)
    require_count: bool = Field(
        description='Large plan: confirming all needs the count ("yes, delete all 37").'
    )


class AgentReply(_Outbound):
    """The final message of a turn."""

    kind: Literal["reply"] = "reply"
    markdown: str
    message_class: MessageClass | None = Field(
        default=None, description="Set when the reply is a catalog message (refusal or error)."
    )
    data_as_of: date | None = None


type OutboundMessage = Annotated[
    ProgressEvent | ConfirmationRequest | AgentReply, Field(discriminator="kind")
]
