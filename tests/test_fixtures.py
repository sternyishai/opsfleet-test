"""The seed Trios and eval cases are valid, and the Trios really teach method, not figures."""

import re
from pathlib import Path

import pytest
import yaml

from agent.contracts.eval import EvalCase, ExpectAnswer
from agent.domain.trios import Trio

ROOT = Path(__file__).resolve().parents[1]
TRIOS = sorted((ROOT / "seed" / "trios").glob("*.yaml"))
CASES = sorted((ROOT / "evals" / "cases").rglob("*.yaml"))
NUMBER = re.compile(r"(?<![\w@.])\d+(?:\.\d+)?%?")


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


@pytest.mark.parametrize("path", TRIOS, ids=lambda p: p.stem)
def test_seed_trio_is_valid(path: Path) -> None:
    trio = Trio.model_validate(_load(path))
    assert trio.trio_id == path.stem


@pytest.mark.parametrize("path", TRIOS, ids=lambda p: p.stem)
def test_seed_trio_has_no_figures_beyond_parameters(path: Path) -> None:
    """The ingestion figure check (HLD 02), applied to the seed: a number may appear only as
    the value of a declared parameter."""
    trio = Trio.model_validate(_load(path))
    allowed = {str(p.value) for p in trio.parameters}
    text = "\n".join([trio.question, *trio.paraphrases, trio.method_summary, trio.sql])
    strays = [n for n in NUMBER.findall(text) if n not in allowed]
    assert not strays, f"undeclared numbers: {strays}"


@pytest.mark.parametrize("path", CASES, ids=lambda p: p.stem)
def test_eval_case_is_valid(path: Path) -> None:
    case = EvalCase.model_validate(_load(path))
    assert case.case_id == path.stem
    assert case.suite.value == path.parent.name


def test_fixture_has_a_negative_control() -> None:
    cases = [EvalCase.model_validate(_load(p)) for p in CASES]
    controls = [c for c in cases if c.negative_control]
    assert controls and all(isinstance(c.expect, ExpectAnswer) for c in controls)


def test_seed_users_are_valid_and_every_case_runs_as_one() -> None:
    from agent.domain.identity import UserContext

    users = {
        u["user_id"]: UserContext(access_version=1, **u)
        for u in _load(ROOT / "seed" / "users.yaml")["users"]
    }
    for path in CASES:
        case = EvalCase.model_validate(_load(path))
        assert case.run_as in users, path.stem
        assert all(r.author_id in users for r in case.setup_reports), path.stem
