"""PersonaStore: versioned Persona with one active pointer (HLD 09)."""

from typing import Protocol

from agent.domain.identity import UserId
from agent.domain.persona import (
    Persona,
    PersonaEvent,
    PersonaGateResult,
    PersonaPath,
    PersonaVersion,
)


class PersonaStore(Protocol):
    """Interface:

    - ``active`` never raises: on a read failure it returns the last cached version, and on a
      cold start with no cache the bundled ``persona.yaml`` (version 1). Cached ~60 s in
      production; read every turn in the prototype.
    - ``publish`` refuses a gate result that did not pass (``ValueError``): the invariant
      "nothing goes live without the gate" lives at this seam, not only in the caller. It
      creates a new immutable version, moves the active pointer, and records the event, all in
      one transaction.
    - ``record_rejection`` writes the "who, when, which path, why" of a failed gate, with no new
      version.
    - ``rollback`` re-activates ``to_version`` (default: the version active before the current
      one) without re-running the gate. Raises ``LookupError`` when there is nothing to roll
      back to. Undo walks back along what was active, so repeated undos reach version 1 and
      then raise.
    - ``events`` is the audit, oldest first: every publish, rejection and rollback.
    - Versions are never deleted. A write that fails raises ``TransientError``.
    """

    def active(self) -> PersonaVersion: ...

    def history(self) -> list[PersonaVersion]: ...

    def publish(
        self,
        persona: Persona,
        *,
        by: UserId,
        path: PersonaPath,
        changed_fields: list[str],
        gate: PersonaGateResult,
    ) -> PersonaVersion: ...

    def record_rejection(
        self, persona: Persona, *, by: UserId, path: PersonaPath, gate: PersonaGateResult
    ) -> None: ...

    def rollback(self, *, by: UserId, to_version: int | None = None) -> PersonaVersion: ...

    def events(self) -> list[PersonaEvent]: ...
