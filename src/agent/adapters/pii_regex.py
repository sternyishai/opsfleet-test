"""PiiFilter on regular expressions: the prototype's detectors (HLD 03 § PII), and later the
fallback behind Sensitive Data Protection.

Tuned against what an answer is made of: money with thousands separators, percentages, dates,
5-digit Customer IDs, SKUs. So a bare 5-digit number is never a postal code on its own; it
needs a label ("ZIP"), the ZIP+4 form, or a US state code in address form: after a word and a
comma, with a code in that state's ZIP range ("Boise, ID 83702"), 4 digits included: the
warehouse stores ZIPs as integers, so "Lakewood, NJ 8701" lost its zero. A state code alone is not
enough, since "ID", "OK" and "OR" are words too: "Customer ID 48213" is a Customer, and so is
"Calvin Klein, ID 48213" (482 is a Michigan prefix, not Idaho's). Bare coordinate pairs need five
decimals, which no figure the analyst writes has.
"""

import re
from collections.abc import Callable

from agent.ports.pii import PiiScan, PiiType

_ZIP_PREFIXES: dict[str, tuple[tuple[int, int], ...]] = {
    "AL": ((350, 369),), "AK": ((995, 999),), "AZ": ((850, 865),), "AR": ((716, 729),),
    "CA": ((900, 961),), "CO": ((800, 816),), "CT": ((60, 69),), "DE": ((197, 199),),
    "DC": ((200, 205), (569, 569)), "FL": ((320, 349),), "GA": ((300, 319), (398, 399)),
    "HI": ((967, 968),), "ID": ((832, 838),), "IL": ((600, 629),), "IN": ((460, 479),),
    "IA": ((500, 528),), "KS": ((660, 679),), "KY": ((400, 427),), "LA": ((700, 714),),
    "ME": ((39, 49),), "MD": ((206, 219),), "MA": ((10, 27), (55, 55)), "MI": ((480, 499),),
    "MN": ((550, 567),), "MS": ((386, 397),), "MO": ((630, 658),), "MT": ((590, 599),),
    "NE": ((680, 693),), "NV": ((889, 898),), "NH": ((30, 38),), "NJ": ((70, 89),),
    "NM": ((870, 884),), "NY": ((5, 5), (100, 149)), "NC": ((270, 289),), "ND": ((580, 588),),
    "OH": ((430, 459),), "OK": ((730, 749),), "OR": ((970, 979),), "PA": ((150, 196),),
    "RI": ((28, 29),), "SC": ((290, 299),), "SD": ((570, 577),), "TN": ((370, 385),),
    "TX": ((733, 733), (750, 799), (885, 885)), "UT": ((840, 847),), "VT": ((50, 59),),
    "VA": ((201, 201), (220, 246)), "WA": ((980, 994),), "WV": ((247, 268),),
    "WI": ((530, 549),), "WY": ((820, 831),),
}  # fmt: skip
"""First three ZIP digits each state's codes start with (USPS ranges, widened over gaps)."""
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
            rf"|(?<=[A-Za-z]),\s*(?P<state>{'|'.join(_ZIP_PREFIXES)})\s+)"
            r"(?P<code>(?(state)\d{4,5}|\d{5})(?:-\d{4})?)\b"
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
    """Replace the match, or only its named group when a label or state comes before it. A
    state's code outside its ZIP range is left as it is (ZIP+4 is redacted anyway)."""

    def replace(match: re.Match[str]) -> str:
        groups = match.groupdict()
        code, state = groups.get("code"), groups.get("state")
        if state and code and "-" not in code and not _in_state(state, code):
            return match.group()
        hits.append(kind)
        tag = f"[REDACTED:{kind.value}]"
        if code is None:
            return tag
        start = match.start("code") - match.start()
        return match.group()[:start] + tag

    return replace


def _in_state(state: str, code: str) -> bool:
    prefix = int(code.zfill(5)[:3])
    return any(low <= prefix <= high for low, high in _ZIP_PREFIXES[state])
