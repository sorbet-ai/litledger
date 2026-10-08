import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Agent, api, PendingLogin } from "../../lib/api";
import { CopyBlock } from "../../ui/CopyBlock";
import { useOverlays } from "../../ui/overlays";
import { AgentTable } from "./agents";

/** Plugin installs name the litledger repository itself (a local clone or its git URL); there is no hosted marketplace. */
const MARKETPLACE = "sorbet-ai/litledger";
const REPO_NOTE = <>Or a local clone of the repo instead of <code>{MARKETPLACE}</code>.</>;

function ConnectApps() {
  const origin = window.location.origin;
  const mcp = `${origin}/mcp`;
  const [tab, setTab] = useState<"claude" | "codex" | "other" | "terminal">("claude");
  const custom = origin !== "http://127.0.0.1:8765";
  return (
    <div className="panel pad stack" style={{ gap: 14 }}>
      <div>
        <h2>Connect an app</h2>
        <div className="small muted">Add the server to your app. It opens this site so you can allow it.</div>
      </div>
      <div className="seg">
        {([["claude", "Claude Code"], ["codex", "Codex"], ["other", "Other MCP apps"], ["terminal", "Terminal"]] as const).map(([id, label]) => (
          <button key={id} className={tab === id ? "on" : ""} onClick={() => setTab(id)}>{label}</button>
        ))}
      </div>
      {tab === "claude" && (
        <div className="stack" style={{ gap: 12 }}>
          <CopyBlock title="Plugin" text={`claude plugin marketplace add ${MARKETPLACE}\nclaude plugin install litledger@litledger`}
                   note={<>{REPO_NOTE} Then <code>/mcp</code> → litledger → Authenticate.{custom && <> Set <code>LITLEDGER_URL={origin}</code> first.</>}</>} />
          <CopyBlock title="Or just the server" text={`claude mcp add --transport http --scope user litledger ${mcp}\nclaude mcp login litledger`} />
        </div>
      )}
      {tab === "codex" && (
        <div className="stack" style={{ gap: 12 }}>
          <CopyBlock title="Server" text={`codex mcp add litledger --url ${mcp}\ncodex mcp login litledger`} />
          <CopyBlock title="Skill (optional)" text={`codex plugin marketplace add ${MARKETPLACE}\ncodex plugin add litledger@litledger`} note={REPO_NOTE} />
        </div>
      )}
      {tab === "other" && <CopyBlock title="Server URL" text={mcp} note="Apps sign in through the browser. For apps that only take a header, make an API token." />}
      {tab === "terminal" && <CopyBlock title="litledger CLI" text={`litledger login --url ${origin}`} note="Approve the code it shows. It appears here." />}
    </div>
  );
}

export function AppsTab() {
  const { toast } = useOverlays();
  const [params, setParams] = useSearchParams();
  const code = params.get("code")?.toUpperCase() ?? "";
  const [pending, setPending] = useState<PendingLogin[]>([]);
  const [agents, setAgents] = useState<Agent[] | null>(null);
  const load = useCallback(() => { api.myAgents().then((a) => setAgents(a.filter((x) => x.origin === "oauth" || x.origin === "login"))); }, []);
  useEffect(load, [load]);
  useEffect(() => {
    let live = true;
    const tick = () => api.logins().then((l) => { if (live) setPending(l); }).catch(() => {});
    tick();
    const t = window.setInterval(tick, 2500);
    return () => { live = false; window.clearInterval(t); };
  }, []);
  const decide = async (l: PendingLogin, allow: boolean) => {
    try {
      if (allow) { await api.approveLogin(l.code); toast(`${l.name} is signed in. It acts for you.`); }
      else await api.denyLogin(l.code);
    } catch (e) { toast(String(e), true); }
    setPending((p) => p.filter((x) => x.code !== l.code));
    if (code) { params.delete("code"); setParams(params, { replace: true }); }
    load();
  };
  return (
    <>
      {pending.length > 0 && (
        <div className="panel">
          <div className="panel-head"><div className="grow"><h2>Waiting for approval</h2>
            <div className="small muted">Only approve codes you started. The terminal will act for you.</div></div></div>
          <table className="list"><tbody>
            {pending.map((l) => (
              <tr key={l.code} className={l.code === code ? "hl" : ""}>
                <td style={{ width: 130 }}><span className="login-code">{l.code}</span></td>
                <td><b>{l.name}</b><div className="tiny muted">from {l.ip} · {l.client || "unknown client"} · {l.age < 60 ? `${l.age}s` : `${Math.round(l.age / 60)} min`} ago</div></td>
                <td style={{ width: 180, textAlign: "right" }}>
                  <div className="row" style={{ justifyContent: "flex-end", gap: 6 }}>
                    <button className="btn ghost sm" onClick={() => decide(l, false)}>Deny</button>
                    <button className="btn primary sm" onClick={() => decide(l, true)}>Approve</button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody></table>
        </div>
      )}
      {code && !pending.some((l) => l.code === code) && (
        <div className="warn">No sign-in with code <b>{code}</b> is waiting. It may have expired or been approved already.</div>
      )}
      <div className="panel">
        <div className="panel-head"><div className="grow"><h2>Connected apps</h2>
          <div className="small muted">Apps and terminals that act for you. They can use the library, not settings.</div></div></div>
        {agents && <AgentTable agents={agents} empty="Nothing connected yet." onChange={load} />}
      </div>
      <ConnectApps />
    </>
  );
}
