"""The package dependency rule of LLD 01 § Packages, read from the source.

ruff's banned-api keeps provider SDKs and ``agent.adapters`` out of the core; this test keeps
the layers inside ``agent`` in order. In slice 7b the review, not a check, found ``tools/``
importing ``core`` and ``graph``. Imports under ``if TYPE_CHECKING:`` count too: they are
still a dependency a reader has to follow.
"""

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "agent"

LAYERS = ("errors", "domain", "contracts", "ports")
"""Bottom up: each may import only the ones before it."""

FORBIDDEN: dict[str, set[str]] = {
    "tools": {"core", "graph", "evals", "adapters"},
    "adapters": {"core", "graph", "tools", "evals"},
}
"""Top-level ``agent`` packages a package above the bottom layers must not import."""

CHANNEL_ADAPTERS = {"web"}
"""``adapters/web`` is a channel: it calls ``ChatService`` and ``Views`` in ``core`` (LLD 07)."""


def imported_packages(source: str) -> set[str]:
    """Top-level ``agent`` packages a module imports."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        names: list[str] = []
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names = [node.module]
        elif isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        found |= {n.split(".")[1] for n in names if n.startswith("agent.")}
    return found


def violations(relative: Path, source: str) -> set[str]:
    """What a module at ``relative`` (to ``src/agent``) imports against the rule."""
    parts = relative.with_suffix("").parts
    package = parts[0]
    imported = imported_packages(source) - {package}
    if package in LAYERS:
        return imported - set(LAYERS[: LAYERS.index(package)])
    forbidden = set(FORBIDDEN.get(package, set()))
    if package == "adapters" and len(parts) > 2 and parts[1] in CHANNEL_ADAPTERS:
        forbidden.discard("core")
    if package != "cli":
        forbidden.add("cli")  # the entry point; nothing imports it
    return imported & forbidden


SOURCES = sorted(SRC.rglob("*.py"))


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: str(p.relative_to(SRC)))
def test_imports_follow_the_package_rule(path: Path) -> None:
    relative = path.relative_to(SRC)
    assert violations(relative, path.read_text()) == set()


@pytest.mark.parametrize(
    ("relative", "source", "expected"),
    [
        (
            "tools/x.py",
            "from agent.core.catalog import render\nimport agent.graph.prompts",
            {"core", "graph"},
        ),
        ("domain/x.py", "from agent.contracts.tools import RunSqlInput", {"contracts"}),
        ("ports/x.py", "from agent.domain.identity import UserId", set()),
        ("adapters/bigquery.py", "from agent.core.chat import ChatService", {"core"}),
        ("adapters/web/app.py", "from agent.core.chat import ChatService", set()),
        ("graph/x.py", "import agent.cli", {"cli"}),
    ],
)
def test_the_rule_flags_what_lld_01_forbids(
    relative: str, source: str, expected: set[str]
) -> None:
    assert violations(Path(relative), source) == expected
