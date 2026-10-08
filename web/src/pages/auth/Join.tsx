// The two pages a link opens without being signed in: accepting an invite (/invite?t=…) and setting a new
// password (/reset?t=…). Both sign the browser in when they're done.
import { FormEvent, useEffect, useState } from "react";
import { api, AuthMethods } from "../../lib/api";
import { Card, Err, errText, Input, ProviderButtons } from "./parts";

const token = () => new URLSearchParams(window.location.search).get("t") ?? "";
const home = () => { window.location.href = "/"; };

function Dead({ title, text }: { title: string; text: string }) {
  return (
    <div className="stack" style={{ gap: 10 }}>
      <h1>{title}</h1>
      <p className="small muted" style={{ margin: 0 }}>{text}</p>
      <a className="btn wide" href="/">Go to sign in</a>
    </div>
  );
}

export function InvitePage() {
  const t = token();
  const [info, setInfo] = useState<Awaited<ReturnType<typeof api.inviteInfo>> | null>(null);
  const [methods, setMethods] = useState<AuthMethods | null>(null);
  const [dead, setDead] = useState("");
  const [v, setV] = useState({ name: "", password: "" });
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    api.inviteInfo(t).then((i) => { setInfo(i); setV((x) => ({ ...x, name: i.email.split("@")[0] })); }).catch((e) => setDead(errText(e)));
    api.authMethods().then(setMethods).catch(() => {});
  }, [t]);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr("");
    try { await api.acceptInvite(t, v.name.trim(), v.password); home(); } catch (ex) { setErr(errText(ex)); setBusy(false); }
  };
  if (dead) return <Card><Dead title="This invite can't be used" text={dead} /></Card>;
  if (!info || !methods) return <Card><p className="muted">Loading…</p></Card>;
  return (
    <Card>
      <div className="stack" style={{ gap: 14 }}>
        <h1>Join litledger</h1>
        <p className="small" style={{ margin: 0 }}><b>{info.invited_by}</b> invited <b>{info.email}</b>{info.role === "admin" ? " as an admin" : ""}.</p>
        <ProviderButtons methods={methods} verb="Continue" query={`invite=${encodeURIComponent(t)}`} />
        {(methods.github || methods.google) && <div className="or"><span>or set a password</span></div>}
        <form className="stack" style={{ gap: 10 }} onSubmit={submit}>
          <Input label="Name" autoComplete="name" value={v.name} onChange={(e) => setV({ ...v, name: e.target.value })} required />
          <Input label="Password" type="password" autoComplete="new-password" value={v.password} minLength={8} required
                 placeholder="at least 8 characters" onChange={(e) => setV({ ...v, password: e.target.value })} autoFocus />
          <Err text={err} />
          <button className="btn primary wide" disabled={busy || v.password.length < 8}>{busy ? "Creating…" : "Create account"}</button>
        </form>
      </div>
    </Card>
  );
}

export function ResetPage() {
  const t = token();
  const [name, setName] = useState<string | null>(null);
  const [dead, setDead] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  useEffect(() => { api.resetInfo(t).then((r) => setName(r.name)).catch((e) => setDead(errText(e))); }, [t]);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr("");
    try {
      const r = await api.reset(t, password);
      if (r.notice) setNotice(r.notice); else home();  // the reset took over an unconfirmed account: say what ended
    } catch (ex) { setErr(errText(ex)); setBusy(false); }
  };
  if (dead) return <Card><Dead title="This link can't be used" text={dead} /></Card>;
  if (notice) return (
    <Card>
      <div className="stack" style={{ gap: 12 }}>
        <h1>Password set</h1>
        <p className="small" role="status" style={{ margin: 0 }}>{notice}</p>
        <a className="btn primary wide" href="/">Continue</a>
      </div>
    </Card>
  );
  if (name === null) return <Card><p className="muted">Loading…</p></Card>;
  return (
    <Card>
      <form className="stack" style={{ gap: 12 }} onSubmit={submit}>
        <h1>Set a new password</h1>
        <p className="small muted" style={{ margin: 0 }}>For {name}. You'll be signed out everywhere else.</p>
        <input type="text" autoComplete="username" value={name} hidden readOnly />
        <Input label="New password" type="password" autoComplete="new-password" value={password} minLength={8} required autoFocus
               placeholder="at least 8 characters" onChange={(e) => setPassword(e.target.value)} />
        <Err text={err} />
        <button className="btn primary wide" disabled={busy || password.length < 8}>{busy ? "Saving…" : "Set password"}</button>
      </form>
    </Card>
  );
}
