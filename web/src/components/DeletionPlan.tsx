import { useState } from "react";
import type { ConfirmationRequest } from "../api/client";

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
  const selected = Array.from({ length: plan.total }, (_, i) => i + 1).filter(
    (i) => !unticked.has(i),
  );
  const needsCount = all && plan.require_count;

  const confirm = () => {
    if (needsCount) props.onAnswer(true, null, `yes, delete all ${count.trim()}`);
    else props.onAnswer(true, all ? null : selected);
  };

  return (
    <fieldset className="plan" disabled={props.disabled}>
      <legend>
        Deletion Plan: {plan.total} report{plan.total === 1 ? "" : "s"}
        {plan.total > plan.items.length &&
          ` (first ${plan.items.length} shown; the other ${plan.total - plan.items.length} are included)`}
      </legend>
      {plan.items.map((item) => (
        <label key={item.index}>
          <input
            type="checkbox"
            checked={!unticked.has(item.index)}
            onChange={() => toggle(item.index)}
          />
          {item.index}. {item.label}
          {item.reason && <span className="reason"> ({item.reason})</span>}
        </label>
      ))}
      {needsCount && (
        <label className="count">
          To delete all of them, type how many there are:{" "}
          <input value={count} onChange={(e) => setCount(e.target.value)} inputMode="numeric" />
        </label>
      )}
      <div className="plan-actions">
        <button onClick={confirm} disabled={kept === 0 || (needsCount && !count.trim())}>
          Delete {all ? "all" : kept}
        </button>
        <button onClick={() => props.onAnswer(false, null)}>Cancel</button>
      </div>
    </fieldset>
  );
}
