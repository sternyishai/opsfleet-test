"""Fakes for the ports, so tests cross the same interfaces the graph uses (LLD 06).

The query guard is never faked: safety tests use the real ``SqlglotQueryGuard``.
"""

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
from agent.errors import ErrorClass
from agent.graph.build import build_graph
from agent.graph.state import CHECKPOINT_TYPES, AgentGraph
from agent.ports.llm import ModelStep
from agent.tools.context import TurnContext


class ScriptedChatModel(GenericFakeChatModel):
    """Replies with the scripted messages in order. ``bind_tools`` is a no-op, and
    ``with_structured_output`` returns the scripted structured value."""

    structured: Any = None
    seen: list[list[BaseMessage]] = []

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> Runnable:  # type: ignore[override]
        return self

    def with_structured_output(self, schema: Any, **kwargs: Any) -> Runnable:  # type: ignore[override]
        return RunnableLambda(lambda _: self.structured)

    def invoke(self, input: Any, config: Any = None, **kwargs: Any) -> AIMessage:  # type: ignore[override]
        self.seen.append(list(input))
        return super().invoke(input, config, **kwargs)  # type: ignore[return-value]


def analysis_decision(question: str = "total revenue in 2025") -> RouterDecision:
    return RouterDecision(verdict=Verdict.ALLOWED, action=Action.ANALYSIS, search_question=question)


class FakeLLMFactory:
    """The router returns ``decision``; the analyst replies with ``analyst`` in order.
    ``fail`` makes every ``chat`` call raise it, like a model outage; ``router_fail`` only the
    router's. ``steps`` records every model asked for."""

    def __init__(
        self,
        analyst: list[AIMessage] | None = None,
        decision: RouterDecision | None = None,
        fail: Exception | None = None,
        router_fail: Exception | None = None,
    ) -> None:
        self.analyst = ScriptedChatModel(messages=iter(analyst or []), seen=[])
        self.router = ScriptedChatModel(
            messages=iter([]), structured=decision or analysis_decision(), seen=[]
        )
        self.fail = fail
        self.router_fail = router_fail
        self.steps: list[ModelStep] = []

    def chat(self, step: ModelStep) -> BaseChatModel:
        self.steps.append(step)
        if self.fail is not None:
            raise self.fail
        if step is ModelStep.ROUTER and self.router_fail is not None:
            raise self.router_fail
        return self.router if step is ModelStep.ROUTER else self.analyst

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
    )


def turn_context(user: UserContext, ports: Ports | None = None) -> TurnContext:
    return TurnContext(
        user=user,
        conversation_id=uuid4(),
        turn_id="t1",
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
