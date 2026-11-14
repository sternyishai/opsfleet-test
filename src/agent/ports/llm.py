"""LLMFactory: every model call goes through one factory (HLD 06, HLD 10)."""

from enum import StrEnum
from typing import Protocol

from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel


class ModelStep(StrEnum):
    """One step per separate model call, each with its own setting.

    Rule: **every separate model call is its own step**, so its model can be changed, evaluated
    and given a fallback on its own. Settings are ``MODEL_<STEP>`` and ``MODEL_<STEP>_FALLBACK``
    in ``.env``. An unset step inherits from ``INHERITS_FROM`` (see ``agent.config``), so a
    short ``.env`` still works.
    """

    # chat path
    ROUTER = "router"  # input guard + intent router: one call (Flash-Lite)
    DELETION_FILTER = "deletion_filter"  # delete request -> DeletionFilter (Flash-Lite)
    ANALYST = "analyst"  # each ReAct step of the analysis sub-agent (Flash)
    REPORT = "report"  # long report generation (Pro allowed)
    SUMMARIZER = "summarizer"  # folds older turns into the running summary (Flash-Lite)
    ENTITY_EXTRACTION = "entity_extraction"  # entities of a Saved Report (Flash-Lite)
    # Persona branch
    PERSONA_DRAFTER = "persona_drafter"  # CEO request -> structured Persona fields (Flash)
    PERSONA_GATE = "persona_gate"  # gate checks 3 and 4: answer writing with the candidate
    # background and offline
    TRIO_INGEST = "trio_ingest"  # method summary with results removed (Flash)
    PREFERENCE_INFERENCE = "preference_inference"  # daily job, production only
    TRIO_CANDIDATE = "trio_candidate"  # Candidate generation, off until Q1
    JUDGE = "judge"  # offline eval and online judge (Pro)


INHERITS_FROM: dict[ModelStep, ModelStep | None] = {
    ModelStep.ROUTER: None,  # required
    ModelStep.ANALYST: None,  # required
    ModelStep.DELETION_FILTER: ModelStep.ROUTER,
    ModelStep.SUMMARIZER: ModelStep.ROUTER,
    ModelStep.ENTITY_EXTRACTION: ModelStep.ROUTER,
    ModelStep.PREFERENCE_INFERENCE: ModelStep.ROUTER,
    ModelStep.REPORT: ModelStep.ANALYST,
    ModelStep.PERSONA_DRAFTER: ModelStep.ANALYST,
    # Must test what Users get: it follows the answer-writing model unless set on purpose.
    ModelStep.PERSONA_GATE: ModelStep.ANALYST,
    ModelStep.TRIO_INGEST: ModelStep.ANALYST,
    ModelStep.TRIO_CANDIDATE: ModelStep.ANALYST,
    ModelStep.JUDGE: ModelStep.ANALYST,
}
"""Where an unset step takes its model from. The fallback is inherited the same way."""


class LLMFactory(Protocol):
    """Interface:

    - ``chat(step)`` returns a LangChain chat model for that step, already wrapped with the
      step's timeout, retryable-error backoff (429, 5xx, timeout; honours ``Retry-After``; at
      most 3 attempts), the per-model token-bucket rate limiter, the per-provider circuit
      breaker, and the step's fallback chain (Flash -> Flash-Lite in the prototype).
    - The model for a step is resolved from its own setting, else ``INHERITS_FROM``.
    - Callers never see a provider exception. When every option fails, the model raises
      ``TransientError(dependency="gemini", message_class=MODEL_BUSY)``.
    - Which model answered (primary or fallback) and the step name are recorded on the current
      span by the factory, so callers do not track them.
    - Settings have the form ``provider:model``; changing one is a ``.env`` change.
    """

    def chat(self, step: ModelStep) -> BaseChatModel: ...

    def embeddings(self) -> Embeddings:
        """``MODEL_EMBEDDING``. Same wrapping, no fallback model (vectors are not comparable
        across embedding models)."""
        ...
