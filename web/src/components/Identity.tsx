import { useEffect, useRef, useState } from "react";
import type { DevUser } from "../api/client";
import { brandsOf, initials, ROLE_LABEL } from "../format";
import { Icon } from "./Icon";

export function Avatar(props: { user: DevUser; size?: "sm" | "md" }) {
  return (
    <span className={`avatar avatar-${props.size ?? "md"} role-${props.user.role ?? "none"}`}>
      {initials(props.user.user_id)}
    </span>
  );
}

export function RoleBadge(props: { user: DevUser }) {
  return (
    <span className={`badge role-${props.user.role ?? "none"}`}>
      {props.user.role ? ROLE_LABEL[props.user.role] : "No role"}
    </span>
  );
}

/**
 * Who the browser is connected as, and the dev-only switcher (the equivalent of `--as`).
 * Switching starts a new Conversation: a Conversation belongs to one User.
 */
export function IdentityCard(props: {
  users: DevUser[];
  active: DevUser | null;
  error: string | null;
  onPick: (user: DevUser) => void;
}) {
  const [open, setOpen] = useState(props.active === null);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const close = (e: MouseEvent) => {
      if (props.active && !ref.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [props.active]);

  return (
    <div className="identity" ref={ref}>
      <div className="eyebrow">Connected as</div>
      <button className="identity-current" onClick={() => setOpen((o) => !o)}>
        {props.active ? (
          <>
            <Avatar user={props.active} />
            <span className="identity-text">
              <span className="identity-name">
                {props.active.user_id} <RoleBadge user={props.active} />
              </span>
              <span className="identity-brands">{brandsOf(props.active)}</span>
            </span>
          </>
        ) : (
          <span className="identity-text">
            <span className="identity-name">Nobody yet</span>
            <span className="identity-brands">Pick a User to start</span>
          </span>
        )}
        <Icon name="chevron" className={open ? "flip" : ""} />
      </button>
      {props.error && <p className="error-text">{props.error}</p>}
      {open && (
        <div className="switcher" role="listbox">
          <div className="switcher-head">Switch User · development only</div>
          {props.users.map((user) => (
            <button
              key={user.user_id}
              role="option"
              aria-selected={user.user_id === props.active?.user_id}
              className="switcher-row"
              onClick={() => {
                props.onPick(user);
                setOpen(false);
              }}
            >
              <Avatar user={user} size="sm" />
              <span className="identity-text">
                <span className="identity-name">
                  {user.user_id} <RoleBadge user={user} />
                </span>
                <span className="identity-brands">{brandsOf(user)}</span>
              </span>
              {user.user_id === props.active?.user_id && <Icon name="check" />}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
