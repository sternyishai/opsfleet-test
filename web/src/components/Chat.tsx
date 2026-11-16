import { useState } from "react";
import { send, type ConfirmationRequest, type OutboundMessage, type WebRequest } from "../api/client";
import { DeletionPlan } from "./DeletionPlan";

type Entry = { from: "you"; text: string } | { from: "agent"; message: OutboundMessage };

/** One Conversation. Remounted (new `key`) for a new chat or another User. */
export function Chat(props: { userId: string; onNewChat: () => void; onTurnEnded: () => void }) {
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
      await send(props.userId, body, (message) => {
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

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    const typed = text.trim();
    if (!typed || busy) return;
    setText("");
    // conversation_id null starts a new Conversation, as on the CLI.
    void turn({ kind: "text", text: typed, conversation_id: conversationId }, typed);
  };

  const answer = (approved: boolean, selected: number[] | null) => {
    if (!plan || !conversationId) return;
    const shown = approved ? `Delete ${selected ? selected.join(", ") : "all"}` : "Cancel";
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

  return (
    <div className="chat">
      <div className="chat-bar">
        <button onClick={props.onNewChat} disabled={busy}>
          New chat
        </button>
      </div>
      <ol className="log">
        {log.map((entry, i) => (
          <li key={i} className={entry.from === "you" ? "you" : `agent ${entry.message.kind}`}>
            {entry.from === "you" ? entry.text : render(entry.message)}
          </li>
        ))}
      </ol>
      {plan && <DeletionPlan plan={plan} disabled={busy} onAnswer={answer} />}
      {error && <p className="error">{error}</p>}
      <form onSubmit={submit} className="ask">
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Ask about sales, save a report, delete reports…"
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
