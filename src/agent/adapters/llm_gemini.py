"""LLMFactory on Gemini through ``langchain-google-genai`` (AI Studio key).

Slice 1: one model per step, the step's timeout, no retries. Retries, the token bucket, the
circuit breaker, the fallback chain and provider-error classification arrive with slice 3;
until then a provider error reaches the graph-boundary catch-all as ``UNCLASSIFIED``.
"""

from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel
from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import SecretStr

from agent.config import Limits, ModelSettings
from agent.ports.llm import ModelStep

_PROVIDERS = {"google_genai", "gemini", "google"}


class GeminiLLMFactory:
    def __init__(self, models: ModelSettings, limits: Limits, api_key: SecretStr | None) -> None:
        self._models = models
        self._limits = limits
        self._api_key = api_key
        self._chats: dict[ModelStep, BaseChatModel] = {}

    def chat(self, step: ModelStep) -> BaseChatModel:
        if step not in self._chats:
            primary, _fallback = self._models.resolve(step)  # fallback: slice 3
            self._chats[step] = ChatGoogleGenerativeAI(
                model=model_name(primary),
                api_key=self._api_key,
                request_timeout=self._timeout(step),
                retries=0,
            )
        return self._chats[step]

    def embeddings(self) -> Embeddings:
        raise NotImplementedError("embeddings arrive with the Trio slice (7)")

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
