"""What Users read is written for non-technical executives (ASSIGNMENT.md, decision-log
§ Slice 3): no warehouse or engineering terms in any catalog message, bundled or in the seed
Persona."""

import re
from pathlib import Path

import pytest

from agent.adapters.persona_file import FilePersonaStore
from agent.core.catalog import BUNDLED

TECHNICAL = re.compile(
    r"\b(sql|query|queries|scan(ned|s)?|bigquery|database|schema|table|column|row|"
    r"exception|stack|timeout|api|token|llm|model)\b",
    re.IGNORECASE,
)
"""Words an executive should never need. A catalog message names the next step in plain
words: "a shorter period", not "the query scanned too much"."""

SEED = Path(__file__).resolve().parents[1] / "seed" / "persona.yaml"


def _messages() -> list[tuple[str, str]]:
    bundled = [(f"bundled:{k}", v) for k, v in BUNDLED.items()]
    persona = FilePersonaStore(SEED).active().persona
    seeded = [(f"persona:{k}", v) for k, v in persona.messages.items()]
    return bundled + seeded


@pytest.mark.parametrize(("where", "text"), _messages(), ids=lambda v: str(v)[:40])
def test_catalog_messages_use_no_technical_words(where: str, text: str) -> None:
    found = TECHNICAL.findall(text)
    assert not found, f"{where} uses {found}: {text!r}"
