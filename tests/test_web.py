"""The web channel (ADR 0010, LLD 07): it only translates, so the browser gets what the CLI
prints, every feature-map row calls the same ``ChatService`` / ``Views`` method, and identity
never comes from a request body."""

import dataclasses
import inspect
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from agent.adapters.persona_sqlite import SqlitePersonaStore
from agent.adapters.profile_sqlite import SqliteProfileStore
from agent.adapters.users_sqlite import SqliteUserDirectory
from agent.adapters.web import SseChannel, create_app
from agent.cli import run_chat
from agent.composition import Ports, build_web
from agent.config import Limits, ModelSettings, Settings
from agent.contracts.channel import (
    Channel,
    ConfirmationResponse,
    InboundMessage,
    OutboundMessage,
)
from agent.core.chat import ChatService
from agent.core.views import Views
from agent.domain.identity import Permission
from agent.domain.profile import PreferenceField, PreferenceScope, PreferenceStatement
from tests.conftest import SEED_USERS
from tests.fakes import (
    CollectingChannel,
    FakeLLMFactory,
    FakeTracer,
    ask,
    chat_service,
    fake_ports,
)
from tests.test_cli import typed
from tests.test_deletion_flow import DELETE
from tests.test_reports import CEO, MANAGER_A

LIMITS = Limits(_env_file=None)  # pyright: ignore[reportCallIssue]
HERE_WEB = UUID(int=0xAB)


def client(
    ports: Ports, *, service: ChatService | None = None, views: Any = None, dev: bool = True
) -> TestClient:
    service = service or chat_service(ports)[0]
    views = views or Views(ports, LIMITS)
    dev_users = ports.users.list_dev_users if dev else None  # type: ignore[attr-defined]
    return TestClient(create_app(service, views, ports.tracer, dev_users=dev_users))


def events(body: str) -> list[tuple[str, dict[str, Any]]]:
    """``(event, data)`` per SSE event; keep-alive comments are skipped."""
    out = []
    for block in body.strip().split("\n\n"):
        fields = dict(
            line.split(": ", 1) for line in block.splitlines() if not line.startswith(":")
        )
        if fields:
            out.append((fields["event"], json.loads(fields["data"])))
    return out


def shown(message: OutboundMessage | dict[str, Any]) -> tuple[str, str]:
    """What a User reads of a message: its kind and its text."""
    m = message if isinstance(message, dict) else message.model_dump(mode="json")
    return m["kind"], m.get("markdown") or m["text"]


def post_text(web: TestClient, user_id: str, text: str, **body: Any) -> str:
    """One ``WebText`` as ``user_id``; the raw event stream."""
    json_body = {"kind": "text", "text": text, **body}
    return web.post("/api/messages", json=json_body, headers={"X-Agent-User": user_id}).text


def revenue_ports() -> Ports:
    llm = FakeLLMFactory(analyst=[AIMessage(content="Revenue was $2. Ask jane@example.com.")])
    return fake_ports(CEO, MANAGER_A, llm=llm)


def test_the_stream_carries_exactly_what_the_cli_prints_for_one_turn() -> None:
    cli, _ = ask(chat_service(revenue_ports())[0], "ceo", "What was total revenue in 2025?")

    response = client(revenue_ports()).post(
        "/api/messages",
        json={"kind": "text", "text": "What was total revenue in 2025?"},
        headers={"X-Agent-User": "ceo"},
    )

    assert response.headers["content-type"].startswith("text/event-stream")
    web = events(response.text)
    assert [kind for kind, _ in web] == [m.kind for m in cli]
    assert [shown(data) for _, data in web] == [shown(m) for m in cli]
    assert "jane@example.com" not in response.text  # the output PII filter ran
    assert web[-1][0] == "reply"


# --- identity --------------------------------------------------------------------------------


def test_the_dev_header_is_refused_when_dev_identity_is_disabled() -> None:
    web = client(revenue_ports(), dev=False)

    with_header = web.get("/api/reports", headers={"X-Agent-User": "ceo"})
    without = web.get("/api/reports")
    chat = web.post(
        "/api/messages", json={"kind": "text", "text": "hi"}, headers={"X-Agent-User": "ceo"}
    )

    assert (with_header.status_code, without.status_code, chat.status_code) == (403, 401, 403)


def test_dev_users_is_not_served_when_dev_identity_is_disabled() -> None:
    assert client(revenue_ports(), dev=False).get("/api/dev/users").status_code == 404


def test_no_body_can_name_the_user() -> None:
    response = client(revenue_ports()).post(
        "/api/messages",
        json={"kind": "text", "text": "What was revenue?", "user_id": "ceo"},
        headers={"X-Agent-User": "manager-a"},
    )

    assert response.status_code == 422


def test_dev_users_lists_each_seeded_users_role_and_brands(tmp_path: Path) -> None:
    seed = tmp_path / "users.yaml"
    seed.write_text(SEED_USERS + "  - {user_id: intern}\n")
    users = SqliteUserDirectory(tmp_path / "app.db", seed=seed)
    ports = dataclasses.replace(revenue_ports(), users=users)

    listed = client(ports).get("/api/dev/users").json()

    assert listed == [
        {"user_id": "ceo", "role": "executive", "brands": []},
        {"user_id": "intern", "role": None, "brands": []},
        {"user_id": "manager-a", "role": "manager", "brands": ["Calvin Klein", "Tommy Hilfiger"]},
        {"user_id": "manager-b", "role": "manager", "brands": ["Carhartt"]},
    ]


def test_an_unknown_dev_user_gets_no_role_from_the_views() -> None:
    response = client(revenue_ports()).get("/api/profile", headers={"X-Agent-User": "stranger"})

    assert response.status_code == 403
    assert response.json()["message_class"] == "no_role"


# --- the feature map -------------------------------------------------------------------------


class RecordingViews(Views):
    """Records every public ``Views`` call with its arguments bound by name, then runs it."""

    def __init__(self, ports: Ports) -> None:
        super().__init__(ports, LIMITS)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __getattribute__(self, name: str) -> Any:
        attr = super().__getattribute__(name)
        if name.startswith("_") or not callable(attr) or name == "calls":
            return attr

        def recorded(*args: Any, **kwargs: Any) -> Any:
            bound = inspect.signature(attr).bind(*args, **kwargs)
            bound.apply_defaults()
            self.calls.append((name, dict(bound.arguments)))
            return attr(*args, **kwargs)

        return recorded


EDITOR = CEO.model_copy(update={"permissions": frozenset({Permission.PERSONA_EDITOR})})


def feature_ports(tmp_path: Path) -> Ports:
    seed = tmp_path / "users.yaml"
    seed.write_text(SEED_USERS)
    SqliteUserDirectory(tmp_path / "app.db", seed=seed)
    profiles = SqliteProfileStore(tmp_path / "app.db")
    note = PreferenceStatement(
        scope=PreferenceScope.STANDING, field=PreferenceField.NOTE, value="compare with 2024"
    )
    profiles.save_standing("ceo", [note])
    personas = SqlitePersonaStore(tmp_path / "app.db", seed=Path("seed/persona.yaml"))
    return fake_ports(EDITOR, MANAGER_A, profiles=profiles, personas=personas)


type Calls = list[tuple[str, dict[str, Any]]]


def cli_calls(ports: Ports, *lines: str) -> Calls:
    views = RecordingViews(ports)
    run_chat(chat_service(ports)[0], views, "ceo", CollectingChannel(), typed(*lines), print)
    return views.calls


def web_calls(
    ports: Ports, method: str, url: str, **kwargs: Any
) -> list[tuple[str, dict[str, Any]]]:
    views = RecordingViews(ports)
    web = client(ports, views=views)
    web.request(method, url, headers={"X-Agent-User": "ceo"}, **kwargs)
    return views.calls


def test_every_feature_map_row_calls_the_same_views_method(tmp_path: Path) -> None:
    ports = feature_ports(tmp_path)
    note_id = ports.profiles.load("ceo").notes[0].note_id  # type: ignore[union-attr]
    rows = [
        (["/reports"], "GET", "/api/reports", {}),
        (["/profile"], "GET", "/api/profile", {}),
        (["/profile clear 1"], "DELETE", f"/api/profile/notes/{note_id}", {}),
        (["/persona"], "GET", "/api/persona", {}),
        (["/persona history"], "GET", "/api/persona/versions", {}),
        (["/persona rollback"], "POST", "/api/persona/rollback", {}),
        (
            ["/persona edit", "formal", "", "", "", ""],
            "PUT",
            "/api/persona",
            {"json": {"tone.preset": "formal"}},
        ),
        (["/trace abc123"], "GET", "/api/traces/abc123", {}),
        (["/stats 24h"], "GET", "/api/stats?since=24h", {}),
        (["/profile clear"], "DELETE", "/api/profile", {}),  # last: it empties the profile
    ]
    for lines, method, url, kwargs in rows:
        cli = cli_calls(ports, *lines)[-1]  # the last call does the work; earlier ones read

        web = web_calls(ports, method, url, **kwargs)[-1]

        assert web == cli, f"{url} vs {lines[0]}"


class RecordingChat(ChatService):
    def __init__(self) -> None:
        self.messages: list[InboundMessage | ConfirmationResponse] = []

    def handle(self, message: Any, channel: Any) -> None:
        self.messages.append(message)


def test_chat_text_reaches_chat_service_as_the_same_message() -> None:
    ports = revenue_ports()
    cli, web = RecordingChat(), RecordingChat()
    run_chat(cli, Views(ports, LIMITS), "ceo", CollectingChannel(), typed("revenue in 2025?"))

    client(ports, service=web).post(
        "/api/messages",
        json={"kind": "text", "text": "revenue in 2025?"},
        headers={"X-Agent-User": "ceo"},
    )

    [sent_cli], [sent_web] = cli.messages, web.messages
    assert isinstance(sent_web, InboundMessage)
    keep = {"user_id", "conversation_id", "text"}
    assert sent_web.model_dump(include=keep) == sent_cli.model_dump(include=keep)
    assert sent_web.channel is Channel.WEB


def test_unchecking_a_box_deletes_what_yes_except_deletes() -> None:
    from tests.test_deletion import HERE, titles
    from tests.test_deletion_flow import say, service, three_ck_reports

    by_text = three_ck_reports()
    svc, _ = service(by_text, [DELETE])
    say(svc, "delete all reports mentioning Calvin Klein")
    [cli_reply] = say(svc, "yes except 2")

    by_box = three_ck_reports()
    svc, _ = service(by_box, [DELETE])
    web = TestClient(
        create_app(svc, Views(fake_ports(MANAGER_A), LIMITS), FakeTracer(), dev_users=list)
    )
    asked = post_text(
        web, "manager-a", "delete all reports mentioning Calvin Klein", conversation_id=str(HERE)
    )
    [(kind, plan)] = events(asked)
    assert kind == "confirmation"
    confirm = {
        "kind": "confirmation",
        "conversation_id": str(HERE),
        "request_id": plan["request_id"],
        "approved": True,
        "selected_indices": [1, 3],
    }
    answered = web.post("/api/messages", json=confirm, headers={"X-Agent-User": "manager-a"})
    [(_, web_reply)] = events(answered.text)

    assert shown(web_reply) == shown(cli_reply)
    assert titles(by_box) == titles(by_text) == ["Churn drivers", "Tommy returns"]


def test_a_text_without_a_conversation_starts_a_new_one() -> None:
    web = client(revenue_ports())
    me = {"X-Agent-User": "ceo"}
    first = events(
        web.post("/api/messages", json={"kind": "text", "text": "revenue?"}, headers=me).text
    )
    again = events(
        web.post("/api/messages", json={"kind": "text", "text": "revenue?"}, headers=me).text
    )

    assert first[-1][1]["conversation_id"] != again[-1][1]["conversation_id"]


# --- disconnects -----------------------------------------------------------------------------


def test_a_closed_stream_drops_and_traces_and_the_turn_is_still_checkpointed() -> None:
    tracer = FakeTracer()
    ports = dataclasses.replace(revenue_ports(), tracer=tracer)
    service, graph = chat_service(ports)
    channel = SseChannel(tracer)
    channel.close()  # the browser left before the turn ended

    service.handle(
        InboundMessage(
            user_id="ceo",
            channel=Channel.WEB,
            conversation_id=HERE_WEB,
            text="What was total revenue in 2025?",
            received_at=datetime.now(UTC),
        ),
        channel,
    )

    dropped = [s.attributes["kind"] for s in tracer.spans if s.name == "channel.dropped"]
    assert dropped[-1] == "reply"
    state = graph.get_state({"configurable": {"thread_id": str(HERE_WEB)}}).values
    assert state["messages"][-1].content.startswith("Revenue was $2.")


def test_messages_queued_but_not_streamed_are_traced_as_dropped() -> None:
    tracer = FakeTracer()
    channel = SseChannel(tracer)
    [reply] = ask(chat_service(revenue_ports())[0], "ceo", "revenue?")[0]
    channel.deliver(reply)

    channel.close()

    assert [s.attributes["kind"] for s in tracer.spans if s.name == "channel.dropped"] == ["reply"]


# --- wiring ----------------------------------------------------------------------------------


def test_the_committed_openapi_document_is_current() -> None:
    """``web/src/api/schema.d.ts`` is generated from ``web/openapi.json``; if this fails,
    run ``make types`` (``make types-check`` also checks the TypeScript side)."""
    from agent.adapters.web.openapi import openapi_document

    committed = json.loads(Path("web/openapi.json").read_text())

    assert committed == openapi_document(), "stale: run `make types`"


def test_the_core_imports_without_the_web_extra() -> None:
    blocked = "import sys\nfor m in ('fastapi', 'starlette', 'uvicorn'): sys.modules[m] = None\n"
    imports = "import agent.cli, agent.composition, agent.core.chat, agent.core.views\n"
    imports += "import agent.graph.build, agent.contracts.web, agent.adapters.users_sqlite\n"

    done = subprocess.run([sys.executable, "-c", blocked + imports], capture_output=True)

    assert done.returncode == 0, done.stderr.decode()


def test_the_dev_identity_refuses_to_start_off_loopback() -> None:
    models = ModelSettings(router="m", analyst="m", _env_file=None)  # pyright: ignore[reportCallIssue]
    settings = Settings(models=models, web_dev_identity=True, _env_file=None)  # pyright: ignore[reportCallIssue]

    with pytest.raises(ValueError, match="127.0.0.1"):
        build_web(settings, host="0.0.0.0")
