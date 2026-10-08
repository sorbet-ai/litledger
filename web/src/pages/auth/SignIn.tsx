import { FormEvent, useEffect, useState } from "react";
import { api, AuthMethods } from "../../lib/api";
import { Card, Err, errText, Input, ProviderButtons, providerName } from "./parts";

type Mode = "signin" | "signup" | "forgot" | "token";

/** Why a Google/GitHub round trip came back to the sign-in page (?error=…&provider=…). */
function returnedError(): string {
  const q = new URLSearchParams(window.location.search);
  const who = providerName(q.get("provider"));
  switch (q.get("error")) {
    case "no_account": return `There's no account for that ${who} login here. Ask an admin for an invite.`;
    case "email_taken": return `An account already uses that email. Sign in with its password (or reset it), then link ${who} under Settings → Profile.`;
    case "limited": return "Too many attempts. Wait a few minutes.";
    case "disabled": return "This account is disabled. Ask an admin.";
    case "invite": return "That invite has expired or was already used.";
    case "expired": return "That took too long. Try again.";
    case "cancelled": return "Sign-in was cancelled.";
    case "failed": return `${who} sign-in didn't work. Try again, or ask an admin to check its setup.`;
    case "not_set_up": return `${who} sign-in isn't set up here.`;
    default: return "";
  }
}

/** The sign-in page, with creating an account, a forgotten password and the admin's token sign-in. */
export default function SignIn({ next, onDone }: { next: string; onDone: () => void }) {
  const [methods, setMethods] = useState<AuthMethods | null>(null);
  const [mode, setMode] = useState<Mode>("signin");
  const [err, setErr] = useState(returnedError);
  useEffect(() => {
    api.authMethods().then(setMethods).catch((e) => setErr(errText(e)));
    if (new URLSearchParams(window.location.search).has("error")) window.history.replaceState(null, "", window.location.pathname);
  }, []);
  const target = next.includes("error=") ? "/" : next;
  if (!methods) return <Card><Err text={err} />{!err && <p className="muted">Loading…</p>}</Card>;
  const providers = methods.github || methods.google;
  const canSignUp = methods.password_signup || (methods.signup !== "invited" && providers);
  const go = (m: Mode) => { setErr(""); setMode(m); };
  const back = <div className="foot"><button className="linkbtn" onClick={() => go("signin")}>Back to sign in</button></div>;
  return (
    <Card>
      {mode === "forgot" ? <>{<Forgot />}{back}</> : mode === "token" ? <>{<AdminToken onDone={onDone} />}{back}</> : mode === "signup" ? (
        <div className="stack" style={{ gap: 14 }}>
          <h1>Create an account</h1>
          {methods.signup === "domains" && <p className="small muted" style={{ margin: 0 }}>For emails at {methods.domains.join(" or ")}.</p>}
          {methods.signup !== "invited" && <ProviderButtons methods={methods} verb="Sign up" query={`next=${encodeURIComponent(target)}`} />}
          {methods.signup !== "invited" && providers && methods.password_signup && <div className="or"><span>or</span></div>}
          {methods.password_signup && <SignUp onDone={onDone} />}
          <div className="foot">Have an account? <button className="linkbtn" onClick={() => go("signin")}>Sign in</button></div>
        </div>
      ) : (
        <div className="stack" style={{ gap: 14 }}>
          <h1>Sign in</h1>
          <ProviderButtons methods={methods} verb="Continue" query={`next=${encodeURIComponent(target)}`} />
          {providers && <div className="or"><span>or</span></div>}
          <Password onDone={onDone} err={err} setErr={setErr} forgot={() => go("forgot")} />
          <div className="foot">
            {canSignUp ? <>No account? <button className="linkbtn" onClick={() => go("signup")}>Create one</button></> : "No account? Ask an admin for an invite."}
          </div>
          <div className="foot tiny"><button className="linkbtn muted" onClick={() => go("token")}>Sign in with admin token</button></div>
        </div>
      )}
    </Card>
  );
}

function Password({ onDone, err, setErr, forgot }: { onDone: () => void; err: string; setErr: (s: string) => void; forgot: () => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr("");
    try { await api.signIn(email.trim(), password); onDone(); } catch (ex) { setErr(errText(ex)); setBusy(false); }
  };
  return (
    <form className="stack" style={{ gap: 10 }} onSubmit={submit}>
      <Input label="Email" type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} autoFocus required />
      <Input label="Password" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required
             aside={<button type="button" className="linkbtn" onClick={forgot}>Forgot password?</button>} />
      <Err text={err} />
      <button className="btn primary wide" disabled={busy || !email.trim() || !password}>{busy ? "Signing in…" : "Sign in"}</button>
    </form>
  );
}

function SignUp({ onDone }: { onDone: () => void }) {
  const [v, setV] = useState({ name: "", email: "", password: "" });
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState("");
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr("");
    try {
      const r = await api.signUp(v.name.trim(), v.email.trim(), v.password);
      if (r.confirm) { setSent(r.confirm); setBusy(false); } else onDone();
    } catch (ex) { setErr(errText(ex)); setBusy(false); }
  };
  if (sent) return <div className="sent">We sent a link to {sent}. Open it to finish.</div>;
  const set = (k: keyof typeof v) => (e: { target: { value: string } }) => setV({ ...v, [k]: e.target.value });
  return (
    <form className="stack" style={{ gap: 10 }} onSubmit={submit}>
      <Input label="Name" autoComplete="name" value={v.name} onChange={set("name")} autoFocus required placeholder="e.g. ada" />
      <Input label="Email" type="email" autoComplete="username" value={v.email} onChange={set("email")} required />
      <Input label="Password" type="password" autoComplete="new-password" value={v.password} onChange={set("password")} required minLength={8}
             placeholder="at least 8 characters" />
      <Err text={err} />
      <button className="btn primary wide" disabled={busy || !v.email.trim() || v.password.length < 8}>{busy ? "Creating…" : "Create account"}</button>
    </form>
  );
}

function Forgot() {
  const [email, setEmail] = useState("");
  const [result, setResult] = useState<{ sent: boolean; command?: string } | null>(null);
  const [err, setErr] = useState("");
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setErr("");
    try { setResult(await api.forgot(email.trim())); } catch (ex) { setErr(errText(ex)); }
  };
  return (
    <div className="stack" style={{ gap: 14 }}>
      <h1>Reset your password</h1>
      {result?.sent ? (
        <div className="sent">If an account uses {email.trim()}, a reset link is on its way. It works for an hour.</div>
      ) : result ? (
        <div className="stack" style={{ gap: 8 }}>
          <p className="small" style={{ margin: 0 }}>This server can't send email. Ask an admin for a reset link. They make one under Admin → People, or run this on the server:</p>
          <code className="cmd">{result.command}</code>
        </div>
      ) : (
        <form className="stack" style={{ gap: 10 }} onSubmit={submit}>
          <Input label="Email" type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} autoFocus required />
          <Err text={err} />
          <button className="btn primary wide" disabled={!email.trim()}>Send reset link</button>
        </form>
      )}
    </div>
  );
}

function AdminToken({ onDone }: { onDone: () => void }) {
  const [token, setToken] = useState("");
  const [err, setErr] = useState("");
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setErr("");
    try { await api.tokenSignIn(token.trim()); onDone(); } catch (ex) { setErr(errText(ex)); }
  };
  return (
    <form className="stack" style={{ gap: 12 }} onSubmit={submit}>
      <h1>Sign in as admin</h1>
      <Input label="Admin token" type="password" autoComplete="off" value={token} onChange={(e) => setToken(e.target.value)} autoFocus required
             placeholder="ll_…" />
      <p className="tiny muted" style={{ margin: 0 }}>On the server: <code>docker compose exec litledger cat /data/tokens/admin</code></p>
      <Err text={err} />
      <button className="btn primary wide" disabled={!token.trim()}>Sign in</button>
    </form>
  );
}
