"""What a tool receives besides its model-facing input (ADR 0007: identity is a state read)."""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from uuid import UUID

from agent.config import Limits
from agent.contracts.channel import Channel
from agent.domain.identity import BrandScope, UserContext

if TYPE_CHECKING:
    from agent.composition import Ports


@dataclass(frozen=True, slots=True)
class TurnContext:
    """Built by the graph from ``AgentState`` for every tool call. The model never sees it.

    ``user`` is the only source of the Author and the Brand scope for every tool.
    """

    user: UserContext
    conversation_id: UUID
    turn_id: str
    channel: Channel
    """Recorded with the Conversation when its first report is saved."""
    ports: "Ports"
    limits: Limits


@dataclass(frozen=True, slots=True)
class SqlBudget:
    """The self-correction counters for one turn, read from and written back to graph state."""

    retries_used: int = 0
    diagnoses_used: int = 0
    attempts: int = 0
    last_scope: BrandScope | None = None
    refused_brand: bool = False
    """An out-of-scope Brand was already refused this turn; asking again ends the turn."""
    tried: tuple[str, ...] = field(default_factory=tuple)
