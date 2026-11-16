import type { DevUser } from "../api/client";

const ROLE_LABEL = { executive: "Executive", manager: "Manager" } as const;

/** `manager-a · Manager · Calvin Klein, Tommy Hilfiger`, `ceo · Executive · all Brands`. */
export function describe(user: DevUser): string {
  if (!user.role) return `${user.user_id} · no role`;
  const brands =
    user.role === "executive" ? "all Brands" : user.brands.join(", ") || "no Brands";
  return `${user.user_id} · ${ROLE_LABEL[user.role]} · ${brands}`;
}

export function UsersPanel(props: {
  users: DevUser[];
  active: DevUser | null;
  error: string | null;
  onPick: (user: DevUser) => void;
}) {
  return (
    <section>
      <h2>Users (dev)</h2>
      {props.error && <p className="error">{props.error}</p>}
      <ul className="users">
        {props.users.map((user) => (
          <li key={user.user_id}>
            <button
              className={user.user_id === props.active?.user_id ? "picked" : ""}
              onClick={() => props.onPick(user)}
            >
              {describe(user)}
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
