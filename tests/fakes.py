"""Fakes for the ports, so tests cross the same interfaces the graph uses (LLD 06).

The query guard is never faked: safety tests use the real ``SqlglotQueryGuard``.
"""

import re
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.runnables import Runnable, RunnableConfig, RunnableLambda
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from agent.adapters.null import NullTrioRetriever
from agent.adapters.pii_regex import RegexPiiFilter
from agent.adapters.query_guard import SqlglotQueryGuard
from agent.composition import Ports
from agent.config import Limits
from agent.contracts.channel import Channel, InboundMessage, OutboundMessage
from agent.contracts.router import Action, RouterDecision, Verdict
from agent.core.chat import ChatService
from agent.domain.identity import UserContext, UserId
from agent.domain.persona import Persona, PersonaPath, PersonaVersion, Tone, TonePreset
from agent.domain.query import DryRunResult, QueryResult
from agent.domain.reports import (
    DeletionAuditRecord,
    DeletionFilter,
    DeletionPlanItem,
    DeletionReceipt,
    ReportEntity,
    ReportListItem,
    ReportQuery,
    SavedReport,
    match_reason,
    normalize_entity,
)
from agent.errors import ErrorClass, TransientError
from agent.graph.build import build_graph
from agent.graph.state import CHECKPOINT_TYPES, AgentGraph
from agent.ports.llm import ModelStep
from agent.ports.reports import ReportRepository
from agent.tools.context import TurnContext


class ScriptedChatModel(GenericFakeChatModel):
    """Replies with the scripted messages in order. ``bind_tools`` is a no-op, and
    ``with_structured_output`` returns the scripted structured value."""

    structured: Any = None
    seen: list[list[BaseMessage]] = []

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> Runnable:  # type: ignore[override]
        return self

    def with_structured_output(self, schema: Any, **kwargs: Any) -> Runnable:  # type: ignore[override]
        """``structured`` is the value, an exception to raise, or a list of either, used in
        order (the last one repeats)."""

        def answer(_: Any) -> Any:
            value = self.structured
            if isinstance(value, list):
                value = _next(value)
            if isinstance(value, Exception):
                raise value
            return value

        return RunnableLambda(answer)

    def invoke(self, input: Any, config: Any = None, **kwargs: Any) -> AIMessage:  # type: ignore[override]
        self.seen.append(list(input))
        return super().invoke(input, config, **kwargs)  # type: ignore[return-value]


def analysis_decision(question: str = "total revenue in 2025") -> RouterDecision:
    return RouterDecision(verdict=Verdict.ALLOWED, action=Action.ANALYSIS, search_question=question)


class FakeLLMFactory:
    """The router returns ``decision`` (a list: one per turn, the last repeats); the analyst
    replies with ``analyst`` in order. ``structured`` gives other structured-output steps
    their value (``ModelStep.ENTITY_EXTRACTION``, ``ModelStep.DELETION_FILTER``); an unset
    one raises, like a model outage. ``fail`` makes every ``chat`` call raise it;
    ``router_fail`` only the router's. ``steps`` records every model asked for."""

    def __init__(
        self,
        analyst: list[AIMessage] | None = None,
        decision: RouterDecision | list[RouterDecision] | None = None,
        fail: Exception | None = None,
        router_fail: Exception | None = None,
        structured: dict[ModelStep, Any] | None = None,
    ) -> None:
        self.analyst = ScriptedChatModel(messages=iter(analyst or []), seen=[])
        self.router = ScriptedChatModel(
            messages=iter([]), structured=decision or analysis_decision(), seen=[]
        )
        self.others = {
            step: ScriptedChatModel(messages=iter([]), structured=value, seen=[])
            for step, value in (structured or {}).items()
        }
        self.fail = fail
        self.router_fail = router_fail
        self.steps: list[ModelStep] = []

    def chat(self, step: ModelStep) -> BaseChatModel:
        self.steps.append(step)
        if self.fail is not None:
            raise self.fail
        if step is ModelStep.ROUTER and self.router_fail is not None:
            raise self.router_fail
        if step is ModelStep.ROUTER:
            return self.router
        if step in (ModelStep.ENTITY_EXTRACTION, ModelStep.DELETION_FILTER):
            return self.others.get(step) or ScriptedChatModel(
                messages=iter([]), structured=TransientError("unscripted", dependency="gemini")
            )
        return self.analyst

    def embeddings(self) -> Embeddings:
        raise AssertionError("no embeddings in slice 1")


type Outcome = QueryResult | Exception


def rows(*records: dict[str, Any]) -> QueryResult:
    """A result holding ``records``; their keys are the columns."""
    columns = list(records[0]) if records else ["value"]
    return QueryResult(
        columns=columns,
        rows=list(records),
        row_count=len(records),
        truncated=False,
        bytes_billed=10,
    )


class FakeQueryRunner:
    """A scripted warehouse. Records every dry run and execute it was given.

    - ``result`` / ``error``: the outcome of every execute (the slice 1 form).
    - ``outcomes``: execute outcomes in order, one per non-probe query; the last one repeats.
    - ``dry_runs``: dry-run outcomes in order (a ``DryRunResult`` or an exception); the last
      repeats. Default: a free, under-cap dry run.
    - ``probes``: answers the empty-result probe queries, ``sql -> outcome``. A query is a
      probe when ``probes`` claims it (returns non-None).
    """

    def __init__(
        self,
        result: QueryResult | None = None,
        error: Exception | None = None,
        *,
        outcomes: list[Outcome] | None = None,
        dry_runs: list[DryRunResult | Exception] | None = None,
        probes: Any = None,
    ) -> None:
        self.result = result or QueryResult(
            columns=["revenue"],
            rows=[{"revenue": 1.5}],
            row_count=1,
            truncated=False,
            bytes_billed=10,
        )
        self.error = error
        self.outcomes = list(outcomes or [])
        self.dry_runs = list(dry_runs or [])
        self.probes = probes
        self.executed: list[tuple[str, dict[str, Any], int, int]] = []
        self.dry_ran: list[str] = []

    def dry_run(self, sql: str, params: dict[str, Any], *, max_bytes: int) -> DryRunResult:
        self.dry_ran.append(sql)
        outcome = _next(self.dry_runs) if self.dry_runs else None
        if isinstance(outcome, Exception):
            raise outcome
        return outcome or DryRunResult(bytes_processed=0, over_cap=False)

    def execute(
        self, sql: str, params: dict[str, Any], *, max_bytes: int, max_rows: int
    ) -> QueryResult:
        self.executed.append((sql, params, max_bytes, max_rows))
        outcome: Outcome | None = self.probes(sql) if self.probes else None
        if outcome is None:
            outcome = _next(self.outcomes) if self.outcomes else (self.error or self.result)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _next[T](script: list[T]) -> T:
    """The next scripted outcome; the last one repeats."""
    return script.pop(0) if len(script) > 1 else script[0]


class FakeUsers:
    def __init__(self, *users: UserContext) -> None:
        self._users = {u.user_id: u for u in users}

    def load(self, user_id: UserId) -> UserContext | None:
        return self._users.get(user_id)


SEED_PERSONA = PersonaVersion(
    version=1,
    persona=Persona(tone=Tone(preset=TonePreset.DIRECT), report_sections=["Summary"]),
    created_by=None,
    created_at=datetime(2026, 11, 15, tzinfo=UTC),
    path=PersonaPath.SEED,
    is_active=True,
)


class FakePersonas:
    def active(self) -> PersonaVersion:
        return SEED_PERSONA

    def history(self) -> list[PersonaVersion]:
        return [SEED_PERSONA]

    def publish(self, persona: Any, **kwargs: Any) -> PersonaVersion:
        raise AssertionError("not in slice 1")

    def record_rejection(self, persona: Any, **kwargs: Any) -> None:
        raise AssertionError("not in slice 1")

    def rollback(self, *, by: UserId, to_version: int | None = None) -> PersonaVersion:
        raise LookupError("version 1 is active")


class FakeSpan:
    def __init__(self, name: str, attributes: dict[str, Any]) -> None:
        self.name = name
        self.attributes = dict(attributes)
        self.errors: list[ErrorClass] = []

    def set(self, **attributes: Any) -> None:
        self.attributes.update(attributes)

    def record_error(self, error_class: ErrorClass, exc: BaseException) -> None:
        self.errors.append(error_class)


class FakeTracer:
    def __init__(self) -> None:
        self.spans: list[FakeSpan] = []

    @contextmanager
    def turn(self, **attributes: Any) -> Iterator[tuple[str, FakeSpan]]:
        span = FakeSpan("turn", attributes)
        self.spans.append(span)
        yield uuid4().hex[:12], span

    @contextmanager
    def span(self, name: str, **attributes: Any) -> Iterator[FakeSpan]:
        span = FakeSpan(name, attributes)
        self.spans.append(span)
        yield span

    def current_trace_id(self) -> str | None:
        return None


class CollectingChannel:
    def __init__(self) -> None:
        self.delivered: list[OutboundMessage] = []
        self.conversation_id: UUID | None = None

    def deliver(self, message: OutboundMessage) -> None:
        self.delivered.append(message)
        self.conversation_id = message.conversation_id


def fake_ports(
    *users: UserContext,
    llm: FakeLLMFactory | None = None,
    warehouse: FakeQueryRunner | None = None,
    trios: Any = None,
    reports: ReportRepository | None = None,
) -> Ports:
    return Ports(
        llm=llm or FakeLLMFactory(),
        guard=SqlglotQueryGuard(),
        warehouse=warehouse or FakeQueryRunner(),
        pii=RegexPiiFilter(),
        users=FakeUsers(*users),
        trios=trios or NullTrioRetriever(),
        personas=FakePersonas(),
        tracer=FakeTracer(),
        reports=reports or InMemoryReportRepository(),
    )


def turn_context(
    user: UserContext,
    ports: Ports | None = None,
    *,
    conversation_id: UUID | None = None,
    turn_id: str = "t1",
) -> TurnContext:
    return TurnContext(
        user=user,
        conversation_id=conversation_id or uuid4(),
        turn_id=turn_id,
        channel=Channel.CLI,
        ports=ports or fake_ports(user),
        limits=Limits(_env_file=None),  # pyright: ignore[reportCallIssue]
    )


def chat_service(ports: Ports) -> tuple[ChatService, AgentGraph]:
    """A ChatService over ``ports`` with an in-memory checkpointer, and its graph to inspect."""
    graph = build_graph(ports, Limits(_env_file=None), checkpointer())  # pyright: ignore[reportCallIssue]
    return ChatService(ports, graph), graph


def ask(
    service: ChatService, user_id: UserId, text: str, conversation_id: UUID | None = None
) -> tuple[list[OutboundMessage], RunnableConfig]:
    """One CLI-like turn. Returns what was delivered and the graph config of the Conversation."""
    channel = CollectingChannel()
    service.handle(
        InboundMessage(
            user_id=user_id,
            channel=Channel.CLI,
            conversation_id=conversation_id,
            text=text,
            received_at=datetime.now(UTC),
        ),
        channel,
    )
    cid = channel.delivered[-1].conversation_id
    return channel.delivered, {"configurable": {"thread_id": str(cid)}}


def checkpointer() -> InMemorySaver:
    """Strict, like production: an unregistered type in state fails the test."""
    return InMemorySaver(serde=JsonPlusSerializer(allowed_msgpack_modules=CHECKPOINT_TYPES))


class InMemoryReportRepository:
    """``ReportRepository`` in memory, held to the same tests as the SQLite adapter.

    Full text is a case-folded word match over title, question and markdown, like FTS5's
    default tokenizer."""

    def __init__(self) -> None:
        self.reports: dict[UUID, SavedReport] = {}
        self.entities: dict[UUID, list[ReportEntity]] = {}
        self.audits: dict[UUID, DeletionAuditRecord] = {}

    def save(
        self, report: SavedReport, entities: list[ReportEntity], *, channel: Channel
    ) -> tuple[SavedReport, bool]:
        for stored in self.reports.values():
            if (stored.conversation_id, stored.turn_id) == (report.conversation_id, report.turn_id):
                return stored, False
        self.reports[report.report_id] = report
        self.entities[report.report_id] = list(entities)
        return report, True

    def find(
        self, author_id: UserId, query: ReportQuery, *, conversation_id: UUID
    ) -> tuple[list[ReportListItem], int]:
        narrowed = bool(query.this_conversation or query.entities or query.text or query.created)
        criteria = DeletionFilter.model_construct(
            all_reports=not narrowed,
            this_conversation=query.this_conversation,
            entities=query.entities,
            topic=query.text,
            created=query.created,
        )
        found = sorted(
            (r for r in self._own(author_id) if self._reasons(r, criteria, conversation_id)),
            key=lambda r: r.created_at,
            reverse=True,
        )
        items = [
            ReportListItem(
                report_id=r.report_id,
                title=r.title,
                created_at=r.created_at,
                conversation_id=r.conversation_id,
                accessible=True,
                scope=r.scope,
            )
            for r in found[: query.limit]
        ]
        return items, len(found)

    def get(self, author_id: UserId, report_id: UUID) -> SavedReport | None:
        report = self.reports.get(report_id)
        return report if report and report.author_id == author_id else None

    def match(
        self, author_id: UserId, filter: DeletionFilter, *, conversation_id: UUID
    ) -> list[DeletionPlanItem]:
        matched = [
            (r, reasons)
            for r in sorted(self._own(author_id), key=lambda r: r.created_at)
            if (reasons := self._reasons(r, filter, conversation_id))
        ]
        return [
            DeletionPlanItem(
                index=i,
                report_id=r.report_id,
                title=r.title,
                created_at=r.created_at,
                reason=reasons,
            )
            for i, (r, reasons) in enumerate(matched, start=1)
        ]

    def delete_with_audit(
        self,
        author_id: UserId,
        plan_id: UUID,
        report_ids: list[UUID],
        titles: dict[UUID, str],
        filter: DeletionFilter,
    ) -> DeletionReceipt:
        if plan_id in self.audits:
            return DeletionReceipt(audit=self.audits[plan_id], already_gone=[], replayed=True)
        own = {r.report_id for r in self._own(author_id)}
        deleted = [i for i in report_ids if i in own]
        for report_id in deleted:
            del self.reports[report_id]
            del self.entities[report_id]
        audit = None
        if deleted:
            audit = DeletionAuditRecord(
                audit_id=uuid4(),
                plan_id=plan_id,
                author_id=author_id,
                deleted_at=datetime.now(UTC),
                report_ids=deleted,
                titles=[titles[i] for i in deleted],
            )
            self.audits[plan_id] = audit
        gone = [i for i in report_ids if i not in own]
        return DeletionReceipt(audit=audit, already_gone=gone, replayed=False)

    def _own(self, author_id: UserId) -> list[SavedReport]:
        return [r for r in self.reports.values() if r.author_id == author_id]

    def _reasons(self, report: SavedReport, f: DeletionFilter, conversation_id: UUID) -> str:
        """Why ``report`` matches ``f``; empty when it does not."""
        if f.all_reports:
            return match_reason(f, [])
        if f.this_conversation and report.conversation_id != conversation_id:
            return ""
        words = _words(f"{report.title} {report.question} {report.markdown}")
        keys = {e.normalized for e in self.entities[report.report_id]}
        hits = [e for e in f.entities if normalize_entity(e) in keys or _phrase_in(e, words)]
        if f.entities and not hits:
            return ""
        if f.topic and not set(_words(f.topic)) <= set(words):
            return ""
        if f.created:
            day = report.created_at.astimezone(UTC).date()
            start, end = f.created.start, f.created.end
            if (start and day < start) or (end and day > end):
                return ""
        return match_reason(f, hits)


def _words(text: str) -> list[str]:
    return re.findall(r"[^\W_]+", text.casefold())


def _phrase_in(phrase: str, words: list[str]) -> bool:
    want = _words(phrase)
    n = len(want)
    return n > 0 and any(words[i : i + n] == want for i in range(len(words) - n + 1))
