"""``agent`` command line.

The CLI only translates (docs/lld/07-web.md): text goes to ``ChatService.handle`` with a
stdout ``ChannelAdapter``; slash commands go to ``agent.core.views.Views``, the same calls the
web UI makes.

- ``agent chat --as <user_id>``: the chat loop. It catches everything, so a failed turn never
  ends the session. Slash commands, answered without a model call: ``/reports``, ``/profile``,
  ``/persona`` (``edit``, ``history``, ``rollback``), ``/trace [id]``, ``/stats``.
- ``agent stats --since 7d``: ``Views.stats`` outside the chat.
- ``agent eval [--suite S] [--trials K] [--no-trios] [--baseline RUN]``: the eval harness
  (docs/lld/08-eval.md); ``make eval`` calls it. Exit 0 PASS, 1 FAIL, 2 CANNOT_MEASURE.
- ``agent web``: serves the HTTP API for the UI (needs the ``web`` extra, ADR 0010).
"""

import argparse
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from agent.contracts.channel import Channel, InboundMessage
from agent.contracts.tools import ListReportsInput, ListReportsOutput
from agent.core.catalog import BUNDLED
from agent.core.chat import ChatService
from agent.core.views import Views
from agent.errors import AgentError
from agent.ports.channel import ChannelAdapter

_log = logging.getLogger("agent.cli")
_QUIT = {"/quit", "/exit"}


class ConversationChannel(ChannelAdapter, Protocol):
    """A channel that remembers the Conversation of the last message it delivered."""

    conversation_id: UUID | None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agent")
    sub = parser.add_subparsers(dest="command", required=True)
    chat = sub.add_parser("chat", help="chat as a seeded User")
    chat.add_argument("--as", dest="user_id", required=True)
    stats = sub.add_parser("stats", help="agent-level metrics from the traces")
    stats.add_argument("--since", default="7d")
    ev = sub.add_parser("eval", help="run the eval suites and print the scorecard")
    ev.add_argument(
        "--suite", action="append", choices=["correctness", "safety", "session", "step"]
    )
    ev.add_argument("--trials", type=int, default=1)
    ev.add_argument("--no-trios", action="store_true", help="the Golden Bucket ablation")
    ev.add_argument("--baseline", help="run ID to compare against")
    web = sub.add_parser("web", help="serve the HTTP API for the web UI")
    web.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    if args.command == "chat":
        return _chat(args.user_id)
    raise NotImplementedError(f"`agent {args.command}` is built in a later slice")


def _chat(user_id: str) -> int:
    from agent.composition import build_app, build_cli_channel
    from agent.config import Settings

    try:
        # Importing readline is what gives input() arrow keys and history; without it, a
        # stray arrow press reaches the model as a raw escape code (^[[C).
        import readline  # noqa: F401
    except ImportError:  # Windows has no readline; input() still works, just unedited.
        pass

    service, views = build_app(Settings())
    run_chat(service, views, user_id, build_cli_channel(), input)
    return 0


def run_chat(
    service: ChatService,
    views: Views,
    user_id: str,
    channel: ConversationChannel,
    read: Callable[[str], str],
    write: Callable[[str], None] = print,
) -> None:
    """Read a line, hand it to ``ChatService.handle``, repeat until EOF or ``/quit``.
    ``/reports`` goes to ``Views.reports`` instead, with no model call; ``Views`` has already
    run the PII filter on what ``write`` prints.

    Catches everything around a turn, so a failed turn never ends the session. ``handle``
    already never raises; this is the last line, and it logs rather than prints, because
    every message to the User leaves through ``Outbound.emit``.
    """
    while True:
        try:
            text = read("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            return
        if not text:
            continue
        if text in _QUIT:
            return
        if text == "/reports":
            try:
                write(render_reports(views.reports(user_id, ListReportsInput(limit=50))))
            except AgentError as exc:
                write(BUNDLED[exc.message_class].replace("{ref}", "none"))
            continue
        try:
            service.handle(
                InboundMessage(
                    user_id=user_id,
                    channel=Channel.CLI,
                    conversation_id=channel.conversation_id,
                    text=text,
                    received_at=datetime.now(UTC),
                ),
                channel,
            )
        except Exception as exc:
            _log.error("the turn failed (%s); the session continues", type(exc).__name__)


def render_reports(listed: ListReportsOutput) -> str:
    if not listed.reports:
        return "You have no saved reports yet."
    lines = [f"Your saved reports ({listed.total}):"]
    for n, r in enumerate(listed.reports, start=1):
        note = "" if r.accessible else "  (covers Brands you no longer have; title only)"
        lines.append(f"  {n}. {r.title}  ({r.created_at:%d %b %Y}){note}")
    if listed.total > len(listed.reports):
        lines.append(f"  … and {listed.total - len(listed.reports)} older.")
    return "\n".join(lines)
