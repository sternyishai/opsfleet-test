"""Who is asking, and what they may see (HLD 03 § Identity and roles).

``UserContext`` is loaded by code from the app DB at the start of every turn and put into graph
state. Nothing the User types and nothing the model outputs can change it.
"""

from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

type UserId = str
"""Internal User ID (``ceo``, ``manager-a`` in the prototype). Never a name or an email."""

type Brand = str
"""A ``products.brand`` value from ``thelook``."""


class Role(StrEnum):
    EXECUTIVE = "executive"  # may analyze all Brands
    MANAGER = "manager"  # may analyze only their Brand Assignments


class Permission(StrEnum):
    """Actions beyond analysis. Today there is one."""

    PERSONA_EDITOR = "persona_editor"


class BrandScope(BaseModel):
    """The Brands a query or a Trio may touch.

    ``all_brands=True`` is an Executive's scope; otherwise ``brands`` is the exact set.
    An empty Manager scope is legal and means "no data" (default deny).
    """

    model_config = ConfigDict(frozen=True)

    all_brands: bool = False
    brands: frozenset[Brand] = frozenset()

    @model_validator(mode="after")
    def _all_means_no_list(self) -> Self:
        if self.all_brands and self.brands:
            raise ValueError("an all-Brands scope carries no Brand list")
        return self

    def covers(self, other: "BrandScope") -> bool:
        """True when every Brand in ``other`` is inside this scope."""
        if self.all_brands:
            return True
        if other.all_brands:
            return False
        return other.brands <= self.brands


class UserContext(BaseModel):
    """The User's identity for one turn. Immutable for the turn."""

    model_config = ConfigDict(frozen=True)

    user_id: UserId
    role: Role | None = Field(description="None means no role: the User gets no data.")
    permissions: frozenset[Permission] = frozenset()
    brands: frozenset[Brand] = Field(
        default=frozenset(), description="Brand Assignments. Ignored for Executives."
    )
    access_version: int = Field(
        ge=0,
        description=(
            "Bumped on every change to role or Brand Assignments. A change since the "
            "Conversation's last turn drops earlier results from the prompt (HLD 03)."
        ),
    )

    @property
    def scope(self) -> BrandScope:
        if self.role is Role.EXECUTIVE:
            return BrandScope(all_brands=True)
        if self.role is Role.MANAGER:
            return BrandScope(brands=self.brands)
        return BrandScope()  # default deny

    def can(self, permission: Permission) -> bool:
        return permission in self.permissions
