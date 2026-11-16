import { useEffect, useState } from "react";
import { api, type DevUser } from "./api/client";
import { Chat } from "./components/Chat";
import { Icon } from "./components/Icon";
import { ReportPanel } from "./components/ReportPanel";
import { Showcase } from "./components/Showcase";
import { Sidebar } from "./components/Sidebar";
import { DIAGRAMS, FILMS } from "./showcase";

const PAGES = [
  ["prototype", "Prototype"],
  ["diagrams", "Diagrams"],
  ["films", "Films"],
] as const;
type Page = (typeof PAGES)[number][0];

const pageFromHash = (): Page => {
  const name = window.location.hash.replace(/^#\/?/, "");
  return PAGES.find(([id]) => id === name)?.[0] ?? "prototype";
};

/** The navigator: the prototype itself, and the design's diagrams and films next to it. */
export function App() {
  const [page, setPage] = useState<Page>(pageFromHash);

  useEffect(() => {
    const onHash = () => setPage(pageFromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  return (
    <div className="shell">
      <nav className="topnav" aria-label="Pages">
        {PAGES.map(([id, label]) => (
          <a key={id} href={`#/${id}`} className={page === id ? "topnav-tab active" : "topnav-tab"}>
            {label}
          </a>
        ))}
      </nav>
      {/* Hidden, not unmounted: a Conversation in progress survives a look at the diagrams. */}
      <div className="page" hidden={page !== "prototype"}>
        <Prototype />
      </div>
      {page === "diagrams" && (
        <div className="page">
          <Showcase
            kind="diagrams"
            title="Diagrams"
            intro="The design drawn: the HLD's four views and the LLD's compiled graph. Click one to open it full size."
            items={DIAGRAMS}
          />
        </div>
      )}
      {page === "films" && (
        <div className="page">
          <Showcase
            kind="films"
            title="Films"
            intro="Three short walkthroughs of the design, under two minutes each."
            items={FILMS}
          />
        </div>
      )}
    </div>
  );
}

/**
 * The web channel's screen. It holds no business logic (LLD 07): it sends what the User
 * typed or ticked and draws what `ChatService` and `Views` return.
 */
function Prototype() {
  const [users, setUsers] = useState<DevUser[]>([]);
  const [active, setActive] = useState<DevUser | null>(null);
  const [usersError, setUsersError] = useState<string | null>(null);
  const [chatKey, setChatKey] = useState(0); // a new key is a new Conversation
  const [refresh, setRefresh] = useState(0); // bumped after every turn and command
  const [openReport, setOpenReport] = useState<string | null>(null);

  useEffect(() => {
    api.devUsers().then(setUsers, (e: Error) => setUsersError(e.message));
  }, []);

  const pick = (user: DevUser) => {
    setActive(user);
    setOpenReport(null);
    setChatKey((k) => k + 1); // a Conversation belongs to one User
  };

  return (
    <div className={openReport ? "app with-panel" : "app"}>
      <Sidebar
        users={users}
        active={active}
        usersError={usersError}
        refresh={refresh}
        onPick={pick}
        onNewChat={() => setChatKey((k) => k + 1)}
        onOpenReport={setOpenReport}
      />
      <main className="main">
        {active ? (
          <Chat
            key={`${active.user_id}-${chatKey}`}
            user={active}
            onNewChat={() => setChatKey((k) => k + 1)}
            onChanged={() => setRefresh((r) => r + 1)}
            onOpenReport={setOpenReport}
          />
        ) : (
          <div className="pick-first">
            <span className="welcome-mark">
              <Icon name="user" size={22} />
            </span>
            <h1>Who are you today?</h1>
            <p className="muted">Pick a User in the sidebar. Each one sees only their own Brands and reports.</p>
          </div>
        )}
      </main>
      {active && openReport && (
        <ReportPanel userId={active.user_id} reportId={openReport} onClose={() => setOpenReport(null)} />
      )}
    </div>
  );
}
