import { NavLink, useParams } from "react-router-dom";
import { useApp } from "../../app/context";
import { Icon } from "../../ui/Icon";
import { AgentsTab } from "./Agents";
import { AuditTab } from "./Audit";
import { PeopleTab } from "./People";
import { BackupsTab, ServerTab } from "./Server";
import { SignInTab } from "./SignInSettings";
import { SourcesTab } from "./Sources";

const TABS = [
  { id: "people", label: "People", icon: "users" },
  { id: "apps", label: "Apps & tokens", icon: "robot" },
  { id: "sources", label: "Sources", icon: "search" },
  { id: "signin", label: "Sign-in", icon: "lock" },
  { id: "server", label: "Server", icon: "server" },
  { id: "backups", label: "Backups", icon: "download" },
  { id: "audit", label: "Audit log", icon: "log" },
];
const PAGES: Record<string, () => JSX.Element> = { people: PeopleTab, apps: AgentsTab, sources: SourcesTab, signin: SignInTab,
  server: ServerTab, backups: BackupsTab, audit: AuditTab };

/** Running the server: admins only (the server refuses everyone else; this page just doesn't offer it). */
export default function AdminPage() {
  const { me } = useApp();
  const tab = useParams().tab ?? "people";
  if (!me.admin) return <div className="page"><div className="empty"><b>Admins only</b>Ask an admin if you need something changed.</div></div>;
  const Page = PAGES[tab] ?? PeopleTab;
  return (
    <div className="page">
      <div className="page-head"><h1>Admin</h1></div>
      <div className="settings-grid">
        <nav className="kinds">
          {TABS.map((t) => (
            <NavLink key={t.id} to={`/admin/${t.id}`} className={() => (tab === t.id ? "on" : "")}>
              <span className="row" style={{ gap: 8 }}><Icon name={t.icon} size={15} />{t.label}</span>
            </NavLink>
          ))}
        </nav>
        <div className="stack" style={{ gap: 18, minWidth: 0 }}><Page /></div>
      </div>
    </div>
  );
}
