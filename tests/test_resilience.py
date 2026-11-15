"""Third-party failures (HLD 06, LLD 06 § LLMFactory, § QueryRunner): retries, fallback, the
rate limiter, the circuit breaker and the graph-boundary catch-all.

The Gemini factory is driven through ``chat(step)`` with scripted inner models that raise the
real ``langchain-google-genai`` error types; sleep and the clock are fakes, so nothing waits.
"""

from collections.abc import Sequence
from types import SimpleNamespace
from typing import Any

import pytest
import requests
from google.api_core import exceptions as gexc
from google.genai.errors import ClientError
from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable, RunnableLambda
from langchain_google_genai._common import GoogleGenerativeAIError
from langchain_google_genai.chat_models import (
    GoogleAPIError,
    GoogleInvalidRequestError,
    GoogleRateLimitError,
)
from pydantic import BaseModel

from agent.adapters.bigquery import BigQueryRunner
from agent.adapters.llm_gemini import GeminiLLMFactory
from agent.config import Limits, ModelSettings
from agent.contracts.channel import AgentReply
from agent.core.catalog import BUNDLED, render
from agent.domain.identity import Role, UserContext
from agent.errors import (
    ErrorClass,
    MessageClass,
    PermanentError,
    SelfCorrectableError,
    TransientError,
)
from agent.ports.llm import ModelStep
from tests.fakes import SEED_PERSONA, FakeLLMFactory, FakeTracer, ask, chat_service, fake_ports

PRIMARY = "gemini-2.5-flash"
FALLBACK = "gemini-2.5-flash-lite"
OK = AIMessage(
    content="Revenue was $2.",
    usage_metadata={"input_tokens": 120, "output_tokens": 8, "total_tokens": 128},
)


class FlakyChatModel(BaseChatModel):
    """Raises or returns the scripted outcomes in order; the last one repeats."""

    script: list[Any]
    calls: int = 0

    @property
    def _llm_type(self) -> str:
        return "flaky"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        outcome = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1
        if isinstance(outcome, Exception):
            raise outcome
        return ChatResult(generations=[ChatGeneration(message=outcome)])

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> Runnable:  # type: ignore[override]
        return self

    def with_structured_output(self, schema: Any, **kwargs: Any) -> Runnable:  # type: ignore[override]
        def parse(message: AIMessage) -> Any:
            return schema(answer=message.text)

        return self | RunnableLambda(parse)


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def rate_limited(retry_delay: str | None = None) -> GoogleRateLimitError:
    """A 429 as langchain-google-genai raises it: wrapping the SDK's ClientError."""
    details = [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": retry_delay}]
    body = {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "details": details}}
    error = GoogleRateLimitError("Error calling model (RESOURCE_EXHAUSTED): quota")
    error.__cause__ = ClientError(429, body if retry_delay else {"error": {"code": 429}})
    return error


def unavailable() -> GoogleAPIError:
    return GoogleAPIError(503, {"error": {"code": 503, "status": "UNAVAILABLE"}})


def factory(
    primary: list[Any], fallback: list[Any] | None = None, **limits: Any
) -> tuple[GeminiLLMFactory, dict[str, FlakyChatModel], FakeTracer, Clock]:
    models = {PRIMARY: FlakyChatModel(script=primary)}
    if fallback is not None:
        models[FALLBACK] = FlakyChatModel(script=fallback)
    settings = ModelSettings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        router=FALLBACK,
        analyst=PRIMARY,
        analyst_fallback=FALLBACK if fallback is not None else None,
    )
    limits.setdefault("llm_requests_per_minute", 6000)  # no limiter waits unless a test asks
    limits.setdefault("llm_burst", 100)
    tracer, clock = FakeTracer(), Clock()
    llm = GeminiLLMFactory(
        settings,
        Limits(_env_file=None, **limits),  # pyright: ignore[reportCallIssue]
        api_key=None,
        tracer=tracer,
        build=lambda name, _timeout: models[name],
        sleep=clock.sleep,
        clock=clock,
    )
    return llm, models, tracer, clock


def llm_spans(tracer: FakeTracer) -> list[dict[str, Any]]:
    return [s.attributes for s in tracer.spans if s.name == "llm"]


# --- retries ---------------------------------------------------------------------------------


def test_a_429_burst_is_retried_with_backoff_then_succeeds() -> None:
    llm, models, tracer, clock = factory([rate_limited(), rate_limited(), OK])

    reply = llm.chat(ModelStep.ANALYST).invoke("revenue?")

    assert reply.text == "Revenue was $2."
    assert models[PRIMARY].calls == 3
    assert len(clock.slept) == 2 and all(0 < s <= 10 for s in clock.slept)
    [span] = llm_spans(tracer)
    assert span["llm.step"] == "analyst"
    assert span["llm.model"] == PRIMARY
    assert span["llm.fallback_used"] is False
    assert span["llm.attempts"] == 3
    assert span["breaker.state"] == "closed"
    assert span["llm.tokens.prompt"] == 120
    assert span["llm.tokens.completion"] == 8
    assert isinstance(span["llm.latency_ms"], int)


def test_retry_after_from_the_provider_is_honoured() -> None:
    llm, _, _, clock = factory([rate_limited(retry_delay="7s"), OK])

    llm.chat(ModelStep.ANALYST).invoke("revenue?")

    assert clock.slept == [7.0]


def test_a_400_is_never_retried_and_is_permanent() -> None:
    llm, models, _, clock = factory(
        [GoogleInvalidRequestError("400 INVALID_ARGUMENT: bad schema")], fallback=[OK]
    )

    with pytest.raises(PermanentError) as raised:
        llm.chat(ModelStep.ANALYST).invoke("revenue?")

    assert models[PRIMARY].calls == 1
    assert models[FALLBACK].calls == 0
    assert clock.slept == []
    assert raised.value.error_class is ErrorClass.PERMANENT


# --- fallback --------------------------------------------------------------------------------


def test_primary_down_falls_back_to_the_steps_fallback_model() -> None:
    llm, models, tracer, _ = factory([unavailable()], fallback=[OK])

    reply = llm.chat(ModelStep.ANALYST).invoke("revenue?")

    assert reply.text == "Revenue was $2."
    assert models[PRIMARY].calls == 3  # retried within its budget first
    [span] = llm_spans(tracer)
    assert span["llm.model"] == FALLBACK
    assert span["llm.fallback_used"] is True
    assert span["llm.errors"] == [f"{PRIMARY}: GoogleAPIError"]


def test_every_model_down_is_model_busy() -> None:
    llm, _, tracer, _ = factory([unavailable()], fallback=[unavailable()])

    with pytest.raises(TransientError) as raised:
        llm.chat(ModelStep.ANALYST).invoke("revenue?")

    assert raised.value.message_class is MessageClass.MODEL_BUSY
    assert raised.value.dependency == "gemini"
    [span] = llm_spans(tracer)
    assert span["llm.model"] is None
    assert span["llm.errors"] == [f"{PRIMARY}: GoogleAPIError", f"{FALLBACK}: GoogleAPIError"]


def test_structured_output_and_bound_tools_get_the_same_fallback() -> None:
    class Answer(BaseModel):
        answer: str

    llm, _, _, _ = factory([unavailable()], fallback=[OK])

    structured = llm.chat(ModelStep.ANALYST).with_structured_output(Answer).invoke("revenue?")
    tooled = llm.chat(ModelStep.ANALYST).bind_tools([]).invoke("revenue?")

    assert structured == Answer(answer="Revenue was $2.")
    assert isinstance(tooled, AIMessage) and tooled.text == "Revenue was $2."


# --- breaker and rate limiter ----------------------------------------------------------------


def test_the_breaker_opens_after_5_failures_in_30_s_and_fails_fast() -> None:
    llm, models, tracer, _ = factory([unavailable()], retry_attempts=1)
    analyst = llm.chat(ModelStep.ANALYST)
    for _ in range(5):
        with pytest.raises(TransientError):
            analyst.invoke("revenue?")
    assert models[PRIMARY].calls == 5

    with pytest.raises(TransientError):
        analyst.invoke("revenue?")

    assert models[PRIMARY].calls == 5  # open: not called
    assert llm_spans(tracer)[-1]["breaker.state"] == "open"
    assert llm_spans(tracer)[-1]["llm.errors"] == [f"{PRIMARY}: circuit breaker open"]


def test_failures_spread_over_more_than_30_s_do_not_open_the_breaker() -> None:
    llm, models, _, clock = factory([unavailable()], retry_attempts=1)
    analyst = llm.chat(ModelStep.ANALYST)
    for _ in range(6):
        with pytest.raises(TransientError):
            analyst.invoke("revenue?")
        clock.now += 10

    assert models[PRIMARY].calls == 6


def test_after_the_cool_down_one_probe_call_closes_the_breaker() -> None:
    llm, models, tracer, clock = factory([unavailable()], retry_attempts=1)
    analyst = llm.chat(ModelStep.ANALYST)
    for _ in range(5):
        with pytest.raises(TransientError):
            analyst.invoke("revenue?")
    models[PRIMARY].script = [OK]
    clock.now += 31

    assert analyst.invoke("revenue?").text == "Revenue was $2."
    assert llm_spans(tracer)[-1]["breaker.state"] == "closed"


def test_the_rate_limiter_makes_a_burst_wait_instead_of_hitting_429() -> None:
    llm, models, _, clock = factory([OK], llm_requests_per_minute=6, llm_burst=1)
    analyst = llm.chat(ModelStep.ANALYST)

    analyst.invoke("one")
    analyst.invoke("two")

    assert models[PRIMARY].calls == 2
    assert clock.slept == [10.0]  # 6 a minute: one every 10 s


def test_a_wait_longer_than_the_queue_limit_is_model_busy_not_a_hang() -> None:
    llm, models, _, _ = factory([OK], llm_requests_per_minute=1, llm_burst=1, llm_max_queue_s=5)
    analyst = llm.chat(ModelStep.ANALYST)
    analyst.invoke("one")

    with pytest.raises(TransientError) as raised:
        analyst.invoke("two")

    assert raised.value.message_class is MessageClass.MODEL_BUSY
    assert models[PRIMARY].calls == 1


def test_unknown_provider_exceptions_never_cross_the_port() -> None:
    llm, _, _, _ = factory([ValueError("SDK internals")])

    with pytest.raises(Exception) as raised:
        llm.chat(ModelStep.ANALYST).invoke("revenue?")

    assert raised.value.__class__.__module__ == "agent.errors"
    assert getattr(raised.value, "error_class", None) is ErrorClass.UNCLASSIFIED


# --- QueryRunner on BigQuery ------------------------------------------------------------------


class FakeJob:
    def __init__(self, outcome: Any, bytes_processed: int = 1024) -> None:
        self.outcome = outcome
        self.total_bytes_processed = bytes_processed
        self.total_bytes_billed = bytes_processed

    def result(self, **kwargs: Any) -> Any:
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class FakeRows(list):
    def __init__(self, records: list[dict[str, Any]]) -> None:
        super().__init__(FakeRow(r) for r in records)
        self.schema = [SimpleNamespace(name=k) for k in (records[0] if records else {})]
        self.total_rows = len(records)


class FakeRow(dict):
    pass


class FakeBigQueryClient:
    """``query`` raises or returns the scripted outcomes in order; the last one repeats."""

    def __init__(self, *script: Any) -> None:
        self.script = list(script)
        self.calls = 0

    def query(self, sql: str, **kwargs: Any) -> FakeJob:
        outcome = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1
        if isinstance(outcome, Exception):
            raise outcome
        return FakeJob(outcome)


def runner(client: FakeBigQueryClient, **limits: Any) -> tuple[BigQueryRunner, Clock]:
    clock = Clock()
    bq = BigQueryRunner(
        project="p",
        dataset="bigquery-public-data.thelook_ecommerce",
        limits=Limits(_env_file=None, **limits),  # pyright: ignore[reportCallIssue]
        client=client,
        sleep=clock.sleep,
        clock=clock,
    )
    return bq, clock


def test_a_bigquery_backend_error_is_retried_then_the_rows_come_back() -> None:
    client = FakeBigQueryClient(gexc.ServiceUnavailable("backendError"), FakeRows([{"n": 3}]))
    bq, clock = runner(client)

    result = bq.execute("SELECT 3 AS n", {}, max_bytes=10, max_rows=5)

    assert result.rows == [{"n": 3}]
    assert client.calls == 2
    assert len(clock.slept) == 1


def test_bigquery_down_is_data_source_down_and_the_breaker_then_fails_fast() -> None:
    client = FakeBigQueryClient(gexc.ServiceUnavailable("backendError"))
    bq, _ = runner(client)

    with pytest.raises(TransientError) as raised:
        bq.execute("SELECT 1", {}, max_bytes=10, max_rows=5)
    assert raised.value.message_class is MessageClass.DATA_SOURCE_DOWN
    assert raised.value.dependency == "bigquery"
    with pytest.raises(TransientError):
        bq.dry_run("SELECT 1", {}, max_bytes=10)  # 3 + 2 attempts: 5 failures open it
    calls = client.calls

    with pytest.raises(TransientError) as fast:
        bq.execute("SELECT 1", {}, max_bytes=10, max_rows=5)

    assert client.calls == calls  # not called while open
    assert fast.value.message_class is MessageClass.DATA_SOURCE_DOWN


def test_a_bad_query_is_self_correctable_cleaned_and_never_retried() -> None:
    bad = gexc.BadRequest(
        "Unrecognized name: sale_prise",
        errors=[{"message": "Unrecognized name: sale_prise at [1:12]"}],
    )
    client = FakeBigQueryClient(bad)
    bq, clock = runner(client)

    with pytest.raises(SelfCorrectableError) as raised:
        bq.dry_run("SELECT sale_prise FROM order_items", {}, max_bytes=10)

    assert raised.value.detail == "Unrecognized name: sale_prise at [1:12]"
    assert client.calls == 1
    assert clock.slept == []


def test_a_permission_error_is_permanent_not_retried() -> None:
    client = FakeBigQueryClient(gexc.Forbidden("Access Denied: Project p"))
    bq, _ = runner(client)

    with pytest.raises(PermanentError) as raised:
        bq.execute("SELECT 1", {}, max_bytes=10, max_rows=5)

    assert raised.value.message_class is MessageClass.DATA_SOURCE_DOWN
    assert client.calls == 1


# --- the graph-boundary catch-all -------------------------------------------------------------

CEO = UserContext(user_id="ceo", role=Role.EXECUTIVE, access_version=1)


def test_an_unclassified_exception_becomes_the_generic_message_with_its_reference() -> None:
    ports = fake_ports(CEO, llm=FakeLLMFactory(fail=KeyError("internal state")))
    service, _ = chat_service(ports)

    [reply], _ = ask(service, "ceo", "What was revenue in 2025?")

    assert isinstance(reply, AgentReply)
    assert reply.message_class is MessageClass.UNCLASSIFIED
    assert reply.markdown == BUNDLED[MessageClass.UNCLASSIFIED].replace("{ref}", reply.trace_id)
    tracer = ports.tracer
    assert isinstance(tracer, FakeTracer)
    [turn] = [s for s in tracer.spans if s.name == "turn"]
    assert turn.errors == [ErrorClass.UNCLASSIFIED]


def test_a_classified_error_becomes_its_catalog_message() -> None:
    busy = TransientError("all down", dependency="gemini", message_class=MessageClass.MODEL_BUSY)
    service, _ = chat_service(fake_ports(CEO, llm=FakeLLMFactory(fail=busy)))

    [reply], _ = ask(service, "ceo", "What was revenue in 2025?")

    assert isinstance(reply, AgentReply)
    assert reply.message_class is MessageClass.MODEL_BUSY
    assert f"(ref: {reply.trace_id})" in reply.markdown


def test_the_conversation_survives_a_failed_turn() -> None:
    llm = FakeLLMFactory(analyst=[AIMessage(content="It was $2.")])
    service, _ = chat_service(fake_ports(CEO, llm=llm))
    llm.fail = TransientError("down", dependency="gemini", message_class=MessageClass.MODEL_BUSY)
    [failed], _ = ask(service, "ceo", "What was revenue in 2025?")
    llm.fail = None

    [reply], _ = ask(service, "ceo", "And in 2024?", conversation_id=failed.conversation_id)

    assert isinstance(reply, AgentReply)
    assert reply.markdown == "It was $2."
    [analyst_input] = llm.analyst.seen
    asked = [m.text for m in analyst_input if m.type == "human"]
    assert asked == ["What was revenue in 2025?", "And in 2024?"]


# --- the message catalog ----------------------------------------------------------------------


def test_every_message_class_has_bundled_wording() -> None:
    assert set(BUNDLED) == set(MessageClass)


def test_the_persona_wording_wins_and_the_reference_is_filled_in() -> None:
    persona = SEED_PERSONA.persona.model_copy(
        update={"messages": {MessageClass.MODEL_BUSY: "Busy, sorry (ref {ref})."}}
    )

    assert render(MessageClass.MODEL_BUSY, persona, ref="a1b2c3") == "Busy, sorry (ref a1b2c3)."
    assert render(MessageClass.DATA_SOURCE_DOWN, persona, ref="a1b2c3").endswith("(ref: a1b2c3)")


def test_a_non_retryable_error_on_the_half_open_probe_still_closes_the_breaker() -> None:
    llm, models, _, clock = factory([unavailable()], retry_attempts=1)
    analyst = llm.chat(ModelStep.ANALYST)
    for _ in range(5):
        with pytest.raises(TransientError):
            analyst.invoke("revenue?")
    clock.now += 31
    models[PRIMARY].script = [GoogleInvalidRequestError("400 bad request"), OK]
    models[PRIMARY].calls = 0

    with pytest.raises(PermanentError):
        analyst.invoke("revenue?")  # the probe: the model answered, so it is up

    assert analyst.invoke("revenue?").text == "Revenue was $2."


def test_a_bigquery_transport_failure_is_retried_then_data_source_down() -> None:
    client = FakeBigQueryClient(requests.exceptions.ConnectionError("dns"))
    bq, _ = runner(client)

    with pytest.raises(TransientError) as raised:
        bq.execute("SELECT 1", {}, max_bytes=10, max_rows=5)

    assert raised.value.message_class is MessageClass.DATA_SOURCE_DOWN
    assert client.calls == 3


# --- embeddings (slice 7): same retries and breaker, no fallback model ------------------------

EMBEDDER = "gemini-embedding-001"


class FlakyEmbeddings(Embeddings):
    """Raises the scripted errors in order, then returns one 768-dim vector per text."""

    def __init__(self, script: list[Exception]) -> None:
        self.script = list(script)
        self.calls = 0

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        if self.script:
            raise self.script.pop(0)
        return [[0.5] * 768 for _ in texts]

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]


def embedding_rate_limited() -> GoogleGenerativeAIError:
    """A 429 as the embeddings class raises it: its own error, wrapping the SDK's."""
    error = GoogleGenerativeAIError("Error embedding content: quota")
    error.__cause__ = ClientError(429, {"error": {"code": 429}})
    return error


def embedding_factory(script: list[Exception]) -> tuple[FlakyEmbeddings, Embeddings, FakeTracer]:
    flaky = FlakyEmbeddings(script)
    tracer, clock = FakeTracer(), Clock()
    llm = GeminiLLMFactory(
        ModelSettings(_env_file=None, router=FALLBACK, analyst=PRIMARY, embedding=EMBEDDER),  # pyright: ignore[reportCallIssue]
        Limits(_env_file=None, llm_requests_per_minute=6000, llm_burst=100),  # pyright: ignore[reportCallIssue]
        api_key=None,
        tracer=tracer,
        build=lambda name, _timeout: FlakyChatModel(script=[]),
        build_embeddings=lambda name: flaky if name == EMBEDDER else FlakyEmbeddings([]),
        sleep=clock.sleep,
        clock=clock,
    )
    return flaky, llm.embeddings(), tracer


def test_an_embedding_429_is_retried_then_the_vectors_come_back() -> None:
    flaky, embeddings, tracer = embedding_factory([embedding_rate_limited()])

    vectors = embeddings.embed_documents(["a", "b"])

    assert len(vectors) == 2 and len(vectors[0]) == 768
    assert flaky.calls == 2
    [span] = llm_spans(tracer)
    assert span["llm.step"] == "embedding"
    assert span["llm.model"] == EMBEDDER


def test_the_embedding_model_down_is_model_busy() -> None:
    _, embeddings, _ = embedding_factory([embedding_rate_limited()] * 3)

    with pytest.raises(TransientError) as raised:
        embeddings.embed_query("revenue")

    assert raised.value.message_class is MessageClass.MODEL_BUSY
