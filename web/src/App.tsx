import { useCallback, useEffect, useState } from "react";
import { api, type DevUser } from "./api/client";
import { Chat } from "./components/Chat";
import { MyReports } from "./components/MyReports";
import { Profile } from "./components/Profile";
import { describe, UsersPanel } from "./components/UsersPanel";

/**
 * The web channel's screen. It holds no business logic (LLD 07): it sends what the User
 * typed or ticked and renders what `ChatService` and `Views` return.
 */
export function App() {
  const [users, setUsers] = useState<DevUser[]>([]);
  const [active, setActive] = useState<DevUser | null>(null);
  const [usersError, setUsersError] = useState<string | null>(null);
  // A new key starts a new Conversation: Chat drops its conversation_id and its log.
  const [chatKey, setChatKey] = useState(0);
  // Bumped after every turn, so "My reports" and the profile show what the turn changed.
  const [refresh, setRefresh] = useState(0);

  useEffect(() => {
    api.devUsers().then(setUsers, (e: Error) => setUsersError(e.message));
  }, []);

  const switchUser = (user: DevUser) => {
    setActive(user);
    setChatKey((k) => k + 1); // a Conversation belongs to one User
    setRefresh((r) => r + 1);
  };
  const turnEnded = useCallback(() => setRefresh((r) => r + 1), []);

  return (
    <div className="app">
      <header className="who">
        {active ? (
          <>
            Signed in as <strong>{describe(active)}</strong>
          </>
        ) : (
          "Pick a User to start"
        )}
      </header>
      <aside className="side">
        <UsersPanel users={users} active={active} error={usersError} onPick={switchUser} />
        {active && <MyReports userId={active.user_id} refresh={refresh} />}
        {active && <Profile userId={active.user_id} refresh={refresh} />}
      </aside>
      <main className="main">
        {active ? (
          <Chat
            key={`${active.user_id}-${chatKey}`}
            userId={active.user_id}
            onNewChat={() => setChatKey((k) => k + 1)}
            onTurnEnded={turnEnded}
          />
        ) : (
          <p className="empty">Choose a User on the left. The Users panel is for development only.</p>
        )}
      </main>
    </div>
  );
}
