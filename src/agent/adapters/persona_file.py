"""PersonaStore over the bundled ``persona.yaml``: version 1 only, read once (HLD 09 § Seed).

The SQLite store reads its seed and cold-start default through this one. Publishing and
rejections need the SQLite store.
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from agent.domain.identity import UserId
from agent.domain.persona import Persona, PersonaEvent, PersonaPath, PersonaVersion


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
        raise NotImplementedError("publishing needs the SQLite Persona store")

    def record_rejection(self, persona: Persona, **_: Any) -> None:
        raise NotImplementedError("rejections need the SQLite Persona store")

    def rollback(self, *, by: UserId, to_version: int | None = None) -> PersonaVersion:
        raise LookupError("version 1 is the only version")

    def events(self) -> list[PersonaEvent]:
        return []
