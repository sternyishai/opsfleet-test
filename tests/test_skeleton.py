"""LLD skeleton checks: every module imports, the DDL loads, and the schema invariants hold.

These pin the contracts in docs/lld/, not behaviour; behaviour tests arrive with each slice.
"""

import importlib
import pkgutil
import sqlite3

import pytest
from pydantic import ValidationError

import agent
from agent.contracts.router import Action, RouterDecision, Verdict
from agent.db import ddl
from agent.domain.identity import BrandScope, Role, UserContext
from agent.domain.persona import GateCheck, GateCheckResult, PersonaGateResult
from agent.domain.reports import DeletionFilter
from agent.graph.build import ANALYSIS_TOOLS
from agent.tools import analysis


def test_every_module_imports() -> None:
    for mod in pkgutil.walk_packages(agent.__path__, "agent."):
        importlib.import_module(mod.name)


def test_sqlite_ddl_loads() -> None:
    db = sqlite3.connect(":memory:")
    db.executescript(ddl("sqlite"))
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {
        "users",
        "user_permissions",
        "brand_assignments",
        "conversations",
        "saved_reports",
        "report_entities",
        "deletion_audit",
        "user_profiles",
        "golden_trios",
        "trio_embeddings",
        "persona_versions",
        "persona_events",
    } <= tables


def test_sqlite_brand_change_bumps_access_version() -> None:
    db = sqlite3.connect(":memory:")
    db.executescript(ddl("sqlite"))
    db.execute("INSERT INTO users (user_id, role) VALUES ('m', 'manager')")
    db.execute("INSERT INTO brand_assignments (user_id, brand) VALUES ('m', 'Nike')")
    assert db.execute("SELECT access_version FROM users").fetchone() == (2,)


def test_postgres_and_sqlite_declare_the_same_tables() -> None:
    def names(sql: str) -> set[str]:
        return {line.split()[2] for line in sql.splitlines() if line.startswith("CREATE TABLE ")}

    assert names(ddl("postgres")) - {"interactions"} == names(ddl("sqlite"))


def test_scope_rules() -> None:
    manager = UserContext(
        user_id="m", role=Role.MANAGER, brands=frozenset({"Nike"}), access_version=1
    )
    nobody = UserContext(user_id="x", role=None, access_version=1)
    exec_ = UserContext(user_id="ceo", role=Role.EXECUTIVE, access_version=1)
    assert exec_.scope.covers(BrandScope(all_brands=True))
    assert manager.scope.covers(BrandScope(brands=frozenset({"Nike"})))
    assert not manager.scope.covers(BrandScope(all_brands=True))
    assert nobody.scope == BrandScope()


def test_router_decision_shape() -> None:
    RouterDecision(verdict=Verdict.ALLOWED, action=Action.ANALYSIS, search_question="q")
    RouterDecision(verdict=Verdict.INJECTION, action=None)
    with pytest.raises(ValidationError):
        RouterDecision(verdict=Verdict.ALLOWED, action=Action.ANALYSIS)  # no search question
    with pytest.raises(ValidationError):
        RouterDecision(
            verdict=Verdict.ALLOWED,
            action=Action.ANALYSIS,
            search_question="q",
            deferred_request="x",
        )


def test_deletion_filter_needs_a_criterion() -> None:
    with pytest.raises(ValidationError):
        DeletionFilter()
    with pytest.raises(ValidationError):
        DeletionFilter(all_reports=True, entities=["Nike"])
    DeletionFilter(entities=["Nike"])


def test_gate_result_passes_only_with_all_checks() -> None:
    ok = [GateCheckResult(check=c, passed=True) for c in GateCheck]
    PersonaGateResult(passed=True, checks=ok)
    with pytest.raises(ValidationError):
        PersonaGateResult(passed=True, checks=ok[:2])


def test_analysis_tool_list_matches_functions() -> None:
    assert all(callable(getattr(analysis, name)) for name in ANALYSIS_TOOLS)


def test_tool_inputs_never_name_a_user() -> None:
    from agent.contracts import tools

    forbidden = {"user_id", "author_id", "author", "brands", "scope", "role"}
    for name in dir(tools):
        obj = getattr(tools, name)
        if name.endswith("Input") and isinstance(obj, type):
            assert not forbidden & set(obj.model_fields), name


def test_every_model_step_has_its_own_settings() -> None:
    from agent.config import ModelSettings
    from agent.ports.llm import INHERITS_FROM, ModelStep

    fields = set(ModelSettings.model_fields)
    for step in ModelStep:
        assert {step.value, f"{step.value}_fallback"} <= fields, step
        assert step in INHERITS_FROM, step


def test_unset_steps_inherit_and_set_steps_win() -> None:
    from agent.config import ModelSettings
    from agent.ports.llm import ModelStep

    m = ModelSettings(
        router="g:lite",
        analyst="g:flash",
        analyst_fallback="g:lite",
        trio_ingest="g:pro",
        _env_file=None,
    )
    assert m.resolve(ModelStep.DELETION_FILTER) == ("g:lite", None)
    assert m.resolve(ModelStep.PERSONA_GATE) == ("g:flash", "g:lite")
    assert m.resolve(ModelStep.TRIO_INGEST) == ("g:pro", "g:lite")
