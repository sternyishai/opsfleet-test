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
from agent.core.chat import ChatService
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
    from agent.composition import build_chat_service, build_cli_channel
    from agent.config import Settings

    try:
        # Importing readline is what gives input() arrow keys and history; without it, a
        # stray arrow press reaches the model as a raw escape code (^[[C).
        import readline  # noqa: F401
    except ImportError:  # Windows has no readline; input() still works, just unedited.
        pass

    run_chat(build_chat_service(Settings()), user_id, build_cli_channel(), input)
    return 0


def run_chat(
    service: ChatService,
    user_id: str,
    channel: ConversationChannel,
    read: Callable[[str], str],
) -> None:
    """Read a line, hand it to ``ChatService.handle``, repeat until EOF or ``/quit``.

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
