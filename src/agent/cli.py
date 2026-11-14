"""``agent`` command line. Stub.

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
    raise NotImplementedError(f"`agent {args.command}` is built in Phase 4")
