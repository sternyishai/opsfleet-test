"""PiiFilter: detection and redaction for the single outbound exit (ADR 0003, HLD 03)."""

from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, Field


class PiiType(StrEnum):
    EMAIL = "email"
    PHONE = "phone"
    STREET_ADDRESS = "street_address"
    POSTAL_CODE = "postal_code"
    COORDINATES = "coordinates"
    PERSON_NAME = "person_name"  # Sensitive Data Protection only; not the regex detectors


class PiiScan(BaseModel):
    text: str = Field(description="The input with every hit replaced by [REDACTED:<type>].")
    hits: list[PiiType] = Field(description="Types only, never the redacted value.")
    detectors: str = Field(description='"regex", "sdp" or "regex+sdp"; "regex" also on fallback.')


class PiiFilter(Protocol):
    """Interface:

    - ``scan`` never raises and never returns unscanned text. If the managed detector is down,
      the adapter falls back to the regex detectors and records the fallback on the span
      (accepted trade-off, HLD 06). The filter is never skipped.
    - ``templated=True`` is for progress events (a fixed phrase plus tool and table names):
      regex detectors only, so no managed-service call is added to the turn.
    - Callers are ``agent.core.outbound.Outbound`` and ``save_report``. Nothing else needs it.
    - Any hit is redacted, traced and alerted by the caller; the target is zero.
    """

    def scan(self, text: str, *, templated: bool = False) -> PiiScan: ...
