"""The error model every port and tool shares (HLD 06).

Every error is sorted into one of four classes, and each class has exactly one response:

==================  ============================================  =========================
ErrorClass          Response                                      Who sees it
==================  ============================================  =========================
SELF_CORRECTABLE    returned to the model, within the SQL budget  nobody, unless exhausted
TRANSIENT           retried with backoff, then model fallback     nobody, unless all fail
PERMANENT           no retry; a catalog message with a next step  the User, at once
UNCLASSIFIED        graph-boundary catch-all, traced and alerted  the User + AI-ops
==================  ============================================  =========================

Ports raise these exceptions; they never leak provider exceptions to callers. User-facing text
is never built from an exception: it comes from the message catalog, keyed by ``MessageClass``,
whose wording lives in the Persona (HLD 09).
"""

from enum import StrEnum


class ErrorClass(StrEnum):
    SELF_CORRECTABLE = "self_correctable"
    TRANSIENT = "transient"
    PERMANENT = "permanent"
    UNCLASSIFIED = "unclassified"


class MessageClass(StrEnum):
    """Keys of the message catalog. Each maps to one plain-language message with a next step."""

    # failures (HLD 06)
    DATA_SOURCE_DOWN = "data_source_down"
    MODEL_BUSY = "model_busy"
    SELF_CORRECTION_EXHAUSTED = "self_correction_exhausted"
    QUERY_TOO_LARGE = "query_too_large"
    OUT_OF_SCOPE = "out_of_scope"
    QUOTA_REACHED = "quota_reached"
    UNAVAILABLE = "unavailable"  # Postgres down: no identity, fail closed
    UNCLASSIFIED = "unclassified"
    # refusals from the input guard (HLD 03)
    OFF_TOPIC = "off_topic"
    PII_REQUEST = "pii_request"
    INJECTION = "injection"
    NO_ROLE = "no_role"  # authenticated, but no role: default deny
    # Persona branch (HLD 09)
    PERSONA_FORBIDDEN = "persona_forbidden"
    PERSONA_GATE_FAILED = "persona_gate_failed"
    PERSONA_NOTHING_TO_ROLL_BACK = "persona_nothing_to_roll_back"
    # deletion branch (HLD 04)
    NOTHING_MATCHED = "nothing_matched"
    PLAN_EXPIRED = "plan_expired"
    PLAN_ANSWER_INVALID = "plan_answer_invalid"  # "yes except 4" to a plan of 3
    # reports (HLD 04)
    REPORT_NOT_FOUND = "report_not_found"
    REPORT_OUT_OF_SCOPE = "report_out_of_scope"


class AgentError(Exception):
    """Base of every classified error. Carries what the graph needs to choose the response."""

    error_class: ErrorClass = ErrorClass.UNCLASSIFIED
    message_class: MessageClass = MessageClass.UNCLASSIFIED

    def __init__(self, detail: str, *, message_class: MessageClass | None = None) -> None:
        super().__init__(detail)
        self.detail = detail  # goes to the trace, never to the User
        if message_class is not None:
            self.message_class = message_class


class SelfCorrectableError(AgentError):
    """SQL syntax/schema error, guard rejection, bytes cap, runtime error. Fed back to the model."""

    error_class = ErrorClass.SELF_CORRECTABLE
    message_class = MessageClass.SELF_CORRECTION_EXHAUSTED


class TransientError(AgentError):
    """429, 5xx, timeout, open circuit breaker. Adapters retry these before raising."""

    error_class = ErrorClass.TRANSIENT

    def __init__(
        self,
        detail: str,
        *,
        dependency: str,
        message_class: MessageClass | None = None,
    ) -> None:
        super().__init__(detail, message_class=message_class)
        self.dependency = dependency  # "gemini", "bigquery", "postgres", ...


class PermanentError(AgentError):
    """Out of scope, PII request, quota reached, forbidden. Never retried."""

    error_class = ErrorClass.PERMANENT
