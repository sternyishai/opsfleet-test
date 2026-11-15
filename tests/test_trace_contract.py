"""Trace attribute names are a contract: ``/stats`` is computed from them, so every name the
code puts on a span is documented in LLD 05 or LLD 06 (docs/lld/06-ports.md § Trace
attributes). A new or renamed attribute fails here until the docs name it."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = (ROOT / "docs/lld/05-tools.md", ROOT / "docs/lld/06-ports.md")
SPAN_CALLS = {"set", "span", "turn"}
"""``Span.set(...)``, ``Tracer.span(name, ...)``, ``Tracer.turn(...)``."""
ATTRIBUTE_BUILDERS = {"_trace", "_turn_attributes"}
"""Functions that return a span's attributes as a dict literal."""


def _keys(literal: ast.Dict) -> set[str]:
    return {
        k.value for k in literal.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)
    }


def _names_in(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            receiver = ast.unparse(node.func.value)
            if node.func.attr in SPAN_CALLS and ("span" in receiver or "tracer" in receiver):
                for kw in node.keywords:
                    if kw.arg is not None:
                        names.add(kw.arg)
                    elif isinstance(kw.value, ast.Dict):  # **{"dotted.name": ...}
                        names |= _keys(kw.value)
        if isinstance(node, ast.FunctionDef) and node.name in ATTRIBUTE_BUILDERS:
            for ret in ast.walk(node):
                if isinstance(ret, ast.Return) and isinstance(ret.value, ast.Dict):
                    names |= _keys(ret.value)
    return names


def _code_names() -> dict[str, str]:
    found: dict[str, str] = {}
    for path in sorted((ROOT / "src/agent").rglob("*.py")):
        if "adapters" in path.parts and path.name.startswith("tracer_"):
            continue  # the tracer itself, not a caller
        for name in _names_in(ast.parse(path.read_text())):
            found.setdefault(name, str(path.relative_to(ROOT)))
    return found


def test_every_trace_attribute_is_named_in_the_lld() -> None:
    docs = "\n".join(p.read_text() for p in DOCS)
    missing = {n: where for n, where in _code_names().items() if f"`{n}`" not in docs}
    assert not missing, f"undocumented trace attributes (add them to LLD 05/06): {missing}"


def test_the_scan_finds_the_root_span_contract() -> None:
    """Guards the scanner itself: if it stops seeing attributes, the test above is empty."""
    names = _code_names()
    assert {"router.action", "outcome", "guard.decision", "sql.written", "pii.hits"} <= set(names)
