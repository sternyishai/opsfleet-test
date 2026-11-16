import type { PersonaVersion, SpanNode, StatsView, TraceView } from "../api/client";
import { COMMANDS, PROFILE_FIELDS, type ViewResult } from "../commands";
import { day, human, seconds } from "../format";
import { Icon } from "./Icon";

/** A slash command's result, drawn as a card in the conversation. */
export function ViewCard(props: { result: ViewResult; onOpenReport: (id: string) => void }) {
  const r = props.result;
  switch (r.kind) {
    case "help":
      return (
        <Card title="Commands" subtitle="Answered without the model, as on the CLI">
          <dl className="commands">
            {COMMANDS.map((c) => (
              <div key={c.name}>
                <dt>
                  <code>{c.name}</code> <span className="muted">{c.args}</span>
                </dt>
                <dd>{c.what}</dd>
              </div>
            ))}
          </dl>
        </Card>
      );
    case "reports":
      return (
        <Card title="Your saved reports" subtitle={`${r.data.total} in total`}>
          {r.data.reports.length === 0 ? (
            <p className="muted">You have no saved reports yet.</p>
          ) : (
            <ul className="report-list">
              {r.data.reports.map((report) => (
                <li key={report.report_id}>
                  <button
                    disabled={!report.accessible}
                    onClick={() => props.onOpenReport(report.report_id)}
                  >
                    <Icon name="file" />
                    <span className="grow">{report.title}</span>
                    <span className="muted">{day(report.created_at)}</span>
                  </button>
                  {!report.accessible && (
                    <span className="hint">Covers Brands you no longer have; title only.</span>
                  )}
                </li>
              ))}
            </ul>
          )}
        </Card>
      );
    case "profile": {
      const values = PROFILE_FIELDS.flatMap(([name, label]) => {
        const v = r.data[name];
        return v ? [{ label, value: String(v.value).replaceAll("_", " "), since: v.confirmed_at }] : [];
      });
      const notes = r.data.notes ?? [];
      return (
        <Card title="Your preferences" subtitle="Used in every answer, until you clear them">
          {values.length === 0 && notes.length === 0 ? (
            <p className="muted">
              No saved preferences yet. Tell me one, for example “always give me tables”.
            </p>
          ) : (
            <>
              <dl className="kv">
                {values.map((v) => (
                  <div key={v.label}>
                    <dt>{v.label}</dt>
                    <dd>
                      {v.value} <span className="muted">since {day(v.since)}</span>
                    </dd>
                  </div>
                ))}
              </dl>
              {notes.length > 0 && (
                <ol className="notes">
                  {notes.map((n) => (
                    <li key={n.note_id}>{n.text}</li>
                  ))}
                </ol>
              )}
              <p className="hint">Remove a note with /profile clear &lt;n&gt;, or all with /profile clear.</p>
            </>
          )}
        </Card>
      );
    }
    case "persona":
      return <PersonaCard version={r.data} />;
    case "history":
      return (
        <Card title="Persona versions" subtitle={`${r.data.length} versions`}>
          <ol className="timeline">
            {r.data.map((v) => (
              <li key={v.version} className={v.is_active ? "active" : ""}>
                <span className="dot" />
                <span className="grow">
                  <strong>v{v.version}</strong> · {v.path.replaceAll("_", " ")}
                  {v.created_by && ` by ${v.created_by}`}
                  {v.changed_fields?.length ? (
                    <span className="muted"> · changed {v.changed_fields.join(", ")}</span>
                  ) : null}
                </span>
                <span className="muted">{day(v.created_at)}</span>
                {v.is_active && <span className="badge">Active</span>}
              </li>
            ))}
          </ol>
        </Card>
      );
    case "trace":
      return <TraceCard view={r.data} />;
    case "stats":
      return <StatsCard view={r.data} />;
    case "note":
      return (
        <div className={`note note-${r.tone}`}>
          <Icon name={r.tone === "done" ? "check" : "info"} />
          <div>
            <strong>{r.title}</strong>
            {r.lines.map((line) => (
              <p key={line}>{line}</p>
            ))}
          </div>
        </div>
      );
  }
}

function Card(props: { title: string; subtitle?: string; children: React.ReactNode }) {
  return (
    <section className="card">
      <header>
        <h3>{props.title}</h3>
        {props.subtitle && <span className="muted">{props.subtitle}</span>}
      </header>
      {props.children}
    </section>
  );
}

function PersonaCard(props: { version: PersonaVersion }) {
  const v = props.version;
  const p = v.persona;
  return (
    <Card
      title={`Persona v${v.version}`}
      subtitle={`${v.path.replaceAll("_", " ")}${v.created_by ? ` by ${v.created_by}` : ""} · ${day(v.created_at)}`}
    >
      <dl className="kv">
        <div>
          <dt>Tone</dt>
          <dd>
            <span className="badge">{p.tone.preset}</span> {p.tone.notes}
          </dd>
        </div>
        <div>
          <dt>Report sections</dt>
          <dd className="chips">
            {(p.report_sections ?? []).map((s) => (
              <span key={s} className="chip">
                {s}
              </span>
            ))}
          </dd>
        </div>
        <div>
          <dt>Emphasis</dt>
          <dd>{p.emphasis || <span className="muted">none</span>}</dd>
        </div>
      </dl>
    </Card>
  );
}

/** The step tree with a timing bar per step, scaled to the whole turn. */
function TraceCard(props: { view: TraceView }) {
  const v = props.view;
  const total = Math.max(v.duration_ms, 1);
  const rows: { node: SpanNode; depth: number }[] = [];
  const walk = (nodes: SpanNode[], depth: number) =>
    nodes.forEach((node) => {
      rows.push({ node, depth });
      walk(node.children ?? [], depth + 1);
    });
  walk(v.root, 0);
  return (
    <Card title={`Turn ${v.trace_id}`} subtitle={`${v.action ?? "no action"} · ${v.outcome}`}>
      <div className="tiles small">
        <Tile label="Duration" value={seconds(v.duration_ms)} />
        <Tile label="Tokens" value={v.tokens.toLocaleString()} />
        <Tile label="Steps" value={String(rows.length)} />
      </div>
      <ol className="trace">
        {rows.map(({ node, depth }, i) => (
          <li key={i} title={node.summary || undefined}>
            <span className="trace-name" style={{ paddingLeft: depth * 16 }}>
              {node.name}
              {node.summary && <span className="muted"> {node.summary}</span>}
            </span>
            <span className="trace-bar">
              <span style={{ width: `${Math.max((node.duration_ms / total) * 100, 0.8)}%` }} />
            </span>
            <span className="trace-time">{seconds(node.duration_ms)}</span>
          </li>
        ))}
      </ol>
    </Card>
  );
}

function Tile(props: { label: string; value: string }) {
  return (
    <div className="tile">
      <span className="tile-label">{props.label}</span>
      <span className="tile-value">{props.value}</span>
    </div>
  );
}

const HEADLINE: [family: string, key: string, label: string][] = [
  ["quality", "task_success_rate", "Task success"],
  ["quality", "first_try_sql_rate", "First-try SQL"],
  ["reliability", "p95_s", "p95 latency"],
  ["cost", "tokens_per_turn", "Tokens per turn"],
  ["safety", "out_of_scope_refusals", "Out-of-scope refusals"],
  ["knowledge", "retrieval_health_rate", "Retrieval health"],
];

function value(key: string, v: number | string | undefined): string {
  if (v === undefined) return "n/a";
  if (typeof v !== "number") return v;
  if (key.endsWith("_rate")) return `${Math.round(v * 100)}%`;
  if (key.endsWith("_s")) return `${v.toFixed(1)} s`;
  if (key.includes("tokens") || key.includes("bytes")) return Math.round(v).toLocaleString();
  return Number.isInteger(v) ? v.toLocaleString() : v.toLocaleString(undefined, { maximumFractionDigits: 1 });
}

/** Headline tiles, then every family; per-node timings as the 3 slowest steps. */
function StatsCard(props: { view: StatsView }) {
  const v = props.view;
  const { by_node: byNode = {}, by_intent: byIntent = {}, ...families } = v.families;
  const names = (m: Record<string, unknown>) =>
    [...new Set(Object.keys(m).map((k) => k.slice(0, k.lastIndexOf("."))))];
  const p95 = (n: string) => Number(byNode[`${n}.p95_s`]) || 0;
  const slowest = names(byNode).sort((a, b) => p95(b) - p95(a)).slice(0, 3);
  return (
    <Card title="Agent metrics" subtitle={`Since ${day(v.since)} · ${v.turns} turns · ${v.users} Users`}>
      <div className="tiles">
        {HEADLINE.map(([family, key, label]) => (
          <Tile key={key} label={label} value={value(key, families[family]?.[key])} />
        ))}
      </div>
      <div className="families">
        {Object.entries(families).map(([family, metrics]) => (
          <details key={family}>
            <summary>{human(family)}</summary>
            <dl className="kv compact">
              {Object.entries(metrics).map(([k, m]) => (
                <div key={k}>
                  <dt>{human(k)}</dt>
                  <dd>{value(k, m)}</dd>
                </div>
              ))}
            </dl>
          </details>
        ))}
        {slowest.length > 0 && (
          <details open>
            <summary>Slowest steps (p95)</summary>
            <dl className="kv compact">
              {slowest.map((n) => (
                <div key={n}>
                  <dt>
                    <code>{n}</code>
                  </dt>
                  <dd>
                    {value("p95_s", p95(n))} <span className="muted">· {byNode[`${n}.calls`]} calls</span>
                  </dd>
                </div>
              ))}
            </dl>
          </details>
        )}
        {names(byIntent).length > 0 && (
          <details open>
            <summary>By intent</summary>
            <dl className="kv compact">
              {names(byIntent).sort().map((a) => (
                <div key={a}>
                  <dt>{human(a)}</dt>
                  <dd>
                    {byIntent[`${a}.turns`]} turns ·{" "}
                    {value("_rate", byIntent[`${a}.task_success_rate`])} success
                  </dd>
                </div>
              ))}
            </dl>
          </details>
        )}
      </div>
    </Card>
  );
}
