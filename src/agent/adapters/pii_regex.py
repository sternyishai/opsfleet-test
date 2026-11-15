"""PiiFilter on regular expressions: the prototype's detectors (HLD 03 § PII), and later the
fallback behind Sensitive Data Protection.

Tuned against what an answer is made of: money with thousands separators, percentages, dates,
5-digit Customer IDs, SKUs. So a bare 5-digit number is never a postal code on its own; it
needs a label ("ZIP"), a US state before it, or the ZIP+4 form. Bare coordinate pairs need five
decimals, which no figure the analyst writes has.
"""

import re
from collections.abc import Callable

from agent.ports.pii import PiiScan, PiiType

_US_STATES = (
    "AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH "
    "NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY"
).split()
_STREET_SUFFIX = (
    "Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Lane|Ln|Drive|Dr|Court|Ct|Way|Place|Pl|"
    "Terrace|Parkway|Pkwy|Circle|Cir|Highway|Hwy"
)

_DETECTORS: list[tuple[PiiType, re.Pattern[str]]] = [
    (PiiType.EMAIL, re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    (
        PiiType.COORDINATES,
        re.compile(
            r"POINT\s*\(\s*-?\d{1,3}\.\d+\s+-?\d{1,3}\.\d+\s*\)"
            r"|\b(?:lat(?:itude)?|lon(?:gitude)?|lng)\s*[:=]?\s*-?\d{1,3}\.\d+"
            r"|(?<![\d.])-?\d{1,3}\.\d{5,}\s*,\s*-?\d{1,3}\.\d{5,}(?![\d.])",
            re.IGNORECASE,
        ),
    ),
    (
        PiiType.PHONE,
        re.compile(
            r"(?<!\w)(?:\+\d{1,3}[\s.-]?)?(?:\(\d{3}\)\s?|\d{3}[\s.-])\d{3}[\s.-]\d{4}(?!\w)"
            r"|(?<!\w)\+\d{1,3}(?:[\s.-]\d{1,4}){2,4}(?!\w)"
        ),
    ),
    (
        PiiType.STREET_ADDRESS,
        re.compile(rf"\b\d{{1,6}}\s+(?:[A-Z][a-z]+\s+){{1,4}}(?:{_STREET_SUFFIX})\b\.?"),
    ),
    (
        PiiType.POSTAL_CODE,
        re.compile(
            r"(?:(?i:\b(?:zip(?:\s*code)?|postal\s*code|postcode)\b)\s*[:#]?\s*"
            rf"|\b(?:{'|'.join(_US_STATES)})\s+)"
            r"(?P<code>\d{5}(?:-\d{4})?)\b"
            r"|(?P<zip4>\b\d{5}-\d{4}\b)"
            r"|(?P<uk>\b[A-Z]{1,2}\d[A-Z\d]?\s+\d[A-Z]{2}\b)"
            r"|(?P<ca>\b[A-Z]\d[A-Z]\s?\d[A-Z]\d\b)"
        ),
    ),
]
"""In order: an email's digits are not a phone, a coordinate's digits are not a postal code."""


class RegexPiiFilter:
    def scan(self, text: str, *, templated: bool = False) -> PiiScan:
        """``templated`` changes nothing here: the regex detectors are the templated path."""
        hits: list[PiiType] = []
        for kind, pattern in _DETECTORS:
            text = pattern.sub(_redact(kind, hits), text)
        return PiiScan(text=text, hits=hits, detectors="regex")


def _redact(kind: PiiType, hits: list[PiiType]) -> Callable[[re.Match[str]], str]:
    """Replace the match, or only its named group when a label or state comes before it."""

    def replace(match: re.Match[str]) -> str:
        hits.append(kind)
        tag = f"[REDACTED:{kind.value}]"
        code = match.groupdict().get("code")
        if code is None:
            return tag
        start = match.start("code") - match.start()
        return match.group()[:start] + tag

    return replace
