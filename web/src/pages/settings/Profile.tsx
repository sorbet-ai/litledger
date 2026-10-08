import { Fragment, useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useApp } from "../../app/context";
import { Account, api, AuthMethods, Session } from "../../lib/api";
import { ago } from "../../lib/format";
import { useOverlays } from "../../ui/overlays";

const ROLE: Record<string, string> = { admin: "Admin", member: "Member" };
const ROLE_NOTE: Record<string, string> = { admin: "You manage the server and people.", member: "You use the library." };

/** "Chrome on Windows" from a user agent. */
function device(ua?: string): string {
  if (!ua) return "Browser";
  const browser = /Edg\//.test(ua) ? "Edge" : /Firefox\//.test(ua) ? "Firefox" : /Chrome\//.test(ua) ? "Chrome" : /Safari\//.test(ua) ? "Safari" : "Browser";
  const os = /Windows/.test(ua) ? "Windows" : /iPhone|iPad/.test(ua) ? "iOS" : /Mac OS/.test(ua) ? "macOS" : /Android/.test(ua) ? "Android" : /Linux/.test(ua) ? "Linux" : "";
  return os ? `${browser} on ${os}` : browser;
}

export function ProfileTab() {
  const { me, refresh, theme, setTheme } = useApp();
  const { toast, ask, form, confirm } = useOverlays();
  const [params, setParams] = useSearchParams();
  const [acct, setAcct] = useState<Account | null>(null);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [methods, setMethods] = useState<AuthMethods | null>(null);
  const load = useCallback(() => {
    api.account().then(setAcct);
    api.mySessions().then(setSessions);
    api.authMethods().then(setMethods);
  }, []);
  useEffect(load, [load]);
  useEffect(() => {
    const msg = params.get("linked") ? `Connected your ${params.get("linked") === "github" ? "GitHub" : "Google"} account.`
      : params.get("error") === "taken" ? "That account is already linked to someone else here."
      : params.get("email") === "confirmed" ? "Your email is changed." : "";
    if (msg) { toast(msg, params.get("error") !== null); setParams({}, { replace: true }); }
  }, [params, setParams, toast]);

  const run = async (fn: () => Promise<unknown>, done?: string) => {
    try { await fn(); if (done) toast(done); load(); refresh(); } catch (e) { toast(String(e).replace(/^Error: /, ""), true); }
  };
  const rename = async () => {
    const v = await ask("Your name", { label: "Name", value: me.name, hint: "Lowercase letters, digits and dashes." }, "Save");
    if (v) run(() => api.updateAccount({ name: v }));
  };
  const changeEmail = async () => {
    const v = await ask("Your email", { label: "Email", value: acct?.email ?? "", placeholder: "you@example.com" }, "Save");
    if (!v) return;
    run(async () => {
      const r = await api.updateAccount({ email: v });
      if (r.confirm) toast(`We sent a link to ${r.confirm}. Your email changes when you open it.`);
    });
  };
  const password = async () => {
    const r = await form({ title: acct?.has_password ? "Change password" : "Set a password", submit: "Save", fields: [
      ...(acct?.has_password ? [{ name: "current", label: "Current password", type: "password" as const, required: true }] : []),
      { name: "next", label: "New password", type: "password", required: true, hint: "At least 8 characters. Your other sessions are signed out." }] });
    if (r) run(() => api.changePassword(r.current ?? "", r.next), "Password saved.");
  };
  const linked = (p: string) => acct?.identities.find((i) => i.provider === p);
  return (
    <>
      <div className="panel">
        <div className="panel-head"><h2 className="grow">Profile</h2></div>
        <div className="kv">
          <div><b>Name</b></div>
          <div className="row">{me.name}<span className="grow" />{!acct?.root && <button className="btn sm" onClick={rename}>Change</button>}</div>
          {!acct?.root && <>
            <div><b>Email</b></div>
            <div className="row">{acct?.email ?? <span className="muted">not set</span>}
              {acct?.email && !acct.email_verified && <span className="badge">not confirmed</span>}<span className="grow" />
              {acct?.email_change === "admin" ? <span className="small muted">Ask an admin to change it.</span>
                : <button className="btn sm" onClick={changeEmail}>Change</button>}</div>
          </>}
          <div><b>Role</b></div>
          <div className="row"><span className={"badge " + me.role}>{ROLE[me.role]}</span><span className="small muted">{ROLE_NOTE[me.role]}</span></div>
          {acct?.projects && <><div><b>Projects</b></div><div className="small">{acct.projects.join(", ")}</div></>}
        </div>
      </div>

      {acct?.root ? (
        <div className="panel pad small muted">The server's admin account. It signs in with the admin token and has no email or password.</div>
      ) : <div className="panel">
        <div className="panel-head"><h2 className="grow">Sign-in</h2></div>
        <div className="kv">
          <div><b>Password</b></div>
          <div className="row">{acct?.has_password ? "Set" : <span className="muted">not set</span>}<span className="grow" />
            <button className="btn sm" onClick={password}>{acct?.has_password ? "Change" : "Set"}</button></div>
          {(["github", "google"] as const).map((p) => (
            <Fragment key={p}>
              <div><b>{p === "github" ? "GitHub" : "Google"}</b></div>
              <div className="row">{linked(p) ? <span>{linked(p)!.login ?? linked(p)!.email}</span> : <span className="muted">not linked</span>}<span className="grow" />
                {linked(p) ? <button className="btn sm ghost" onClick={() => run(() => api.unlinkIdentity(p), "Unlinked.")}>Unlink</button>
                  : methods?.[p] ? <a className="btn sm" href={`/auth/${p}/start?link=1`}>Link</a>
                  : <span className="small muted">not set up</span>}</div>
            </Fragment>
          ))}
        </div>
      </div>}

      <div className="panel">
        <div className="panel-head"><h2 className="grow">Where you're signed in</h2>
          {sessions.length > 1 && <button className="btn sm" onClick={async () => {
            if (await confirm("Sign out other sessions?", undefined, "Sign out")) run(() => api.endOtherSessions(), "Signed out everywhere else.");
          }}>Sign out others</button>}
        </div>
        <table className="list"><tbody>
          {sessions.map((s) => (
            <tr key={s.id}>
              <td><b>{device(s.agent)}</b>{s.current && <span className="badge" style={{ marginLeft: 8 }}>this browser</span>}
                <div className="tiny muted">active {ago(s.used_at ?? s.created_at)} · signed in {ago(s.created_at)} with {s.how ?? "?"}{s.ip ? ` · ${s.ip}` : ""}</div></td>
              <td style={{ width: 110, textAlign: "right" }}>
                <button className="btn ghost sm" onClick={async () => {
                  await api.endSession(s.id);
                  if (s.current) { window.location.href = "/"; return; }
                  load();
                }}>Sign out</button></td>
            </tr>
          ))}
        </tbody></table>
      </div>

      <div className="panel">
        <div className="panel-head"><h2 className="grow">Appearance</h2>
          <select className="select" style={{ width: 200 }} value={theme} onChange={(e) => setTheme(e.target.value)}>
            <option value="system">Match the system</option><option value="light">Light</option><option value="dark">Dark</option>
          </select>
        </div>
      </div>
    </>
  );
}
