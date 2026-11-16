// Slash commands in the browser: the same commands as `agent chat`, each one the same `Views`
// call as the CLI (LLD 07 feature map), through the `/api/...` routes, as the picked User.
// They return the view's own data; `ViewCard` draws it. No model call, no Conversation turn.
import {
  api,
  Refused,
  type ListReportsOutput,
  type PersonaChanges,
  type PersonaVersion,
  type StatsView,
  type TraceView,
  type UserProfile,
} from "./api/client";

export const COMMANDS = [
  { name: "/reports", args: "", what: "Your saved reports" },
  { name: "/profile", args: "", what: "Your saved preferences" },
  { name: "/profile clear", args: "[n]", what: "Remove note n, or every preference" },
  { name: "/persona", args: "", what: "The active Persona (Persona editors)" },
  { name: "/persona history", args: "", what: "Every Persona version" },
  { name: "/persona edit", args: "field=value", what: "e.g. tone.preset=formal" },
  { name: "/persona rollback", args: "", what: "Back to the previous Persona" },
  { name: "/trace", args: "[id]", what: "The steps of your last turn, or of turn id" },
  { name: "/stats", args: "[7d]", what: "Agent metrics (Executives): 7d, 24h or 30m" },
  { name: "/new", args: "", what: "Start a new chat" },
  { name: "/help", args: "", what: "Every command" },
] as const;

const KNOWN = new Set(["/reports", "/profile", "/persona", "/trace", "/stats", "/new", "/help"]);

/** True for a command this page answers; any other text goes to the agent, as on the CLI. */
export function isCommand(text: string): boolean {
  return KNOWN.has(text.trim().split(/\s+/, 1)[0]);
}

export type ViewResult =
  | { kind: "help" }
  | { kind: "reports"; data: ListReportsOutput }
  | { kind: "profile"; data: UserProfile }
  | { kind: "persona"; data: PersonaVersion }
  | { kind: "history"; data: PersonaVersion[] }
  | { kind: "trace"; data: TraceView }
  | { kind: "stats"; data: StatsView }
  | { kind: "note"; tone: "done" | "refused"; title: string; lines: string[] };

const refused = (title: string, ...lines: string[]): ViewResult => ({
  kind: "note",
  tone: "refused",
  title,
  lines,
});

/** The command's result; a refusal (the catalog message) is a result too. */
export async function run(userId: string, text: string): Promise<ViewResult> {
  const [name, ...rest] = text.trim().split(/\s+/);
  const arg = rest.join(" ");
  try {
    switch (name) {
      case "/help":
        return { kind: "help" };
      case "/reports":
        return { kind: "reports", data: await api.reports(userId) };
      case "/profile":
        return await profile(userId, arg);
      case "/persona":
        return await persona(userId, arg);
      case "/trace":
        return { kind: "trace", data: await api.trace(userId, arg || null) };
      case "/stats":
        return { kind: "stats", data: await api.stats(userId, arg || "7d") };
    }
    return refused("Unknown command", "Type /help to see every command.");
  } catch (e) {
    if (e instanceof Refused) return refused("Not available", e.message);
    throw e;
  }
}

async function profile(userId: string, arg: string): Promise<ViewResult> {
  if (arg === "") return { kind: "profile", data: await api.profile(userId) };
  const [word, number] = arg.split(/\s+/);
  if (word !== "clear") return refused("Unknown option", "Use /profile, /profile clear <n> or /profile clear.");
  if (!number) return { kind: "profile", data: await api.resetProfile(userId) };
  // The number is the one /profile shows; the note is removed by its ID, as the CLI does.
  const notes = (await api.profile(userId)).notes ?? [];
  const n = /^\d+$/.test(number) ? Number(number) : 0;
  if (n < 1 || n > notes.length) {
    return refused(`There is no note ${number}`, "/profile lists your notes with their numbers.");
  }
  return { kind: "profile", data: await api.removeNote(userId, notes[n - 1].note_id) };
}

async function persona(userId: string, arg: string): Promise<ViewResult> {
  if (arg === "") return { kind: "persona", data: await api.persona(userId) };
  if (arg === "history") return { kind: "history", data: await api.personaVersions(userId) };
  if (arg === "rollback") {
    const out = await api.rollbackPersona(userId);
    return {
      kind: "note",
      tone: "done",
      title: `Back to Persona v${out.active.version}`,
      lines: [`Was v${out.from_version}. Your next answer uses it.`],
    };
  }
  if (arg.startsWith("edit")) {
    const changes = parseChanges(arg.slice("edit".length));
    if (!changes) return refused("Nothing to change", "Use /persona edit field=value, e.g. /persona edit tone.preset=formal");
    const out = await api.editPersona(userId, changes);
    if (out.status === "published") {
      return {
        kind: "note",
        tone: "done",
        title: `Published Persona v${out.active.version}`,
        lines: [`All ${out.gate.checks.length} checks passed (was v${out.previous_version}). Your next answer uses it.`],
      };
    }
    const failed = out.gate.checks.filter((c) => !c.passed && c.reason).map((c) => c.reason!);
    return refused(`Not published: v${out.current_version} stays`, ...failed);
  }
  return refused("Unknown option", "Use /persona, /persona history, /persona edit or /persona rollback.");
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

export const PROFILE_FIELDS = [
  ["format", "Format"],
  ["depth", "Level of detail"],
  ["charts", "Charts"],
  ["language", "Language"],
] as const;
