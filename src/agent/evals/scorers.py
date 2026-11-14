"""Scorers: one per check, deterministic first (HLD 07). Stubs.

Each scorer reads a ``Transcript`` (what the channel received, plus the turn traces) and the
case's expectation, and returns one ``LayerScore``. Scorers never call the agent.
"""

from typing import Protocol

from pydantic import BaseModel

from agent.contracts.channel import OutboundMessage
from agent.contracts.eval import EvalCase, LayerScore
from agent.contracts.views import TraceView


class Transcript(BaseModel):
    """Everything one trial produced. The outcome is graded from this, never from the model's
    own description of what it did."""

    case_id: str
    trial: int
    outbound: list[OutboundMessage]  # exactly what the channel got, after the PII filter
    traces: list[TraceView]
    executed_sql: list[str]  # every SQL the turn executed, as the model wrote it, in order
    store_report_ids: dict[str, bool]  # session cases: ref -> still present


class Scorer(Protocol):
    name: str

    def score(self, case: EvalCase, transcript: Transcript) -> LayerScore | None:
        """``None`` when the scorer does not apply to this case."""
        ...


class ExecutionMatch:
    """Deterministic. Runs ``reference_sql`` as the case's User (same guard, same scope) and
    passes when the result of **any** query in ``executed_sql`` matches it: order-insensitive,
    column names ignored, numbers within ``rel_tolerance``. "Any", because in a multi-step
    "why" the headline fact is often computed before the breakdown. A reference that fails to
    run is CANNOT_MEASURE."""

    name = "exec_match"

    def score(self, case: EvalCase, transcript: Transcript) -> LayerScore | None:
        raise NotImplementedError


class SafetyChecks:
    """Deterministic. Refusal class from the final ``AgentReply``; ``no_pii`` from a PII scan
    of every outbound message; ``within_scope`` from each ``run_sql`` span's guard scope;
    ``no_write`` from the store. Each check is reported on its own."""

    name = "safety"

    def score(self, case: EvalCase, transcript: Transcript) -> LayerScore | None:
        raise NotImplementedError


class StoreState:
    """Deterministic, session cases. ``deleted`` refs gone, ``kept`` refs present (including
    other Authors'), audit row count."""

    name = "store_state"

    def score(self, case: EvalCase, transcript: Transcript) -> LayerScore | None:
        raise NotImplementedError


class SectionsPresent:
    """Deterministic, report requests. Every required section heading is in the answer."""

    name = "sections"

    def score(self, case: EvalCase, transcript: Transcript) -> LayerScore | None:
        raise NotImplementedError


class RubricJudge:
    """Judged layer (``ModelStep.JUDGE``). Scores the rubric dimensions, blind to the arm.
    Reported, not gating, until calibrated against human labels (decision log § QA)."""

    name = "judge"

    def score(self, case: EvalCase, transcript: Transcript) -> LayerScore | None:
        raise NotImplementedError


class TrajectoryStats:
    """Trajectory layer, reported only: SQL attempts, retries, guard rejections, model calls,
    tokens, cost, latency, Trios retrieved."""

    name = "trajectory"

    def score(self, case: EvalCase, transcript: Transcript) -> LayerScore | None:
        raise NotImplementedError
