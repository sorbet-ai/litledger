import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, OAuthRequest } from "../lib/api";

/** The consent page an app (Claude Code, Codex, …) sends the browser to when it connects. */
export default function Authorize({ projects }: { projects: { id: string; works: number }[] }) {
  const [params] = useSearchParams();
  const id = params.get("request") ?? "";
  const [req, setReq] = useState<OAuthRequest | null>(null);
  const [err, setErr] = useState("");
  const [scope, setScope] = useState<"all" | "some">("all");
  const [picked, setPicked] = useState<string[]>([]);
  const [done, setDone] = useState<"allowed" | "denied" | null>(null);
  useEffect(() => { api.oauthRequest(id).then(setReq).catch((e) => setErr(String(e).replace(/^.*?: /, ""))); }, [id]);

  const decide = async (allow: boolean) => {
    try {
      const r = await api.oauthDecide(id, allow, allow && scope === "some" ? picked : undefined);
      setDone(allow ? "allowed" : "denied");
      window.location.href = r.redirect;
    } catch (e) { setErr(String(e).replace(/^.*?: /, "")); }
  };

  return (
    <div className="signin">
      <div className="signin-card">
        <div className="brand"><span className="brand-mark" />litledger</div>
        {err ? (
          <div className="stack" style={{ gap: 8 }}><h1>Can't continue</h1><p className="small muted" style={{ margin: 0 }}>{err}</p></div>
        ) : !req ? (
          <p className="muted">Loading…</p>
        ) : done ? (
          <div className="stack" style={{ gap: 8 }}><h1>{done === "allowed" ? `${req.client_name} is connected` : "Declined"}</h1>
            <p className="small muted" style={{ margin: 0 }}>You can close this tab and go back to {req.client_name}.</p></div>
        ) : (
          <div className="stack" style={{ gap: 14 }}>
            <h1>Connect {req.client_name}?</h1>
            <p style={{ margin: 0 }}><b>{req.client_name}</b> wants to read and add to your library and maps. It can't change settings.</p>
            <dl className="fields small">
              <dt className="muted">App</dt><dd>{req.verified ? <>{req.client_name} <span className="badge verified">verified by {new URL(req.client_id).host}</span></> : <>{req.client_name} <span className="badge">self-registered</span></>}</dd>
              <dt className="muted">Returns to</dt><dd>{req.redirect_host}{req.loopback && <span className="muted"> (this computer)</span>}</dd>
            </dl>
            <div className="stack" style={{ gap: 6 }}>
              <label className="row small" style={{ gap: 8 }}><input type="radio" checked={scope === "all"} onChange={() => setScope("all")} />All projects</label>
              <label className="row small" style={{ gap: 8 }}><input type="radio" checked={scope === "some"} onChange={() => setScope("some")} />Only some projects</label>
              {scope === "some" && (
                <div className="row" style={{ flexWrap: "wrap", gap: 6, paddingLeft: 22 }}>
                  {projects.map((p) => (
                    <label key={p.id} className={"chip" + (picked.includes(p.id) ? " on" : "")} style={{ cursor: "pointer" }}>
                      <input type="checkbox" hidden checked={picked.includes(p.id)} onChange={() => setPicked((x) => (x.includes(p.id) ? x.filter((y) => y !== p.id) : [...x, p.id]))} />{p.id}
                    </label>
                  ))}
                </div>
              )}
            </div>
            <div className="row" style={{ gap: 8 }}>
              <button className="btn ghost" onClick={() => decide(false)}>Deny</button><span className="grow" />
              <button className="btn primary" disabled={scope === "some" && !picked.length} onClick={() => decide(true)}>Allow</button>
            </div>
            <p className="tiny muted" style={{ margin: 0 }}>Disconnect it any time under Settings → Connected apps.</p>
          </div>
        )}
      </div>
    </div>
  );
}
