import { useEffect, useState } from "react";
import { api, type DevUser, type ListReportsOutput, type UserProfile } from "../api/client";
import { PROFILE_FIELDS } from "../commands";
import { day } from "../format";
import { Icon } from "./Icon";
import { IdentityCard } from "./Identity";

export function Sidebar(props: {
  users: DevUser[];
  active: DevUser | null;
  usersError: string | null;
  refresh: number;
  onPick: (user: DevUser) => void;
  onNewChat: () => void;
  onOpenReport: (id: string) => void;
}) {
  const userId = props.active?.user_id ?? null;
  const [reports, setReports] = useState<ListReportsOutput | null>(null);
  const [profile, setProfile] = useState<UserProfile | null>(null);

  useEffect(() => {
    setReports(null);
    setProfile(null);
    if (!userId) return;
    api.reports(userId).then(setReports, () => setReports(null));
    api.profile(userId).then(setProfile, () => setProfile(null));
  }, [userId, props.refresh]);

  const preferences = profile
    ? [
        ...PROFILE_FIELDS.flatMap(([name, label]) => {
          const v = profile[name];
          return v ? [`${label}: ${String(v.value).replaceAll("_", " ")}`] : [];
        }),
        ...(profile.notes ?? []).map((n) => n.text),
      ]
    : [];

  return (
    <aside className="sidebar">
      <div className="brand">
        <span className="brand-mark">
          <Icon name="spark" size={16} />
        </span>
        Retail Analysis
      </div>
      <IdentityCard users={props.users} active={props.active} error={props.usersError} onPick={props.onPick} />
      <button className="new-chat" onClick={props.onNewChat} disabled={!props.active}>
        <Icon name="plus" /> New chat
      </button>
      {props.active && (
        <nav className="side-scroll">
          <div className="side-section">
            <div className="eyebrow">
              My reports {reports ? <span className="count">{reports.total}</span> : null}
            </div>
            {reports && reports.reports.length === 0 && <p className="side-empty">No saved reports yet.</p>}
            {reports?.reports.map((r) => (
              <button
                key={r.report_id}
                className="side-item"
                disabled={!r.accessible}
                onClick={() => props.onOpenReport(r.report_id)}
                title={r.title}
              >
                <Icon name="file" />
                <span className="side-item-text">
                  <span className="truncate">{r.title}</span>
                  <span className="side-meta">{day(r.created_at)}</span>
                </span>
              </button>
            ))}
          </div>
          <div className="side-section">
            <div className="eyebrow">My preferences</div>
            {profile && preferences.length === 0 && (
              <p className="side-empty">None yet. Say “always give me tables”.</p>
            )}
            {preferences.map((p) => (
              <div key={p} className="side-pref">
                {p}
              </div>
            ))}
          </div>
        </nav>
      )}
      <div className="side-foot">
        Development build: the User switcher stands in for single sign-on.
      </div>
    </aside>
  );
}
