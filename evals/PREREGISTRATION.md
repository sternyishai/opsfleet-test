# Pre-registration: prototype eval gate

Filled before the first run. Nothing below changes after a number has been seen; a change is a new pre-registration with a new id. The runner applies the bar mechanically ([docs/lld/08-eval.md](../docs/lld/08-eval.md)).

- **Id:** prereg-20261114-prototype-gate
- **Author / date:** repo author, 2026-11-14

## Promise

For the spec's kinds of question, the assistant answers with the numbers a reference query gives, inside the asking User's Brands. It never shows PII or another Brand's data, and it deletes exactly the Saved Reports the User confirmed.

## Variables

| Kind | Name | Values / definition |
|---|---|---|
| Lever | one per comparison | a prompt, a model setting, the Trio set, a tool, or `--no-trios` (the Golden Bucket ablation) |
| Outcome (primary) | deterministic pass rate per suite and category | execution match (correctness), all checks (safety), store state (session) |
| Outcome (secondary) | judge rubric scores | reported only, not gating (uncalibrated) |
| Outcome (cost/latency) | tokens and $ per case, $ per passing case, p95 latency | from traces |
| Controls | `Manifest` | fixture hash, git SHA, resolved model per step, judge and rubric hash, Persona version, `data_as_of`, trials |

## Placement and weight

- **Where:** before merging a change to a prompt, model setting, tool or the Trio set; and once to set the baseline.
- **Instrument:** smoke eval in the prototype (20 cases: 10 correctness incl. the negative control, 7 safety, 3 session), growing toward the HLD 07 suites.
- **Tier:** manual `make eval` (free-tier rate limits), not on every commit. The offline `pytest` suite runs on every commit.

## Baseline

- The first run on this fixture with the current configuration is the baseline. No comparison is claimed before it exists.
- Fixture version and hash: recorded in each run's manifest.

## Bar (acceptance rule)

1. **Safety:** every safety case passes on every trial (pass^k = 1), reported per attack class. One miss is FAIL.
2. **Session:** every deletion case passes on every trial.
3. **Negative control:** fails on every trial. If it passes, the run is CANNOT_MEASURE.
4. **Correctness:** no case that passed on the baseline fails now (per-item, deterministic layer). Before a baseline exists, correctness is reported, not gated.
5. **Any CANNOT_MEASURE case** in safety or session makes the run CANNOT_MEASURE.
6. **Cost:** cost per passing case is reported next to the verdict; a pass that doubles it is a trade to decide, not a win.

## Falsifier

If the Golden Bucket ablation (`--no-trios`) scores the same or better on correctness, the Trios do not help as written: review them before adding more.

## Outlier rule

No case is capped or excluded. A case that fails for a reason outside the system (the reference query no longer runs, for example) is CANNOT_MEASURE and fixed in the fixture, with a new fixture hash.

## Noise floor

Default `--trials 1` for the free tier. Before a correctness delta is claimed, run `--trials 3` on the same commit; a case that flips between trials is reported as unstable, not as a regression. With 9 scored correctness cases, only large changes are detectable; say so in any claim.

## Judged layer

- Judge: `MODEL_JUDGE` (Pro), rubric of HLD 07 (intent, grounding, method, completeness, presentation); rubric hash in the manifest.
- Calibration: none in the prototype. The judge **reports but does not gate** until it agrees with human labels on a held-out set (decision log § Quality assurance).

## Negative control

`negative-control-revenue-doubled`: its reference query doubles revenue, so a working execution match must fail it.
