"""Branch functions: called only from router-opened fixed branches, never bound to a model
(ADR 0004). The Persona functions are also what ``Views`` calls for ``/persona edit`` and
``/persona rollback``, so both paths share one gate and one store (HLD 09)."""

from datetime import UTC, datetime
from uuid import uuid4

from agent.contracts.tools import (
    ExecuteDeletionInput,
    ExecuteDeletionOk,
    ExecuteDeletionOutput,
    PersonaDraft,
    PersonaPublished,
    PersonaRejected,
    PersonaRolledBack,
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
from agent.domain.identity import Permission, UserContext
from agent.domain.persona import Persona, PersonaGateResult, PersonaVersion
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
            filter=ctx.ports.pii.scan(args.filter.model_dump_json(exclude_defaults=True)).text,
            **{"plan.size": len(items)},
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


def may_edit_persona(user: UserContext) -> bool:
    """The ``persona_editor`` permission, from the identity tables (HLD 09 § Who may change
    it). Checked by the caller before any Persona function, and before any model call."""
    return Permission.PERSONA_EDITOR in user.permissions


def draft_persona(drafted: Persona, ctx: TurnContext, current: PersonaVersion) -> PersonaDraft:
    """The draft from the ``PERSONA_DRAFTER`` model's structured ``Persona``, which the node
    asks for before this function (like the deletion filter). Code, not the model, says what
    changed. Only reached after the ``persona_editor`` check; untrusted until the gate passes."""
    with ctx.ports.tracer.span("draft_persona") as span:
        changed = changed_fields(current.persona, drafted)
        span.set(changed_fields=changed)
    return PersonaDraft(persona=drafted, changed_fields=changed, summary=describe(drafted, changed))


def publish_persona(
    args: PublishPersonaInput, ctx: TurnContext, gate: PersonaGateResult
) -> PublishPersonaOutput:
    """``gate`` is ``PersonaQualityGate.check(args.persona)``, run by the caller (the
    ``persona_gate`` node, or ``Views.edit_persona``). On pass ``PersonaStore.publish``; on
    fail, or when the gate could not run, ``PersonaStore.record_rejection``. The store refuses
    a result that did not pass, so a wrong caller cannot publish either. The caller has
    checked ``persona_editor``."""
    store = ctx.ports.personas
    with ctx.ports.tracer.span("publish_persona", path=args.path.value) as span:
        current = store.active()
        span.set(
            **{f"gate.{c.check.name.lower()}": c.passed for c in gate.checks},
            **{"gate.could_not_run": gate.could_not_run, "version.before": current.version},
        )
        if not gate.passed:
            store.record_rejection(args.persona, by=ctx.user.user_id, path=args.path, gate=gate)
            span.set(outcome="rejected", **{"version.after": current.version})
            return PersonaRejected(
                gate=gate,
                current_version=current.version,
                message_class=MessageClass.MODEL_BUSY
                if gate.could_not_run
                else MessageClass.PERSONA_GATE_FAILED,
            )
        active = store.publish(
            args.persona,
            by=ctx.user.user_id,
            path=args.path,
            changed_fields=changed_fields(current.persona, args.persona),
            gate=gate,
        )
        span.set(outcome="published", **{"version.after": active.version})
    return PersonaPublished(active=active, previous_version=current.version, gate=gate)


def rollback_persona(args: RollbackPersonaInput, ctx: TurnContext) -> RollbackPersonaOutput:
    """``PersonaStore.rollback``; no gate, the version already passed it. The caller has
    checked ``persona_editor``."""
    store = ctx.ports.personas
    with ctx.ports.tracer.span("rollback_persona") as span:
        current = store.active()
        span.set(**{"version.before": current.version, "rollback.target": args.to_version})
        try:
            active = store.rollback(by=ctx.user.user_id, to_version=args.to_version)
        except LookupError as exc:
            span.set(outcome="nothing_to_roll_back")
            return ToolError(
                message_class=MessageClass.PERSONA_NOTHING_TO_ROLL_BACK, detail=str(exc)
            )
        span.set(outcome="rolled_back", **{"version.after": active.version})
    return PersonaRolledBack(active=active, from_version=current.version)


def changed_fields(before: Persona, after: Persona) -> list[str]:
    """The top-level Persona fields that differ, in the Persona's own field order."""
    return [f for f in Persona.model_fields if getattr(before, f) != getattr(after, f)]


def describe(persona: Persona, fields: list[str]) -> str:
    """What the new version says, for the fields that changed: "warm (Encouraging and
    plain.); report sections Summary, Risks, Action items"."""
    parts: list[str] = []
    for field in fields:
        match field:
            case "tone":
                notes = f" ({persona.tone.notes})" if persona.tone.notes else ""
                parts.append(f"{persona.tone.preset.value}{notes}")
            case "report_sections":
                parts.append(f"report sections {', '.join(persona.report_sections)}")
            case "emphasis":
                parts.append(f"emphasis: {persona.emphasis or 'none'}")
            case "vocabulary":
                parts.append("new vocabulary")
            case "examples":
                parts.append(f"{len(persona.examples)} example answers")
            case "messages":
                parts.append("new wording for some messages")
    return "; ".join(parts) or "no change"
