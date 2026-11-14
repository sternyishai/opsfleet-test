"""User Profile and preference statements (HLD 05).

Precedence when answers are written, strongest first: locked safety rules, this-turn
statements, explicit standing values, inferred values, Persona defaults.
"""

from datetime import datetime
from enum import StrEnum
from typing import Any, ClassVar
from uuid import UUID

from pydantic import BaseModel, Field

from agent.domain.identity import UserId


class AnswerFormat(StrEnum):
    TABLE = "table"
    BULLETS = "bullets"
    PROSE = "prose"


class Depth(StrEnum):
    HEADLINE = "headline"
    STANDARD = "standard"
    DEEP_DIVE = "deep_dive"


class Charts(StrEnum):
    NEVER = "never"
    WHEN_HELPFUL = "when_helpful"
    ALWAYS = "always"


class Source(StrEnum):
    EXPLICIT = "explicit"  # the User said it; never overwritten by an inferred value
    INFERRED = "inferred"  # the daily inference job (not in the prototype)


class ProfileValue[T](BaseModel):
    value: T
    source: Source
    confidence: float = Field(ge=0, le=1, default=1.0)
    confirmed_at: datetime


class ProfileNote(BaseModel):
    """A short free-text preference. Passes the input guard; injected as data."""

    note_id: UUID
    text: str = Field(max_length=200)
    source: Source
    created_at: datetime


class UserProfile(BaseModel):
    MAX_NOTES: ClassVar[int] = 20

    user_id: UserId
    format: ProfileValue[AnswerFormat] | None = None
    depth: ProfileValue[Depth] | None = None
    charts: ProfileValue[Charts] | None = None
    language: ProfileValue[str] | None = None
    attributes: dict[str, ProfileValue[Any]] = Field(
        default_factory=dict, description="Extensible kinds, e.g. default_period."
    )
    notes: list[ProfileNote] = Field(default_factory=list, max_length=20)


class PreferenceScope(StrEnum):
    STANDING = "standing"  # "from now on, tables": applied now and saved
    THIS_TURN = "this_turn"  # "this time, just bullets": applied now, never saved


class PreferenceField(StrEnum):
    FORMAT = "format"
    DEPTH = "depth"
    CHARTS = "charts"
    LANGUAGE = "language"
    NOTE = "note"  # anything without a typed field


class PreferenceStatement(BaseModel):
    """One preference the router extracted from the User's message."""

    scope: PreferenceScope
    field: PreferenceField
    value: str = Field(
        max_length=200,
        description="An enum value for typed fields, the note text for NOTE.",
    )
