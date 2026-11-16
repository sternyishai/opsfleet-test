import { useEffect, useRef, useState } from "react";
import { COMMANDS } from "../commands";
import { Icon } from "./Icon";

/** The message box. Enter sends, Shift+Enter breaks the line; "/" opens the command picker. */
export function Composer(props: { userId: string; disabled: boolean; onSend: (text: string) => void }) {
  const [text, setText] = useState("");
  const [picked, setPicked] = useState(0);
  const box = useRef<HTMLTextAreaElement>(null);

  const typed = text.trimStart();
  const matches = typed.startsWith("/") && !typed.includes("\n")
    ? COMMANDS.filter((c) => c.name.startsWith(typed.trimEnd()) || typed.startsWith(c.name + " "))
    : [];
  const open = matches.length > 0 && !(matches.length === 1 && typed.trimEnd() === matches[0].name && !matches[0].args);

  useEffect(() => {
    setPicked(0);
  }, [typed]);
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 180)}px`;
  }, [text]);
  useEffect(() => {
    if (!props.disabled) box.current?.focus();
  }, [props.disabled]);

  const send = () => {
    const message = text.trim();
    if (!message || props.disabled) return;
    props.onSend(message);
    setText("");
  };

  const complete = (name: string, args: string) => setText(args ? `${name} ` : name);

  const onKey = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (open && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
      e.preventDefault();
      setPicked((p) => (p + (e.key === "ArrowDown" ? 1 : matches.length - 1)) % matches.length);
      return;
    }
    if (open && e.key === "Tab") {
      e.preventDefault();
      complete(matches[picked].name, matches[picked].args);
      return;
    }
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      const choice = open ? matches[picked] : null;
      if (choice && typed.trimEnd() !== choice.name && !typed.startsWith(choice.name + " ")) {
        complete(choice.name, choice.args);
      } else {
        send();
      }
    }
  };

  return (
    <div className="composer-wrap">
      {open && (
        <div className="picker" role="listbox">
          {matches.map((c, i) => (
            <button
              key={c.name}
              role="option"
              aria-selected={i === picked}
              className={i === picked ? "picked" : ""}
              onMouseEnter={() => setPicked(i)}
              onMouseDown={(e) => {
                e.preventDefault();
                complete(c.name, c.args);
                box.current?.focus();
              }}
            >
              <code>{c.name}</code>
              {c.args && <span className="muted"> {c.args}</span>}
              <span className="picker-what">{c.what}</span>
            </button>
          ))}
        </div>
      )}
      <div className="composer">
        <textarea
          ref={box}
          rows={1}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKey}
          placeholder={`Message as ${props.userId}… or type / for commands`}
          maxLength={4000}
          disabled={props.disabled}
          aria-label="Message"
        />
        <button className="send" onClick={send} disabled={props.disabled || !text.trim()} aria-label="Send">
          <Icon name="send" size={18} />
        </button>
      </div>
      <p className="composer-hint">
        Answers come from the sales data you may see. <kbd>Enter</kbd> to send · <kbd>/</kbd> for commands
      </p>
    </div>
  );
}
