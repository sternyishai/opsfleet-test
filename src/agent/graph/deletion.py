# load_context and ChatService set every key these nodes read (see graph/build.py).
# pyright: reportTypedDictNotRequiredAccess=false
"""The deletion branch: ``plan_deletion`` -> ``confirm_deletion`` -> ``execute_deletion``
(HLD 04, LLD 03 § Pause and resume).

The model only turns the request into a ``DeletionFilter``; code picks the reports. The plan
is built in its own node, so the pause in ``confirm_deletion`` never rebuilds it: LangGraph
re-runs a node from its start on resume, and everything before ``interrupt()`` there is pure.
"""

from typing import Any

from langchain_core.exceptions import OutputParserException
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.runtime import Runtime
from langgraph.types import Command, interrupt
from pydantic import ValidationError

from agent.composition import Ports
from agent.config import Limits
from agent.contracts.channel import ConfirmationItem, ConfirmationResponse
from agent.contracts.tools import (
    ExecuteDeletionInput,
    ExecuteDeletionOk,
    PlanDeletionInput,
    PlanDeletionOk,
)
from agent.core.catalog import render
from agent.core.confirmation import (
    ConfirmAll,
    ConfirmationDecision,
    ConfirmExcept,
    Decline,
    NeedsCount,
    NotAnAnswer,
    parse_reply,
)
from agent.domain.reports import DeletionFilter, DeletionOutcome, DeletionPlan
from agent.errors import MessageClass
from agent.graph.prompts import deletion_filter_messages
from agent.graph.state import AgentState, TurnRuntime
from agent.ports.llm import ModelStep
from agent.tools.branches import execute_deletion, plan_deletion
from agent.tools.context import TurnContext


class DeletionNodes:
    """The three nodes. ``confirm_deletion`` jumps to ``execute``, ``after`` or, for a
    message that is not an answer, ``restart`` (``confirm_to`` lists them for the graph)."""

    def __init__(self, ports: Ports, limits: Limits, *, execute: str, after: str, restart: str):
        self._ports = ports
        self._limits = limits
        self._execute = execute
        self._after = after
        self._restart = restart

    @property
    def confirm_to(self) -> tuple[str, str, str]:
        return self._execute, self._after, self._restart

    def plan_deletion(self, state: AgentState) -> dict[str, Any]:
        """The filter model call, then ``plan_deletion``. A filter the model cannot make
        (an empty or contradictory request) matches nothing, so nothing is proposed."""
        persona = state["persona"].persona
        model = self._ports.llm.chat(ModelStep.DELETION_FILTER).with_structured_output(
            DeletionFilter, method="json_schema"
        )
        try:
            f = model.invoke(deletion_filter_messages(state["messages"][-1].text))
        except (ValidationError, OutputParserException):
            f = None
        out = (
            plan_deletion(PlanDeletionInput(filter=f), self._ctx(state))
            if isinstance(f, DeletionFilter)
            else None
        )
        if not isinstance(out, PlanDeletionOk):
            text = render(MessageClass.NOTHING_MATCHED, persona, ref=state["trace_id"])
            return {
                "open_plan": None,
                "message_class": MessageClass.NOTHING_MATCHED,
                "messages": [AIMessage(content=text)],
            }
        plan = out.plan
        listing = "\n".join(f"{i.index}. {i.title} ({i.reason})" for i in plan.items)
        return {
            "open_plan": plan,
            "last_plan_id": plan.plan_id,
            "messages": [AIMessage(content=f"Deletion Plan, awaiting confirmation:\n{listing}")],
        }

    def confirm_deletion(self, state: AgentState, runtime: Runtime[TurnRuntime]) -> Command:
        """Pause with the plan; read the reply with ``parse_reply``. A bare yes to a large plan
        asks once more for the count; a second one cancels like any other non-answer."""
        plan = state["open_plan"]
        assert plan is not None, "routed here only with an open plan"
        deferred = state.get("deferred_request")
        reply = interrupt({"request": _request(plan, deferred, recount=False)})
        decision = _decide(reply, plan, runtime)
        if isinstance(decision, NeedsCount):
            reply = interrupt({"request": _request(plan, deferred, recount=True)})
            decision = _decide(reply, plan, runtime)
            if isinstance(decision, NeedsCount):
                decision = NotAnAnswer()
        text: str | None = reply.get("text")
        update: dict[str, Any] = {
            "trace_id": reply["trace_id"],
            "turn_id": reply["turn_id"],
            "messages": [HumanMessage(content=text)] if text else [],
        }
        persona = state["persona"].persona
        match decision:
            case ConfirmAll() | ConfirmExcept():
                excluded = set(decision.excluded) if isinstance(decision, ConfirmExcept) else set()
                ids = [i.report_id for i in plan.items if i.index not in excluded]
                return Command(goto=self._execute, update=update | {"confirmed_report_ids": ids})
            case Decline():
                said = f"Nothing was deleted. I kept all {_reports(plan.total)}."
                update["messages"].append(AIMessage(content=said))
                return Command(goto=self._after, update=update | {"open_plan": None})
            case _:  # NotAnAnswer: cancel; a typed message is then handled as a new one
                update |= {"open_plan": None, "deferred_request": None}
                if text:
                    return Command(goto=self._restart, update=update)
                expired = render(MessageClass.PLAN_EXPIRED, persona, ref=reply["trace_id"])
                update["messages"].append(AIMessage(content=expired))
                update["message_class"] = MessageClass.PLAN_EXPIRED
                return Command(goto=self._after, update=update)

    def execute_deletion(self, state: AgentState) -> dict[str, Any]:
        plan = state["open_plan"]
        ids = state["confirmed_report_ids"] or []
        assert plan is not None and ids, "routed here only with a confirmed plan"
        out = execute_deletion(
            ExecuteDeletionInput(plan_id=plan.plan_id, confirmed_report_ids=ids),
            self._ctx(state),
            plan,
        )
        if isinstance(out, ExecuteDeletionOk):
            return {
                "open_plan": None,
                "deletion_outcome": out.outcome,
                "messages": [AIMessage(content=outcome_text(out.outcome))],
            }
        persona = state["persona"].persona
        return {
            "open_plan": None,
            "message_class": out.message_class,
            "messages": [
                AIMessage(content=render(out.message_class, persona, ref=state["trace_id"]))
            ],
        }

    def _ctx(self, state: AgentState) -> TurnContext:
        user = state["user"]
        assert user is not None, "load_context refuses a turn without a User"
        return TurnContext(
            user=user,
            conversation_id=state["conversation_id"],
            turn_id=state["turn_id"],
            channel=state["channel"],
            ports=self._ports,
            limits=self._limits,
        )


def _decide(
    reply: dict[str, Any], plan: DeletionPlan, runtime: Runtime[TurnRuntime]
) -> ConfirmationDecision:
    """Only the plan's User can answer it (``ChatService`` checks too)."""
    if runtime.context.user_id != plan.author_id:
        return NotAnAnswer()
    if "response" in reply:
        return parse_reply(ConfirmationResponse.model_validate(reply["response"]), plan)
    return parse_reply(reply["text"], plan)


def _request(plan: DeletionPlan, deferred: str | None, *, recount: bool) -> dict[str, Any]:
    """The ``ConfirmationRequest`` fields a node can know; ``ChatService`` adds the turn's
    trace ID and time when it sends it. Plain JSON, so the checkpoint stores no new type."""
    shown = plan.items[: DeletionPlan.LARGE_PLAN]
    if recount:
        text = (
            f'To delete all {plan.total}, reply "yes, delete all {plan.total}". '
            'You can also reply "yes except 2,3" or "no".'
        )
    elif plan.is_large:
        text = (
            f"I found {plan.total} of your reports to delete. Here are the first {len(shown)}. "
            f'To delete them all, reply "yes, delete all {plan.total}"; or reply '
            '"yes except 2,3" or "no". You can also ask again more narrowly.'
        )
    else:
        text = (
            f"I found {_reports(plan.total)} to delete. "
            'Delete them? Reply yes, no, or "yes except 2".'
        )
    if deferred and not recount:
        text += f' After that, I\'ll answer: "{deferred}".'
    items = [
        ConfirmationItem(
            index=i.index, label=f"{i.title} ({i.created_at:%d %b %Y})", reason=i.reason
        )
        for i in shown
    ]
    return {
        "request_id": str(plan.plan_id),
        "text": text,
        "items": [i.model_dump(mode="json") for i in items],
        "total": plan.total,
        "require_count": plan.is_large,
    }


def outcome_text(outcome: DeletionOutcome) -> str:
    """'Deleted 2 reports (1 and 3). Kept "Churn drivers".' The same text on a replay."""
    parts: list[str] = []
    if outcome.deleted:
        numbers = _joined([str(i.index) for i in outcome.deleted])
        parts.append(f"Deleted {_reports(len(outcome.deleted))} ({numbers}).")
    else:
        parts.append("Nothing was deleted.")
    if outcome.kept:
        parts.append(f"Kept {_joined([f'"{i.title}"' for i in outcome.kept])}.")
    if outcome.already_gone:
        gone = _joined([f'"{i.title}"' for i in outcome.already_gone])
        parts.append(
            f"{gone} {'was' if len(outcome.already_gone) == 1 else 'were'} already deleted."
        )
    return " ".join(parts)


def _reports(n: int) -> str:
    return f"{n} report" if n == 1 else f"{n} reports"


def _joined(words: list[str]) -> str:
    return words[0] if len(words) == 1 else f"{', '.join(words[:-1])} and {words[-1]}"
