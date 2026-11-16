"""``agent`` command line.

The CLI only translates (docs/lld/07-web.md): text goes to ``ChatService.handle`` with a
stdout ``ChannelAdapter``; slash commands go to ``agent.core.views.Views``, the same calls the
web UI makes.

- ``agent chat --as <user_id>``: the chat loop. It catches everything, so a failed turn never
  ends the session. Slash commands, answered without a model call: ``/reports``, ``/profile``,
  ``/persona`` (``edit``, ``history``, ``rollback``), ``/trace [id]``, ``/stats``.
- ``agent stats --as <user_id> --since 7d``: ``Views.stats`` outside the chat (Executives).
- ``agent eval [--suite S] [--case ID] [--category C] [--trials K] [--no-trios]
  [--baseline RUN]``: the eval harness (docs/lld/08-eval.md); ``make eval`` calls it. Exit 0
  PASS, 1 FAIL, 2 CANNOT_MEASURE (also an unknown case ID or category).
- ``agent web [--host H] [--port P]``: serves the HTTP API for the UI (needs the ``web``
  extra, ADR 0010); ``make dev`` runs it with the Vite dev server.
"""

import argparse
import logging
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from agent.contracts.channel import Channel, InboundMessage
from agent.contracts.eval import Layer, LayerScore, Outcome, Scorecard
from agent.contracts.tools import (
    ListReportsInput,
    ListReportsOutput,
    PersonaPublished,
    PersonaRolledBack,
)
from agent.contracts.views import SpanNode, StatsView, TraceView
from agent.core.catalog import BUNDLED, render
from agent.core.chat import ChatService
from agent.core.persona_gate import rejection_text
from agent.core.views import Views
from agent.domain.persona import PersonaVersion
from agent.domain.profile import UserProfile
from agent.errors import AgentError, MessageClass
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
    stats.add_argument("--as", dest="user_id", required=True, help="an Executive's user ID")
    stats.add_argument("--since", default="7d", help="7d, 24h or 30m")
    ev = sub.add_parser("eval", help="run the eval suites and print the scorecard")
    ev.add_argument(
        "--suite", action="append", choices=["correctness", "safety", "session", "step"]
    )
    ev.add_argument("--case", action="append", dest="case_ids", metavar="ID", help="repeatable")
    ev.add_argument("--category", action="append", dest="categories", metavar="NAME")
    ev.add_argument("--trials", type=int, default=1, choices=range(1, 11), metavar="K")
    ev.add_argument("--no-trios", action="store_true", help="the Golden Bucket ablation")
    ev.add_argument("--baseline", help="run ID to compare against")
    web = sub.add_parser("web", help="serve the HTTP API for the web UI")
    web.add_argument("--host", default="127.0.0.1")
    web.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    if args.command == "chat":
        return _chat(args.user_id)
    if args.command == "stats":
        return _stats_command(args.user_id, args.since)
    if args.command == "eval":
        return _eval(
            select={"suites": args.suite, "case_ids": args.case_ids, "categories": args.categories},
            trials=args.trials,
            trios=not args.no_trios,
            baseline=args.baseline,
        )
    return _web(args.host, args.port)


def _web(host: str, port: int) -> int:
    from agent.composition import build_web
    from agent.config import Settings

    try:
        import uvicorn
    except ImportError:
        print("agent web needs the web extra: uv sync --extra web", file=sys.stderr)
        return 2
    try:
        app = build_web(Settings(), host=host)
    except ValueError as exc:
        print(f"agent web: {exc}", file=sys.stderr)
        return 2
    uvicorn.run(app, host=host, port=port)
    return 0


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
    ``/reports``, ``/profile [clear [n]]``, ``/persona [history|edit|rollback]``,
    ``/trace [id]`` and ``/stats [7d]``
    go to ``Views`` instead, with no model call; ``Views`` has already run the PII filter on
    what ``write`` prints.

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
        command = _command(text, views, user_id, read)
        if command is not None:
            try:
                write(command())
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


def _command(
    text: str, views: Views, user_id: str, read: Callable[[str], str]
) -> Callable[[], str] | None:
    """The view a slash command asks for, or None for a message to the agent."""
    name, _, arg = text.partition(" ")
    arg = arg.strip()
    if name == "/persona":
        return lambda: _persona(views, user_id, arg, read)
    if name == "/profile":
        return lambda: _profile(views, user_id, arg)
    if name == "/reports":
        return lambda: render_reports(views.reports(user_id, ListReportsInput(limit=50)))
    if name == "/trace":
        return lambda: _trace(views, user_id, arg or None)
    if name == "/stats":
        return lambda: _stats(views, user_id, arg or "7d")
    return None


def _profile(views: Views, user_id: str, arg: str) -> str:
    """``/profile clear 2`` names a note by the number ``/profile`` shows; the number is
    turned into that note's ID here, and ``Views`` removes it."""
    if arg == "":
        return render_profile(views.profile(user_id))
    word, _, number = arg.partition(" ")
    if word != "clear":
        return "Use /profile, /profile clear <number> or /profile clear."
    if not number.strip():
        return render_profile(views.remove_preference(user_id))
    notes = views.profile(user_id).notes
    n = int(number) if number.strip().isdigit() else 0
    if not 1 <= n <= len(notes):
        return f"There is no note {number.strip()}. /profile lists your notes with their numbers."
    return render_profile(views.remove_preference(user_id, notes[n - 1].note_id))


_PROFILE_FIELDS = (
    ("format", "Format"),
    ("depth", "Level of detail"),
    ("charts", "Charts"),
    ("language", "Language"),
)


def render_profile(profile: UserProfile) -> str:
    values = [
        (label, getattr(profile, name)) for name, label in _PROFILE_FIELDS if getattr(profile, name)
    ]
    if not values and not profile.notes:
        return (
            'You have no saved preferences yet. Tell me one, for example "always give me tables".'
        )
    lines = ["Your preferences:"]
    for label, v in values:
        lines.append(
            f"  {label}: {str(v.value).replace('_', ' ')}  (since {v.confirmed_at:%d %b %Y})"
        )
    if profile.notes:
        lines.append("Your notes:")
        lines += [f"  {n}. {note.text}" for n, note in enumerate(profile.notes, start=1)]
    lines.append("Remove a note with /profile clear <number>, or everything with /profile clear.")
    return "\n".join(lines)


def _persona(views: Views, user_id: str, arg: str, read: Callable[[str], str]) -> str:
    if arg == "":
        return render_persona(views.persona(user_id))
    if arg == "history":
        return render_persona_history(views.persona_history(user_id))
    if arg == "rollback":
        before = views.persona(user_id)  # the permission check, and the wording to use
        out = views.rollback_persona(user_id)
        if isinstance(out, PersonaRolledBack):
            return (
                f"Back to v{out.active.version} (was v{out.from_version}). "
                "Your next answer uses it."
            )
        return render(out.message_class, before.persona, ref="none")
    if arg == "edit":
        return _edit_persona(views, user_id, read)
    return "Use /persona, /persona history, /persona edit or /persona rollback."


def _edit_persona(views: Views, user_id: str, read: Callable[[str], str]) -> str:
    """Field by field; Enter keeps the current value (HLD 09 § In the prototype). Only what
    was typed is sent: ``Views`` applies it to the stored version, and the gate judges it."""
    current = views.persona(user_id)  # the permission check, before any prompt
    p = current.persona
    changes: dict[str, object] = {}

    def ask(field: str, label: str, shown: str) -> None:
        if answer := read(f"{label} [{shown}]: ").strip():
            changes[field] = answer

    try:
        ask("tone.preset", "tone preset", p.tone.preset.value)
        ask("tone.notes", "tone notes", p.tone.notes)
        ask("report_sections", "report sections", ", ".join(p.report_sections))
        ask("emphasis", "emphasis", p.emphasis)
        message = read("reword a message, e.g. model_busy (Enter to skip): ").strip()
        if message:
            known = MessageClass(message) if message in set(MessageClass) else None
            shown = (p.messages.get(known) or BUNDLED[known]) if known else ""
            ask(f"messages.{message}", "wording", shown)
    except (EOFError, KeyboardInterrupt):
        return "Edit cancelled; nothing changed."
    if not changes:
        return f"Nothing changed; v{current.version} stays."
    if isinstance(sections := changes.get("report_sections"), str):
        changes["report_sections"] = [s.strip() for s in sections.split(",")]
    out = views.edit_persona(user_id, changes)
    if isinstance(out, PersonaPublished):
        return (
            f"Checking the new tone… ✓ all {len(out.gate.checks)} checks\n"
            f"Published v{out.active.version} (was v{out.previous_version}). "
            "Your next answer uses it."
        )
    return rejection_text(out.gate, render(out.message_class, p, ref="none"))


def render_persona(v: PersonaVersion) -> str:
    p = v.persona
    by = f" by {v.created_by}" if v.created_by else ""
    lines = [
        f"Persona v{v.version} · {v.path.value.replace('_', ' ')}{by} · {v.created_at:%d %b %Y}",
        f"  tone: {p.tone.preset.value}" + (f". {p.tone.notes}" if p.tone.notes else ""),
        f"  report sections: {', '.join(p.report_sections)}",
        f"  emphasis: {p.emphasis or 'none'}",
    ]
    if p.vocabulary.prefer or p.vocabulary.avoid:
        say = "; ".join(f'say "{new}" for "{old}"' for old, new in p.vocabulary.prefer.items())
        avoid = f"avoid {', '.join(p.vocabulary.avoid)}" if p.vocabulary.avoid else ""
        lines.append(f"  vocabulary: {'; '.join(x for x in (say, avoid) if x)}")
    lines.append(f"  examples: {len(p.examples)} · message wordings: {len(p.messages)}")
    return "\n".join(lines)


def render_persona_history(versions: list[PersonaVersion]) -> str:
    lines = ["Persona versions:"]
    for v in versions:
        by = f" by {v.created_by}" if v.created_by else ""
        changed = f"  changed: {', '.join(v.changed_fields)}" if v.changed_fields else ""
        active = "  (active)" if v.is_active else ""
        lines.append(
            f"  v{v.version}  {v.created_at:%d %b %Y %H:%M}  "
            f"{v.path.value.replace('_', ' ')}{by}{changed}{active}"
        )
    return "\n".join(lines)


def _trace(views: Views, user_id: str, trace_id: str | None) -> str:
    view = views.trace(user_id, trace_id)
    if view is not None:
        return render_trace(view)
    return f"No turn of yours has the ID {trace_id}." if trace_id else "No turn to show yet."


def _stats(views: Views, user_id: str, since: str) -> str:
    try:
        return render_stats(views.stats(user_id, since))
    except ValueError:
        return "Give the window as 7d, 24h or 30m, for example /stats 24h."


def _stats_command(user_id: str, since: str) -> int:
    from agent.composition import build_app
    from agent.config import Settings

    _, views = build_app(Settings())
    try:
        print(_stats(views, user_id, since))
    except AgentError as exc:
        print(BUNDLED[exc.message_class].replace("{ref}", "none"))
        return 1
    return 0


def _eval(
    *, select: dict[str, list[str] | None], trials: int, trios: bool, baseline: str | None
) -> int:
    from agent.composition import build_eval
    from agent.config import Settings
    from agent.evals.runner import exit_code, select_cases

    logging.getLogger("agent.evals").setLevel(logging.INFO)  # one line per case and trial
    app = build_eval(Settings(), trios_enabled=trios)
    try:
        cases = select_cases(app.cases, **select)
    except ValueError as exc:
        print(f"agent eval: {exc}", file=sys.stderr)
        return 2
    card = app.runner.run(cases, trials=trials, baseline_run_id=baseline)
    print(render_scorecard(card))
    print(f"\nscorecard: {app.scorecard_path(card.run_id)}")
    if app.sink.failures:
        print(f"not published to: {', '.join(app.sink.failures)} (see the log above)")
    return exit_code(card.outcome)


def render_scorecard(card: Scorecard) -> str:
    """The scorecard as text: the verdict and why, the rates per suite and category, one line
    per case (deterministic outcome first; judge and trajectory after it, never blended)."""
    m = card.manifest
    lines = [
        f"eval {card.run_id}  {card.outcome.value.upper()}",
        *(f"  {r}" for r in card.reasons),
        f"git {m.git_sha} · fixture {m.fixture_hash} · {m.prereg_id} · Persona v"
        f"{m.persona_version} · Trios {'on' if m.trios_enabled else 'off'} · data as of "
        f"{m.data_as_of} · trials {m.trials}",
        f"analyst {m.models.get('analyst', '?')} · router {m.models.get('router', '?')} · "
        f"judge {m.judge or 'off'}",
    ]
    for suite in card.suites:
        rates = "   ".join(
            f"{category.value} {r.passed}/{r.total} [{r.low:.0%}–{r.high:.0%}]"
            for category, r in suite.by_category.items()
        )
        metrics = "".join(
            f"   {name} {r.passed}/{r.total} [{r.low:.0%}–{r.high:.0%}]"
            for name, r in suite.metrics.items()
        )
        unmeasured = f"   cannot measure {suite.cannot_measure}" if suite.cannot_measure else ""
        lines.append(f"{suite.suite.value:<12} {rates}{metrics}{unmeasured}")
    lines.append("")
    for case in card.cases:
        trials = " ".join(_TRIAL_MARK[t.outcome] for t in case.trials)
        k = f"pass@k {'y' if case.pass_at_k else 'n'} pass^k {'y' if case.pass_all_k else 'n'}"
        lines.append(f"{case.outcome.value:<15}{case.case_id:<36}{trials:<8}{k}")
        for t in case.trials:
            detail = [t.error] if t.error else []
            detail += [_layer(s) for s in t.layers if _layer(s)]
            if detail:
                lines.append(f"{'':15}  trial {t.trial}: {'; '.join(detail)}")
    passing = sum(c.outcome is Outcome.PASS for c in card.cases)
    per_pass = f" (${card.cost_usd / passing:.4f} per passing case)" if passing else ""
    lines += [
        "",
        f"cost ${card.cost_usd:.4f}{per_pass} · {card.tokens:,} tokens · "
        f"p95 {card.p95_latency_s:.1f} s per turn",
        f"baseline {card.baseline_run_id or 'none'} · regressions "
        f"{', '.join(card.regressions) or 'none'} · unstable "
        f"{', '.join(card.unstable) or 'none'} · negative control "
        f"{'failed as it must' if card.negative_control_failed else 'did not fail'}",
    ]
    return "\n".join(lines)


_TRIAL_MARK = {Outcome.PASS: "P", Outcome.FAIL: "F", Outcome.CANNOT_MEASURE: "?"}


def _layer(score: LayerScore) -> str:
    """A layer's line in the per-trial detail: failures, the judge's scores, the trajectory."""
    if score.layer is Layer.TRAJECTORY:
        v = score.values
        return (
            f"trajectory {v.get('sql_attempts', 0):.0f} sql, {v.get('model_calls', 0):.0f} "
            f"calls, {v.get('tokens', 0):,.0f} tokens"
        )
    if score.layer is Layer.JUDGED:
        if score.outcome is Outcome.CANNOT_MEASURE:
            return f"judge (reported) {score.reason}"
        scores = " ".join(f"{d} {v:.0f}" for d, v in score.values.items())
        return f"judge (reported) {scores}"
    if score.outcome is Outcome.PASS:
        return ""
    return f"{score.scorer}: {score.reason or score.outcome.value}"


def render_trace(view: TraceView) -> str:
    """The step tree of HLD 08 § In the prototype."""
    header = (
        f"turn {view.trace_id}  {view.action or 'no action'}  {_seconds(view.duration_ms)}  "
        f"{view.tokens:,} tokens  {view.outcome}"
    )
    lines = [header]

    def walk(nodes: list[SpanNode], prefix: str) -> None:
        for i, node in enumerate(nodes):
            last = i == len(nodes) - 1
            label = f"{node.name}  {_seconds(node.duration_ms)}"
            if node.summary:
                label += f"  {node.summary}"
            lines.append(f"{prefix}{'└─' if last else '├─'} {label}")
            walk(node.children, prefix + ("   " if last else "│  "))

    walk(view.root, "")
    return "\n".join(lines)


def render_stats(view: StatsView) -> str:
    """The metric families of HLD 08, one block each."""
    families = view.families
    quality, safety, cost = families["quality"], families["safety"], families["cost"]
    reliability, knowledge = families["reliability"], families["knowledge"]
    deletion = families["deletion"]
    blocked = ", ".join(
        f"{key.split('.', 1)[1].replace('_', '-')} {n}"
        for key, n in safety.items()
        if key.startswith("input_guard.")
    )
    rejected = ", ".join(
        f"{key.split('.', 1)[1].replace('_', ' ')} {n}"
        for key, n in safety.items()
        if key.startswith("query_guard.")
    )
    usd, usd_q = cost["usd_per_successful_answer"], cost["usd_per_question"]
    rows = [
        (
            "quality",
            [
                f"task success {_pct(quality['task_success_rate'])}",
                f"first-try SQL {_pct(quality['first_try_sql_rate'])}",
                "self-correction saved "
                f"{quality['self_correction_saved']} of {quality['self_correction_needed']}",
            ],
        ),
        (
            "",
            [
                f"empty results {_pct(quality['empty_result_rate'])}",
                f"retries/question {_num(quality['retries_per_question'], 1)}",
            ],
        ),
        (
            "safety",
            [
                f"input guard blocked {safety['input_guard_blocked']}"
                + (f" ({blocked})" if blocked else ""),
                f"query guard rejected {safety['query_guard_rejected']}"
                + (f" ({rejected})" if rejected else ""),
            ],
        ),
        (
            "",
            [
                f"out-of-scope refusals {safety['out_of_scope_refusals']}",
                f"PII-filter hits {safety['pii_filter_hits']}",
                f"Persona changes {safety['persona_changes']} "
                f"(rollbacks {safety['persona_rollbacks']}, "
                f"gate rejections {safety['persona_rejections']})",
            ],
        ),
        (
            "cost",
            [
                f"{_num(cost['tokens_per_turn'], 0)} tokens/turn",
                f"{_mb(cost['bigquery_bytes_per_turn'])} BigQuery/turn",
                f"${usd:.4f} per successful answer"
                if isinstance(usd, float)
                else "$ n/a (set MODEL_PRICES)",
                *([f"${usd_q:.4f} per question"] if isinstance(usd_q, float) else []),
            ],
        ),
        (
            "reliability",
            [
                f"p50 {_num(reliability['p50_s'], 1)} s",
                f"p95 {_num(reliability['p95_s'], 1)} s",
                f"fallbacks {reliability['fallbacks']}",
                f"circuit opens {reliability['circuit_opens']}",
                f"model failures {reliability['model_failures']}",
                f"unclassified {reliability['unclassified']}",
            ],
        ),
        (
            "",
            [
                f"LLM errors {_pct(reliability['llm_error_rate'])}",
                f"BigQuery errors {_pct(reliability['bigquery_error_rate'])}",
            ],
        ),
        (
            "knowledge",
            [
                f"retrieval health {_pct(knowledge['retrieval_health_rate'])}",
                f"retrieval degraded {knowledge['retrieval_degraded']}",
            ],
        ),
        (
            "deletion",
            [
                f"plans {deletion['plans']}",
                f"zero-match {deletion['zero_match']}",
                f"confirmed {deletion['confirmed']}",
                f"partial {deletion['partial']}",
                f"cancelled {deletion['cancelled']}",
                f"expired {deletion['expired']}",
                f"mean plan size {_num(deletion['mean_plan_size'], 1)}",
            ],
        ),
    ]
    turns = "turn" if view.turns == 1 else "turns"
    users = "User" if view.users == 1 else "Users"
    lines = [f"{_window(view.since)} · {view.turns} {turns} · {view.users} {users}"]
    rows += [("by node", _slowest_nodes(families["by_node"])), ("by intent", _intents(families))]
    lines += [f"{family:<12} {'   '.join(items)}" for family, items in rows if items]
    return "\n".join(lines)


_SLOWEST_SHOWN = 3


def _slowest_nodes(by_node: dict[str, float | int | str]) -> list[str]:
    """The nodes with the highest p95, slowest first: where to look when turns are slow."""
    names = {key.rsplit(".", 1)[0] for key in by_node}
    ranked = sorted(names, key=lambda n: _sortable(by_node[f"{n}.p95_s"]), reverse=True)
    return [
        f"{n} p95 {_num(by_node[f'{n}.p95_s'], 1)} s "
        f"(p50 {_num(by_node[f'{n}.p50_s'], 1)} s, {_count(by_node[f'{n}.calls'], 'call')})"
        for n in ranked[:_SLOWEST_SHOWN]
    ]


def _intents(families: dict[str, dict[str, float | int | str]]) -> list[str]:
    by_intent = families["by_intent"]
    actions = sorted({key.rsplit(".", 1)[0] for key in by_intent})
    return [
        f"{a} {_count(by_intent[f'{a}.turns'], 'turn')}, "
        f"success {_pct(by_intent[f'{a}.task_success_rate'])}"
        for a in actions
    ]


def _count(n: float | int | str, noun: str) -> str:
    return f"{n} {noun}{'' if n == 1 else 's'}"


def _sortable(value: float | int | str) -> float:
    return float(value) if isinstance(value, float | int) else -1.0


def _window(since: datetime) -> str:
    minutes = round((datetime.now(UTC) - since).total_seconds() / 60)
    for size, unit in ((1440, "day"), (60, "hour"), (1, "minute")):
        if minutes >= size and minutes % size == 0 or size == 1:
            n = max(1, round(minutes / size))
            return f"last {n} {unit}{'' if n == 1 else 's'}"
    return "last while"  # unreachable: the minute row always matches


def _seconds(ms: int) -> str:
    return f"{ms / 1000:.1f} s"


def _pct(value: float | int | str) -> str:
    return f"{value:.0%}" if isinstance(value, float | int) else str(value)


def _num(value: float | int | str, digits: int) -> str:
    return f"{value:,.{digits}f}" if isinstance(value, float | int) else str(value)


def _mb(value: float | int | str) -> str:
    return f"{value / 1_000_000:,.1f} MB" if isinstance(value, float | int) else str(value)


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
