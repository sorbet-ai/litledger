import { useCallback, useEffect, useState } from "react";
import { useApp } from "../../app/context";
import { api, Invite, Person } from "../../lib/api";
import { ago } from "../../lib/format";
import { CopyBlock } from "../../ui/CopyBlock";
import { Icon } from "../../ui/Icon";
import { MenuItem, useOverlays } from "../../ui/overlays";

const ROLE: Record<string, string> = { admin: "Admin", member: "Member" };
const csv = (s: string) => s.split(",").map((x) => x.trim()).filter(Boolean);
const days = (iso: string) => Math.max(0, Math.round((new Date(iso).getTime() - Date.now()) / 86400000));

export function PeopleTab() {
  const { me } = useApp();
  const { form, confirm, toast, menu, show } = useOverlays();
  const [people, setPeople] = useState<Person[]>([]);
  const [invites, setInvites] = useState<Invite[]>([]);
  const load = useCallback(() => {
    api.principals().then((r) => setPeople(r.people));
    api.invites().then(setInvites);
  }, []);
  useEffect(load, [load]);
  const run = async (fn: () => Promise<unknown>, done?: string) => {
    try { await fn(); if (done) toast(done); load(); } catch (e) { toast(String(e).replace(/^Error: /, ""), true); }
  };

  const invite = async () => {
    const r = await form({ title: "Invite someone", submit: "Invite", fields: [
      { name: "email", label: "Email", placeholder: "ada@example.com", required: true },
      { name: "role", label: "Role", type: "select", value: "member", options: [
        { value: "member", label: "Member: uses the library" }, { value: "admin", label: "Admin: also manages the server and people" }] },
      { name: "projects", label: "Projects", placeholder: "all", hint: "Comma-separated. Leave empty for all." }] });
    if (!r) return;
    try {
      const out = await api.invite(r.email.trim(), r.role, csv(r.projects));
      load();
      await show("Invite ready", <>
        <p className="small" style={{ margin: 0 }}>{out.sent ? `We emailed ${r.email.trim()}. You can also send the link yourself.` : "This server can't send email, so send them this link."}</p>
        <CopyBlock title="Invite link" text={out.link} note={`Works once, for ${out.days} days.`} />
      </>, true);
    } catch (e) { toast(String(e).replace(/^Error: /, ""), true); }
  };
  const resetLink = async (p: Person) => {
    try {
      const r = await api.resetLink(p.name);
      await show(`Reset link for ${p.name}`, <CopyBlock title="Link" text={r.link} note={`Send it to them. It works once, for ${r.minutes} minutes.`} />, true);
    } catch (e) { toast(String(e), true); }
  };
  const edit = async (p: Person) => {
    const r = await form({ title: `Edit ${p.name}`, submit: "Save", fields: [
      { name: "email", label: "Email", value: p.email ?? "" },
      { name: "projects", label: "Projects", value: (p.projects ?? []).join(", "), placeholder: "all", hint: "Comma-separated. Leave empty for all." }] });
    if (!r) return;
    // Only what changed: an unchanged email is never resent (an empty one is refused for people who sign in with it).
    const body: { email?: string; projects?: string[] | null } = {};
    if (r.email.trim() !== (p.email ?? "")) body.email = r.email.trim();
    if (csv(r.projects).join(",") !== (p.projects ?? []).join(",")) body.projects = csv(r.projects).length ? csv(r.projects) : null;
    if (Object.keys(body).length) run(() => api.updatePrincipal(p.name, body), "Saved.");
  };
  const items = (p: Person): MenuItem[] => {
    const self = p.name === me.name;
    const signOut: MenuItem = { label: "Sign out everywhere", icon: "x", onClick: () => run(() => api.signOutPerson(p.name), `${p.name} is signed out.`) };
    if (p.root) return [{ header: "Signs in with the admin token" }, signOut];
    return [
      p.role === "admin" ? { label: "Make member", icon: "user", onClick: () => run(() => api.updatePrincipal(p.name, { role: "member" }), `${p.name} is a member.`) }
        : { label: "Make admin", icon: "shield", onClick: () => run(() => api.updatePrincipal(p.name, { role: "admin" }), `${p.name} is an admin.`) },
      { label: "Edit email and projects…", icon: "settings", onClick: () => edit(p) },
      ...(p.has_password ? [{ label: "Password reset link…", icon: "key", onClick: () => resetLink(p) }] : []),
      signOut,
      ...(!self ? [{ sep: true },
        p.disabled ? { label: "Enable", icon: "check", onClick: () => run(() => api.updatePrincipal(p.name, { disabled: false }), `${p.name} can sign in again.`) }
          : { label: "Disable", icon: "lock", danger: true, onClick: async () => {
            if (await confirm(`Disable ${p.name}?`, <p className="small muted" style={{ margin: 0 }}>They're signed out, and their tokens and apps stop working until you enable them again.</p>, "Disable", true))
              run(() => api.updatePrincipal(p.name, { disabled: true }), `${p.name} is disabled.`);
          } },
        { label: "Remove…", icon: "trash", danger: true, onClick: async () => {
          if (await confirm(`Remove ${p.name}?`, <p className="small muted" style={{ margin: 0 }}>Their account, tokens and apps are removed for good. What they added to the library stays.</p>, "Remove", true))
            run(() => api.removePrincipal(p.name), `Removed ${p.name}.`);
        } }] : []),
    ];
  };

  return (
    <>
      <div className="panel">
        <div className="panel-head">
          <div className="grow"><h2>People</h2><div className="small muted">Admins manage the server and people. Members use the library. New accounts are members.</div></div>
          <button className="btn primary" onClick={invite}><Icon name="plus" />Invite</button>
        </div>
        <table className="list"><tbody>
          {people.map((p) => (
            <tr key={p.name} onContextMenu={(e) => menu(e, items(p))}>
              <td>
                <div className="row" style={{ gap: 6 }}><b>{p.name}</b>{p.name === me.name && <span className="badge">you</span>}
                  {p.role !== "member" && <span className={"badge " + p.role}>{ROLE[p.role]}</span>}
                  {p.root && <span className="badge">admin token</span>}
                  {p.disabled && <span className="badge off">disabled</span>}</div>
                <div className="tiny muted">{p.root ? "The server's admin account" : <>{p.email ?? "no email"}{p.email && !p.email_verified ? " (not confirmed)" : ""} · {[p.has_password && "password", ...p.identities.map((i) => `${i.provider === "github" ? "GitHub" : "Google"}${i.login ? ` (${i.login})` : ""}`)].filter(Boolean).join(", ") || "no way to sign in yet"}</>}
                  {p.projects ? ` · ${p.projects.join(", ")}` : ""}{p.agents ? ` · ${p.agents} app${p.agents > 1 ? "s and tokens" : " or token"}` : ""}</div>
              </td>
              <td className="small muted" style={{ width: 130 }}>{p.last_seen_at ? `seen ${ago(p.last_seen_at)}` : "never signed in"}</td>
              <td style={{ width: 44 }}>
                <button className="btn ghost sm icon" title="More" aria-label={`Actions for ${p.name}`} aria-haspopup="menu" onClick={(e) => {
                  const r = e.currentTarget.getBoundingClientRect();  // below the button, for a click or the keyboard
                  menu({ clientX: r.left, clientY: r.bottom + 4 }, items(p));
                }}><Icon name="more" /></button>
              </td>
            </tr>
          ))}
        </tbody></table>
      </div>
      {invites.length > 0 && (
        <div className="panel">
          <div className="panel-head"><h2 className="grow">Invited</h2></div>
          <table className="list"><tbody>
            {invites.map((i) => (
              <tr key={i.id}>
                <td><div className="row" style={{ gap: 6 }}><b>{i.email}</b>{i.role === "admin" && <span className="badge admin">Admin</span>}</div>
                  <div className="tiny muted">by {i.invited_by} · expires in {days(i.expires_at)} days{i.projects ? ` · ${i.projects.join(", ")}` : ""}</div></td>
                <td style={{ width: 90, textAlign: "right" }}>
                  <button className="btn ghost sm" onClick={() => run(() => api.revokeInvite(i.id), "Invite revoked.")}>Revoke</button></td>
              </tr>
            ))}
          </tbody></table>
        </div>
      )}
    </>
  );
}
