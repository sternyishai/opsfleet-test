import { useEffect, useRef, useState } from "react";
import {
  send,
  type AgentReply,
  type ConfirmationRequest,
  type DevUser,
  type WebRequest,
} from "../api/client";
import { isCommand, run, type ViewResult } from "../commands";
import { brandsOf } from "../format";
import { Composer } from "./Composer";
import { DeletionPlan } from "./DeletionPlan";
import { Icon } from "./Icon";
import { Avatar } from "./Identity";
import { Markdown } from "./Markdown";
import { ViewCard } from "./ViewCard";

/** One exchange with the agent: what was sent, its progress, and how it ended. */
type Turn = {
  kind: "turn";
  id: number;
  asked: string;
  progress: string[];
  outcome: AgentReply | ConfirmationRequest | null;
  error: string | null;
};
/** A slash command: a `Views` call, shown as a card. */
type View = { kind: "view"; id: number; command: string; result: ViewResult | null; error: string | null };
type Item = Turn | View;

/** One Conversation. Remounted (new `key`) for a new chat or another User. */
export function Chat(props: {
  user: DevUser;
  onNewChat: () => void;
  onChanged: () => void;
  onOpenReport: (id: string) => void;
}) {
  const userId = props.user.user_id;
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [items, setItems] = useState<Item[]>([]);
  const [plan, setPlan] = useState<ConfirmationRequest | null>(null);
  const [busy, setBusy] = useState(false);
  const bottom = useRef<HTMLDivElement>(null);
  const nextId = useRef(0);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [items, plan]);

  const update = <T extends Item>(id: number, change: (item: T) => T) =>
    setItems((all) => all.map((it) => (it.id === id ? change(it as T) : it)));

  const turn = async (body: WebRequest, asked: string) => {
    const id = nextId.current++;
    setBusy(true);
    setPlan(null);
    setItems((all) => [...all, { kind: "turn", id, asked, progress: [], outcome: null, error: null }]);
    try {
      await send(userId, body, (message) => {
        setConversationId(message.conversation_id);
        if (message.kind === "progress") {
          update<Turn>(id, (t) => ({ ...t, progress: [...t.progress, message.text] }));
        } else {
          update<Turn>(id, (t) => ({ ...t, outcome: message }));
          if (message.kind === "confirmation") setPlan(message);
        }
      });
    } catch (e) {
      update<Turn>(id, (t) => ({ ...t, error: (e as Error).message }));
    } finally {
      setBusy(false);
      props.onChanged(); // a saved report or preference shows in the sidebar
    }
  };

  const view = async (command: string) => {
    if (command.trim() === "/new") return props.onNewChat();
    const id = nextId.current++;
    setBusy(true);
    setItems((all) => [...all, { kind: "view", id, command, result: null, error: null }]);
    try {
      const result = await run(userId, command);
      update<View>(id, (v) => ({ ...v, result }));
    } catch (e) {
      update<View>(id, (v) => ({ ...v, error: (e as Error).message }));
    } finally {
      setBusy(false);
      props.onChanged(); // /profile clear and /persona change what the sidebar shows
    }
  };

  const submit = (text: string) => {
    if (isCommand(text)) return void view(text);
    // conversation_id null starts a new Conversation, as on the CLI.
    void turn({ kind: "text", text, conversation_id: conversationId }, text);
  };

  /** `selected` null: every item of the plan. `typed`: the CLI's text, for the count. */
  const answer = (approved: boolean, selected: number[] | null, typed?: string) => {
    if (!plan || !conversationId) return;
    if (typed) return void turn({ kind: "text", text: typed, conversation_id: conversationId }, typed);
    const asked = !approved ? "Keep all" : selected ? `Delete ${selected.length} of ${plan.total}` : "Delete all";
    void turn(
      {
        kind: "confirmation",
        conversation_id: conversationId,
        request_id: plan.request_id,
        approved,
        selected_indices: selected,
      },
      asked,
    );
  };

  return (
    <div className="chat">
      <header className="chat-head">
        <div className="chat-title">{items.length ? "Conversation" : "New conversation"}</div>
        <div className="who-pill" title="Every question and command runs as this User">
          <Avatar user={props.user} size="sm" />
          <span>
            <strong>{userId}</strong> · {brandsOf(props.user)}
          </span>
        </div>
      </header>
      <div className="thread">
        <div className="thread-inner">
          {items.length === 0 && <Welcome user={props.user} onPick={submit} />}
          {items.map((item) =>
            item.kind === "turn" ? (
              <TurnView key={item.id} turn={item} user={props.user} onTrace={(id) => view(`/trace ${id}`)} />
            ) : (
              <div key={item.id} className="exchange">
                <div className="bubble-row mine">
                  <div className="bubble mine command">
                    <Icon name="slash" size={14} /> {item.command.replace(/^\//, "")}
                  </div>
                </div>
                <div className="answer">
                  {item.result ? (
                    <ViewCard result={item.result} onOpenReport={props.onOpenReport} />
                  ) : item.error ? (
                    <div className="note note-refused">{item.error}</div>
                  ) : (
                    <Working text="Loading…" />
                  )}
                </div>
              </div>
            ),
          )}
          {plan && <DeletionPlan key={plan.request_id} plan={plan} disabled={busy} onAnswer={answer} />}
          <div ref={bottom} />
        </div>
      </div>
      <Composer userId={userId} disabled={busy} onSend={submit} />
    </div>
  );
}

function TurnView(props: { turn: Turn; user: DevUser; onTrace: (traceId: string) => void }) {
  const t = props.turn;
  const o = t.outcome;
  const done = o !== null || t.error !== null;
  return (
    <div className="exchange">
      <div className="bubble-row mine">
        <div className="bubble mine">{t.asked}</div>
      </div>
      <div className="answer">
        <span className="agent-mark">
          <Icon name="spark" size={14} />
        </span>
        <div className="answer-body">
          {!done && <Working text={t.progress.at(-1) ?? "Thinking…"} />}
          {done && t.progress.length > 0 && (
            <details className="steps">
              <summary>
                <Icon name="steps" size={14} /> {t.progress.length} step{t.progress.length === 1 ? "" : "s"}
              </summary>
              <ol>
                {t.progress.map((p, i) => (
                  <li key={i}>{p}</li>
                ))}
              </ol>
            </details>
          )}
          {o?.kind === "reply" && (
            <div className={o.message_class ? "reply refusal" : "reply"}>
              {o.message_class && <Icon name="info" />}
              <Markdown text={o.markdown} />
            </div>
          )}
          {o?.kind === "confirmation" && <p className="reply-text">{o.text}</p>}
          {t.error && <div className="note note-refused">{t.error}</div>}
          {o && (
            <div className="answer-foot">
              <span>Ref {o.trace_id}</span>
              <button className="link" onClick={() => props.onTrace(o.trace_id)}>
                How was this answered?
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function Working(props: { text: string }) {
  return (
    <div className="working">
      <span className="pulse" />
      {props.text}
    </div>
  );
}

function Welcome(props: { user: DevUser; onPick: (text: string) => void }) {
  const u = props.user;
  const brand = u.brands[0];
  const ideas =
    u.role === "manager" && brand
      ? [
          `What was ${brand} revenue in 2025?`,
          `Which ${brand} products sold most in 2025?`,
          "Which categories get returned most?",
        ]
      : [
          "What was total revenue in 2025?",
          "Top 5 Brands by revenue in 2025",
          "Which categories get returned most?",
        ];
  return (
    <div className="welcome">
      <span className="welcome-mark">
        <Icon name="spark" size={22} />
      </span>
      <h1>What would you like to know, {u.user_id}?</h1>
      <p className="muted">
        You can analyze <strong>{brandsOf(u)}</strong>. Ask about sales, save an answer as a report, or type{" "}
        <kbd>/</kbd> for commands.
      </p>
      <div className="ideas">
        {ideas.map((idea) => (
          <button key={idea} onClick={() => props.onPick(idea)}>
            {idea}
          </button>
        ))}
        <button onClick={() => props.onPick("/help")}>/help · every command</button>
      </div>
    </div>
  );
}
