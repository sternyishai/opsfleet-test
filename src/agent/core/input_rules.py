"""The input guard's deterministic rules (HLD 03 § Input guard), run before the router call.

Cheap and conservative: each rule matches a shape no analysis question has, so a block costs
no model call. They are an early filter, not the guarantee; the query guard and the output
filter hold whatever gets past them. Every rule blocks as an injection attempt: an oversized
or encoded message is the usual carrier for smuggled instructions.
"""

import re

from agent.contracts.router import Verdict

_PHRASES = re.compile(
    r"\b(?:ignore|disregard|forget|override|bypass)\b(?:\s+\w+){0,3}?\s+"
    r"(?:instructions?|rules?|prompts?|guardrails?|restrictions?)\b"
    r"|\byou\s+are\s+now\b"
    r"|\b(?:system|hidden|initial)\s+prompt\b"
    r"|\bdeveloper\s+mode\b"
    r"|\bjailbreak"
    r"|\bnew\s+instructions\s*:"
    r"|</?\s*(?:message|conversation|system|instructions?|persona|current|tool_result|"
    r"reference_trios)\s*>",
    re.IGNORECASE,
)
"""Known injection phrasings, and tags that try to close the router's data delimiters."""

_ENCODED = re.compile(
    r"[A-Za-z0-9+/]{40,}={0,2}"  # base64; a SKU is 32 hex characters
    r"|(?:%[0-9A-Fa-f]{2}){4,}"  # URL-encoded
    r"|(?:\\u[0-9A-Fa-f]{4}){3,}|(?:\\x[0-9A-Fa-f]{2}){4,}"  # escaped code points
    r"|[​-‏‪-‮⁠-⁤﻿]"  # invisible and bidi controls
)


def screen(text: str, *, max_chars: int) -> Verdict | None:
    """``Verdict.INJECTION`` if a rule blocks the message, else ``None`` (go to the router)."""
    if len(text) > max_chars or _PHRASES.search(text) or _ENCODED.search(text):
        return Verdict.INJECTION
    return None
