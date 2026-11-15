"""UserDirectory: the seeded Users load from ``seed/users.yaml`` into SQLite (LLD 06)."""

from pathlib import Path

from agent.adapters.users_sqlite import SqliteUserDirectory
from agent.domain.identity import BrandScope, Permission, Role

ROOT = Path(__file__).resolve().parents[1]


def test_seeded_users_load(tmp_path: Path) -> None:
    users = SqliteUserDirectory(tmp_path / "app.db", seed=ROOT / "seed" / "users.yaml")

    ceo = users.load("ceo")
    manager = users.load("manager-a")

    assert ceo is not None and ceo.role is Role.EXECUTIVE
    assert ceo.can(Permission.PERSONA_EDITOR)
    assert ceo.scope == BrandScope(all_brands=True)
    assert manager is not None and manager.role is Role.MANAGER
    assert not manager.can(Permission.PERSONA_EDITOR)
    assert manager.scope == BrandScope(brands=frozenset({"Calvin Klein", "Tommy Hilfiger"}))


def test_manager_with_no_brands_has_an_empty_scope(tmp_path: Path) -> None:
    seed = tmp_path / "users.yaml"
    seed.write_text("users:\n  - {user_id: lonely, role: manager, permissions: [], brands: []}\n")

    lonely = SqliteUserDirectory(tmp_path / "app.db", seed=seed).load("lonely")

    assert lonely is not None
    assert lonely.scope == BrandScope()


def test_unknown_user_is_refused(tmp_path: Path) -> None:
    users = SqliteUserDirectory(tmp_path / "app.db", seed=ROOT / "seed" / "users.yaml")

    assert users.load("nobody") is None


def test_reseeding_keeps_the_access_version(tmp_path: Path) -> None:
    """A restart must not look like an access change, or every Conversation loses its results."""
    seed = ROOT / "seed" / "users.yaml"
    first = SqliteUserDirectory(tmp_path / "app.db", seed=seed).load("manager-a")

    again = SqliteUserDirectory(tmp_path / "app.db", seed=seed).load("manager-a")

    assert first is not None and again is not None
    assert again.access_version == first.access_version
