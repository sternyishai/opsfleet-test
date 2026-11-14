"""ScorecardSink: where an eval run's dataset, results and scorecard go (HLD 07, HLD 08)."""

from typing import Protocol

from agent.contracts.eval import EvalCase, Scorecard


class ScorecardSink(Protocol):
    """Interface:

    - ``publish`` stores one run. Adapters: a JSON file in ``evals/runs/<run_id>.json``
      (always), and Phoenix when it is running: the cases become a Phoenix **dataset**
      versioned by ``manifest.fixture_hash``, the run becomes an **experiment** with the
      manifest as metadata, and each case's layer scores become its evaluations.
    - ``baseline`` returns the run a new run is compared with (by ID, or the latest run with
      the same fixture hash). ``None`` when there is none: a regression gate then returns
      CANNOT_MEASURE, never PASS.
    - Never fails the run: a Phoenix outage is reported, and the JSON file is still written.
    """

    def publish(self, cases: list[EvalCase], scorecard: Scorecard) -> None: ...

    def baseline(self, run_id: str | None = None, *, fixture_hash: str) -> Scorecard | None: ...
