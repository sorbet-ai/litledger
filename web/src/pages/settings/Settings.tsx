import { NavLink, useParams } from "react-router-dom";
import { Icon } from "../../ui/Icon";
import { AppsTab } from "./Apps";
import { ProfileTab } from "./Profile";
import { TokensTab } from "./Tokens";

const TABS = [
  { id: "profile", label: "Profile", icon: "user" },
  { id: "apps", label: "Connected apps", icon: "robot" },
  { id: "tokens", label: "API tokens", icon: "key" },
];
// Older links: Settings → Access (sign-in approvals, apps) and Your account.
const OLD: Record<string, string> = { access: "apps", agents: "apps", account: "profile" };

/** Everyone's own account. Server-wide things live under Admin. */
export default function SettingsPage() {
  const raw = useParams().tab ?? "profile";
  const tab = OLD[raw] ?? raw;
  return (
    <div className="page">
      <div className="page-head"><h1>Settings</h1></div>
      <div className="settings-grid">
        <nav className="kinds">
          {TABS.map((t) => (
            <NavLink key={t.id} to={`/settings/${t.id}`} className={() => (tab === t.id ? "on" : "")}>
              <span className="row" style={{ gap: 8 }}><Icon name={t.icon} size={15} />{t.label}</span>
            </NavLink>
          ))}
        </nav>
        <div className="stack" style={{ gap: 18, minWidth: 0 }}>
          {tab === "apps" ? <AppsTab /> : tab === "tokens" ? <TokensTab /> : <ProfileTab />}
        </div>
      </div>
    </div>
  );
}
