// Calls to `agent web`. Every shape comes from the generated `schema.d.ts` (ADR 0010):
// these are names for generated types, never hand-written ones.
import type { components, paths } from "./schema";

type Schemas = components["schemas"];
export type DevUser = Schemas["DevUser"];
export type OutboundMessage = Schemas["OutboundMessage"];
export type ConfirmationRequest = Schemas["ConfirmationRequest"];
export type WebRequest = Schemas["WebRequest"];
export type ApiError = Schemas["ApiError"];
export type ListReportsOutput =
  paths["/api/reports"]["get"]["responses"][200]["content"]["application/json"];
export type GetReportOk =
  paths["/api/reports/{report_id}"]["get"]["responses"][200]["content"]["application/json"];
export type UserProfile =
  paths["/api/profile"]["get"]["responses"][200]["content"]["application/json"];
export type PersonaVersion = Schemas["PersonaVersion"];
export type PublishPersonaOutput = Schemas["PublishPersonaOutput"];
export type PersonaRolledBack = Schemas["PersonaRolledBack"];
export type PersonaChanges =
  paths["/api/persona"]["put"]["requestBody"]["content"]["application/json"];
export type TraceView = Schemas["TraceView"];
export type SpanNode = Schemas["SpanNode"];
export type StatsView = Schemas["StatsView"];

const USER_HEADER = "X-Agent-User";

/** A refused request, with the message to show (the catalog message for a view). */
export class Refused extends Error {}

async function call<T>(
  userId: string | null,
  method: string,
  url: string,
  body?: unknown,
): Promise<T> {
  const headers: Record<string, string> = userId ? { [USER_HEADER]: userId } : {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const json = body === undefined ? undefined : JSON.stringify(body);
  const response = await fetch(url, { method, headers, body: json });
  if (!response.ok) throw await refused(response);
  return (await response.json()) as T;
}

async function refused(response: Response): Promise<Refused> {
  try {
    const body = (await response.json()) as ApiError;
    return new Refused(body.message ?? response.statusText);
  } catch {
    return new Refused(response.statusText);
  }
}

export const api = {
  devUsers: () => call<DevUser[]>(null, "GET", "/api/dev/users"),
  reports: (userId: string) => call<ListReportsOutput>(userId, "GET", "/api/reports"),
  report: (userId: string, reportId: string) =>
    call<GetReportOk>(userId, "GET", `/api/reports/${reportId}`),
  profile: (userId: string) => call<UserProfile>(userId, "GET", "/api/profile"),
  resetProfile: (userId: string) => call<UserProfile>(userId, "DELETE", "/api/profile"),
  removeNote: (userId: string, noteId: string) =>
    call<UserProfile>(userId, "DELETE", `/api/profile/notes/${noteId}`),
  persona: (userId: string) => call<PersonaVersion>(userId, "GET", "/api/persona"),
  personaVersions: (userId: string) =>
    call<PersonaVersion[]>(userId, "GET", "/api/persona/versions"),
  editPersona: (userId: string, changes: PersonaChanges) =>
    call<PublishPersonaOutput>(userId, "PUT", "/api/persona", changes),
  rollbackPersona: (userId: string) =>
    call<PersonaRolledBack>(userId, "POST", "/api/persona/rollback"),
  trace: (userId: string, traceId: string | null) =>
    call<TraceView>(userId, "GET", traceId ? `/api/traces/${traceId}` : "/api/traces"),
  stats: (userId: string, since: string) =>
    call<StatsView>(userId, "GET", `/api/stats?since=${encodeURIComponent(since)}`),
};

/**
 * `POST /api/messages`: calls `onMessage` for each event of the turn's stream, in order.
 * EventSource cannot POST, so the stream is read from `fetch` and split on blank lines.
 */
export async function send(
  userId: string,
  body: WebRequest,
  onMessage: (message: OutboundMessage) => void,
): Promise<void> {
  const response = await fetch("/api/messages", {
    method: "POST",
    headers: { "Content-Type": "application/json", [USER_HEADER]: userId },
    body: JSON.stringify(body),
  });
  if (!response.ok || !response.body) throw await refused(response);
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) return;
    buffer += value;
    let end: number;
    while ((end = buffer.indexOf("\n\n")) >= 0) {
      const block = buffer.slice(0, end);
      buffer = buffer.slice(end + 2);
      const data = block
        .split("\n")
        .filter((line) => line.startsWith("data: "))
        .map((line) => line.slice("data: ".length))
        .join("\n");
      if (data) onMessage(JSON.parse(data) as OutboundMessage);
    }
  }
}
