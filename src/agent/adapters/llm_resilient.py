"""A chat model that runs a step's fallback chain under the resilience policy (HLD 06).

Provider-neutral: the provider adapter builds the inner models and says which of its
exceptions are retryable. For each model in the chain (primary, then fallback): the breaker,
the rate limiter, then up to ``attempts`` tries with backoff. When every model is spent the call
raises ``TransientError(MODEL_BUSY)``. A non-retryable error (400, permission, unknown model)
is raised at once as ``PermanentError``, without fallback: another model would get the same
bad request. Anything unrecognised becomes an unclassified ``AgentError``. No provider
exception leaves this module.

``bind_tools`` and ``with_structured_output`` are applied to every model in the chain, so a
bound or structured call keeps the same fallback.
"""

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.exceptions import ModelError
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult, LLMResult
from langchain_core.runnables import Runnable, RunnableConfig
from langchain_core.runnables.config import ensure_config, merge_configs

from agent.adapters.resilience import CircuitBreaker, Clock, Retry, Sleep, TokenBucket, Unavailable
from agent.errors import AgentError, MessageClass, PermanentError, TransientError
from agent.ports.tracer import Tracer


@dataclass(frozen=True, slots=True)
class ModelOption:
    """One model of a step's chain, with the breaker and limiter of that model (shared by
    every step that uses it: quota and outages are per model)."""

    name: str
    runnable: Runnable[Any, Any]
    breaker: CircuitBreaker
    limiter: TokenBucket


def is_retryable(exc: BaseException) -> bool:
    """LangChain tags provider errors: 429, 5xx, connection and timeout are retryable."""
    if isinstance(exc, ModelError):
        return exc.is_retryable
    return isinstance(exc, TimeoutError | ConnectionError)


class LlmPolicy:
    """Runs one call down a step's chain of models: each model behind its breaker, limiter
    and retries, the next model when one is spent, ``MODEL_BUSY`` when all are. Records on an
    ``llm`` span which model answered and why the others did not."""

    def __init__(
        self,
        *,
        dependency: str,
        retry: Retry,
        max_queue_s: float,
        tracer: Tracer,
        sleep: Sleep,
        clock: Clock,
    ) -> None:
        self._dependency = dependency
        self._retry = retry
        self._max_queue_s = max_queue_s
        self._tracer = tracer
        self._sleep = sleep
        self._clock = clock

    def run(
        self,
        step: str,
        options: Sequence[ModelOption],
        call: Callable[[Runnable[Any, Any], RunnableConfig], Any],
        config: RunnableConfig | None,
    ) -> Any:
        with self._tracer.span("llm", **{"llm.step": step}) as span:
            started = time.perf_counter()
            errors: list[str] = []  # "model: ErrorType" per model that failed, for the trace
            for index, option in enumerate(options):
                usage = _Usage()
                cfg = merge_configs(ensure_config(config), {"callbacks": [usage]})
                try:
                    value, attempts = self._retry.call(
                        lambda option=option, cfg=cfg: self._admitted(option, call, cfg),
                        option.breaker,
                    )
                except Unavailable as exc:
                    errors.append(f"{option.name}: {exc}")
                    continue
                except Exception as exc:
                    errors.append(f"{option.name}: {type(exc).__name__}")
                    if self._retry.is_retryable(exc):
                        continue  # this model's retries are spent: next in the chain
                    span.set(**{"llm.model": None, "llm.errors": errors})
                    raise _classified(exc) from exc
                span.set(
                    **{
                        "llm.model": option.name,
                        "llm.fallback_used": index > 0,
                        "llm.attempts": attempts,
                        "llm.errors": errors,
                        "breaker.state": option.breaker.state,
                        "llm.tokens.prompt": usage.prompt,
                        "llm.tokens.completion": usage.completion,
                        "llm.latency_ms": int((time.perf_counter() - started) * 1000),
                    }
                )
                return value
            span.set(
                **{
                    "llm.model": None,
                    "llm.fallback_used": len(options) > 1,
                    "llm.errors": errors,
                    "breaker.state": options[0].breaker.state,
                    "llm.latency_ms": int((time.perf_counter() - started) * 1000),
                }
            )
        raise TransientError(
            f"every model for {step} failed",
            dependency=self._dependency,
            message_class=MessageClass.MODEL_BUSY,
        )

    def _admitted(
        self,
        option: ModelOption,
        call: Callable[[Runnable[Any, Any], RunnableConfig], Any],
        cfg: RunnableConfig,
    ) -> Any:
        wait = option.limiter.reserve(max_wait_s=self._max_queue_s)
        if wait is None:
            raise Unavailable("rate limit: the wait is longer than a turn may queue")
        if wait:
            self._sleep(wait)
        return call(option.runnable, cfg)


def _classified(exc: Exception) -> AgentError:
    if isinstance(exc, AgentError):
        return exc
    detail = f"{type(exc).__name__}: {exc}"[:500]
    if isinstance(exc, ModelError):  # known and not retryable: our request or our config
        return PermanentError(detail)
    return AgentError(detail)  # unclassified: traced and alerted at the graph boundary


class _Usage(BaseCallbackHandler):
    """Token counts of the call that answered."""

    def __init__(self) -> None:
        self.prompt = 0
        self.completion = 0

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        for generations in response.generations:
            for generation in generations:
                message = getattr(generation, "message", None)
                usage = getattr(message, "usage_metadata", None)
                if usage:
                    self.prompt += usage.get("input_tokens", 0)
                    self.completion += usage.get("output_tokens", 0)


class ResilientRunnable(Runnable[Any, Any]):
    """The chain of one step, as a Runnable; ``derive`` gives a bound or structured chain."""

    def __init__(self, step: str, options: Sequence[ModelOption], policy: LlmPolicy) -> None:
        self._step = step
        self._options = tuple(options)
        self._policy = policy

    def invoke(self, input: Any, config: RunnableConfig | None = None, **kwargs: Any) -> Any:
        return self._policy.run(
            self._step,
            self._options,
            lambda runnable, cfg: runnable.invoke(input, cfg, **kwargs),
            config,
        )

    def derive(self, apply: Callable[[Any], Runnable[Any, Any]]) -> "ResilientRunnable":
        options = [replace(o, runnable=apply(o.runnable)) for o in self._options]
        return ResilientRunnable(self._step, options, self._policy)


class ResilientChatModel(BaseChatModel):
    """What ``LLMFactory.chat(step)`` returns: a chat model whose every call runs the chain."""

    chain: Any  # ResilientRunnable; Any keeps pydantic from validating a Runnable

    @property
    def _llm_type(self) -> str:
        return "resilient"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        message = self.chain.invoke(messages, stop=stop, **kwargs)
        if not isinstance(message, AIMessage):  # never a bare AssertionError across the port
            raise AgentError(f"chat model returned {type(message).__name__}")
        return ChatResult(generations=[ChatGeneration(message=message)])

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> Runnable[Any, Any]:  # type: ignore[override]
        return self.chain.derive(lambda model: model.bind_tools(tools, **kwargs))

    def with_structured_output(self, schema: Any, **kwargs: Any) -> Runnable[Any, Any]:  # type: ignore[override]
        return self.chain.derive(lambda model: model.with_structured_output(schema, **kwargs))
