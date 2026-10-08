import { useCallback, useEffect, useState } from "react";
import { api, AuditEntry } from "../../lib/api";

// "ada changed the role of bo": the verb for each op, between the actor and the target.
const WHAT: Record<string, string> = {
  "signin": "signed in", "signin.failed": "failed to sign in as", "signin.refused": "was refused sign-in as", "signout": "signed out",
  "limit.hit": "hit the rate limit for", "session.end": "signed out a session", "sessions.end": "signed out everywhere",
  "account.create": "created the account", "account.join": "signed up as", "account.join_requested": "asked to sign up as",
  "account.rename": "renamed themselves to", "account.reclaim": "took over their unconfirmed account, ending what was set up before",
  "password.change": "changed their password", "password.change_failed": "gave a wrong current password",
  "password.reset_requested": "asked for a password reset for",
  "password.reset_link": "made a reset link for", "password.reset": "reset their password",
  "email.change": "changed their email to", "email.change_requested": "asked to change their email to", "email.confirm": "confirmed",
  "identity.link": "linked", "identity.link_failed": "couldn't link", "identity.unlink": "unlinked",
  "invite.create": "invited", "invite.revoke": "revoked the invite for", "invite.accept": "accepted the invite for",
  "invite.expired": "opened an expired invite for",
  "role.change": "changed the role of", "person.disable": "disabled", "person.enable": "enabled", "person.remove": "removed",
  "principal.update": "edited",
  "token.create": "made the token", "token.rotate": "re-issued the token", "token.revoke": "removed the token",
  "terminal.approve": "approved the terminal", "terminal.deny": "denied the terminal",
  "app.register": "registered the app", "app.connect": "connected", "app.deny": "declined", "app.disconnect": "disconnected",
  "app.token_revoke": "revoked its tokens:", "app.token_reuse": "reused a refresh token (possible theft); revoked",
  "settings.change": "changed", "source.add": "added the source", "source.remove": "removed the source", "backup.create": "made the backup",
};
const KINDS: [string, string][] = [["", "All events"], ["signin", "Sign-ins"], ["account", "Accounts"], ["people", "People"],
  ["tokens", "Tokens"], ["apps", "Apps"], ["server", "Server"]];
const VIA: Record<string, string> = { cli: "server shell", schedule: "schedule", startup: "server" };
const detail = (e: AuditEntry) => Object.entries(e.detail).filter(([k, v]) => k !== "via" && v !== null && v !== undefined && v !== "")
  .map(([k, v]) => `${k.replace(/_/g, " ")}: ${Array.isArray(v) ? v.join(", ") : String(v)}`).join(" · ");
const when = (iso: string) => new Date(iso).toLocaleString();

export function AuditTab() {
  const [rows, setRows] = useState<AuditEntry[]>([]);
  const [more, setMore] = useState(true);
  const [people, setPeople] = useState<string[]>([]);
  const [f, setF] = useState({ person: "", kind: "", problems: false });
  const page = useCallback(async (before?: number) => {
    const got = await api.audit({ before, ...f });
    setRows((r) => (before ? [...r, ...got] : got));
    setMore(got.length === 100);
  }, [f]);
  useEffect(() => { page(); }, [page]);
  useEffect(() => { api.principals().then((p) => setPeople(p.people.map((x) => x.name))).catch(() => {}); }, []);
  const actor = (e: AuditEntry) => e.actor ?? VIA[String(e.detail.via)] ?? "someone";
  return (
    <div className="panel">
      <div className="panel-head">
        <div className="grow"><h2>Audit log</h2><div className="small muted">Sign-ins, people, tokens, apps and settings.</div></div>
        <select className="select" style={{ width: 150 }} value={f.person} onChange={(e) => setF({ ...f, person: e.target.value })} aria-label="Person">
          <option value="">Everyone</option>{people.map((p) => <option key={p} value={p}>{p}</option>)}
        </select>
        <select className="select" style={{ width: 140 }} value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value })} aria-label="Kind">
          {KINDS.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
        </select>
        <label className="row small" style={{ gap: 6, whiteSpace: "nowrap" }}>
          <input type="checkbox" checked={f.problems} onChange={(e) => setF({ ...f, problems: e.target.checked })} />Problems only</label>
      </div>
      <table className="list audit"><tbody>
        {rows.map((e) => (
          <tr key={e.seq} className={e.level}>
            <td className="small muted" style={{ width: 160 }}>{when(e.ts)}
              {e.count > 1 && <div className="tiny" title={e.last_at ? `last ${when(e.last_at)}` : undefined}>×{e.count}{e.last_at ? `, last ${new Date(e.last_at).toLocaleTimeString()}` : ""}</div>}</td>
            <td className="small">
              {e.level !== "info" && <span className={"badge " + (e.level === "failure" ? "off" : "warnish")} style={{ marginRight: 6 }}>{e.level === "failure" ? "failed" : "security"}</span>}
              <b>{actor(e)}</b> {WHAT[e.op] ?? e.op} {e.target && <b>{e.target}</b>}
              {e.subject && e.subject !== e.actor && e.subject !== e.target && <span className="muted"> ({e.subject})</span>}
              {detail(e) && <div className="tiny muted">{detail(e)}</div>}
            </td>
            <td className="small muted" style={{ width: 150, textAlign: "right" }} title={e.ua ?? undefined}>{e.ip ?? (e.detail.via ? VIA[String(e.detail.via)] : "")}</td>
          </tr>
        ))}
        {!rows.length && <tr><td className="small muted">Nothing here.</td></tr>}
      </tbody></table>
      {more && rows.length > 0 && <div style={{ padding: 10, textAlign: "center" }}><button className="btn sm" onClick={() => page(rows[rows.length - 1].seq)}>Older</button></div>}
    </div>
  );
}
