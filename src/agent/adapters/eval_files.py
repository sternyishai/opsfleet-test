"""The eval's files: case YAML, the fixture hash, the git SHA, the pre-registration ID, and
the JSON ``ScorecardSink`` (``evals/runs/<run_id>.json``, docs/lld/08-eval.md § Phoenix).

The JSON file is the baseline store: it works with no server, and it is always written.
"""

import hashlib
import logging
import re
import subprocess
from pathlib import Path

import yaml
from pydantic import ValidationError

from agent.contracts.eval import EvalCase, Outcome, Scorecard

_log = logging.getLogger(__name__)


def read_case_files(root: Path) -> list[tuple[str, object]]:
    """Every ``*.yaml`` under ``root``, parsed, keyed by its path relative to ``root``
    (``safety/destructive-sql.yaml``), in a stable order."""
    return [
        (path.relative_to(root).as_posix(), yaml.safe_load(path.read_text()))
        for path in sorted(root.rglob("*.yaml"))
    ]


def fixture_hash(cases_root: Path, trios_root: Path) -> str:
    """One hash over every case file and seed Trio, names included: any change to the
    fixture is a new hash, so runs on different fixtures never compare."""
    digest = hashlib.sha256()
    for root in (cases_root, trios_root):
        for path in sorted(root.rglob("*.yaml")):
            digest.update(path.relative_to(root.parent).as_posix().encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


def git_sha(repo: Path) -> str:
    """The short commit, ``-dirty`` when the working tree has changes; ``unknown`` outside
    a git checkout."""
    try:
        sha = _git(repo, "rev-parse", "--short", "HEAD")
        dirty = bool(_git(repo, "status", "--porcelain", "--untracked-files=no"))
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return f"{sha}-dirty" if dirty else sha


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True, timeout=10
    ).stdout.strip()


def prereg_id(path: Path) -> str:
    """The ``**Id:**`` line of ``evals/PREREGISTRATION.md``."""
    match = re.search(r"\*\*Id:\*\*\s*(\S+)", path.read_text())
    if match is None:
        raise ValueError(f"{path} has no **Id:** line")
    return match.group(1)


class JsonScorecardSink:
    """``ScorecardSink`` over a folder of ``<run_id>.json`` files.

    ``baseline(run_id)`` reads that run; with no ID, the latest run on the same fixture hash
    whose outcome was PASS, so a failed run never becomes the bar the next one is held to.
    Unreadable files are skipped."""

    def __init__(self, runs_dir: Path) -> None:
        self._dir = runs_dir

    def publish(self, cases: list[EvalCase], scorecard: Scorecard) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._dir / f"{scorecard.run_id}.json"
        path.write_text(scorecard.model_dump_json(indent=2) + "\n")
        _log.info("scorecard written to %s", path)

    def baseline(self, run_id: str | None = None, *, fixture_hash: str) -> Scorecard | None:
        if run_id is not None:
            return self._read(self._dir / f"{run_id}.json")
        runs = [card for path in sorted(self._dir.glob("*.json")) if (card := self._read(path))]
        same = [
            card
            for card in runs
            if card.manifest.fixture_hash == fixture_hash and card.outcome is Outcome.PASS
        ]
        return max(same, key=lambda card: card.started_at) if same else None

    def _read(self, path: Path) -> Scorecard | None:
        try:
            return Scorecard.model_validate_json(path.read_text())
        except (OSError, ValidationError):
            return None
