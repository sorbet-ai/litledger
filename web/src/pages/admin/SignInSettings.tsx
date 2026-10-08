import { api } from "../../lib/api";
import { useOverlays } from "../../ui/overlays";
import { ConfigPanel } from "./config";

export function SignInTab() {
  const { toast } = useOverlays();
  const origin = window.location.origin;
  const test = async () => {
    try { const r = await api.mailTest(); toast(r.message, !r.ok); } catch (e) { toast(String(e), true); }
  };
  return (
    <>
      <ConfigPanel title="Sign-up" keys={["SIGNUP", "SIGNUP_DOMAINS"]} />
      <ConfigPanel title="Google and GitHub" keys={["GITHUB_CLIENT_ID", "GITHUB_CLIENT_SECRET", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"]}
                   sub={<>Callback URLs: <code>{origin}/auth/github/callback</code> and <code>{origin}/auth/google/callback</code></>} />
      <ConfigPanel title="Email" keys={["SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM"]}
                   sub="Sends invites and password resets." actions={<button className="btn" onClick={test}>Send a test email</button>} />
      <ConfigPanel title="Apps" keys={["OAUTH_CLIENT_HOSTS"]} />
    </>
  );
}
