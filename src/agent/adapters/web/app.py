"""The FastAPI app of the web channel (ADR 0010, LLD 07 § Feature map).

Like the CLI it only translates: a ``WebRequest`` becomes ``ChatService.handle`` on an SSE
stream, every other route is one ``Views`` call, and a classified error becomes its catalog
message. Identity is resolved here, before either is called, and never read from a body.
"""

import threading
from collections.abc import AsyncIterable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID

import anyio.to_thread
from fastapi import Body, Depends, FastAPI, Header, Query, Request
from fastapi.responses import JSONResponse
from fastapi.sse import EventSourceResponse, ServerSentEvent
from fastapi.staticfiles import StaticFiles

from agent.contracts.channel import (
    Channel,
    ConfirmationResponse,
    InboundMessage,
    OutboundMessage,
)
from agent.contracts.tools import (
    GetReportOk,
    ListReportsInput,
    ListReportsOutput,
    PersonaRolledBack,
    PublishPersonaOutput,
    ToolError,
)
from agent.contracts.views import StatsView, TraceView
from agent.contracts.web import ApiError, DevUser, WebConfirmation, WebRequest, WebText
from agent.core.catalog import BUNDLED
from agent.core.chat import ChatService
from agent.core.views import Views
from agent.domain.identity import UserId
from agent.domain.persona import PersonaVersion
from agent.domain.profile import UserProfile
from agent.errors import AgentError, MessageClass, PermanentError
from agent.ports.tracer import Tracer

from .sse import SseChannel

DEV_USER_HEADER = "X-Agent-User"
_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": ApiError},
    403: {"model": ApiError},
    400: {"model": ApiError},
    404: {"model": ApiError},
    409: {"model": ApiError},
    503: {"model": ApiError},
}
_STATUS = {
    MessageClass.REPORT_NOT_FOUND: 404,
    MessageClass.PERSONA_NOTHING_TO_ROLL_BACK: 409,
}
_WAIT_S = 0.5
"""How long one wait for the next message holds a worker thread; a disconnect abandons it."""


def _resolve_user(
    request: Request,
    x_agent_user: Annotated[str | None, Header(alias=DEV_USER_HEADER)] = None,
) -> UserId:
    """The User of a request, before ``ChatService`` or ``Views`` sees it. The prototype's
    only source is the dev header; ``ChatService`` and ``Views`` load role and Brands."""
    if not request.app.state.dev_identity:
        if x_agent_user is None:
            raise _refusal(401, "Sign in to use the assistant.")
        raise _refusal(403, "Picking a User is only possible in development.")
    if not x_agent_user:
        raise _refusal(401, "Pick a User first.")
    return x_agent_user


CurrentUser = Annotated[UserId, Depends(_resolve_user)]


class _Refused(Exception):
    """An HTTP error with an ``ApiError`` body."""

    def __init__(self, status: int, error: ApiError) -> None:
        self.status = status
        self.error = error


def _refusal(status: int, message: str) -> _Refused:
    return _Refused(status, ApiError(message_class=None, message=message))


def _ok[T](out: T | ToolError) -> T:
    """A view's value, or its ``ToolError`` as the catalog message (``detail`` stays here)."""
    if isinstance(out, ToolError):
        raise _catalog(out.message_class, _STATUS.get(out.message_class, 403))
    return out


def _catalog(message_class: MessageClass, status: int) -> _Refused:
    """The catalog message, as the CLI prints it for a view (no reference ID)."""
    return _Refused(
        status,
        ApiError(
            message_class=message_class,
            message=BUNDLED[message_class].replace("{ref}", "none"),
        ),
    )


def create_app(
    service: ChatService,
    views: Views,
    tracer: Tracer,
    *,
    dev_users: Callable[[], list[DevUser]] | None,
    ui_dir: Path | None = None,
) -> FastAPI:
    """``dev_users`` is the dev identity (``WEB_DEV_IDENTITY``): the picker's list, and with
    it the ``X-Agent-User`` header is accepted. None, the default in production, refuses
    the header and serves no ``/api/dev/users``; the identity would come from IAP or the
    Channel Gateway's SSO, which the prototype does not have, so every request is 401.

    ``ui_dir`` is the built React app (``web/dist``, ``WEB_UI_DIR``), served at ``/`` next
    to ``/api`` so one origin needs no CORS; None serves the API only (``make dev`` runs
    Vite instead)."""
    app = FastAPI(title="Retail Analysis Assistant", responses=_ERRORS)
    app.state.dev_identity = dev_users is not None

    @app.exception_handler(_Refused)
    async def _refused(_: Request, exc: _Refused) -> JSONResponse:
        return _response(exc)

    @app.exception_handler(AgentError)
    async def _agent_error(_: Request, exc: AgentError) -> JSONResponse:
        return _response(
            _catalog(exc.message_class, 403 if isinstance(exc, PermanentError) else 503)
        )

    @app.post("/api/messages", response_class=EventSourceResponse)
    async def messages(
        body: Annotated[WebRequest, Body()], user_id: CurrentUser
    ) -> AsyncIterable[OutboundMessage]:
        """One turn: ``event:`` is the message's ``kind``, ``data:`` its JSON. The stream
        closes after a ``reply`` or a ``confirmation``."""
        channel = SseChannel(tracer)
        message = _inbound(body, user_id)

        def run() -> None:
            try:
                service.handle(message, channel)
            finally:
                channel.end()

        # Not a daemon: a turn whose browser left still finishes and is checkpointed.
        threading.Thread(target=run, name="web-turn").start()
        try:
            while not channel.finished:
                # Waiting takes nothing, so a disconnect cancelling it loses no message.
                await anyio.to_thread.run_sync(channel.wait, _WAIT_S, abandon_on_cancel=True)
                while (sent := channel.take()) is not None:
                    # The annotation documents the data (OutboundMessage) in OpenAPI; the
                    # envelope sets `event:` to its kind. FastAPI does not re-validate it.
                    yield ServerSentEvent(event=sent.kind, raw_data=sent.model_dump_json())  # pyright: ignore[reportReturnType]
                    if sent.kind != "progress":
                        return
        finally:
            channel.close()

    @app.get("/api/reports")
    def reports(
        user_id: CurrentUser, limit: Annotated[int, Query(ge=1, le=50)] = 50
    ) -> ListReportsOutput:
        return views.reports(user_id, ListReportsInput(limit=limit))

    @app.get("/api/reports/{report_id}")
    def report(user_id: CurrentUser, report_id: UUID) -> GetReportOk:
        return _ok(views.report(user_id, report_id))

    @app.get("/api/profile")
    def profile(user_id: CurrentUser) -> UserProfile:
        return views.profile(user_id)

    @app.delete("/api/profile")
    def reset_profile(user_id: CurrentUser) -> UserProfile:
        return views.remove_preference(user_id)

    @app.delete("/api/profile/notes/{note_id}")
    def remove_note(user_id: CurrentUser, note_id: UUID) -> UserProfile:
        return views.remove_preference(user_id, note_id)

    @app.get("/api/persona")
    def persona(user_id: CurrentUser) -> PersonaVersion:
        return views.persona(user_id)

    @app.get("/api/persona/versions")
    def persona_versions(user_id: CurrentUser) -> list[PersonaVersion]:
        return views.persona_history(user_id)

    @app.put("/api/persona")
    def edit_persona(
        user_id: CurrentUser, changes: Annotated[dict[str, str | list[str]], Body()]
    ) -> PublishPersonaOutput:
        """``changes`` maps a field (``emphasis``, ``tone.preset``, ``messages.model_busy``,
        ``report_sections``) to its new value; the Quality Gate judges it."""
        return views.edit_persona(user_id, changes)

    @app.post("/api/persona/rollback")
    def rollback_persona(
        user_id: CurrentUser, to_version: Annotated[int | None, Query(ge=1)] = None
    ) -> PersonaRolledBack:
        return _ok(views.rollback_persona(user_id, to_version))

    @app.get("/api/traces")
    def last_trace(user_id: CurrentUser) -> TraceView:
        """The User's last turn, as ``/trace`` with no ID."""
        view = views.trace(user_id)
        if view is None:
            raise _refusal(404, "No turn to show yet.")
        return view

    @app.get("/api/traces/{trace_id}")
    def trace(user_id: CurrentUser, trace_id: str) -> TraceView:
        view = views.trace(user_id, trace_id)
        if view is None:  # a missing turn and another User's look the same
            raise _refusal(404, f"No turn of yours has the ID {trace_id}.")
        return view

    @app.get("/api/stats")
    def stats(user_id: CurrentUser, since: str = "7d") -> StatsView:
        try:
            return views.stats(user_id, since)
        except ValueError:
            raise _refusal(400, "Give the window as 7d, 24h or 30m.") from None

    if dev_users is not None:
        listed = dev_users  # a name the closure can keep narrowed

        @app.get("/api/dev/users")
        def users() -> list[DevUser]:
            """The seeded Users for the picker, with role and Brands from the directory."""
            return listed()

    if ui_dir is not None:
        # Mounted last: every /api route above is matched first.
        app.mount("/", StaticFiles(directory=ui_dir, html=True), name="ui")

    return app


def _response(refused: _Refused) -> JSONResponse:
    return JSONResponse(refused.error.model_dump(mode="json"), status_code=refused.status)


def _inbound(
    body: WebText | WebConfirmation, user_id: UserId
) -> InboundMessage | ConfirmationResponse:
    if isinstance(body, WebText):
        return InboundMessage(
            user_id=user_id,
            channel=Channel.WEB,
            conversation_id=body.conversation_id,
            text=body.text,
            received_at=datetime.now(UTC),
        )
    return ConfirmationResponse(
        user_id=user_id,
        conversation_id=body.conversation_id,
        request_id=body.request_id,
        approved=body.approved,
        selected_indices=body.selected_indices,
    )
