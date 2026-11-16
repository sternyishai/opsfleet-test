import { useState } from "react";
import type { ConfirmationRequest } from "../api/client";

/**
 * The Deletion Plan as checkboxes. Unticking item 2 sends `selected_indices` without it:
 * the same decision as typing `yes except 2`. All ticked sends null ("all items").
 */
export function DeletionPlan(props: {
  plan: ConfirmationRequest;
  disabled: boolean;
  onAnswer: (approved: boolean, selected: number[] | null) => void;
}) {
  const { plan } = props;
  const [checked, setChecked] = useState(() => new Set(plan.items.map((i) => i.index)));
  const toggle = (index: number) =>
    setChecked((c) => {
      const next = new Set(c);
      if (next.has(index)) next.delete(index);
      else next.add(index);
      return next;
    });
  const all = checked.size === plan.items.length;
  const selected = all ? null : [...checked].sort((a, b) => a - b);

  return (
    <fieldset className="plan" disabled={props.disabled}>
      <legend>
        Deletion Plan: {plan.total} report{plan.total === 1 ? "" : "s"}
        {plan.total > plan.items.length && ` (first ${plan.items.length} shown)`}
      </legend>
      {plan.items.map((item) => (
        <label key={item.index}>
          <input
            type="checkbox"
            checked={checked.has(item.index)}
            onChange={() => toggle(item.index)}
          />
          {item.index}. {item.label}
          {item.reason && <span className="reason"> ({item.reason})</span>}
        </label>
      ))}
      <div className="plan-actions">
        <button onClick={() => props.onAnswer(true, selected)} disabled={checked.size === 0}>
          Delete {all ? "all" : checked.size}
        </button>
        <button onClick={() => props.onAnswer(false, null)}>Cancel</button>
      </div>
    </fieldset>
  );
}
