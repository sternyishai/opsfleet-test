"""Views: the model-free commands, in one place for every channel. Reports are built; the
other views arrive with their slice.

Rule: **a channel only translates.** The CLI turns ``/reports`` into ``Views.reports``; the web
adapter turns ``GET /api/reports`` into the same call. No channel reads a store itself, so
the CLI and the UI cannot drift, and Author isolation and scope checks are written once.

``chat`` (``ChatService.handle``) covers everything that needs the agent; ``Views`` covers
everything that does not.
"""

from uuid import UUID

from agent.composition import Ports
from agent.config import Limits
from agent.contracts.channel import Channel
from agent.contracts.tools import (
    GetReportInput,
    GetReportOk,
    GetReportOutput,
    ListReportsInput,
    ListReportsOutput,
    PublishPersonaOutput,
    RollbackPersonaOutput,
)
from agent.contracts.views import StatsView, TraceView
from agent.domain.identity import UserId
from agent.domain.persona import Persona, PersonaVersion
from agent.domain.profile import UserProfile
from agent.errors import MessageClass, PermanentError
from agent.tools.context import TurnContext
from agent.tools.reports import get_my_report, list_my_reports

_NO_CONVERSATION = UUID(int=0)
"""A view runs outside any Conversation, so ``this_conversation`` matches nothing."""


class Views:
    """Interface:

    - Every method takes the ``user_id`` the driving adapter resolved, loads the User through
      ``UserDirectory`` and is restricted to them, exactly like the tools: reports are the
      Author's own and scope-checked; Persona methods check ``persona_editor`` in code.
    - Report methods reuse the tool functions (``list_my_reports``, ``get_my_report``), so
      the agent, the CLI and the UI share one implementation.
    - Every text field of a result passes the ``PiiFilter`` before it is returned, so a view is
      a way out like any other (ADR 0003). Stored content was already filtered on write; this
      is the second check.
    - ``edit_persona`` is the direct-edit path: it enters the Persona branch at the permission
      check, skips drafting, and runs the same Quality Gate (HLD 09).
    - ``trace`` and ``stats`` read the JSONL traces. ``trace`` is limited to the User's own
      turns; ``stats`` is limited to Executives (LLD default) because it spans all Users.
    - Raises only classified errors (``agent.errors``); adapters render their catalog message.
    """

    def __init__(self, ports: Ports, limits: Limits) -> None:
        self._ports = ports
        self._limits = limits

    # reports: /reports, GET /api/reports, GET /api/reports/{id}
    def reports(self, user_id: UserId, query: ListReportsInput) -> ListReportsOutput:
        out = list_my_reports(query, self._context(user_id))
        reports = [r.model_copy(update={"title": self._clean(r.title)}) for r in out.reports]
        return out.model_copy(update={"reports": reports})

    def report(self, user_id: UserId, report_id: UUID) -> GetReportOutput:
        out = get_my_report(GetReportInput(report_id=report_id), self._context(user_id))
        if not isinstance(out, GetReportOk):
            return out
        r, clean = out.report, self._clean
        structured = r.structured.model_copy(
            update={
                "title": clean(r.structured.title),
                "summary": clean(r.structured.summary),
                "insights": [clean(i) for i in r.structured.insights],
                "action_items": [clean(a) for a in r.structured.action_items],
                "period": clean(r.structured.period) if r.structured.period else None,
            }
        )
        fields = {f: clean(getattr(r, f)) for f in ("title", "question", "sql", "markdown")}
        report = r.model_copy(update={**fields, "structured": structured})
        return out.model_copy(update={"report": report})

    def _context(self, user_id: UserId) -> TurnContext:
        """The same context a tool gets, for the User the channel resolved."""
        user = self._ports.users.load(user_id)
        if user is None or user.role is None:
            raise PermanentError(f"no role for {user_id!r}", message_class=MessageClass.NO_ROLE)
        return TurnContext(
            user=user,
            conversation_id=_NO_CONVERSATION,
            turn_id="view",
            channel=Channel.CLI,
            ports=self._ports,
            limits=self._limits,
        )

    def _clean(self, text: str) -> str:
        """The second check on the way out; stored content was filtered on write."""
        return self._ports.pii.scan(text).text

    # profile: /profile, GET|DELETE /api/profile
    def profile(self, user_id: UserId) -> UserProfile:
        raise NotImplementedError

    def remove_preference(self, user_id: UserId, note_id: UUID | None = None) -> UserProfile:
        raise NotImplementedError

    # Persona: /persona, /persona history|edit|rollback, /api/persona...
    def persona(self, user_id: UserId) -> PersonaVersion:
        raise NotImplementedError

    def persona_history(self, user_id: UserId) -> list[PersonaVersion]:
        raise NotImplementedError

    def edit_persona(self, user_id: UserId, persona: Persona) -> PublishPersonaOutput:
        raise NotImplementedError

    def rollback_persona(
        self, user_id: UserId, to_version: int | None = None
    ) -> RollbackPersonaOutput:
        raise NotImplementedError

    # observability: /trace [id], /stats, GET /api/traces/{id}, GET /api/stats
    def trace(self, user_id: UserId, trace_id: str | None = None) -> TraceView:
        """``None``: the User's last turn."""
        raise NotImplementedError

    def stats(self, user_id: UserId, since: str = "7d") -> StatsView:
        raise NotImplementedError
