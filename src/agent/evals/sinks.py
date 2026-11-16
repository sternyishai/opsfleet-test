"""``FanOutSink``: one ``ScorecardSink`` over several, so a run publishes to the JSON file
and to Phoenix and never fails because one of them is down (docs/lld/08-eval.md § Phoenix)."""

import logging

from agent.contracts.eval import EvalCase, Scorecard
from agent.ports.scorecards import ScorecardSink

_log = logging.getLogger(__name__)


class FanOutSink:
    """``publish`` calls every sink in order; one that raises is logged and listed in
    ``failures`` (its class name), and the others still run. ``baseline`` asks each sink in
    order and returns the first answer; a sink that raises counts as no answer."""

    def __init__(self, sinks: list[ScorecardSink]) -> None:
        self._sinks = sinks
        self.failures: list[str] = []

    def publish(self, cases: list[EvalCase], scorecard: Scorecard) -> None:
        self.failures = []
        for sink in self._sinks:
            name = type(sink).__name__
            try:
                sink.publish(cases, scorecard)
            except Exception as exc:
                self.failures.append(name)
                _log.warning("%s did not take the run (%s); the others did", name, exc)

    def baseline(
        self,
        run_id: str | None = None,
        *,
        fixture_hash: str,
        covering: frozenset[str] = frozenset(),
    ) -> Scorecard | None:
        for sink in self._sinks:
            try:
                found = sink.baseline(run_id, fixture_hash=fixture_hash, covering=covering)
            except Exception:
                continue
            if found is not None:
                return found
        return None
