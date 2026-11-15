"""Branch functions: called only from router-opened fixed branches, never bound to a model
(ADR 0004). The deletion pair is built; the Persona functions are stubs until slice 7."""

from datetime import UTC, datetime
from uuid import uuid4

from agent.contracts.tools import (
    DraftPersonaInput,
    ExecuteDeletionInput,
    ExecuteDeletionOk,
    ExecuteDeletionOutput,
    PersonaDraft,
    PlanDeletionInput,
    PlanDeletionNothing,
    PlanDeletionOk,
    PlanDeletionOutput,
    PublishPersonaInput,
    PublishPersonaOutput,
    RollbackPersonaInput,
    RollbackPersonaOutput,
    ToolError,
)
from agent.domain.persona import PersonaVersion
from agent.domain.reports import DeletionOutcome, DeletionPlan
from agent.errors import MessageClass
from agent.tools.context import TurnContext


def plan_deletion(args: PlanDeletionInput, ctx: TurnContext) -> PlanDeletionOutput:
    """``ReportRepository.match`` for the session User and Conversation; a new ``plan_id``.
    Zero matches returns ``nothing_matched`` and no prompt. The plan holds IDs, so a report
    saved after this call is never in it."""
    with ctx.ports.tracer.span("plan_deletion") as span:
        items = ctx.ports.reports.match(
            ctx.user.user_id, args.filter, conversation_id=ctx.conversation_id
        )
        span.set(
            filter=args.filter.model_dump_json(exclude_defaults=True), **{"plan.size": len(items)}
        )
        if not items:
            return PlanDeletionNothing()
        plan = DeletionPlan(
            plan_id=uuid4(),
            author_id=ctx.user.user_id,
            conversation_id=ctx.conversation_id,
            created_at=datetime.now(UTC),
            filter=args.filter,
            items=items,
        )
        span.set(plan_id=str(plan.plan_id))
    return PlanDeletionOk(plan=plan)


def execute_deletion(
    args: ExecuteDeletionInput, ctx: TurnContext, open_plan: DeletionPlan | None
) -> ExecuteDeletionOutput:
    """Checks the plan is ``open_plan``, of this User and Conversation, and that every confirmed
    ID is in it; then ``ReportRepository.delete_with_audit``. Otherwise ``PLAN_EXPIRED``.
    A replay (the plan was already executed) changes nothing and reports the first outcome."""
    with ctx.ports.tracer.span("execute_deletion", plan_id=str(args.plan_id)) as span:
        confirmed = set(args.confirmed_report_ids)
        if (
            open_plan is None
            or open_plan.plan_id != args.plan_id
            or open_plan.author_id != ctx.user.user_id
            or open_plan.conversation_id != ctx.conversation_id
            or not confirmed <= {i.report_id for i in open_plan.items}
        ):
            span.set(outcome="expired")
            return ToolError(
                message_class=MessageClass.PLAN_EXPIRED,
                detail=f"plan {args.plan_id} is not the open plan of this User and Conversation",
            )
        chosen = [i for i in open_plan.items if i.report_id in confirmed]
        receipt = ctx.ports.reports.delete_with_audit(
            ctx.user.user_id,
            open_plan.plan_id,
            [i.report_id for i in chosen],
            {i.report_id: i.title for i in chosen},
            open_plan.filter,
        )
        deleted_ids = set(receipt.audit.report_ids) if receipt.audit else set()
        gone = set(receipt.already_gone)
        outcome = DeletionOutcome(
            plan_id=open_plan.plan_id,
            deleted=[i for i in open_plan.items if i.report_id in deleted_ids],
            kept=[i for i in open_plan.items if i.report_id not in confirmed],
            already_gone=[i for i in chosen if i.report_id in gone],
            audit_id=receipt.audit.audit_id if receipt.audit else None,
        )
        span.set(
            outcome="partial" if outcome.kept else "confirmed",
            replayed=receipt.replayed,
            deleted=len(outcome.deleted),
            kept=len(outcome.kept),
            already_gone=len(outcome.already_gone),
        )
    return ExecuteDeletionOk(outcome=outcome, replayed=receipt.replayed)


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
