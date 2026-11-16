"""UserDirectory: role, permissions and Brand Assignments (HLD 03 § Identity and roles).

Not in ADR 0006's original list; added in the LLD because the "load context" node needs a seam
between the graph and the ``users`` / ``user_permissions`` / ``brand_assignments`` tables.
Two adapters are real: the seeded ``seed/users.yaml`` + SQLite (prototype) and Postgres kept
in step by the admin sync job (production).

The seeded adapter also lists its Users for the web prototype's picker (``list_dev_users``,
LLD 07 § Identity). That is not part of this port: production never lists Users to a channel.
"""

from typing import Protocol

from agent.domain.identity import UserContext, UserId


class UserDirectory(Protocol):
    """Interface:

    - ``load`` returns the User's context for this turn, read fresh every turn so an access
      change applies at once.
    - An unknown or inactive User returns ``None``; the caller replies with ``NO_ROLE``.
      A known User with no role returns a context with ``role=None`` (default deny).
    - Raises ``TransientError(dependency="app_db", UNAVAILABLE)``: without identity the turn
      fails closed.
    """

    def load(self, user_id: UserId) -> UserContext | None: ...
