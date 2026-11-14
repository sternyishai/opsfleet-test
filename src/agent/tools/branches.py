"""Branch functions: called only from router-opened fixed branches, never bound to a model
(ADR 0004). Stubs."""

from agent.contracts.tools import (
    DraftPersonaInput,
    ExecuteDeletionInput,
    ExecuteDeletionOutput,
    PersonaDraft,
    PlanDeletionInput,
    PlanDeletionOutput,
    PublishPersonaInput,
    PublishPersonaOutput,
    RollbackPersonaInput,
    RollbackPersonaOutput,
)
from agent.domain.persona import PersonaVersion
from agent.domain.reports import DeletionPlan
from agent.tools.context import TurnContext


def plan_deletion(args: PlanDeletionInput, ctx: TurnContext) -> PlanDeletionOutput:
    """``ReportRepository.match`` for the session User and Conversation; a new ``plan_id``.
    Zero matches returns ``nothing_matched`` and no prompt."""
    raise NotImplementedError


def execute_deletion(
    args: ExecuteDeletionInput, ctx: TurnContext, open_plan: DeletionPlan | None
) -> ExecuteDeletionOutput:
    """Checks the plan is ``open_plan``, of this User and Conversation, and that every confirmed
    ID is in it; then ``ReportRepository.delete_with_audit``. Otherwise ``PLAN_EXPIRED``."""
    raise NotImplementedError


def draft_persona(
    args: DraftPersonaInput, ctx: TurnContext, current: PersonaVersion
) -> PersonaDraft:
    """``ModelStep.PERSONA_DRAFTER`` with structured output into ``Persona``. Only reached after
    the ``persona_editor`` check."""
    raise NotImplementedError


def publish_persona(args: PublishPersonaInput, ctx: TurnContext) -> PublishPersonaOutput:
    """Run ``agent.core.persona_gate.PersonaQualityGate``; on pass ``PersonaStore.publish``,
    on fail ``PersonaStore.record_rejection``."""
    raise NotImplementedError


def rollback_persona(args: RollbackPersonaInput, ctx: TurnContext) -> RollbackPersonaOutput:
    """``PersonaStore.rollback``; no gate, the version already passed it."""
    raise NotImplementedError
