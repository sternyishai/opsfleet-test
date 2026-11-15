"""The empty-result probe (HLD 06 § SQL self-correction): code reading the guard's AST, not a
model call.

An empty result is often a wrong filter, not a real answer. For each filtered column the probe
runs one small query: the distinct values of a text column, or the range of a date column.
Its findings become the hint the model reads. The probe SQL is built only from allowlisted
table and column names (``FilterLiteral`` comes from the guard, which resolved both); no
literal the model wrote is ever formatted into it. The caller runs each probe through the same
guard and dry run as any query.
"""

import difflib
import re
from collections.abc import Callable
from dataclasses import dataclass

from agent.domain.query import FilterLiteral, QueryResult, TableName
from agent.domain.warehouse import ALLOWLIST

type RunProbe = Callable[[str], QueryResult | None]
"""Runs one probe statement; None when it could not run (rejected, over the cap, an error)."""

_TYPES: dict[tuple[str, str], str] = {
    (spec.name, column.name): column.type for spec in ALLOWLIST for column in spec.columns
}
_DATES = {"TIMESTAMP", "DATE", "DATETIME"}
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


@dataclass(frozen=True, slots=True)
class Diagnosis:
    hint: str | None
    """The findings in plain words; None when nothing could be checked."""
    final: bool
    """True: every checked filter is valid, so the empty result is the answer."""
    checked: list[FilterLiteral]


def diagnose(filters: list[FilterLiteral], run: RunProbe, *, max_columns: int) -> Diagnosis:
    findings: list[str] = []
    checked: list[FilterLiteral] = []
    wrong = False
    for (table, column), group in list(_by_column(filters).items())[:max_columns]:
        kind = _TYPES.get((table, column))
        if kind == "STRING":
            finding = _check_values(table, column, group, run)
        elif kind in _DATES:
            finding = _check_range(table, column, group, run)
        else:
            continue  # numbers: nothing a probe can usefully say
        if finding is None:
            continue
        text, valid = finding
        checked += group
        findings += text
        wrong = wrong or not valid
    return Diagnosis(hint="; ".join(findings) or None, final=not wrong, checked=checked)


def _by_column(filters: list[FilterLiteral]) -> dict[tuple[TableName, str], list[FilterLiteral]]:
    grouped: dict[tuple[TableName, str], list[FilterLiteral]] = {}
    for f in filters:
        grouped.setdefault((f.table, f.column), []).append(f)
    return grouped


def _check_values(
    table: TableName, column: str, group: list[FilterLiteral], run: RunProbe
) -> tuple[list[str], bool] | None:
    """Exact-match filters only: a ``LIKE`` pattern or ``!=`` names no single value."""
    wanted = [f.value for f in group if f.operator in ("=", "IN")]
    if not wanted:
        return None
    result = run(
        f"SELECT {column}, COUNT(*) AS n FROM {table} WHERE {column} IS NOT NULL "
        f"GROUP BY {column} ORDER BY n DESC"
    )
    if result is None:
        return None
    present = [str(row[column]) for row in result.rows]  # most common first
    missing = [value for value in wanted if value not in present]
    return [_closest(column, value, present) for value in missing], not missing


def _closest(column: str, value: str, present: list[str]) -> str:
    """``state has 'CA', not 'California'``: prefixes either way (abbreviations, case), then
    near spellings; else the most common values."""
    lower = value.lower()
    prefixes = [p for p in present if lower.startswith(p.lower()) or p.lower().startswith(lower)]
    near = difflib.get_close_matches(value, present, n=3, cutoff=0.6)
    candidates = list(dict.fromkeys(prefixes + near))[:3]
    if candidates:
        return f"{column} has {_quoted(candidates)}, not '{value}'"
    return f"{column} has no '{value}'; the most common values are {_quoted(present[:3])}"


def _quoted(values: list[str]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def _check_range(
    table: TableName, column: str, group: list[FilterLiteral], run: RunProbe
) -> tuple[list[str], bool] | None:
    result = run(f"SELECT MIN({column}) AS earliest, MAX({column}) AS latest FROM {table}")
    if result is None or not result.rows or result.rows[0].get("latest") is None:
        return None
    earliest = str(result.rows[0]["earliest"])[:10]
    latest = str(result.rows[0]["latest"])[:10]
    valid = not any(_outside(f, earliest, latest) for f in group)
    return [f"{column} runs from {earliest} to {latest}"], valid


def _outside(f: FilterLiteral, earliest: str, latest: str) -> bool:
    """Whether the bound alone rules out every row. ISO dates compare as strings."""
    if not _ISO_DATE.match(f.value):
        return False
    day = f.value[:10]
    if f.operator in (">", ">="):
        return day > latest
    if f.operator in ("<", "<="):
        return day < earliest
    if f.operator in ("=", "IN"):
        return not earliest <= day <= latest
    return False
