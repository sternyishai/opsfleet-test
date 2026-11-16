import { useEffect, useState } from "react";
import { api, type UserProfile } from "../api/client";
import { PROFILE_FIELDS } from "../commands";

export function Profile(props: { userId: string; refresh: number }) {
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.profile(props.userId).then(
      (p) => {
        setProfile(p);
        setError(null);
      },
      (e: Error) => setError(e.message),
    );
  }, [props.userId, props.refresh]);

  const values = profile
    ? PROFILE_FIELDS.flatMap(([name, label]) => {
        const v = profile[name];
        return v ? [`${label}: ${String(v.value).replaceAll("_", " ")}`] : [];
      })
    : [];

  return (
    <section>
      <h2>My preferences</h2>
      {error && <p className="error">{error}</p>}
      {profile && values.length === 0 && (profile.notes ?? []).length === 0 && (
        <p>No saved preferences yet.</p>
      )}
      <ul>
        {values.map((v) => (
          <li key={v}>{v}</li>
        ))}
        {(profile?.notes ?? []).map((n) => (
          <li key={n.note_id}>{n.text}</li>
        ))}
      </ul>
    </section>
  );
}
