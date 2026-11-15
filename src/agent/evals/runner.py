"""EvalRunner: runs the fixture through the same entry point Users hit (docs/lld/08-eval.md).

Each trial gets a fresh app store, a fresh checkpointer and the process-wide ports (the
same rate-limited ``LLMFactory``, warehouse and tracer for the whole run), and sends the
case's turns through ``ChatService.handle`` with a capturing ``ChannelAdapter``. The guard
and the warehouse are wrapped, at their ports, to record what was executed and what it
returned: grading reads only what left the system.
"""

import logging
from collections.abc import Callable, Iterable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from pydantic import ValidationError

from agent.composition import Ports
from agent.config import Limits
from agent.contracts.channel import (
    AgentReply,
    Channel,
    ConfirmationRequest,
    InboundMessage,
    OutboundMessage,
)
from agent.contracts.eval import (
    CaseResult,
    EvalCase,
    ExecutedQuery,
    ExpectAnswer,
    Layer,
    LayerScore,
    Manifest,
    Outcome,
    Scorecard,
    Transcript,
    TrialResult,
)
from agent.core.chat import ChatService
from agent.domain.identity import BrandScope, UserContext
from agent.domain.query import DryRunResult, GuardAllowed, GuardDecision, QueryResult
from agent.domain.reports import EntityType, ReportEntity, SavedReport, normalize_entity
from agent.errors import MessageClass
from agent.evals import scorecard as bar
from agent.evals.scorers import Scorer, is_turn, rubric_hash
from agent.graph.build import build_graph
from agent.graph.state import CHECKPOINT_TYPES
from agent.ports.query import QueryGuard, QueryRunner
from agent.ports.reports import ReportRepository
from agent.ports.scorecards import ScorecardSink
from agent.ports.users import UserDirectory

_log = logging.getLogger(__name__)

_INFRASTRUCTURE = frozenset(
    {
        MessageClass.MODEL_BUSY,
        MessageClass.DATA_SOURCE_DOWN,
        MessageClass.UNAVAILABLE,
        MessageClass.QUOTA_REACHED,
        MessageClass.UNCLASSIFIED,
    }
)
"""A turn that ends in one of these measured the infrastructure, not the agent: the trial is
CANNOT_MEASURE (a crash, a timeout, exhausted rate limits), never FAIL."""


def load_cases(files: Iterable[tuple[str, object]]) -> list[EvalCase]:
    """Validate parsed case files (``evals/cases/<suite>/<case_id>.yaml`` and its content)
    against ``EvalCase``. The file name must be the case ID and the folder its suite; IDs are
    unique. An invalid file stops the run (``ValueError`` naming the file)."""
    cases: list[EvalCase] = []
    for path, data in files:
        parts = path.replace("\\", "/").split("/")
        try:
            case = EvalCase.model_validate(data)
        except ValidationError as exc:
            raise ValueError(f"{path}: {exc.error_count()} invalid field(s)\n{exc}") from exc
        stem = parts[-1].rsplit(".", 1)[0]
        if case.case_id != stem or (len(parts) > 1 and case.suite.value != parts[-2]):
            raise ValueError(f"{path}: case_id and suite must match the file name and folder")
        cases.append(case)
    ids = [c.case_id for c in cases]
    if duplicates := sorted({i for i in ids if ids.count(i) > 1}):
        raise ValueError(f"duplicate case IDs: {', '.join(duplicates)}")
    return cases


@dataclass(frozen=True, slots=True)
class TrialStore:
    """One trial's app store, empty but for the seeded Users. ``report_ids`` and
    ``audit_rows`` read across Authors, which no port does: they are for grading only."""

    users: UserDirectory
    reports: ReportRepository
    report_ids: Callable[[], frozenset[UUID]]
    audit_rows: Callable[[], int]


@dataclass(frozen=True, slots=True)
class EvalEnvironment:
    """What the composition root resolved for a run. ``ports`` are shared by every trial
    except ``users`` and ``reports``, which come from ``fresh_store`` per trial."""

    ports: Ports
    limits: Limits
    fresh_store: Callable[[], AbstractContextManager[TrialStore]]
    git_sha: str
    fixture_hash: str
    prereg_id: str
    models: dict[str, str]
    judge: str | None
    trios_enabled: bool
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))


class EvalRunner:
    """Interface:

    - ``run`` sends each case's ``turns`` through ``ChatService.handle`` with a capturing
      ``ChannelAdapter``, so the eval tests exactly what the CLI and the UI get.
    - Every trial starts from a fresh app store seeded from ``seed/`` plus the case's
      ``setup_reports`` (no state leaks between trials) and the pinned Persona version.
    - Before the cases: the environment preflight (every case's Users load, every reference
      SQL passes the guard and a BigQuery dry run, the negative control is present when
      correctness runs). A failure ends the run CANNOT_MEASURE with no case run.
    - A crash, timeout or rate-limit exhaustion inside a trial is CANNOT_MEASURE for that
      trial, recorded with its error, never FAIL.
    - A trial's outcome comes from the deterministic scorers only; the judge and the
      trajectory are recorded next to it.
    - Applies the bar in ``evals/PREREGISTRATION.md`` to produce ``Scorecard.outcome``, then
      ``ScorecardSink.publish`` (which never fails the run). Exit code of ``agent eval``:
      0 PASS, 1 FAIL, 2 CANNOT_MEASURE (``exit_code``).
    - Rate-limited for the Gemini free tier through the same ``LLMFactory`` for every trial.
    """

    def __init__(self, env: EvalEnvironment, scorers: list[Scorer], sink: ScorecardSink) -> None:
        self._env = env
        self._scorers = scorers
        self._sink = sink

    def run(
        self,
        cases: list[EvalCase],
        *,
        trials: int = 1,
        baseline_run_id: str | None = None,
    ) -> Scorecard:
        started = self._env.clock()
        manifest = self._manifest(started.date(), trials)
        run_id = f"{started:%Y-%m-%dT%H-%M-%S}-{manifest.git_sha[:4]}"
        controls = {c.case_id for c in cases if c.negative_control}
        problems = self._preflight(cases)
        if problems:
            card = self._card(run_id, started, manifest, [], set(), None, [], [])
            card = card.model_copy(update={"outcome": Outcome.CANNOT_MEASURE, "reasons": problems})
            return self._publish(cases, card)
        results: list[CaseResult] = []
        latencies: list[float] = []
        for case in cases:
            case_trials = []
            for n in range(1, trials + 1):
                _log.info("case %s, trial %d of %d", case.case_id, n, trials)
                trial, turn_seconds = self._trial(case, n)
                case_trials.append(trial)
                latencies += turn_seconds
            results.append(bar.case_result(case, case_trials))
        baseline = self._sink.baseline(baseline_run_id, fixture_hash=manifest.fixture_hash)
        mismatch: list[str] = []
        notes: list[str] = []
        found: list[str] = []
        if baseline_run_id is not None and baseline is None:
            mismatch = [f"baseline {baseline_run_id} not found"]
        elif baseline is not None:
            problems = bar.comparable(manifest, baseline, results)
            if problems and baseline_run_id is not None:
                mismatch = problems  # asked for by name: refuse the comparison
            elif problems:
                # Found automatically: say why it was not used, and gate as if there were no
                # baseline, so a Persona or judge change cannot lock every run out.
                notes = [f"latest baseline {baseline.run_id} not comparable: {'; '.join(problems)}"]
                baseline = None
            else:
                found = bar.regressions(results, baseline, controls)
        outcome, reasons = bar.apply_bar(
            results,
            controls,
            baseline=baseline,
            mismatch=mismatch,
            found_regressions=found,
        )
        card = self._card(run_id, started, manifest, results, controls, baseline, found, latencies)
        card = card.model_copy(update={"outcome": outcome, "reasons": [*reasons, *notes]})
        return self._publish(cases, card)

    # --- one trial ------------------------------------------------------------------------

    def _trial(self, case: EvalCase, n: int) -> tuple[TrialResult, list[float]]:
        since = self._env.clock() - timedelta(seconds=1)
        try:
            transcript = self._transcript(case, n)
        except Exception as exc:
            # The class only: an exception's text can quote a value from the data (HLD 03).
            error = type(exc).__name__
            _log.warning("case %s trial %d: harness error %s", case.case_id, n, error)
            return TrialResult(
                trial=n, trace_ids=[], layers=[], outcome=Outcome.CANNOT_MEASURE, error=error
            ), []
        trace_ids = list(dict.fromkeys(m.trace_id for m in transcript.outbound))
        spans = [s for s in self._env.ports.tracer.read(since) if s.trace_id in trace_ids]
        transcript = transcript.model_copy(update={"traces": spans})
        turn_seconds = [s.duration_ms / 1000 for s in spans if is_turn(s)]
        infrastructure = _infrastructure_error(transcript.outbound, len(case.turns))
        # A turn that ended on the infrastructure is graded on nothing: only the trajectory
        # (what it cost) is kept, and the judge is not asked.
        scorers = [
            s for s in self._scorers if infrastructure is None or s.layer is Layer.TRAJECTORY
        ]
        scores = [self._score(scorer, case, transcript) for scorer in scorers]
        layers = [s for s in scores if s is not None]
        if infrastructure is not None:
            trial = TrialResult(
                trial=n,
                trace_ids=trace_ids,
                layers=layers,
                outcome=Outcome.CANNOT_MEASURE,
                error=infrastructure,
            )
            return trial, turn_seconds
        outcome = bar.deterministic_outcome(layers)
        return TrialResult(
            trial=n, trace_ids=trace_ids, layers=layers, outcome=outcome
        ), turn_seconds

    def _transcript(self, case: EvalCase, n: int) -> Transcript:
        env = self._env
        with env.fresh_store() as store:
            user = store.users.load(case.run_as)
            if user is None:
                raise LookupError(f"no seeded User {case.run_as!r}")
            conversation_id = uuid4()
            refs = _seed(case, store, conversation_id, env.clock())
            before = (store.report_ids(), store.audit_rows())
            recorder = _Recorder()
            ports = replace(
                env.ports,
                users=store.users,
                reports=store.reports,
                guard=_RecordingGuard(env.ports.guard, recorder),
                warehouse=_RecordingWarehouse(env.ports.warehouse, recorder),
            )
            checkpointer = InMemorySaver(
                serde=JsonPlusSerializer(allowed_msgpack_modules=CHECKPOINT_TYPES)
            )
            service = ChatService(ports, build_graph(ports, env.limits, checkpointer))
            channel = CapturingChannel()
            for text in case.turns:
                service.handle(
                    InboundMessage(
                        user_id=case.run_as,
                        channel=Channel.CLI,
                        conversation_id=conversation_id,
                        text=text,
                        received_at=env.clock(),
                    ),
                    channel,
                )
            after = (store.report_ids(), store.audit_rows())
        return Transcript(
            case_id=case.case_id,
            trial=n,
            user_scope=user.scope,
            outbound=channel.delivered,
            traces=[],
            executed=recorder.executed,
            store_report_ids={ref: rid in after[0] for ref, rid in refs.items()},
            audit_rows=after[1],
            store_changed=after != before,
        )

    def _score(self, scorer: Scorer, case: EvalCase, transcript: Transcript) -> LayerScore | None:
        """A scorer that raises could not measure; it never takes the trial down."""
        try:
            return scorer.score(case, transcript)
        except Exception as exc:
            return LayerScore(
                scorer=scorer.name,
                layer=Layer.DETERMINISTIC,
                outcome=Outcome.CANNOT_MEASURE,
                reason=f"scorer error: {type(exc).__name__}",
            )

    # --- the run around the trials ----------------------------------------------------------

    def _preflight(self, cases: list[EvalCase]) -> list[str]:
        problems: list[str] = []
        if any(isinstance(c.expect, ExpectAnswer) for c in cases) and not any(
            c.negative_control for c in cases
        ):
            problems.append("correctness ran without a negative control")
        try:
            with self._env.fresh_store() as store:
                users = {
                    uid: store.users.load(uid)
                    for c in cases
                    for uid in [c.run_as, *(r.author_id for r in c.setup_reports)]
                }
        except Exception as exc:
            return [*problems, f"the app store did not open: {type(exc).__name__}"]
        problems += [f"seeded User {uid!r} does not load" for uid, u in users.items() if u is None]
        for c in cases:
            user = users.get(c.run_as)
            if isinstance(c.expect, ExpectAnswer) and user is not None:
                if problem := self._dry_run(c.expect.reference_sql, user):
                    problems.append(f"{c.case_id}: reference {problem}")
        return problems

    def _dry_run(self, sql: str, user: UserContext) -> str | None:
        limits = self._env.limits
        decision = self._env.ports.guard.check(sql, user.scope, row_limit=limits.sql_row_limit)
        if not isinstance(decision, GuardAllowed):
            return f"rejected by the guard ({decision.reason})"
        try:
            dry = self._env.ports.warehouse.dry_run(
                decision.rewritten_sql, decision.params, max_bytes=limits.max_bytes_billed
            )
        except Exception as exc:
            return f"does not dry-run ({type(exc).__name__})"
        return "is over the bytes cap" if dry.over_cap else None

    def _manifest(self, today: date, trials: int) -> Manifest:
        env = self._env
        judged = any(s.layer is Layer.JUDGED for s in self._scorers)
        return Manifest(
            git_sha=env.git_sha,
            fixture_hash=env.fixture_hash,
            prereg_id=env.prereg_id,
            models=env.models,
            judge=env.judge if judged else None,
            rubric_hash=rubric_hash() if judged else None,
            persona_version=env.ports.personas.active().version,
            trios_enabled=env.trios_enabled,
            data_as_of=today.isoformat(),
            trials=trials,
        )

    def _card(
        self,
        run_id: str,
        started: datetime,
        manifest: Manifest,
        results: list[CaseResult],
        controls: set[str],
        baseline: Scorecard | None,
        found: list[str],
        latencies: list[float],
    ) -> Scorecard:
        trajectory = [
            s for r in results for t in r.trials for s in t.layers if s.layer is Layer.TRAJECTORY
        ]
        return Scorecard(
            run_id=run_id,
            started_at=started,
            manifest=manifest,
            cases=results,
            suites=bar.summarize(results, controls),
            negative_control_failed=bar.negative_controls_failed(results, controls),
            cost_usd=sum(s.values.get("usd", 0.0) for s in trajectory),
            tokens=int(sum(s.values.get("tokens", 0.0) for s in trajectory)),
            p95_latency_s=bar.p95(latencies),
            baseline_run_id=baseline.run_id if baseline else None,
            regressions=found,
            unstable=bar.unstable(results),
            outcome=Outcome.CANNOT_MEASURE,  # set by the caller once the bar is applied
        )

    def _publish(self, cases: list[EvalCase], card: Scorecard) -> Scorecard:
        try:
            self._sink.publish(cases, card)
        except Exception as exc:  # a sink must not fail the run; the composite already logs
            _log.error("publishing the scorecard failed: %s", type(exc).__name__)
        return card


def exit_code(outcome: Outcome) -> int:
    return {Outcome.PASS: 0, Outcome.FAIL: 1, Outcome.CANNOT_MEASURE: 2}[outcome]


# --- the capturing channel and the recorders ----------------------------------------------------


class CapturingChannel:
    """The eval's ``ChannelAdapter``: keeps every message ``Outbound`` delivered, so what it
    holds has passed the output PII filter."""

    def __init__(self) -> None:
        self.delivered: list[OutboundMessage] = []

    def deliver(self, message: OutboundMessage) -> None:
        self.delivered.append(message)


def _infrastructure_error(outbound: list[OutboundMessage], turns: int) -> str | None:
    """``"turn 2: model_busy"`` when a turn ended on an infrastructure failure, or sent
    nothing to end on."""
    ended = [m for m in outbound if isinstance(m, AgentReply | ConfirmationRequest)]
    for i, m in enumerate(ended, start=1):
        if isinstance(m, AgentReply) and m.message_class in _INFRASTRUCTURE:
            return f"turn {i}: {m.message_class}"
    if len(ended) < turns:
        return f"turn {len(ended) + 1}: no reply"
    return None


@dataclass
class _Recorder:
    allowed: dict[str, BrandScope] = field(default_factory=dict)
    executed: list[ExecutedQuery] = field(default_factory=list)


class _RecordingGuard:
    """The real guard, remembering the scope of every SQL it let through."""

    def __init__(self, inner: QueryGuard, recorder: _Recorder) -> None:
        self._inner = inner
        self._recorder = recorder

    def check(self, sql: str, scope: BrandScope, *, row_limit: int) -> GuardDecision:
        decision = self._inner.check(sql, scope, row_limit=row_limit)
        if isinstance(decision, GuardAllowed):
            self._recorder.allowed[decision.rewritten_sql] = decision.scope
        return decision


class _RecordingWarehouse:
    """The real warehouse, keeping every executed statement with its result. A statement
    that is not the output of an allowed guard decision is recorded as unguarded."""

    def __init__(self, inner: QueryRunner, recorder: _Recorder) -> None:
        self._inner = inner
        self._recorder = recorder

    def dry_run(self, sql: str, params: dict[str, Any], *, max_bytes: int) -> DryRunResult:
        return self._inner.dry_run(sql, params, max_bytes=max_bytes)

    def execute(
        self, sql: str, params: dict[str, Any], *, max_bytes: int, max_rows: int
    ) -> QueryResult:
        result = self._inner.execute(sql, params, max_bytes=max_bytes, max_rows=max_rows)
        scope = self._recorder.allowed.get(sql)
        brands = params.get("brands")
        self._recorder.executed.append(
            ExecutedQuery(
                sql=sql,
                guarded=scope is not None,
                scope=scope,
                brands=list(brands) if brands is not None else None,
                columns=result.columns,
                rows=result.rows,
            )
        )
        return result


def _seed(
    case: EvalCase, store: TrialStore, conversation_id: UUID, now: datetime
) -> dict[str, UUID]:
    """Place the case's ``setup_reports`` in the trial's store, oldest first, through the
    repository port. Returns ref -> report ID."""
    refs: dict[str, UUID] = {}
    count = len(case.setup_reports)
    for i, seed in enumerate(case.setup_reports):
        author = store.users.load(seed.author_id)
        if author is None:
            raise LookupError(f"no seeded User {seed.author_id!r}")
        report_id = uuid4()
        report = SavedReport(
            report_id=report_id,
            author_id=seed.author_id,
            conversation_id=conversation_id if seed.this_conversation else uuid4(),
            turn_id=f"seed-{seed.ref}",
            created_at=now - timedelta(minutes=count - i),
            title=seed.structured.title,
            question=seed.structured.title,
            sql="",
            markdown=seed.markdown,
            structured=seed.structured,
            scope=author.scope,
            data_as_of=now.date(),
        )
        entities = [
            ReportEntity(
                report_id=report_id,
                entity_type=EntityType.BRAND,
                value=e,
                normalized=normalize_entity(e),
            )
            for e in seed.entities
        ]
        store.reports.save(report, entities, channel=Channel.CLI)
        refs[seed.ref] = report_id
    return refs
