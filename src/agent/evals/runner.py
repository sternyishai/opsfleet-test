"""EvalRunner: runs the fixture through the same entry point Users hit. Stub."""

from agent.composition import Ports
from agent.contracts.eval import EvalCase, Outcome, Scorecard
from agent.evals.scorers import Scorer
from agent.ports.scorecards import ScorecardSink


def load_cases(root: str = "evals/cases") -> list[EvalCase]:
    """Parse and validate every ``*.yaml`` under ``root``. An invalid file stops the run."""
    raise NotImplementedError


class EvalRunner:
    """Interface:

    - ``run`` sends each case's ``turns`` through ``ChatService.handle`` with a capturing
      ``ChannelAdapter``, so the eval tests exactly what the CLI and the UI get.
    - Every trial starts from a fresh app store seeded from ``seed/`` plus the case's
      ``setup_reports`` (no state leaks between trials) and a pinned Persona version.
    - Before the cases: the environment preflight (seeded Users load, BigQuery dry run of every
      reference SQL, the negative control is present). A failure ends the run CANNOT_MEASURE.
    - A crash, timeout or rate-limit exhaustion inside a trial is CANNOT_MEASURE for that
      trial, recorded with its error, never FAIL.
    - Applies the bar in ``evals/PREREGISTRATION.md`` to produce ``Scorecard.outcome``, then
      ``ScorecardSink.publish``. Exit code of ``agent eval``: 0 PASS, 1 FAIL, 2 CANNOT_MEASURE.
    - Rate-limited for the Gemini free tier through the same ``LLMFactory``.
    """

    def __init__(self, ports: Ports, scorers: list[Scorer], sink: ScorecardSink) -> None:
        self._ports = ports
        self._scorers = scorers
        self._sink = sink

    def run(
        self,
        cases: list[EvalCase],
        *,
        trials: int = 1,
        trios_enabled: bool = True,
        baseline_run_id: str | None = None,
    ) -> Scorecard:
        raise NotImplementedError


def exit_code(outcome: Outcome) -> int:
    return {Outcome.PASS: 0, Outcome.FAIL: 1, Outcome.CANNOT_MEASURE: 2}[outcome]
