"""PersonaStore over the bundled ``persona.yaml``: version 1 only, read once (HLD 09 § Seed).

Publishing, rejections and history beyond version 1 need the SQLite store (slice 7).
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from agent.domain.identity import UserId
from agent.domain.persona import Persona, PersonaPath, PersonaVersion


class FilePersonaStore:
    def __init__(self, path: Path) -> None:
        raw = yaml.safe_load(path.read_text())
        version = raw.pop("version")
        self._active = PersonaVersion(
            version=version,
            persona=Persona.model_validate(raw),
            created_by=None,
            created_at=datetime.fromtimestamp(path.stat().st_mtime, UTC),
            path=PersonaPath.SEED,
            is_active=True,
        )

    def active(self) -> PersonaVersion:
        return self._active

    def history(self) -> list[PersonaVersion]:
        return [self._active]

    def publish(self, persona: Persona, **_: Any) -> PersonaVersion:
        raise NotImplementedError("publishing needs the SQLite Persona store (slice 7)")

    def record_rejection(self, persona: Persona, **_: Any) -> None:
        raise NotImplementedError("rejections need the SQLite Persona store (slice 7)")

    def rollback(self, *, by: UserId, to_version: int | None = None) -> PersonaVersion:
        raise LookupError("version 1 is the only version")
