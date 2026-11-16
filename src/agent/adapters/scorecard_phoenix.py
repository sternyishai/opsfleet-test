"""``ScorecardSink`` to Arize Phoenix over its REST API (``/v1``), with the standard library
only (docs/lld/08-eval.md § Phoenix).

- The cases become a **dataset** named after the fixture hash, created once: the same
  fixture is the same dataset, so two runs on it are two experiments side by side. Cases a
  subset run (``--suite``, ``--case``) brings that the dataset lacks are appended to it.
- A run becomes an **experiment** named after the run ID, with the manifest, the outcome and
  the bar's reasons as metadata.
- Each trial becomes an experiment **run** linked to its turn's trace (the IDs ``Tracer``
  sends to Phoenix), and each of its layer scores an **evaluation** on it, plus one
  evaluation per check (``retrieval.in_scope``) so each check can be compared on its own.

A Phoenix that is down or answers with an error raises ``OSError`` here; the ``FanOutSink``
reports it and the JSON file is still written. ``baseline`` is the JSON sink's job.
"""

import json
from datetime import UTC
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from agent.contracts.eval import EvalCase, Layer, LayerScore, Outcome, Scorecard


class PhoenixScorecardSink:
    def __init__(self, endpoint: str, timeout_s: float = 5.0) -> None:
        self._base = endpoint.rstrip("/")
        self._timeout = timeout_s

    def publish(self, cases: list[EvalCase], scorecard: Scorecard) -> None:
        name = f"retail-eval-{scorecard.manifest.fixture_hash}"
        dataset_id = self._dataset(cases, name)
        examples = self._examples(dataset_id)
        if missing := [c for c in cases if c.case_id not in examples]:
            self._upload("append", name, missing)
            examples = self._examples(dataset_id)
        experiment = self._call(
            "POST",
            f"/v1/datasets/{dataset_id}/experiments",
            {
                "name": scorecard.run_id,
                "description": f"outcome {scorecard.outcome.value}",
                "metadata": {
                    **scorecard.manifest.model_dump(mode="json"),
                    "outcome": scorecard.outcome.value,
                    "reasons": scorecard.reasons,
                },
                "repetitions": scorecard.manifest.trials,
            },
        )["data"]["id"]
        at = scorecard.started_at.astimezone(UTC).isoformat()
        for case in scorecard.cases:
            example = examples.get(case.case_id)
            if example is None:
                continue
            for trial in case.trials:
                run_id = self._call(
                    "POST",
                    f"/v1/experiments/{experiment}/runs",
                    {
                        "dataset_example_id": example,
                        "output": {
                            "outcome": trial.outcome.value,
                            "checks": {s.scorer: s.checks for s in trial.layers},
                        },
                        "repetition_number": trial.trial,
                        "start_time": at,
                        "end_time": at,
                        "trace_id": _otel_trace_id(trial.trace_ids),
                        "error": trial.error,
                    },
                )["data"]["id"]
                for score in trial.layers:
                    for name, outcome in _evaluations(score):
                        self._call(
                            "POST",
                            "/v1/experiment_evaluations",
                            {
                                "experiment_run_id": run_id,
                                "name": name,
                                "annotator_kind": "LLM" if score.layer is Layer.JUDGED else "CODE",
                                "start_time": at,
                                "end_time": at,
                                "result": {
                                    "label": outcome.value,
                                    "score": _score(outcome),
                                    "explanation": score.reason,
                                },
                                "metadata": {"layer": score.layer.value, **score.values},
                            },
                        )

    def baseline(
        self,
        run_id: str | None = None,
        *,
        fixture_hash: str,
        covering: frozenset[str] = frozenset(),
    ) -> Scorecard | None:
        return None

    def _dataset(self, cases: list[EvalCase], name: str) -> str:
        found = self._call("GET", f"/v1/datasets?name={quote(name)}")["data"]
        if found:
            return found[0]["id"]
        return self._upload("create", name, cases)

    def _examples(self, dataset_id: str) -> dict[str, str]:
        """Case ID -> example ID."""
        found = self._call("GET", f"/v1/datasets/{dataset_id}/examples")["data"]["examples"]
        return {e["metadata"].get("case_id"): e["id"] for e in found}

    def _upload(self, action: str, name: str, cases: list[EvalCase]) -> str:
        """``create`` a dataset of ``cases``, or ``append`` them to it (never deletes)."""
        uploaded = self._call(
            "POST",
            "/v1/datasets/upload?sync=true",
            {
                "action": action,
                "name": name,
                "description": "evals/cases, versioned by fixture hash",
                "inputs": [
                    {"run_as": c.run_as, "turns": c.turns, "suite": c.suite.value} for c in cases
                ],
                "outputs": [{"expect": c.expect.model_dump(mode="json")} for c in cases],
                "metadata": [
                    {
                        "case_id": c.case_id,
                        "category": c.category.value,
                        "negative_control": c.negative_control,
                    }
                    for c in cases
                ],
            },
        )
        return uploaded["data"]["dataset_id"]

    def _call(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        request = Request(
            self._base + path,
            data=json.dumps(body).encode() if body is not None else None,
            method=method,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        try:
            with urlopen(request, timeout=self._timeout) as response:
                return json.loads(response.read() or b"null")
        except HTTPError as exc:
            raise OSError(f"Phoenix answered {exc.code} to {method} {path}") from exc
        except (URLError, TimeoutError) as exc:
            raise OSError(f"Phoenix did not answer at {self._base}") from exc


def _otel_trace_id(trace_ids: list[str]) -> str | None:
    """Our 12-hex trace ID as the 32-hex OTel ID the tracer exported it under."""
    return f"{int(trace_ids[0], 16):032x}" if trace_ids else None


def _evaluations(score: LayerScore) -> list[tuple[str, Outcome]]:
    """The scorer's own evaluation, then one per check it reports."""
    checks = [
        (f"{score.scorer}.{check}", Outcome.PASS if ok else Outcome.FAIL)
        for check, ok in score.checks.items()
    ]
    return [(score.scorer, score.outcome), *checks]


def _score(outcome: Outcome) -> float | None:
    return {Outcome.PASS: 1.0, Outcome.FAIL: 0.0}.get(outcome)
