"""Configuration: the developer-owned lever (HLD 09 § Two kinds of instructions).

Model IDs, fallbacks, limits, budgets and timeouts. Read from ``.env`` at start-up; a change
takes effect on restart. Tone and wording are not here: they are the Persona.

Limits marked *LLD default* are not fixed by the HLD; they are starting values to tune from
traces.
"""

from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from agent.ports.llm import INHERITS_FROM, ModelStep


class ModelSettings(BaseSettings):
    """``provider:model`` per step, one pair per separate model call (``ModelStep``).

    Exact versions live in ``.env``, not in code or docs. Only ``MODEL_ROUTER`` and
    ``MODEL_ANALYST`` are required; every other step inherits per ``INHERITS_FROM``.
    """

    model_config = SettingsConfigDict(env_prefix="MODEL_", env_file=".env", extra="ignore")

    router: str  # Flash-Lite
    router_fallback: str | None = None
    analyst: str  # Flash
    analyst_fallback: str | None = None  # Flash-Lite in the prototype
    deletion_filter: str | None = None
    deletion_filter_fallback: str | None = None
    report: str | None = None  # Pro allowed
    report_fallback: str | None = None
    summarizer: str | None = None
    summarizer_fallback: str | None = None
    entity_extraction: str | None = None
    entity_extraction_fallback: str | None = None
    persona_drafter: str | None = None
    persona_drafter_fallback: str | None = None
    persona_gate: str | None = None
    persona_gate_fallback: str | None = None
    trio_ingest: str | None = None  # Flash
    trio_ingest_fallback: str | None = None
    preference_inference: str | None = None
    preference_inference_fallback: str | None = None
    trio_candidate: str | None = None
    trio_candidate_fallback: str | None = None
    judge: str | None = None  # Pro
    judge_fallback: str | None = None
    embedding: str | None = None  # Gemini embedding model; needed for the Trio slice

    def resolve(self, step: ModelStep) -> tuple[str, str | None]:
        """The (primary, fallback) model for ``step``. Primary and fallback are resolved
        separately: each unset value is taken from the parent step in ``INHERITS_FROM``."""
        primary: str | None = getattr(self, step.value)
        fallback: str | None = getattr(self, f"{step.value}_fallback")
        parent = INHERITS_FROM[step]
        if parent is not None and (primary is None or fallback is None):
            parent_primary, parent_fallback = self.resolve(parent)
            if primary is None:
                primary = parent_primary
            if fallback is None:
                fallback = parent_fallback
        assert primary is not None  # router and analyst are required
        return primary, fallback


class Limits(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # run_sql (HLD 06)
    sql_max_retries: int = Field(default=2, ge=0)
    empty_result_diagnoses: int = Field(default=1, ge=0)
    sql_row_limit: int = Field(default=1000, ge=1, description="Forced outer LIMIT.")
    sql_rows_to_model: int = Field(default=200, ge=1, description="LLD default.")
    max_bytes_billed: int = Field(default=2 * 1024**3, ge=1, description="LLD default: 2 GiB.")
    # analysis loop (HLD 06)
    max_analysis_steps: int = Field(default=8, ge=1, description="LLD default.")
    turn_deadline_s: float = Field(default=90.0, description="LLD default.")
    # retrieval (HLD 02)
    trio_top_k: int = Field(default=3, ge=1)
    trio_min_score: float = Field(default=0.75, ge=0, le=1, description="LLD default.")
    # deletion (HLD 04)
    deletion_plan_shown: int = Field(default=20, ge=1)
    # profile (HLD 05)
    profile_max_notes: int = Field(default=20, ge=1)
    # conversation (HLD 01)
    verbatim_turns: int = Field(default=6, ge=1, description="LLD default.")
    # timeouts, seconds (HLD 06)
    timeout_router_s: float = 10.0
    timeout_analyst_s: float = 60.0
    timeout_bigquery_s: float = 60.0
    timeout_app_db_s: float = 5.0
    # retries and breaker (HLD 06)
    retry_attempts: int = 3
    breaker_failures: int = 5
    breaker_window_s: float = 30.0
    breaker_cooldown_s: float = Field(default=30.0, description="LLD default.")
    # Persona (HLD 09)
    persona_cache_s: float = 60.0


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    models: ModelSettings = Field(default_factory=lambda: ModelSettings())  # pyright: ignore[reportCallIssue]  # read from .env
    limits: Limits = Field(default_factory=Limits)
    google_api_key: SecretStr | None = None  # AI Studio; GOOGLE_API_KEY in .env
    bigquery_project: str | None = None  # billing project; ADC credentials
    bigquery_dataset: str = "bigquery-public-data.thelook_ecommerce"
    app_db_url: str = "sqlite:///./data/app.db"
    checkpoint_db_url: str = "sqlite:///./data/checkpoints.db"
    seed_dir: Path = Path("seed")
    traces_dir: Path = Path("traces")
    phoenix_endpoint: str | None = None  # set when Phoenix is running
