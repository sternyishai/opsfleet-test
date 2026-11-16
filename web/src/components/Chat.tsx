import { useState } from "react";
import {
  send,
  type ConfirmationRequest,
  type DevUser,
  type OutboundMessage,
  type WebRequest,
} from "../api/client";
import { COMMANDS, isCommand, run } from "../commands";
import { DeletionPlan } from "./DeletionPlan";
import { describe } from "./UsersPanel";

type Entry =
  | { from: "you"; text: string }
  | { from: "agent"; message: OutboundMessage }
  | { from: "view"; text: string };

/** One Conversation. Remounted (new `key`) for a new chat or another User. */
export function Chat(props: { user: DevUser; onNewChat: () => void; onTurnEnded: () => void }) {
  const userId = props.user.user_id;
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [log, setLog] = useState<Entry[]>([]);
  const [plan, setPlan] = useState<ConfirmationRequest | null>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const turn = async (body: WebRequest, shown: string) => {
    setBusy(true);
    setError(null);
    setPlan(null);
    setLog((l) => [...l, { from: "you", text: shown }]);
    try {
      await send(userId, body, (message) => {
        setConversationId(message.conversation_id);
        setLog((l) => [...l, { from: "agent", message }]);
        if (message.kind === "confirmation") setPlan(message);
      });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
      props.onTurnEnded();
    }
  };

  /** A slash command: a `Views` call, no model and no Conversation turn, as on the CLI. */
  const command = async (typed: string) => {
    if (typed === "/new") return props.onNewChat();
    setBusy(true);
    setError(null);
    setLog((l) => [...l, { from: "you", text: typed }]);
    try {
      const output = await run(userId, typed);
      setLog((l) => [...l, { from: "view", text: output }]);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
      props.onTurnEnded(); // /profile clear and /persona change what the side panels show
    }
  };

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    const typed = text.trim();
    if (!typed || busy) return;
    setText("");
    if (isCommand(typed)) return void command(typed);
    // conversation_id null starts a new Conversation, as on the CLI.
    void turn({ kind: "text", text: typed, conversation_id: conversationId }, typed);
  };

  /** `selected` null: every item of the plan. `typed`: the CLI's text, for a count. */
  const answer = (approved: boolean, selected: number[] | null, typed?: string) => {
    if (!plan || !conversationId) return;
    if (typed) {
      void turn({ kind: "text", text: typed, conversation_id: conversationId }, typed);
      return;
    }
    const shown = !approved ? "Cancel" : selected ? `Delete ${selected.length}` : "Delete all";
    void turn(
      {
        kind: "confirmation",
        conversation_id: conversationId,
        request_id: plan.request_id,
        approved,
        selected_indices: selected,
      },
      shown,
    );
  };

  const hints = text.startsWith("/")
    ? COMMANDS.filter(([c]) => c.startsWith(text.split(" ", 1)[0]))
    : [];

  return (
    <div className="chat">
      <div className="chat-bar">
        <span className={`as role-${props.user.role ?? "none"}`}>
          You are: <strong>{describe(props.user)}</strong>
        </span>
        <button onClick={props.onNewChat} disabled={busy}>
          New chat
        </button>
      </div>
      <ol className="log">
        {log.length === 0 && (
          <li className="hint-line">Ask a question, or type / for commands (/help lists them).</li>
        )}
        {log.map((entry, i) => (
          <li key={i} className={entry.from === "agent" ? `agent ${entry.message.kind}` : entry.from}>
            {entry.from === "agent" ? render(entry.message) : entry.text}
          </li>
        ))}
      </ol>
      {plan && <DeletionPlan plan={plan} disabled={busy} onAnswer={answer} />}
      {error && <p className="error">{error}</p>}
      {hints.length > 0 && (
        <ul className="hints">
          {hints.map(([c, what]) => (
            <li key={c}>
              <code>{c}</code> {what}
            </li>
          ))}
        </ul>
      )}
      <form onSubmit={submit} className="ask">
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={`Ask as ${userId}, or type / for commands`}
          maxLength={4000}
          disabled={busy}
        />
        <button disabled={busy || !text.trim()}>Send</button>
      </form>
    </div>
  );
}

function render(message: OutboundMessage): string {
  switch (message.kind) {
    case "progress":
      return `… ${message.text}`;
    case "confirmation":
      return message.text;
    case "reply":
      return message.markdown;
  }
}
