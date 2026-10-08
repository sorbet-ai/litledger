// Tokens, terminals and apps: the table both Settings (yours) and Admin (everyone's) show, and the dialog that shows a
// new token once.
import { ReactNode } from "react";
import { useApp } from "../../app/context";
import { Agent, api, TokenRequest } from "../../lib/api";
import { ago } from "../../lib/format";
import { CopyBlock } from "../../ui/CopyBlock";
import { Dialog } from "../../ui/Dialog";
import { useOverlays } from "../../ui/overlays";

const ORIGIN: Record<string, string> = { oauth: "app", login: "terminal", token: "token", bootstrap: "admin token" };
const csv = (s: string) => s.split(",").map((x) => x.trim()).filter(Boolean);

export function AgentTable({ agents, empty, owners, onChange }: { agents: Agent[]; empty: ReactNode; owners?: boolean; onChange: () => void }) {
  const { form, confirm, toast } = useOverlays();
  const editProjects = async (a: Agent) => {
    const r = await form({ title: `Projects for ${a.by}`, submit: "Save", fields: [
      { name: "projects", label: "Projects", value: (a.projects ?? []).join(", "), placeholder: "all", hint: "Comma-separated. Leave empty for all." }] });
    if (!r) return;
    try { await api.updatePrincipal(a.name, { projects: csv(r.projects) }); onChange(); } catch (e) { toast(String(e), true); }
  };
  const revoke = async (a: Agent) => {
    const what = a.origin === "oauth" ? `${a.client_name ?? "The app"} loses access at once. It can ask again.` : "Anything using it stops working at once.";
    if (!(await confirm(`Remove ${a.by}?`, <p className="small muted" style={{ margin: 0 }}>{what}</p>, "Remove", true))) return;
    try { await api.removePrincipal(a.name); toast(`Removed ${a.by}`); onChange(); } catch (e) { toast(String(e), true); }
  };
  if (!agents.length) return <div className="small muted" style={{ padding: "12px 16px" }}>{empty}</div>;
  return (
    <table className="list"><tbody>
      {agents.map((a) => (
        <tr key={a.name}>
          <td>
            <div className="row" style={{ gap: 6 }}><b>{owners ? a.by : a.label}</b><span className="badge">{ORIGIN[a.origin ?? "token"] ?? "token"}</span>
              {a.access === "full" && <span className="badge admin">full access</span>}
              {a.expired && <span className="badge off">expired</span>}</div>
            <div className="tiny muted">
              {a.origin === "oauth" ? a.client_name : a.origin === "login" ? "litledger login" : owners && !a.owner ? "shared" : "API token"}
              {" · "}{a.projects ? a.projects.join(", ") : "all projects"}
              {a.expires_at && !a.expired ? ` · expires ${a.expires_at.slice(0, 10)}` : ""}
            </div>
          </td>
          <td className="small muted" style={{ width: 130 }}>used {ago(a.last_seen_at)}</td>
          <td style={{ width: 160, textAlign: "right" }}>
            <div className="row" style={{ justifyContent: "flex-end", gap: 4 }}>
              <button className="btn ghost sm" onClick={() => editProjects(a)}>Projects</button>
              <button className="btn ghost sm" onClick={() => revoke(a)}>{a.origin === "oauth" ? "Disconnect" : "Remove"}</button>
            </div>
          </td>
        </tr>
      ))}
    </tbody></table>
  );
}

/** Ask for a new token's name, expiry, projects (and, for admins, access). */
export function useTokenForm() {
  const { form } = useOverlays();
  const { me } = useApp();
  return async (title: string, defaults: { expires: string }): Promise<TokenRequest | null> => {
    const r = await form({ title, submit: "Create token", fields: [
      { name: "name", label: "Name", placeholder: "e.g. ci, laptop", required: true },
      { name: "expires", label: "Expires", type: "select", value: defaults.expires, options: [
        { value: "30", label: "In 30 days" }, { value: "90", label: "In 90 days" }, { value: "365", label: "In a year" }, { value: "never", label: "Never" }] },
      { name: "projects", label: "Projects", placeholder: "all", hint: "Comma-separated. Leave empty for all." },
      ...(me.admin ? [{ name: "access", label: "Access", type: "select" as const, value: "library", options: [
        { value: "library", label: "Library: papers, notes, maps" }, { value: "full", label: "Full: also settings and people" }] }] : []),
    ] });
    if (!r) return null;
    return { name: r.name, expires: r.expires, projects: csv(r.projects), access: (r.access || "library") as "library" | "full" };
  };
}

export function TokenDialog({ made, onClose }: { made: { name: string; token: string }; onClose: () => void }) {
  const { project } = useApp();
  const origin = window.location.origin;
  return (
    <Dialog title="Your new token" wide onClose={onClose}>
      <div className="warn">Copy it now. It won't be shown again.</div>
      <CopyBlock title="Token" text={made.token} />
      <CopyBlock title="CLI and scripts" text={`export LITLEDGER_URL=${origin}\nexport LITLEDGER_TOKEN=${made.token}`} />
      <CopyBlock title=".mcp.json (apps that take a header)" text={JSON.stringify({ mcpServers: { litledger: { type: "http", url: `${origin}/mcp`,
        headers: { Authorization: "Bearer ${LITLEDGER_TOKEN}", "X-Litledger-Project": project } } } }, null, 2)} />
      <div className="actions"><button className="btn primary" onClick={onClose}>Done</button></div>
    </Dialog>
  );
}
