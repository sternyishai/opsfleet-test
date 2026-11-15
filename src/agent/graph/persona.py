# load_context and ChatService set every key these nodes read (see graph/build.py).
# pyright: reportTypedDictNotRequiredAccess=false
"""The Persona branch: ``persona_permission`` -> ``draft_persona`` -> ``persona_gate`` ->
``publish_persona``, or ``persona_permission`` -> ``rollback_persona`` (HLD 09 § Two ways to
change it, one gate).

Only the router opens it, from the User's own message. The permission is checked in code
before any model call. ``/persona edit`` runs the same ``PersonaQualityGate`` and the same
``publish_persona`` function, so the chat and the direct edit cannot drift.
"""

from typing import Any

from langchain_core.exceptions import OutputParserException
from langchain_core.messages import AIMessage
from pydantic import ValidationError

from agent.composition import Ports
from agent.config import Limits
from agent.contracts.router import PersonaOp
from agent.contracts.tools import (
    DraftPersonaInput,
    PersonaPublished,
    PersonaRolledBack,
    PublishPersonaInput,
    RollbackPersonaInput,
)
from agent.core.catalog import render
from agent.core.persona_gate import PersonaQualityGate, checks_line, rejection_text
from agent.domain.persona import Persona, PersonaPath
from agent.errors import ErrorClass, MessageClass
from agent.graph.prompts import persona_drafter_messages
from agent.graph.state import AgentState
from agent.ports.llm import ModelStep
from agent.tools.branches import (
    draft_persona,
    may_edit_persona,
    publish_persona,
    rollback_persona,
)
from agent.tools.context import TurnContext


class PersonaNodes:
    """The five nodes. ``permission_to`` routes the permission node: refused, change or undo."""

    def __init__(
        self, ports: Ports, limits: Limits, *, refuse: str, draft: str, rollback: str
    ) -> None:
        self._ports = ports
        self._limits = limits
        self._refuse = refuse
        self._draft = draft
        self._rollback = rollback

    def persona_permission(self, state: AgentState) -> dict[str, Any]:
        user = state["user"]
        assert user is not None, "load_context refuses a turn without a User"
        if may_edit_persona(user):
            return {}
        return {
            "error_class": ErrorClass.PERMANENT,
            "message_class": MessageClass.PERSONA_FORBIDDEN,
        }

    def permission_to(self, state: AgentState) -> str:
        if state.get("message_class"):
            return self._refuse
        router = state["router"]
        return self._rollback if router.persona_op is PersonaOp.UNDO else self._draft

    def draft_persona(self, state: AgentState) -> dict[str, Any]:
        """The drafter model call, then ``draft_persona``. A draft the model cannot shape into
        a Persona is a failed change, like a gate failure: nothing is stored, and the CEO is
        told to ask differently. A model outage raises ``TransientError`` (``MODEL_BUSY``)."""
        args = DraftPersonaInput(request=state["messages"][-1].text[:2000])
        current = state["persona"]
        model = self._ports.llm.chat(ModelStep.PERSONA_DRAFTER).with_structured_output(
            Persona, method="json_schema"
        )
        try:
            drafted = model.invoke(persona_drafter_messages(current.persona, args.request))
        except (ValidationError, OutputParserException):
            drafted = None
        if not isinstance(drafted, Persona):
            return self._failed(
                state,
                reason="I couldn't turn that request into a tone. Try it in fewer words.",
            )
        return {"persona_draft": draft_persona(drafted, self._ctx(state), current)}

    def drafted_to(self, gate: str, after: str):
        def route(state: AgentState) -> str:
            return gate if state.get("persona_draft") else after

        return route

    def persona_gate(self, state: AgentState) -> dict[str, Any]:
        draft = state["persona_draft"]
        assert draft is not None, "routed here only with a draft"
        gate = PersonaQualityGate(self._ports.llm, self._ports.pii).check(draft.persona)
        return {"persona_gate": gate}

    def publish_persona(self, state: AgentState) -> dict[str, Any]:
        draft, gate = state["persona_draft"], state["persona_gate"]
        assert draft is not None and gate is not None, "routed here after the gate"
        out = publish_persona(
            PublishPersonaInput(persona=draft.persona, path=PersonaPath.CHAT),
            self._ctx(state),
            gate,
        )
        if isinstance(out, PersonaPublished):
            text = (
                f"{checks_line(out.gate)}\n"
                f"Done. Reports now use v{out.active.version}: {draft.summary}.\n"
                f'Say "undo the tone change" to go back to v{out.previous_version}.'
            )
            return {"persona": out.active, "messages": [AIMessage(content=text)]}
        said = render(out.message_class, state["persona"].persona, ref=state["trace_id"])
        return {
            "error_class": ErrorClass.TRANSIENT if out.gate.could_not_run else None,
            "message_class": out.message_class,
            "messages": [AIMessage(content=rejection_text(out.gate, said))],
        }

    def rollback_persona(self, state: AgentState) -> dict[str, Any]:
        out = rollback_persona(RollbackPersonaInput(), self._ctx(state))
        if isinstance(out, PersonaRolledBack):
            text = (
                f"Done. Reports are back to v{out.active.version} (from v{out.from_version}). "
                "Your next answer uses it."
            )
            return {"persona": out.active, "messages": [AIMessage(content=text)]}
        return self._failed(state, message_class=out.message_class)

    def _failed(
        self,
        state: AgentState,
        *,
        reason: str = "",
        message_class: MessageClass = MessageClass.PERSONA_GATE_FAILED,
    ) -> dict[str, Any]:
        """The catalog message for ``message_class``, then ``reason``, which says what to try
        instead. A refused change is an outcome, not an error, so no ``error_class``."""
        said = render(message_class, state["persona"].persona, ref=state["trace_id"])
        return {
            "message_class": message_class,
            "messages": [AIMessage(content=f"{said} {reason}" if reason else said)],
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
