"""The single composition root (ADR 0006).

The only module that names concrete adapters. Moving from SQLite to Cloud SQL, or from the
public dataset to a governed copy, is a new adapter plus one line here.

Prototype wiring (built slice by slice in Phase 4):

==================  ====================================================  =========
Port                Prototype adapter                                     Slice
==================  ====================================================  =========
LLMFactory          ``langchain-google-genai`` (AI Studio), tenacity,     1, 3
                    token bucket, in-process breaker
QueryGuard          ``sqlglot`` (BigQuery dialect): minimal, then full     1, 2
QueryRunner         ``google-cloud-bigquery`` with ADC                    1
PiiFilter           pass-through placeholder; regex detectors             1, 2
UserDirectory       ``seed/users.yaml`` loaded into SQLite                1
ReportRepository    SQLite + FTS5                                         4
TrioRetriever       ``NullTrioRetriever``; SQLite + cosine (stretch)      1, 7
ProfileStore        SQLite notes (stretch)                                7
PersonaStore        bundled ``persona.yaml``; SQLite versions (stretch)   1, 7
ChannelAdapter      CLI (stdout); web SSE stream (optional, ADR 0010)     1, 8
Tracer              JSONL in ``./traces/``; OTel -> Phoenix if running    1, 5
Delivery            not wired (ADR 0009)                                  -
==================  ====================================================  =========
"""

import sqlite3
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
    from langgraph.checkpoint.sqlite import SqliteSaver

    from agent.adapters.channel_stdout import StdoutChannel
    from agent.core.chat import ChatService


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
    reports: ReportRepository | None = None
    """None until slice 4 wires the SQLite store."""
    profiles: ProfileStore | None = None
    """None until slice 7: every User then has an empty profile."""


def build_ports(settings: Settings) -> Ports:
    from agent.adapters.bigquery import BigQueryRunner
    from agent.adapters.llm_gemini import GeminiLLMFactory
    from agent.adapters.null import NullTrioRetriever, PassThroughPiiFilter
    from agent.adapters.persona_file import FilePersonaStore
    from agent.adapters.query_guard import SqlglotQueryGuard
    from agent.adapters.tracer_jsonl import JsonlTracer
    from agent.adapters.users_sqlite import SqliteUserDirectory

    limits = settings.limits
    return Ports(
        llm=GeminiLLMFactory(settings.models, limits, settings.google_api_key),
        guard=SqlglotQueryGuard(dialect="bigquery"),
        warehouse=BigQueryRunner(
            project=settings.bigquery_project,
            dataset=settings.bigquery_dataset,
            timeout_s=limits.timeout_bigquery_s,
        ),
        pii=PassThroughPiiFilter(),
        users=SqliteUserDirectory(
            _sqlite_path(settings.app_db_url), seed=settings.seed_dir / "users.yaml"
        ),
        trios=NullTrioRetriever(),
        personas=FilePersonaStore(settings.seed_dir / "persona.yaml"),
        tracer=JsonlTracer(settings.traces_dir),
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


def build_chat_service(settings: Settings) -> "ChatService":
    from agent.core.chat import ChatService
    from agent.graph.build import build_graph

    ports = build_ports(settings)
    graph = build_graph(ports, settings.limits, build_checkpointer(settings))
    return ChatService(ports, graph)


def build_cli_channel() -> "StdoutChannel":
    from agent.adapters.channel_stdout import StdoutChannel

    return StdoutChannel()


def _sqlite_path(url: str) -> Path:
    prefix = "sqlite:///"
    if not url.startswith(prefix):
        raise ValueError(f"the prototype stores app state in SQLite, not {url!r}")
    return Path(url.removeprefix(prefix))
