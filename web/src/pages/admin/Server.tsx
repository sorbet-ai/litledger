import { useCallback, useEffect, useState } from "react";
import { api } from "../../lib/api";
import { useOverlays } from "../../ui/overlays";
import { ConfigPanel } from "./config";

export function ServerTab() {
  return <ConfigPanel title="Server" keys={["PUBLIC_URL", "CONTACT_EMAIL", "FETCH_FULLTEXT", "MAX_UPLOAD_MB", "TOOLS", "QUIET_RECOMMENDATIONS", "ALLOW_PRIVATE_FETCH"]} />;
}

export function BackupsTab() {
  const { toast } = useOverlays();
  const [data, setData] = useState<{ folder: string; backups: { name: string; size: number; at: string }[] } | null>(null);
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => { api.backups().then(setData).catch(() => {}); }, []);
  useEffect(load, [load]);
  const now = async () => {
    setBusy(true);
    try { const r = await api.backupNow(); toast(`Backed up: ${r.name}`); load(); } catch (e) { toast(String(e).replace(/^Error: /, ""), true); }
    setBusy(false);
  };
  return (
    <>
      <div className="panel">
        <div className="panel-head">
          <div className="grow"><h2>Backups</h2><div className="small muted">In {data?.folder ?? "…"}</div></div>
          <button className="btn" disabled={busy} onClick={now}>{busy ? "Backing up…" : "Back up now"}</button>
        </div>
        <table className="list"><tbody>
          {data?.backups.map((b) => (
            <tr key={b.name}>
              <td><a href={`/api/v1/backups/${encodeURIComponent(b.name)}`} download>{b.name}</a></td>
              <td className="small muted" style={{ width: 120, textAlign: "right" }}>{(b.size / 1048576).toFixed(1)} MB</td>
              <td className="small muted" style={{ width: 200 }}>{new Date(b.at).toLocaleString()}</td>
            </tr>
          ))}
          {data && !data.backups.length && <tr><td className="small muted">No backups yet.</td></tr>}
        </tbody></table>
      </div>
      <ConfigPanel title="Schedule" keys={["BACKUP_EVERY_HOURS", "BACKUP_KEEP", "BACKUP_DIR"]} />
    </>
  );
}
