// A panel of server settings (the config registry) that admins edit and save together.
import { ReactNode, useCallback, useEffect, useState } from "react";
import { useApp } from "../../app/context";
import { api, ConfigField } from "../../lib/api";
import { useOverlays } from "../../ui/overlays";

const CHOICE_LABELS: Record<string, Record<string, string>> = {
  FETCH_FULLTEXT: { on_demand: "When a paper is first read", on_add: "As soon as a paper is captured" },
  QUIET_RECOMMENDATIONS: { "0": "Show setup warnings", "1": "Silence them" },
  ALLOW_PRIVATE_FETCH: { "0": "Public internet only (safe)", "1": "Also intranet and private addresses" },
  SIGNUP: { anyone: "Anyone", domains: "People with an email at these domains", invited: "Only people who are invited" },
};

export function SettingInput({ f, value, onChange, disabled }: { f: ConfigField; value: string; onChange: (v: string) => void; disabled?: boolean }) {
  if (f.choices && f.multi) {
    const cur = new Set((value || f.value || f.default || "").split(",").map((x) => x.trim()).filter(Boolean));
    return (
      <div className="row" style={{ flexWrap: "wrap", gap: 6 }}>
        {f.choices.map((c) => (
          <label key={c} className={"chip" + (cur.has(c) ? " on" : "")} style={{ cursor: disabled ? "default" : "pointer" }}>
            <input type="checkbox" hidden disabled={disabled} checked={cur.has(c)} onChange={() => {
              const next = new Set(cur);
              if (next.has(c)) next.delete(c); else next.add(c);
              onChange([...next].join(","));
            }} />{c}
          </label>
        ))}
      </div>
    );
  }
  if (f.choices) {
    return (
      <select className="select" disabled={disabled} value={value || f.value || f.default || ""} onChange={(e) => onChange(e.target.value)}>
        {f.choices.map((c) => <option key={c} value={c}>{CHOICE_LABELS[f.key]?.[c] ?? c}</option>)}
      </select>
    );
  }
  return <input className="input" type={f.secret ? "password" : f.type === "int" ? "number" : "text"} disabled={disabled} autoComplete="off"
                placeholder={f.value || (f.default ? `default: ${f.default}` : "not set")} value={value} onChange={(e) => onChange(e.target.value)} />;
}

export function ConfigPanel({ title, sub, keys, actions }: { title: string; sub?: ReactNode; keys: string[]; actions?: ReactNode }) {
  const { refresh } = useApp();
  const { toast } = useOverlays();
  const [fields, setFields] = useState<ConfigField[]>([]);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => { api.config().then((c) => setFields(c.fields)); }, []);
  useEffect(load, [load]);
  const shown = keys.map((k) => fields.find((f) => f.key === k)).filter(Boolean) as ConfigField[];
  const save = async (values: Record<string, string | null>) => {
    setBusy(true);
    try {
      setFields((await api.saveConfig(values)).fields);
      setDraft({});
      toast("Saved");
      refresh();
    } catch (e) { toast(String(e), true); }
    setBusy(false);
  };
  return (
    <div className="panel">
      <div className="panel-head">
        <div className="grow"><h2>{title}</h2>{sub && <div className="small muted">{sub}</div>}</div>
        {actions}
        <button className="btn primary" disabled={!Object.keys(draft).length || busy} onClick={() => save(draft)}>{busy ? "Saving…" : "Save"}</button>
      </div>
      <table className="list"><tbody>
        {shown.map((f) => (
          <tr key={f.key}>
            <td style={{ width: "38%" }}><b>{f.label}</b>{f.help && <div className="small muted">{f.help}</div>}</td>
            <td><div className="row">
              <div className="grow"><SettingInput f={f} value={draft[f.key] ?? ""} onChange={(v) => setDraft({ ...draft, [f.key]: v })} /></div>
              {f.set && <button className="btn sm ghost" title="Back to the default" onClick={() => save({ [f.key]: null })}>Clear</button>}
            </div></td>
          </tr>
        ))}
      </tbody></table>
    </div>
  );
}
