import { useState } from "react";
import type { ConfirmationRequest } from "../api/client";
import { Icon } from "./Icon";

/**
 * The Deletion Plan as checkboxes. The plan shows at most its first 20 items; the ones not
 * shown stay selected. Unticking item 2 sends every index of the plan but 2: the same
 * decision as typing `yes except 2`. Deleting everything sends null ("all items"); a large
 * plan (`require_count`) needs the count instead, typed as on the CLI: "yes, delete all 37".
 */
export function DeletionPlan(props: {
  plan: ConfirmationRequest;
  disabled: boolean;
  onAnswer: (approved: boolean, selected: number[] | null, typed?: string) => void;
}) {
  const { plan } = props;
  const [unticked, setUnticked] = useState<Set<number>>(new Set());
  const [count, setCount] = useState("");
  const toggle = (index: number) =>
    setUnticked((u) => {
      const next = new Set(u);
      if (next.has(index)) next.delete(index);
      else next.add(index);
      return next;
    });
  const kept = plan.total - unticked.size;
  const all = unticked.size === 0;
  const selected = Array.from({ length: plan.total }, (_, i) => i + 1).filter((i) => !unticked.has(i));
  const needsCount = all && plan.require_count;
  const hidden = plan.total - plan.items.length;

  const confirm = () => {
    if (needsCount) props.onAnswer(true, null, `yes, delete all ${count.trim()}`);
    else props.onAnswer(true, all ? null : selected);
  };

  return (
    <section className="plan">
      <header>
        <span className="plan-icon">
          <Icon name="trash" />
        </span>
        <div>
          <h3>Delete {plan.total} saved report{plan.total === 1 ? "" : "s"}?</h3>
          <p className="muted">Untick any you want to keep. Nothing is deleted until you confirm.</p>
        </div>
      </header>
      <ul>
        {plan.items.map((item) => (
          <li key={item.index}>
            <label className={unticked.has(item.index) ? "kept" : ""}>
              <input
                type="checkbox"
                checked={!unticked.has(item.index)}
                onChange={() => toggle(item.index)}
                disabled={props.disabled}
              />
              <span className="grow">
                <span className="plan-label">{item.label}</span>
                {item.reason && <span className="muted"> · {item.reason}</span>}
              </span>
            </label>
          </li>
        ))}
        {hidden > 0 && <li className="muted plan-more">…and {hidden} more, included unless you type “yes except …”.</li>}
      </ul>
      {needsCount && (
        <label className="plan-count">
          This deletes all {plan.total}. Type the number to confirm:
          <input value={count} onChange={(e) => setCount(e.target.value)} inputMode="numeric" disabled={props.disabled} />
        </label>
      )}
      <footer>
        <button className="ghost" onClick={() => props.onAnswer(false, null)} disabled={props.disabled}>
          Keep all
        </button>
        <button
          className="danger"
          onClick={confirm}
          disabled={props.disabled || kept === 0 || (needsCount && !count.trim())}
        >
          Delete {all ? `all ${plan.total}` : kept}
        </button>
      </footer>
    </section>
  );
}
