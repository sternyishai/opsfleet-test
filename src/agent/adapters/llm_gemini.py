"""LLMFactory on Gemini through ``langchain-google-genai`` (AI Studio key).

Each step's chat model is its primary and fallback model (``MODEL_<STEP>`` and
``MODEL_<STEP>_FALLBACK``) wrapped by ``ResilientChatModel``: the step's timeout, the
per-model token bucket and circuit breaker, tenacity retries on 429/5xx/timeout honouring the
provider's retry delay, then the fallback, then ``TransientError(MODEL_BUSY)``.

Breakers and buckets are per model, not per provider: a Flash outage or quota leaves
Flash-Lite, the fallback, callable.

SDK gotchas (``retries`` semantics, ``$defs``, free-tier quota): docs/lld/06-ports.md § LLMFactory.
"""

import re
import time
from collections.abc import Callable

import httpx
from google.genai import errors as genai_errors
from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import RunnableLambda
from langchain_google_genai import ChatGoogleGenerativeAI, GoogleGenerativeAIEmbeddings
from langchain_google_genai._common import GoogleGenerativeAIError
from pydantic import SecretStr

from agent.adapters.llm_resilient import (
    LlmPolicy,
    ModelOption,
    ResilientChatModel,
    ResilientEmbeddings,
    ResilientRunnable,
    is_retryable,
)
from agent.adapters.resilience import CircuitBreaker, Clock, Retry, Sleep, TokenBucket
from agent.config import Limits, ModelSettings
from agent.db import EMBEDDING_DIMENSIONS
from agent.errors import PermanentError
from agent.ports.llm import ModelStep
from agent.ports.tracer import Tracer

_PROVIDERS = {"google_genai", "gemini", "google"}

type BuildModel = Callable[[str, float], BaseChatModel]
"""``(model name, timeout seconds) -> chat model``; tests pass scripted models."""

EMBEDDING_STEP = "embedding"
"""``llm.step`` on the span of an embedding call; not a ``ModelStep`` (no fallback pair)."""

type BuildEmbeddings = Callable[[str], Embeddings]
"""``model name -> embeddings``; tests pass scripted ones."""


class GeminiLLMFactory:
    def __init__(
        self,
        models: ModelSettings,
        limits: Limits,
        api_key: SecretStr | None,
        *,
        tracer: Tracer,
        build: BuildModel | None = None,
        build_embeddings: BuildEmbeddings | None = None,
        sleep: Sleep = time.sleep,
        clock: Clock = time.monotonic,
    ) -> None:
        self._models = models
        self._limits = limits
        self._api_key = api_key
        self._build = build or self._gemini
        self._build_embeddings = build_embeddings or self._gemini_embeddings
        self._embeddings: Embeddings | None = None
        self._clock = clock
        self._chats: dict[ModelStep, BaseChatModel] = {}
        self._breakers: dict[str, CircuitBreaker] = {}
        self._buckets: dict[str, TokenBucket] = {}
        self._policy = LlmPolicy(
            dependency="gemini",
            retry=Retry(
                attempts=limits.retry_attempts,
                max_wait_s=limits.retry_max_wait_s,
                is_retryable=gemini_retryable,
                retry_after=retry_after,
                sleep=sleep,
            ),
            max_queue_s=limits.llm_max_queue_s,
            tracer=tracer,
            sleep=sleep,
            clock=clock,
        )

    def chat(self, step: ModelStep) -> BaseChatModel:
        if step not in self._chats:
            primary, fallback = self._models.resolve(step)
            names = list(dict.fromkeys(n for n in (primary, fallback) if n))
            timeout = self._timeout(step)
            options = [
                ModelOption(
                    name=model_name(n),
                    runnable=self._build(model_name(n), timeout),
                    breaker=self._breaker(model_name(n)),
                    limiter=self._bucket(model_name(n)),
                )
                for n in names
            ]
            self._chats[step] = ResilientChatModel(
                chain=ResilientRunnable(step.value, options, self._policy)
            )
        return self._chats[step]

    def embeddings(self) -> Embeddings:
        if self._embeddings is None:
            if self._models.embedding is None:
                raise PermanentError("MODEL_EMBEDDING is not set")
            name = model_name(self._models.embedding)
            base = self._build_embeddings(name)

            def embed(request: str | list[str]) -> list[float] | list[list[float]]:
                if isinstance(request, str):
                    return base.embed_query(request)
                return base.embed_documents(request)

            option = ModelOption(
                name=name,
                runnable=RunnableLambda(embed),
                breaker=self._breaker(name),
                limiter=self._bucket(name),
            )
            self._embeddings = ResilientEmbeddings(EMBEDDING_STEP, option, self._policy)
        return self._embeddings

    def _gemini_embeddings(self, name: str) -> Embeddings:
        return GoogleGenerativeAIEmbeddings(
            model=name,
            api_key=self._api_key,
            output_dimensionality=EMBEDDING_DIMENSIONS,
        )

    def _gemini(self, name: str, timeout: float) -> BaseChatModel:
        return ChatGoogleGenerativeAI(
            model=name,
            api_key=self._api_key,
            request_timeout=timeout,
            retries=1,  # 1 = one attempt (0 would mean the SDK default of 5); we retry ourselves
        )

    def _breaker(self, name: str) -> CircuitBreaker:
        if name not in self._breakers:
            self._breakers[name] = CircuitBreaker(
                failures=self._limits.breaker_failures,
                window_s=self._limits.breaker_window_s,
                cooldown_s=self._limits.breaker_cooldown_s,
                clock=self._clock,
            )
        return self._breakers[name]

    def _bucket(self, name: str) -> TokenBucket:
        if name not in self._buckets:
            self._buckets[name] = TokenBucket(
                per_minute=self._limits.llm_requests_per_minute,
                burst=self._limits.llm_burst,
                clock=self._clock,
            )
        return self._buckets[name]

    def _timeout(self, step: ModelStep) -> float:
        if step is ModelStep.ROUTER:
            return self._limits.timeout_router_s
        return self._limits.timeout_analyst_s


def model_name(setting: str) -> str:
    """``provider:model`` or a bare model name, which means Gemini."""
    provider, sep, name = setting.partition(":")
    if not sep:
        return setting
    if provider not in _PROVIDERS:
        raise ValueError(f"this factory serves Gemini only, not {provider!r}")
    return name


def gemini_retryable(exc: BaseException) -> bool:
    """LangChain's tags, plus what the SDK can raise unwrapped: transport errors and raw
    ``ServerError`` / 429 ``ClientError``."""
    if is_retryable(exc):
        return True
    if isinstance(exc, GoogleGenerativeAIError) and exc.__cause__ is not None:
        return gemini_retryable(exc.__cause__)  # the embeddings class wraps every SDK error
    if isinstance(exc, httpx.TimeoutException | httpx.TransportError | genai_errors.ServerError):
        return True
    return isinstance(exc, genai_errors.ClientError) and exc.code == 429


_SECONDS = re.compile(r"^(\d+(?:\.\d+)?)s$")


def retry_after(exc: BaseException) -> float | None:
    """The provider's retry delay: an HTTP ``Retry-After`` header, or the ``RetryInfo`` detail
    Gemini puts in a 429 body (``"retryDelay": "33s"``). Looked for on the exception and the
    SDK error it wraps."""
    seen: BaseException | None = exc
    while seen is not None:
        headers = getattr(getattr(seen, "response", None), "headers", None)
        header = headers.get("retry-after") if headers is not None else None
        if header is not None:
            try:
                return float(header)
            except ValueError:
                pass
        details = getattr(seen, "details", None)
        error = details.get("error", details) if isinstance(details, dict) else None
        for item in error.get("details", []) if isinstance(error, dict) else []:
            delay = item.get("retryDelay") if isinstance(item, dict) else None
            if isinstance(delay, str) and (match := _SECONDS.match(delay)):
                return float(match.group(1))
        seen = seen.__cause__
    return None
