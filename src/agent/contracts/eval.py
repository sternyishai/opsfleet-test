"""Eval cases, results and the scorecard (HLD 07, docs/lld/08-eval.md).

Measurement rules these types encode:

- **Three-way outcome.** Every trial, case and run is PASS, FAIL or CANNOT_MEASURE. A harness
  crash, a missing baseline or a fixture mismatch is CANNOT_MEASURE, never FAIL or PASS.
- **Layers are reported separately.** Deterministic checks, the judge and the trajectory each
  have their own score; there is no blended number.
- **Outcome before trajectory.** The verdict comes from the end state (numbers, refusal class,
  remaining reports). How the agent got there (attempts, retries, tokens) is reported next to
  it, never folded into it.
- **Reliability.** Each case runs ``k`` trials; ``pass@k`` and ``pass^k`` are both reported.
- **Negative control.** At least one case is known-bad and must FAIL, or the run is
  CANNOT_MEASURE: a gate that cannot go red proves nothing.
"""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, Field, model_validator

from agent.domain.identity import UserId
from agent.domain.reports import ReportStructured
from agent.errors import MessageClass


class Suite(StrEnum):
    CORRECTNESS = "correctness"  # end-to-end analysis answers
    SAFETY = "safety"  # red team, 100% per attack class
    SESSION = "session"  # multi-turn flows graded on end state (deletion)
    STEP = "step"  # one model step on its own labelled input


class Category(StrEnum):
    """The spec's capability list (correctness) and the attack classes (safety, HLD 03)."""

    # correctness
    CUSTOMER_BEHAVIOR = "customer_behavior"
    PRODUCT_PERFORMANCE = "product_performance"
    TIME_BASED = "time_based"
    DATA_QUESTION = "data_question"
    MULTI_STEP = "multi_step"
    REPORT_REQUEST = "report_request"
    # safety
    PROMPT_INJECTION = "prompt_injection"
    PII_REQUEST = "pii_request"
    CROSS_BRAND = "cross_brand"
    DESTRUCTIVE_SQL = "destructive_sql"
    COST_ATTACK = "cost_attack"
    INDIRECT_INJECTION = "indirect_injection"
    OTHER_USERS_REPORTS = "other_users_reports"
    # session
    DELETION = "deletion"
    # step
    ROUTER = "router"
    DELETION_FILTER = "deletion_filter"


class Source(StrEnum):
    SEED = "seed"  # written from the spec
    HOLDOUT_TRIO = "holdout_trio"  # a Trio kept out of retrieval (HLD 07)
    FAILURE = "failure"  # a real failure, added before its fix
    RED_TEAM = "red_team"


# --- expectations: what "right" means, written before any run --------------------------------


class ResultMatch(BaseModel):
    """Execution match: the reference runs as the case's User; it must match the result of
    any query the agent executed in the turn."""

    order_insensitive: bool = True
    ignore_column_names: bool = True
    subset_columns: bool = Field(
        default=True,
        description="Each reference column must match one agent column; extra agent columns "
        "(a share, a margin) are allowed. Rows must match exactly.",
    )
    rel_tolerance: float = Field(default=0.005, ge=0, description="Per numeric cell.")


class ExpectAnswer(BaseModel):
    kind: Literal["answer"] = "answer"
    reference_sql: str = Field(description="Run through the same guard and scope as the agent.")
    match: ResultMatch = Field(default_factory=ResultMatch)
    required_sections: list[str] = Field(
        default_factory=list, description="Report requests: sections the Persona requires."
    )
    rubric: list[str] = Field(
        default_factory=lambda: ["intent", "grounding", "method", "completeness", "presentation"],
        description="Judge dimensions scored (reported; gating only once calibrated).",
    )


class ExpectRefusal(BaseModel):
    """The right answer is to refuse, or to answer without the forbidden content."""

    kind: Literal["refusal"] = "refusal"
    message_classes: list[MessageClass] | None = Field(
        description="The turn must end with one of these catalog classes; None: any answer is "
        "allowed, as long as the checks below hold (e.g. 'top customers by user_id')."
    )
    no_pii: bool = True  # no PII detector hit in any outbound message
    within_scope: bool = True  # every executed query's guard scope is inside the User's
    no_write: bool = True  # no Saved Report written or deleted


class SeedReport(BaseModel):
    """A Saved Report placed in the fresh store before a session case starts."""

    ref: str = Field(description='Name used in expectations, e.g. "nike_q1".')
    author_id: UserId
    this_conversation: bool = Field(description="Saved in the session's Conversation.")
    structured: ReportStructured
    markdown: str
    entities: list[str] = Field(default_factory=list)


class ExpectDeletion(BaseModel):
    """Outcome grading on the store, after every turn has run."""

    kind: Literal["deletion"] = "deletion"
    deleted: list[str] = Field(description="Refs that must be gone.")
    kept: list[str] = Field(description="Refs that must remain, including other Authors'.")
    audit_rows: int = Field(ge=0, description="Rows in deletion_audit afterwards.")


class ExpectStep(BaseModel):
    """A per-step eval: one model call, structured output compared field by field."""

    kind: Literal["step"] = "step"
    step: str = Field(description="A ModelStep value, e.g. 'router'.")
    expected: dict[str, object] = Field(
        description="Fields that must equal; fields not listed are ignored."
    )


type Expectation = Annotated[
    ExpectAnswer | ExpectRefusal | ExpectDeletion | ExpectStep, Field(discriminator="kind")
]


class EvalCase(BaseModel):
    """One item of the fixture. Files: ``evals/cases/<suite>/<case_id>.yaml``."""

    case_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    suite: Suite
    category: Category
    source: Source
    run_as: UserId = Field(description="A seeded User; the right answer depends on scope.")
    turns: list[str] = Field(min_length=1, description="User messages, in order.")
    setup_reports: list[SeedReport] = Field(default_factory=list)
    expect: Expectation
    negative_control: bool = Field(
        default=False, description="Known-bad: the grader must return FAIL on every run."
    )
    notes: str | None = None

    @model_validator(mode="after")
    def _fits_suite(self) -> Self:
        allowed = {
            Suite.CORRECTNESS: ExpectAnswer,
            Suite.SAFETY: ExpectRefusal,
            Suite.SESSION: ExpectDeletion,
            Suite.STEP: ExpectStep,
        }[self.suite]
        if not isinstance(self.expect, allowed):
            raise ValueError(f"a {self.suite} case expects {allowed.__name__}")
        if self.suite is not Suite.SESSION and self.setup_reports:
            raise ValueError("only session cases seed reports")
        return self


# --- results -----------------------------------------------------------------------------


class Outcome(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    CANNOT_MEASURE = "cannot_measure"


class Layer(StrEnum):
    DETERMINISTIC = "deterministic"  # execution match, refusal class, PII scan, scope, store
    JUDGED = "judged"  # rubric judge (MODEL_JUDGE)
    TRAJECTORY = "trajectory"  # attempts, retries, tokens, cost, latency: reported only


class LayerScore(BaseModel):
    layer: Layer
    outcome: Outcome
    checks: dict[str, bool] = Field(default_factory=dict, description='{"exec_match": true}')
    values: dict[str, float] = Field(default_factory=dict, description='{"sql_attempts": 2}')
    reason: str | None = None


class TrialResult(BaseModel):
    trial: int = Field(ge=1)
    trace_ids: list[str]
    layers: list[LayerScore]
    outcome: Outcome = Field(description="From the deterministic layer only, in the prototype.")
    error: str | None = Field(default=None, description="Set when CANNOT_MEASURE.")


class CaseResult(BaseModel):
    case_id: str
    suite: Suite
    category: Category
    trials: list[TrialResult]
    pass_at_k: bool = Field(description="At least one trial passed.")
    pass_all_k: bool = Field(description="pass^k: every trial passed.")
    outcome: Outcome = Field(description="The gate's view: pass^k, CANNOT_MEASURE if any is.")


class Manifest(BaseModel):
    """Everything that must be equal for two runs to be compared. A mismatch -> CANNOT_MEASURE."""

    git_sha: str
    fixture_hash: str = Field(description="Hash of every case file and seed Trio.")
    prereg_id: str
    models: dict[str, str] = Field(description="Resolved ModelStep -> provider:model.")
    judge: str | None
    rubric_hash: str | None
    persona_version: int
    trios_enabled: bool = Field(description="The Golden Bucket ablation lever.")
    data_as_of: str
    trials: int = Field(ge=1)


class Rate(BaseModel):
    passed: int
    total: int
    low: float = Field(description="Wilson 95% interval, lower bound.")
    high: float


class SuiteSummary(BaseModel):
    suite: Suite
    by_category: dict[Category, Rate] = Field(
        description="Safety is reported per attack class, never blended."
    )
    cannot_measure: int


class Scorecard(BaseModel):
    run_id: str
    started_at: datetime
    manifest: Manifest
    cases: list[CaseResult]
    suites: list[SuiteSummary]
    negative_control_failed: bool = Field(description="Must be True, or the run is CANNOT_MEASURE.")
    cost_usd: float
    tokens: int
    p95_latency_s: float
    baseline_run_id: str | None = None
    regressions: list[str] = Field(
        default_factory=list, description="Case IDs that passed on the baseline and fail now."
    )
    outcome: Outcome = Field(description="The bar in evals/PREREGISTRATION.md, applied.")
