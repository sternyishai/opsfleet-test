"""The single composition root (ADR 0006).

The only module that names concrete adapters. Moving from SQLite to Cloud SQL, or from the
public dataset to a governed copy, is a new adapter plus one line here.

Prototype wiring (built slice by slice in Phase 4):

==================  ====================================================  =========
Port                Prototype adapter                                     Slice
==================  ====================================================  =========
LLMFactory          ``langchain-google-genai`` (AI Studio), tenacity,     1, 3
                    token bucket and breaker per model, fallback chain
QueryGuard          ``sqlglot`` (BigQuery dialect), full scope rewrite    1, 2
QueryRunner         ``google-cloud-bigquery`` with ADC, tenacity, breaker 1, 3
PiiFilter           regex detectors                                       2
UserDirectory       ``seed/users.yaml`` loaded into SQLite                1
ReportRepository    SQLite + FTS5, same file as the users                 4
TrioRetriever       SQLite + cosine over ``seed/trios/``; Null if        1, 7
                    ``MODEL_EMBEDDING`` is empty or ``eval --no-trios``
ProfileStore        SQLite notes (stretch)                                7
PersonaStore        SQLite versions, ``persona.yaml`` as version 1 and    1, 7
                    the cold-start default; same file as the users
ChannelAdapter      CLI (stdout); web SSE stream (optional, ADR 0010)     1, 8
                    ``build_web``: FastAPI over the same ``build_app``
Tracer              JSONL in ``./traces/``; OTel -> Phoenix if it answers 1, 5
                    at start-up (``PHOENIX_ENDPOINT``)
Delivery            not wired (ADR 0009)                                  -
==================  ====================================================  =========
"""

import atexit
import shutil
import sqlite3
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from agent.config import Settings
from agent.ports import (
    LLMFactory,
    PersonaStore,
    PiiFilter,
    ProfileStore,
    QueryGuard,
    QueryRunner,
    ReportRepository,
    Tracer,
    TrioRetriever,
    UserDirectory,
)

if TYPE_CHECKING:
    from fastapi import FastAPI
    from langgraph.checkpoint.sqlite import SqliteSaver

    from agent.adapters.channel_stdout import StdoutChannel
    from agent.contracts.eval import EvalCase
    from agent.core.chat import ChatService
    from agent.core.views import Views
    from agent.domain.identity import BrandScope
    from agent.evals.runner import EvalRunner, TrialStore
    from agent.evals.sinks import FanOutSink


@dataclass(frozen=True, slots=True)
class Ports:
    """Every process-wide port the graph and tools may use. Tests build this from fakes.

    ``ChannelAdapter`` is not here: it is per turn, passed to ``ChatService.handle`` by the
    channel that received the message."""

    llm: LLMFactory
    guard: QueryGuard
    warehouse: QueryRunner
    pii: PiiFilter
    users: UserDirectory
    trios: TrioRetriever
    personas: PersonaStore
    tracer: Tracer
    reports: ReportRepository
    profiles: ProfileStore | None = None
    """``build_ports`` always wires it (slice 7c); None, as in most tests, is an empty
    profile for every User and nothing saved."""


def build_ports(settings: Settings, *, trios_enabled: bool = True) -> Ports:
    """``trios_enabled=False`` (or an empty ``MODEL_EMBEDDING``) is the Null retriever."""
    from agent.adapters.bigquery import BigQueryRunner
    from agent.adapters.llm_gemini import GeminiLLMFactory
    from agent.adapters.null import NullTrioRetriever
    from agent.adapters.persona_sqlite import SqlitePersonaStore
    from agent.adapters.pii_regex import RegexPiiFilter
    from agent.adapters.profile_sqlite import SqliteProfileStore
    from agent.adapters.query_guard import SqlglotQueryGuard
    from agent.adapters.reports_sqlite import SqliteReportRepository
    from agent.adapters.tracer_jsonl import JsonlTracer
    from agent.adapters.tracer_phoenix import PhoenixExporter
    from agent.adapters.trios_sqlite import SqliteTrioRetriever
    from agent.adapters.users_sqlite import SqliteUserDirectory

    limits = settings.limits
    endpoint = settings.phoenix_endpoint
    phoenix = PhoenixExporter.connect(endpoint) if endpoint else None
    activate = None
    if phoenix is not None and settings.phoenix_full_content:
        phoenix.instrument_langchain()
        activate = phoenix.active
    tracer = JsonlTracer(settings.traces_dir, export=phoenix, activate=activate)
    app_db = _sqlite_path(settings.app_db_url)
    # The users first: they create the schema and the Authors the reports point at.
    users = SqliteUserDirectory(app_db, seed=settings.seed_dir / "users.yaml")
    llm = GeminiLLMFactory(settings.models, limits, settings.google_api_key, tracer=tracer)
    trios: TrioRetriever = (
        SqliteTrioRetriever(
            app_db,
            seed=settings.seed_dir / "trios",
            embeddings=llm.embeddings(),
            min_score=limits.trio_min_score,
        )
        if trios_enabled and settings.models.embedding
        else NullTrioRetriever()
    )
    return Ports(
        llm=llm,
        guard=SqlglotQueryGuard(dialect="bigquery"),
        warehouse=BigQueryRunner(
            project=settings.bigquery_project, dataset=settings.bigquery_dataset, limits=limits
        ),
        pii=RegexPiiFilter(),
        users=users,
        trios=trios,
        personas=SqlitePersonaStore(app_db, seed=settings.seed_dir / "persona.yaml"),
        tracer=tracer,
        reports=SqliteReportRepository(app_db),
        profiles=SqliteProfileStore(app_db, max_notes=limits.profile_max_notes),
    )


def build_checkpointer(settings: Settings) -> "SqliteSaver":
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
    from langgraph.checkpoint.sqlite import SqliteSaver

    from agent.graph.state import CHECKPOINT_TYPES

    path = _sqlite_path(settings.checkpoint_db_url)
    path.parent.mkdir(parents=True, exist_ok=True)
    return SqliteSaver(
        sqlite3.connect(path, check_same_thread=False),
        serde=JsonPlusSerializer(allowed_msgpack_modules=CHECKPOINT_TYPES),
    )


def build_app(settings: Settings) -> tuple["ChatService", "Views"]:
    """The two shared entry points every channel calls, over one set of ports (LLD 07)."""
    _, service, views = _build_app(settings)
    return service, views


def _build_app(settings: Settings) -> tuple[Ports, "ChatService", "Views"]:
    from agent.core.chat import ChatService
    from agent.core.views import Views
    from agent.graph.build import build_graph

    ports = build_ports(settings)
    graph = build_graph(ports, settings.limits, build_checkpointer(settings))
    return ports, ChatService(ports, graph), Views(ports, settings.limits, settings.models.prices)


DEV_IDENTITY_HOST = "127.0.0.1"


def build_web(settings: Settings, *, host: str) -> "FastAPI":
    """The web channel over the same ``ChatService`` and ``Views`` as the CLI (needs the
    ``web`` extra). The dev identity is served only when ``WEB_DEV_IDENTITY`` is on and the
    server is bound to ``127.0.0.1`` (or ``WEB_DEV_IDENTITY_ANY_HOST``, for a container
    published on the host's loopback); on any other host it refuses to start (ValueError), as
    it does when ``WEB_UI_DIR`` names no built UI."""
    from agent.adapters.users_sqlite import SqliteUserDirectory
    from agent.adapters.web import create_app

    loopback_only = not settings.web_dev_identity_any_host
    if settings.web_dev_identity and loopback_only and host != DEV_IDENTITY_HOST:
        raise ValueError(f"WEB_DEV_IDENTITY needs --host {DEV_IDENTITY_HOST}, not {host}")
    ui = settings.web_ui_dir
    if ui is not None and not (ui / "index.html").is_file():
        raise ValueError(f"WEB_UI_DIR={ui} has no index.html; build it: pnpm --dir web build")
    ports, service, views = _build_app(settings)
    dev_users = None
    if settings.web_dev_identity:
        if not isinstance(ports.users, SqliteUserDirectory):  # not `assert`: -O drops it
            raise ValueError("WEB_DEV_IDENTITY needs the seeded User directory")
        dev_users = ports.users.list_dev_users
    return create_app(service, views, ports.tracer, dev_users=dev_users, ui_dir=settings.web_ui_dir)


def build_cli_channel() -> "StdoutChannel":
    from agent.adapters.channel_stdout import StdoutChannel

    return StdoutChannel()


EVAL_CASES = Path("evals/cases")
EVAL_RUNS = Path("evals/runs")
PREREGISTRATION = Path("evals/PREREGISTRATION.md")
_EVAL_MAX_QUEUE_S = 180.0
"""An eval waits for the free tier's rate limit instead of ending a trial as MODEL_BUSY."""


@dataclass(frozen=True, slots=True)
class EvalApp:
    runner: "EvalRunner"
    cases: "list[EvalCase]"
    scorecard_path: "Callable[[str], Path]"
    """Where the JSON file of a run ID is."""
    sink: "FanOutSink"
    """Its ``failures`` name the sinks that did not take the last run (Phoenix down)."""


def build_eval(settings: Settings, *, trios_enabled: bool) -> EvalApp:
    """The eval harness over the real adapters (docs/lld/08-eval.md). Differences from the
    chat app: traces go to ``traces/eval/`` (kept out of ``/stats``), the rate limiter waits
    longer, the base ports use a throwaway app store (each trial gets its own), and the
    scorecard goes to ``evals/runs/`` and, if it answers, Phoenix. ``trios_enabled=False`` is
    the Golden Bucket ablation: the Null retriever in place of the seed Trios."""
    from agent.adapters.eval_files import (
        JsonScorecardSink,
        fixture_hash,
        git_sha,
        prereg_id,
        read_case_files,
    )
    from agent.adapters.scorecard_phoenix import PhoenixScorecardSink
    from agent.evals.runner import EvalEnvironment, EvalRunner, load_cases
    from agent.evals.scorers import (
        ExecutionMatch,
        RetrievalMatch,
        RubricJudge,
        SafetyChecks,
        SectionsPresent,
        StepMatch,
        StoreState,
        TrajectoryStats,
    )
    from agent.evals.sinks import FanOutSink
    from agent.ports.llm import ModelStep
    from agent.ports.scorecards import ScorecardSink

    limits = settings.limits.model_copy(
        update={"llm_max_queue_s": max(settings.limits.llm_max_queue_s, _EVAL_MAX_QUEUE_S)}
    )
    scratch = Path(tempfile.mkdtemp(prefix="agent-eval-"))
    atexit.register(shutil.rmtree, scratch, ignore_errors=True)
    eval_settings = settings.model_copy(
        update={
            "limits": limits,
            "traces_dir": settings.traces_dir / "eval",
            "app_db_url": f"sqlite:///{scratch / 'base.db'}",
        }
    )
    ports = build_ports(eval_settings, trios_enabled=trios_enabled)
    models = settings.models
    resolved = {step: models.resolve(step) for step in ModelStep}
    env = EvalEnvironment(
        ports=ports,
        limits=limits,
        fresh_store=lambda: fresh_eval_store(settings.seed_dir / "users.yaml"),
        git_sha=git_sha(Path.cwd()),
        fixture_hash=fixture_hash(
            EVAL_CASES, settings.seed_dir / "trios", settings.seed_dir / "users.yaml"
        ),
        prereg_id=prereg_id(PREREGISTRATION),
        models={
            step.value: primary + (f" -> {fallback}" if fallback else "")
            for step, (primary, fallback) in resolved.items()
        },
        judge=resolved[ModelStep.JUDGE][0],
        trios_enabled=trios_enabled,
    )
    scorers = [
        ExecutionMatch(ports.guard, ports.warehouse, limits),
        SafetyChecks(ports.pii),
        StoreState(),
        SectionsPresent(),
        StepMatch(),
        RetrievalMatch(seed_trio_scopes(settings.seed_dir / "trios")),
        RubricJudge(ports.llm, ports.pii),
        TrajectoryStats(models.prices),
    ]
    json_sink = JsonScorecardSink(EVAL_RUNS)
    sinks: list[ScorecardSink] = [json_sink]
    if settings.phoenix_endpoint:
        sinks.append(PhoenixScorecardSink(settings.phoenix_endpoint))
    cases = load_cases(read_case_files(EVAL_CASES))
    sink = FanOutSink(sinks)
    return EvalApp(EvalRunner(env, scorers, sink), cases, json_sink.path, sink)


def seed_trio_scopes(trios_dir: Path) -> "dict[str, BrandScope]":
    """Trio ID -> the Brands its data covers, from the seed Golden Bucket: what the
    retrieval step eval checks every returned Trio against."""
    from agent.adapters.trios_sqlite import read_seed_trios

    return {t.trio_id: t.scope for t in read_seed_trios(trios_dir)}


@contextmanager
def fresh_eval_store(users_seed: Path) -> "Iterator[TrialStore]":
    """One trial's app store: a new SQLite file seeded with the Users, deleted afterwards."""
    from agent.adapters.reports_sqlite import SqliteReportRepository
    from agent.adapters.users_sqlite import SqliteUserDirectory
    from agent.evals.runner import TrialStore

    with tempfile.TemporaryDirectory(prefix="agent-eval-trial-") as folder:
        db = Path(folder) / "app.db"
        users = SqliteUserDirectory(db, seed=users_seed)
        reports = SqliteReportRepository(db)
        yield TrialStore(
            users=users,
            reports=reports,
            report_ids=reports.all_report_ids,
            audit_rows=reports.audit_rows,
        )


def _sqlite_path(url: str) -> Path:
    prefix = "sqlite:///"
    if not url.startswith(prefix):
        raise ValueError(f"the prototype stores app state in SQLite, not {url!r}")
    return Path(url.removeprefix(prefix))
