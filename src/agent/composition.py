"""The single composition root (ADR 0006). Stub.

The only module that names concrete adapters. Moving from SQLite to Cloud SQL, or from the
public dataset to a governed copy, is a new adapter plus one line here.

Prototype wiring (built slice by slice in Phase 4):

==================  ====================================================  =========
Port                Prototype adapter                                     Slice
==================  ====================================================  =========
LLMFactory          ``langchain-google-genai`` (AI Studio), tenacity,     1, 3
                    token bucket, in-process breaker
QueryGuard          ``sqlglot`` (BigQuery dialect)                        2
QueryRunner         ``google-cloud-bigquery`` with ADC                    1
PiiFilter           regex detectors                                       2
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

from dataclasses import dataclass

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
    reports: ReportRepository
    trios: TrioRetriever
    profiles: ProfileStore
    personas: PersonaStore
    tracer: Tracer


def build_ports(settings: Settings) -> Ports:
    raise NotImplementedError("wired slice by slice in Phase 4")
