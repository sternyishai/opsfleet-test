"""User Profile and preference statements (HLD 05).

Precedence when answers are written, strongest first: locked safety rules, this-turn
statements, explicit standing values, inferred values, Persona defaults.
"""

from datetime import datetime
from enum import StrEnum
from typing import Any, ClassVar
from uuid import UUID, uuid4

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


_TYPED: dict[PreferenceField, type[StrEnum] | type[str]] = {
    PreferenceField.FORMAT: AnswerFormat,
    PreferenceField.DEPTH: Depth,
    PreferenceField.CHARTS: Charts,
    PreferenceField.LANGUAGE: str,
}


def merged(
    profile: UserProfile,
    statements: list[PreferenceStatement],
    *,
    at: datetime,
    max_notes: int = UserProfile.MAX_NOTES,
) -> UserProfile:
    """``profile`` with ``statements`` applied in order, each as an ``explicit`` value: the
    User said it, so it overwrites an inferred or earlier value of the same field. A value
    that is not one of the field's options is skipped (the router's mistake, not the User's
    wish). A note already present (case ignored) is not added twice; past ``max_notes`` the
    oldest notes are dropped. The one merge both the store and this turn's profile use, so
    what is saved is what was applied."""
    update: dict[str, Any] = {}
    notes = list(profile.notes)
    for s in statements:
        if s.field is PreferenceField.NOTE:
            text = s.value.strip()
            if text and all(n.text.casefold() != text.casefold() for n in notes):
                notes.append(
                    ProfileNote(note_id=uuid4(), text=text, source=Source.EXPLICIT, created_at=at)
                )
            continue
        kind = _TYPED[s.field]
        try:
            value = kind(s.value.strip())
        except ValueError:
            continue
        update[s.field.value] = {"value": value, "source": Source.EXPLICIT, "confirmed_at": at}
    update["notes"] = notes[-min(max_notes, UserProfile.MAX_NOTES) :]
    return UserProfile.model_validate(dict(profile) | update)
