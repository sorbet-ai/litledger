import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { NavLink, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { api, ApiError, eventStream, JournalEntry, Me, Warning } from "../lib/api";
import { applyTheme, savedTheme, saveTheme, session } from "../lib/storage";
import Activity from "../pages/Activity";
import AdminPage from "../pages/admin/Admin";
import { InvitePage, ResetPage } from "../pages/auth/Join";
import SignIn from "../pages/auth/SignIn";
import Authorize from "../pages/Authorize";
import Knowledge from "../pages/Knowledge";
import Library from "../pages/Library";
import MapsPage from "../pages/Maps";
import SettingsPage from "../pages/settings/Settings";
import WorkPage from "../pages/Work";
import { Icon } from "../ui/Icon";
import { OverlayProvider, useOverlays } from "../ui/overlays";
import { Action, Picked, PickerProvider, usePicker } from "../ui/pickers";
import { AppCtx, Ctx } from "./context";

// The graph and map canvas carry big libraries; load them when first opened.
const GraphPage = lazy(() => import("../pages/Graph"));
const MapEditor = lazy(() => import("../map/MapEditor"));

function Shell({ ctx, warnings, projects }: { ctx: Ctx; warnings: Warning[]; projects: { id: string; works: number }[] }) {
  const { palette } = usePicker();
  const { ask } = useOverlays();
  const { project, setProject, me, theme, setTheme } = ctx;
  return (
    <div className="shell">
      <aside className="rail">
        <div className="brand"><span className="brand-mark" />litledger</div>
        <button className="search-trigger" onClick={palette}><Icon name="search" />Search<kbd>Ctrl K</kbd></button>
        <div className="proj stack" style={{ gap: 4 }}>
          <span className="tiny muted" style={{ paddingLeft: 4 }}>Project</span>
          <select className="select" value={project} onChange={async (e) => {
            if (e.target.value === "__new") {
              const name = await ask("New project", { label: "Name", placeholder: "e.g. thesis", required: true }, "Create");
              if (name) setProject(name.toLowerCase().replace(/[^a-z0-9_.-]/g, "-"));
            } else setProject(e.target.value);
          }}>
            {!projects.find((p) => p.id === project) && <option value={project}>{project}</option>}
            {projects.map((p) => <option key={p.id} value={p.id}>{p.id} · {p.works}</option>)}
            <option value="__new">New project…</option>
          </select>
        </div>
        <nav className="nav">
          <NavLink to="/" end><Icon name="library" />Library</NavLink>
          <NavLink to="/knowledge"><Icon name="knowledge" />Knowledge</NavLink>
          <NavLink to="/maps"><Icon name="map" />Mind maps</NavLink>
          <NavLink to="/graph"><Icon name="graph" />Graph</NavLink>
          <NavLink to="/activity"><Icon name="activity" />Activity</NavLink>
          <div className="sep" />
          <NavLink to="/settings"><Icon name="settings" />Settings</NavLink>
          {me.admin && <NavLink to="/admin"><Icon name="shield" />Admin{warnings.length ? <span className="count">{warnings.length}</span> : null}</NavLink>}
        </nav>
        <div className="rail-foot">
          <div className="row" style={{ justifyContent: "space-between" }}>
            <span>{me.name}</span>
            <button className="btn ghost sm icon" title={`Theme: ${theme}`} aria-label={`Theme: ${theme}`} onClick={() => setTheme(theme === "light" ? "dark" : theme === "dark" ? "system" : "light")}>
              <Icon name={theme === "dark" ? "moon" : "sun"} />
            </button>
          </div>
          <a href="#" onClick={async (e) => { e.preventDefault(); await api.logout().catch(() => {}); window.location.href = "/"; }}>Sign out</a>
        </div>
      </aside>
      <main className="main">
        <Routes>
          <Route path="/" element={<Library />} />
          <Route path="/work/*" element={<WorkPage />} />
          <Route path="/knowledge" element={<Knowledge />} />
          <Route path="/graph" element={<Suspense fallback={null}><GraphPage /></Suspense>} />
          <Route path="/maps" element={<MapsPage />} />
          <Route path="/maps/:id" element={<Suspense fallback={null}><MapEditor /></Suspense>} />
          <Route path="/activity" element={<Activity />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="/settings/:tab" element={<SettingsPage />} />
          <Route path="/admin" element={<AdminPage />} />
          <Route path="/admin/:tab" element={<AdminPage />} />
        </Routes>
      </main>
    </div>
  );
}

function Inner() {
  const [me, setMe] = useState<(Me & { project: string }) | null>(null);
  const [warnings, setWarnings] = useState<Warning[]>([]);
  const [needLogin, setNeedLogin] = useState(false);
  const [project, setProjectState] = useState(session.project);
  const [projects, setProjects] = useState<{ id: string; works: number }[]>([]);
  const [tick, setTick] = useState(0);
  const [lastEvent, setLastEvent] = useState<JournalEntry | null>(null);
  const [theme, setThemeState] = useState(savedTheme);
  const { toast } = useOverlays();
  const nav = useNavigate();
  const location = useLocation();
  const listeners = useRef(new Set<(e: JournalEntry) => void>());
  const subscribe = useCallback((fn: (e: JournalEntry) => void) => {
    listeners.current.add(fn);
    return () => { listeners.current.delete(fn); };
  }, []);

  useEffect(() => { applyTheme(theme); }, [theme]);
  const setTheme = (t: string) => { saveTheme(t); setThemeState(t); };

  const load = useCallback(async () => {
    try {
      const m = await api.me();
      setMe({ name: m.name, kind: m.kind, role: m.role, admin: m.admin, project: m.project });
      setWarnings(m.warnings);
      setProjects(await api.projects());
      setNeedLogin(false);
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) setNeedLogin(true);
    }
  }, []);
  useEffect(() => { load(); }, [load]);
  useEffect(() => { if (lastEvent?.op === "config.set" || lastEvent?.op === "project.create") load(); }, [lastEvent, load]);
  // One live stream per signed-in person and project: refreshing `me` (after a settings change) must not reconnect it.
  const signedIn = me?.name;
  useEffect(() => {
    if (!signedIn) return;
    let timer: number | undefined;
    const stop = eventStream((e) => {
      listeners.current.forEach((fn) => { try { fn(e); } catch { /* a page's handler must not break the stream */ } });
      setLastEvent(e);
      // Pages refetch on tick; a burst of events (one agent call can write dozens) becomes one refetch.
      window.clearTimeout(timer);
      timer = window.setTimeout(() => setTick((t) => t + 1), 500);
    });
    return () => { window.clearTimeout(timer); stop(); };
  }, [signedIn, project]);

  const setProject = (p: string) => { session.project = p; setProjectState(p); nav("/"); load(); };
  const actions: Action[] = useMemo(() => [
    { label: "Go to Library", icon: "library", run: () => nav("/") },
    { label: "Go to Knowledge", icon: "knowledge", run: () => nav("/knowledge"), keywords: "methods datasets benchmarks results" },
    { label: "Go to Mind maps", icon: "map", run: () => nav("/maps") },
    { label: "Go to Graph", icon: "graph", run: () => nav("/graph") },
    { label: "Go to Activity", icon: "activity", run: () => nav("/activity") },
    { label: "Go to Settings", icon: "settings", run: () => nav("/settings"), keywords: "profile password account tokens apps" },
    ...(me?.admin ? [{ label: "Go to Admin", icon: "shield", run: () => nav("/admin"), keywords: "people invite sources sign-in server backups audit" }] : []),
    { label: "Connect an app (Claude Code, Codex)", icon: "robot", run: () => nav("/settings/apps"), keywords: "mcp oauth plugin terminal login" },
    { label: "Add papers", icon: "plus", run: () => nav("/?add=1"), keywords: "capture import" },
    { label: "New mind map", icon: "map", run: () => nav("/maps?new=1") },
    { label: "Toggle dark mode", icon: "moon", run: () => setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark") },
  ], [nav, me?.admin]);
  const onOpen = (p: Picked) => {
    if (p.kind === "work" && p.ref) nav(`/work/${encodeURIComponent(p.ref)}`);
    else if (p.kind === "map" && p.ref) nav(`/maps/${encodeURIComponent(p.ref)}`);
    else if (p.kind === "tag" && p.ref) nav(`/?tag=${encodeURIComponent(p.ref.slice(4))}`);
    else if (p.ref) nav(`/knowledge?item=${encodeURIComponent(p.ref)}`);
  };

  // Links from an email (or an admin) work signed out.
  if (location.pathname === "/invite") return <InvitePage />;
  if (location.pathname === "/reset") return <ResetPage />;
  if (needLogin) return <SignIn next={location.pathname + location.search} onDone={load} />;
  if (!me) return <div className="empty">Loading…</div>;
  const ctx: Ctx = { me, project, setProject, toast, tick, lastEvent, subscribe, refresh: load, theme, setTheme };
  if (location.pathname === "/authorize") return <AppCtx.Provider value={ctx}><Authorize projects={projects} /></AppCtx.Provider>;
  return (
    <AppCtx.Provider value={ctx}>
      <PickerProvider actions={actions} onOpen={onOpen}>
        <Shell ctx={ctx} warnings={warnings} projects={projects} />
      </PickerProvider>
    </AppCtx.Provider>
  );
}

export default function App() {
  return (
    <OverlayProvider>
      <Inner />
    </OverlayProvider>
  );
}
