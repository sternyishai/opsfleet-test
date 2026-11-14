"""``agent`` command line. Stub.

The CLI only translates (docs/lld/07-web.md): text goes to ``ChatService.handle`` with a
stdout ``ChannelAdapter``; slash commands go to ``agent.core.views.Views``, the same calls the
web UI makes.

- ``agent chat --as <user_id>``: the chat loop. It catches everything, so a failed turn never
  ends the session. Slash commands, answered without a model call: ``/reports``, ``/profile``,
  ``/persona`` (``edit``, ``history``, ``rollback``), ``/trace [id]``, ``/stats``.
- ``agent stats --since 7d``: ``Views.stats`` outside the chat.
- ``agent web``: serves the HTTP API for the UI (needs the ``web`` extra, ADR 0010).
"""

import argparse


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agent")
    sub = parser.add_subparsers(dest="command", required=True)
    chat = sub.add_parser("chat", help="chat as a seeded User")
    chat.add_argument("--as", dest="user_id", required=True)
    stats = sub.add_parser("stats", help="agent-level metrics from the traces")
    stats.add_argument("--since", default="7d")
    web = sub.add_parser("web", help="serve the HTTP API for the web UI")
    web.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)
    raise NotImplementedError(f"`agent {args.command}` is built in Phase 4")
