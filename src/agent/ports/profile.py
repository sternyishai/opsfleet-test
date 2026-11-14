"""ProfileStore: User Profiles (HLD 05)."""

from typing import Protocol
from uuid import UUID

from agent.domain.identity import UserId
from agent.domain.profile import PreferenceStatement, UserProfile


class ProfileStore(Protocol):
    """Interface:

    - ``load`` of an unknown User returns an empty profile, never ``None``.
    - ``save_standing`` takes only ``STANDING`` statements (raises ``ValueError`` otherwise;
      this-turn preferences are never saved). It writes them as ``explicit``, overwriting
      earlier values of the same field; at the notes cap the oldest note is dropped.
      Note text has already passed the input guard.
    - ``remove`` deletes one note (``note_id``) or, with ``None``, resets the whole profile.
    - Called off the critical path (the parallel "save to User Profile" branch), so a failure
      is traced and does not fail the turn.
    """

    def load(self, user_id: UserId) -> UserProfile: ...

    def save_standing(
        self, user_id: UserId, statements: list[PreferenceStatement]
    ) -> UserProfile: ...

    def remove(self, user_id: UserId, note_id: UUID | None = None) -> UserProfile: ...
