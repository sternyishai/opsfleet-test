// Slash commands in the browser: the same commands as `agent chat`, each one the same `Views`
// call as the CLI (LLD 07 feature map), through the `/api/...` routes, as the picked User.
// Only the rendering lives here; it follows the CLI's text so both channels read alike.
import {
  api,
  Refused,
  type ListReportsOutput,
  type PersonaChanges,
  type PersonaVersion,
  type SpanNode,
  type StatsView,
  type TraceView,
  type UserProfile,
} from "./api/client";

export const COMMANDS = [
  ["/reports", "your saved reports"],
  ["/profile", "your saved preferences"],
  ["/profile clear <n>", "remove note n; /profile clear removes everything"],
  ["/persona", "the active Persona (Persona editors)"],
  ["/persona history", "every Persona version"],
  ["/persona edit field=value …", "e.g. /persona edit tone.preset=formal"],
  ["/persona rollback", "back to the previous Persona"],
  ["/trace [id]", "the steps of your last turn, or of turn id"],
  ["/stats [7d]", "agent metrics (Executives); 7d, 24h or 30m"],
  ["/new", "start a new chat"],
  ["/help", "this list"],
] as const;

const KNOWN = new Set(["/reports", "/profile", "/persona", "/trace", "/stats", "/new", "/help"]);

/** True for a command this page answers; any other text goes to the agent, as on the CLI. */
export function isCommand(text: string): boolean {
  return KNOWN.has(text.split(" ", 1)[0]);
}

/** The command's output as text; a refusal (a catalog message) is output too. */
export async function run(userId: string, text: string): Promise<string> {
  const [name, ...rest] = text.trim().split(/\s+/);
  const arg = rest.join(" ");
  try {
    switch (name) {
      case "/help":
        return ["Commands:", ...COMMANDS.map(([c, what]) => `  ${c}  ${what}`)].join("\n");
      case "/reports":
        return renderReports(await api.reports(userId));
      case "/profile":
        return await profile(userId, arg);
      case "/persona":
        return await persona(userId, arg);
      case "/trace": {
        return renderTrace(await api.trace(userId, arg || null));
      }
      case "/stats":
        return renderStats(await api.stats(userId, arg || "7d"));
    }
    return "Unknown command. /help lists them.";
  } catch (e) {
    if (e instanceof Refused) return e.message;
    throw e;
  }
}

async function profile(userId: string, arg: string): Promise<string> {
  if (arg === "") return renderProfile(await api.profile(userId));
  const [word, number] = arg.split(/\s+/);
  if (word !== "clear") return "Use /profile, /profile clear <number> or /profile clear.";
  if (!number) return renderProfile(await api.resetProfile(userId));
  // The number is the one /profile shows; the note is removed by its ID, as the CLI does.
  const notes = (await api.profile(userId)).notes ?? [];
  const n = /^\d+$/.test(number) ? Number(number) : 0;
  if (n < 1 || n > notes.length) {
    return `There is no note ${number}. /profile lists your notes with their numbers.`;
  }
  return renderProfile(await api.removeNote(userId, notes[n - 1].note_id));
}

async function persona(userId: string, arg: string): Promise<string> {
  if (arg === "") return renderPersona(await api.persona(userId));
  if (arg === "history") return renderHistory(await api.personaVersions(userId));
  if (arg === "rollback") {
    const out = await api.rollbackPersona(userId);
    return `Back to v${out.active.version} (was v${out.from_version}). Your next answer uses it.`;
  }
  if (arg.startsWith("edit")) {
    const changes = parseChanges(arg.slice("edit".length));
    if (!changes) return "Use /persona edit field=value, e.g. /persona edit tone.preset=formal";
    const out = await api.editPersona(userId, changes);
    if (out.status === "published") {
      return (
        `✓ all ${out.gate.checks.length} checks\n` +
        `Published v${out.active.version} (was v${out.previous_version}). Your next answer uses it.`
      );
    }
    const failed = out.gate.checks.filter((c) => !c.passed && c.reason).map((c) => c.reason);
    return [`Not published; v${out.current_version} stays.`, ...failed].join("\n");
  }
  return "Use /persona, /persona history, /persona edit or /persona rollback.";
}

/** `tone.preset=formal emphasis="growth first"`; `report_sections` takes a comma list. */
function parseChanges(text: string): PersonaChanges | null {
  const changes: PersonaChanges = {};
  for (const m of text.matchAll(/([\w.]+)=("([^"]*)"|\S+)/g)) {
    const value = m[3] ?? m[2];
    changes[m[1]] = m[1] === "report_sections" ? value.split(",").map((s) => s.trim()) : value;
  }
  return Object.keys(changes).length ? changes : null;
}

const day = (iso: string) =>
  new Date(iso).toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" });

function renderReports(listed: ListReportsOutput): string {
  if (listed.reports.length === 0) return "You have no saved reports yet.";
  const lines = [`Your saved reports (${listed.total}):`];
  listed.reports.forEach((r, i) => {
    const note = r.accessible ? "" : "  (covers Brands you no longer have; title only)";
    lines.push(`  ${i + 1}. ${r.title}  (${day(r.created_at)})${note}`);
  });
  return lines.join("\n");
}

export const PROFILE_FIELDS = [
  ["format", "Format"],
  ["depth", "Level of detail"],
  ["charts", "Charts"],
  ["language", "Language"],
] as const;

function renderProfile(p: UserProfile): string {
  const values = PROFILE_FIELDS.flatMap(([name, label]) => {
    const v = p[name];
    return v ? [`  ${label}: ${String(v.value).replaceAll("_", " ")}  (since ${day(v.confirmed_at)})`] : [];
  });
  const notes = p.notes ?? [];
  if (values.length === 0 && notes.length === 0) {
    return 'You have no saved preferences yet. Tell me one, for example "always give me tables".';
  }
  const lines = ["Your preferences:", ...values];
  if (notes.length) lines.push("Your notes:", ...notes.map((n, i) => `  ${i + 1}. ${n.text}`));
  lines.push("Remove a note with /profile clear <number>, or everything with /profile clear.");
  return lines.join("\n");
}

function renderPersona(v: PersonaVersion): string {
  const p = v.persona;
  const by = v.created_by ? ` by ${v.created_by}` : "";
  return [
    `Persona v${v.version} · ${v.path.replaceAll("_", " ")}${by} · ${day(v.created_at)}`,
    `  tone: ${p.tone.preset}${p.tone.notes ? `. ${p.tone.notes}` : ""}`,
    `  report sections: ${(p.report_sections ?? []).join(", ")}`,
    `  emphasis: ${p.emphasis || "none"}`,
  ].join("\n");
}

function renderHistory(versions: PersonaVersion[]): string {
  return [
    "Persona versions:",
    ...versions.map((v) => {
      const by = v.created_by ? ` by ${v.created_by}` : "";
      const changed = v.changed_fields?.length ? `  changed: ${v.changed_fields.join(", ")}` : "";
      return `  v${v.version}  ${day(v.created_at)}  ${v.path.replaceAll("_", " ")}${by}${changed}${v.is_active ? "  (active)" : ""}`;
    }),
  ].join("\n");
}

const seconds = (ms: number) => `${(ms / 1000).toFixed(1)} s`;

function renderTrace(view: TraceView): string {
  const lines = [
    `turn ${view.trace_id}  ${view.action ?? "no action"}  ${seconds(view.duration_ms)}  ` +
      `${view.tokens.toLocaleString()} tokens  ${view.outcome}`,
  ];
  const walk = (nodes: SpanNode[], prefix: string) =>
    nodes.forEach((node, i) => {
      const last = i === nodes.length - 1;
      const summary = node.summary ? `  ${node.summary}` : "";
      lines.push(`${prefix}${last ? "└─" : "├─"} ${node.name}  ${seconds(node.duration_ms)}${summary}`);
      walk(node.children ?? [], prefix + (last ? "   " : "│  "));
    });
  walk(view.root, "");
  return lines.join("\n");
}

/** Like the CLI: one line per family, rates as percentages, the 3 slowest steps by p95. */
function renderStats(view: StatsView): string {
  const fmt = (key: string, v: number | string) =>
    typeof v !== "number"
      ? v
      : key.endsWith("_rate")
        ? `${Math.round(v * 100)}%`
        : Number.isInteger(v)
          ? v.toLocaleString()
          : v.toFixed(2);
  const label = (key: string) => key.replace(/_rate$/, "").replaceAll("_", " ");
  const { by_node: byNode = {}, by_intent: byIntent = {}, ...families } = view.families;
  const lines = [`since ${day(view.since)} · ${view.turns} turns · ${view.users} Users`];
  for (const [family, metrics] of Object.entries(families)) {
    const shown = Object.entries(metrics).map(([k, v]) => `${label(k)} ${fmt(k, v)}`);
    if (shown.length) lines.push(`${family}: ${shown.join(" · ")}`);
  }
  const p95 = (node: string) => Number(byNode[`${node}.p95_s`]) || 0;
  const nodes = [...new Set(Object.keys(byNode).map((k) => k.slice(0, k.lastIndexOf("."))))];
  const slowest = nodes.sort((a, b) => p95(b) - p95(a)).slice(0, 3);
  if (slowest.length) {
    lines.push(
      "slowest steps: " +
        slowest.map((n) => `${n} p95 ${p95(n).toFixed(1)} s (${byNode[`${n}.calls`]} calls)`).join(" · "),
    );
  }
  const intents = [...new Set(Object.keys(byIntent).map((k) => k.slice(0, k.lastIndexOf("."))))];
  if (intents.length) {
    lines.push(
      "by intent: " +
        intents
          .sort()
          .map((a) => `${a} ${byIntent[`${a}.turns`]} turns, success ${fmt("_rate", byIntent[`${a}.task_success_rate`])}`)
          .join(" · "),
    );
  }
  return lines.join("\n");
}
