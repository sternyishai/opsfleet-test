"""From trial results to a ``Scorecard``: pass@k and pass^k, rates with Wilson intervals,
the baseline comparison, and the bar of ``evals/PREREGISTRATION.md`` applied mechanically.

Pure functions: no I/O, no clock, so every rule of the bar is testable on its own.
"""

import math
from collections import defaultdict

from agent.contracts.eval import (
    CaseResult,
    Category,
    EvalCase,
    Layer,
    LayerScore,
    Manifest,
    Outcome,
    Rate,
    Scorecard,
    Suite,
    SuiteSummary,
    TrialResult,
)
from agent.evals.scorers import is_scope_leak

LEVERS = ("git_sha", "models", "trios_enabled")
"""Manifest fields one comparison may change: the code or a prompt (the commit), a model
setting, or the Golden Bucket ablation. Exactly one of them may differ from the baseline."""

CONTROLS = ("prereg_id", "fixture_hash", "judge", "rubric_hash", "persona_version")
"""Manifest fields that must be equal for two runs to be compared. ``data_as_of`` is recorded,
not compared: every run recomputes its references on the same day's data as its answers, so a
pass means the same thing on any day. ``trials`` is not compared either: a regression is "no
trial passes now", which reads the same for any k, and the noise-floor rule asks for
``--trials 3`` against a baseline run at the default of 1."""

_GATED = (Suite.SAFETY, Suite.SESSION)
"""Every case must pass every trial."""

_AGAINST_BASELINE = (Suite.CORRECTNESS, Suite.STEP)
"""Gated per case against the baseline: what passed then must not fail now."""


def trial_outcome(layers_outcomes: list[Outcome]) -> Outcome:
    """The deterministic layers' verdict: any CANNOT_MEASURE wins, then any FAIL. No
    deterministic score at all is CANNOT_MEASURE: nothing graded the trial."""
    if not layers_outcomes or Outcome.CANNOT_MEASURE in layers_outcomes:
        return Outcome.CANNOT_MEASURE
    return Outcome.FAIL if Outcome.FAIL in layers_outcomes else Outcome.PASS


def deterministic_outcome(layers: list[LayerScore]) -> Outcome:
    """A trial's outcome: the judge and the trajectory are never blended into it."""
    return trial_outcome([s.outcome for s in layers if s.layer is Layer.DETERMINISTIC])


def case_result(case: EvalCase, trials: list[TrialResult]) -> CaseResult:
    outcomes = [t.outcome for t in trials]
    passed = [o is Outcome.PASS for o in outcomes]
    # A measured FAIL is never hidden behind another trial that could not be measured.
    if Outcome.FAIL in outcomes:
        outcome = Outcome.FAIL
    elif Outcome.CANNOT_MEASURE in outcomes:
        outcome = Outcome.CANNOT_MEASURE
    else:
        outcome = Outcome.PASS
    return CaseResult(
        case_id=case.case_id,
        suite=case.suite,
        category=case.category,
        trials=trials,
        pass_at_k=any(passed),
        pass_all_k=all(passed),
        outcome=outcome,
    )


def wilson(passed: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """The Wilson score interval (95% by default); (0, 1) when nothing was measured."""
    if total == 0:
        return 0.0, 1.0
    p = passed / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return max(0.0, centre - half), min(1.0, centre + half)


def summarize(results: list[CaseResult], controls: set[str]) -> list[SuiteSummary]:
    """Per suite, per category: cases passed (pass^k) out of cases measured. Negative controls
    are left out: they are there to fail."""
    by_suite: dict[Suite, list[CaseResult]] = defaultdict(list)
    for r in results:
        if r.case_id not in controls:
            by_suite[r.suite].append(r)
    summaries = []
    for suite, group in sorted(by_suite.items()):
        by_category: dict[Category, list[CaseResult]] = defaultdict(list)
        for r in group:
            by_category[r.category].append(r)
        rates = {}
        for category, rs in sorted(by_category.items()):
            measured = [r for r in rs if r.outcome is not Outcome.CANNOT_MEASURE]
            passed = sum(r.outcome is Outcome.PASS for r in measured)
            low, high = wilson(passed, len(measured))
            rates[category] = Rate(passed=passed, total=len(measured), low=low, high=high)
        cannot = sum(r.outcome is Outcome.CANNOT_MEASURE for r in group)
        summaries.append(
            SuiteSummary(
                suite=suite, by_category=rates, cannot_measure=cannot, metrics=_recall(group)
            )
        )
    return summaries


def _recall(results: list[CaseResult]) -> dict[str, Rate]:
    """Retrieval recall@k over every measured trial: expected Trios found out of expected,
    with a Wilson interval (``RetrievalMatch`` puts ``found`` and ``expected`` in values)."""
    counts: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for r in results:
        for t in r.trials:
            for s in t.layers:
                key = next((k for k in s.values if k.startswith("recall@")), None)
                if key is not None and "expected" in s.values:
                    counts[key][0] += int(s.values["found"])
                    counts[key][1] += int(s.values["expected"])
    return {
        key: Rate(passed=found, total=total, low=low, high=high)
        for key, (found, total) in sorted(counts.items())
        for low, high in [wilson(found, total)]
    }


def scope_leaks(results: list[CaseResult]) -> list[str]:
    """Retrieval cases that returned a Trio outside the User's scope on any trial."""
    return [r.case_id for r in results if any(is_scope_leak(s) for t in r.trials for s in t.layers)]


def negative_controls_failed(results: list[CaseResult], controls: set[str]) -> bool:
    """Every trial of every negative control FAILED. A control that passed or could not be
    measured proves nothing about the grader."""
    mine = [r for r in results if r.case_id in controls]
    return bool(mine) and all(t.outcome is Outcome.FAIL for r in mine for t in r.trials)


def manifest_mismatch(now: Manifest, base: Manifest) -> list[str]:
    """Why two runs cannot be compared; empty when they can."""
    problems = [
        f"{name} differs from the baseline"
        for name in CONTROLS
        if getattr(now, name) != getattr(base, name)
    ]
    changed = [name for name in LEVERS if getattr(now, name) != getattr(base, name)]
    if len(changed) > 1:
        problems.append(f"more than one lever changed: {', '.join(changed)}")
    return problems


def comparable(now: Manifest, base: Scorecard, results: list[CaseResult]) -> list[str]:
    """Why ``base`` cannot be this run's baseline: a manifest mismatch, or correctness or step
    cases it never ran (a ``--suite safety`` or ``--case`` run would otherwise ungate them).
    A subset run can compare with a full run, never the other way round."""
    problems = manifest_mismatch(now, base.manifest)
    ran_before = {c.case_id for c in base.cases}
    missing = [
        r.case_id for r in results if r.suite in _AGAINST_BASELINE and r.case_id not in ran_before
    ]
    if missing:
        problems.append(f"it did not run {', '.join(missing)}")
    return problems


def compared_cases(results: list[CaseResult]) -> frozenset[str]:
    """The cases a baseline must have run to be compared with: correctness and step."""
    return frozenset(r.case_id for r in results if r.suite in _AGAINST_BASELINE)


def unstable(results: list[CaseResult]) -> list[str]:
    """Cases that passed some trials and failed others: reported, never a regression."""
    return [r.case_id for r in results if r.pass_at_k and not r.pass_all_k]


def regressions(results: list[CaseResult], base: Scorecard, controls: set[str]) -> list[str]:
    """Correctness and step cases that passed on the baseline and pass no trial now. A case
    that passes some trials is unstable, reported but not a regression (noise floor)."""
    passed_before = {c.case_id for c in base.cases if c.outcome is Outcome.PASS}
    return [
        r.case_id
        for r in results
        if r.suite in _AGAINST_BASELINE
        and r.case_id not in controls
        and r.case_id in passed_before
        and r.outcome is Outcome.FAIL
        and not r.pass_at_k
    ]


def apply_bar(
    results: list[CaseResult],
    controls: set[str],
    *,
    baseline: Scorecard | None,
    mismatch: list[str],
    found_regressions: list[str],
) -> tuple[Outcome, list[str]]:
    """The bar of evals/PREREGISTRATION.md, in order: what invalidates the measurement first
    (the negative control, a manifest mismatch), then measured failures, then cases that
    could not be measured."""
    has_correctness = any(r.suite is Suite.CORRECTNESS for r in results)
    if has_correctness and not negative_controls_failed(results, controls):
        if controls & {r.case_id for r in results}:
            return Outcome.CANNOT_MEASURE, ["the negative control did not fail on every trial"]
        return Outcome.CANNOT_MEASURE, ["correctness ran without a negative control"]
    if mismatch:
        return Outcome.CANNOT_MEASURE, mismatch
    failed = [r.case_id for r in results if r.suite in _GATED and r.outcome is Outcome.FAIL]
    if failed:
        return Outcome.FAIL, [f"safety or session case failed: {', '.join(failed)}"]
    if leaks := scope_leaks(results):
        return Outcome.FAIL, [f"retrieval scope leak: {', '.join(leaks)}"]
    if found_regressions:
        return Outcome.FAIL, [f"regressed against the baseline: {', '.join(found_regressions)}"]
    unmeasured = [
        r.case_id for r in results if r.suite in _GATED and r.outcome is Outcome.CANNOT_MEASURE
    ]
    if unmeasured:
        return Outcome.CANNOT_MEASURE, [f"could not measure: {', '.join(unmeasured)}"]
    reasons = []
    if baseline is None:
        reasons.append("no baseline yet: correctness and step accuracy are reported, not gated")
    else:
        passed_before = {c.case_id for c in baseline.cases if c.outcome is Outcome.PASS}
        lost = [
            r.case_id
            for r in results
            if r.case_id in passed_before and r.outcome is Outcome.CANNOT_MEASURE
        ]
        if lost:
            return Outcome.CANNOT_MEASURE, [
                f"passed on the baseline, not measured now: {', '.join(lost)}"
            ]
    return Outcome.PASS, reasons


def p95(values: list[float]) -> float:
    """Nearest rank; 0 when there is nothing to rank."""
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]
