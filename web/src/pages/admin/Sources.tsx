import { useCallback, useEffect, useMemo, useState } from "react";
import { NavLink } from "react-router-dom";
import { useApp } from "../../app/context";
import { api, ConfigField, Provider, Warning } from "../../lib/api";
import { Dialog } from "../../ui/Dialog";
import { Icon } from "../../ui/Icon";
import { MenuItem, useOverlays } from "../../ui/overlays";

const KEY_HINT: Record<string, string> = { email: "needs the contact email (Admin → Server)", key: "needs an API key", account: "needs an account" };

export function SourcesTab() {
  const { refresh, me } = useApp();
  const { menu, confirm, toast } = useOverlays();
  const [data, setData] = useState<{ providers: Provider[]; warnings: Warning[] } | null>(null);
  const [fields, setFields] = useState<ConfigField[]>([]);
  const [dialog, setDialog] = useState<{ mode: "add" | "keys"; id?: string } | null>(null);
  const [tests, setTests] = useState<Record<string, { ok: boolean; message: string }>>({});
  const editable = me.admin;
  const load = useCallback(() => {
    api.providers().then(setData);
    api.config().then((c) => setFields(c.fields));
  }, []);
  useEffect(load, [load]);

  const test = async (id: string) => {
    setTests((t) => ({ ...t, [id]: { ok: true, message: "Testing…" } }));
    const r = await api.testSource(id);
    setTests((t) => ({ ...t, [id]: r }));
    return r;
  };
  const remove = async (p: Provider, forget: boolean) => {
    const ok = await confirm(`Remove ${p.name}?`, <p className="small muted" style={{ margin: 0 }}>
      Searches, lookups and full-text fetches stop using it. {forget ? "Its keys are deleted." : "Its keys are kept, so adding it back is one click."}</p>, "Remove", true);
    if (!ok) return;
    await api.setSource(p.id, { action: "remove", forget_keys: forget });
    toast(`Removed ${p.name}`);
    load();
    refresh();
  };
  const added = data?.providers.filter((p) => p.status === "enabled") ?? [];
  const available = data?.providers.filter((p) => p.status !== "enabled") ?? [];
  const rowMenu = (p: Provider): MenuItem[] => [
    { label: "Test connection", icon: "check", onClick: () => test(p.id) },
    ...(p.keys.length || p.optional_keys.length ? [{ label: "Keys…", icon: "settings", disabled: !editable, onClick: () => setDialog({ mode: "keys", id: p.id }) }] : []),
    { sep: true },
    { label: "Remove", icon: "x", danger: true, disabled: !editable, onClick: () => remove(p, false) },
    ...(p.keys.length || p.optional_keys.length ? [{ label: "Remove and delete keys", icon: "trash", danger: true, disabled: !editable, onClick: () => remove(p, true) }] : []),
  ];

  return (
    <>
      {data?.warnings.map((w) => (
        <div key={w.provider} className="warn row" style={{ alignItems: "flex-start" }}>
          <div className="grow"><b>{w.headline}</b> {w.impact}.</div>
          {editable && w.provider !== "contact_email" && <button className="btn sm" onClick={() => setDialog({ mode: "keys", id: w.provider })}>Add key</button>}
          {editable && w.provider === "contact_email" && <NavLink className="btn sm" to="/admin/server">Set email</NavLink>}
        </div>
      ))}
      <div className="panel">
        <div className="row" style={{ padding: "12px 16px", borderBottom: "1px solid var(--line)" }}>
          <div className="grow">
            <h2>Sources in use</h2>
          </div>
          {editable ? <button className="btn primary" onClick={() => setDialog({ mode: "add" })}><Icon name="plus" />Add source</button>
            : <span className="small muted">Only admins can change sources.</span>}
        </div>
        <table className="list"><tbody>
          {added.map((p) => (
            <tr key={p.id} onContextMenu={(e) => { e.preventDefault(); menu(e, rowMenu(p)); }}>
              <td>
                <div className="row" style={{ gap: 8 }}><b>{p.name}</b>
                  {p.discover_default && <span className="badge">searched by default</span>}
                  {p.note && <span className="badge warnish">{p.note}</span>}
                </div>
                <div className="small muted">{p.about}</div>
                {tests[p.id] && <div className="small" style={{ color: tests[p.id].ok ? "var(--ok)" : "var(--danger)", marginTop: 3 }}>{tests[p.id].message}</div>}
                {p.stats?.last_error && !tests[p.id] && <div className="small" style={{ color: "var(--danger)", marginTop: 3 }}>Last error: {p.stats.last_error.slice(0, 100)}</div>}
              </td>
              <td className="small muted" style={{ width: 130, textAlign: "right" }}>{p.stats ? `${p.stats.ok} ok · ${p.stats.errors} failed` : "not used yet"}</td>
              <td style={{ width: 44 }}>
                <button className="btn ghost sm icon" title="More" aria-label="More" onClick={(e) => menu(e, rowMenu(p))}><Icon name="more" /></button>
              </td>
            </tr>
          ))}
        </tbody></table>
      </div>
      {available.length > 0 && (
        <div className="panel">
          <div style={{ padding: "12px 16px", borderBottom: "1px solid var(--line)" }}>
            <h2>Not in use</h2>
          </div>
          <table className="list"><tbody>
            {available.map((p) => (
              <tr key={p.id}>
                <td>
                  <div className="row" style={{ gap: 8 }}><b>{p.name}</b>
                    {p.recommendation === "strongly-recommended" && <span className="badge verified">recommended</span>}
                    {p.status === "disabled" && <span className="badge">removed</span>}
                  </div>
                  <div className="small muted">{p.about}{p.status === "missing_credential" && KEY_HINT[p.auth] ? ` · ${KEY_HINT[p.auth]}` : ""}</div>
                </td>
                <td style={{ width: 90, textAlign: "right" }}>
                  {editable && <button className="btn sm" onClick={() => setDialog({ mode: "add", id: p.id })}>Add…</button>}
                </td>
              </tr>
            ))}
          </tbody></table>
        </div>
      )}
      {dialog && data && (
        <SourceDialog mode={dialog.mode} initial={dialog.id} providers={data.providers} fields={fields}
                      onClose={() => setDialog(null)} onDone={async (id, verb) => {
                        setDialog(null);
                        load();
                        refresh();
                        const r = await test(id);
                        const name = data.providers.find((p) => p.id === id)?.name ?? id;
                        toast(r.ok ? `${verb} ${name}. Test passed.` : `${verb} ${name}, but the test failed: ${r.message}`, !r.ok);
                      }} />
      )}
    </>
  );
}

function SourceDialog({ mode, initial, providers, fields, onClose, onDone }: {
  mode: "add" | "keys"; initial?: string; providers: Provider[]; fields: ConfigField[];
  onClose: () => void; onDone: (id: string, verb: string) => void;
}) {
  const choices = useMemo(() => (mode === "add" ? providers.filter((p) => p.status !== "enabled") : providers), [mode, providers]);
  const [id, setId] = useState(initial ?? choices.find((p) => p.recommendation === "strongly-recommended")?.id ?? choices[0]?.id ?? "");
  const [vals, setVals] = useState<Record<string, string>>({});
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const p = providers.find((x) => x.id === id);
  const field = (k: string) => fields.find((f) => f.key === k);
  const required = p && !p.keyless_ok ? p.keys : [];
  const optional = p ? [...(p.keyless_ok ? p.keys : []), ...p.optional_keys] : [];
  const missing = required.some((k) => !vals[k]?.trim() && !field(k)?.set);
  const groups = [
    { label: "Recommended", items: choices.filter((x) => x.recommendation !== "optional") },
    { label: "Needs a key or account", items: choices.filter((x) => x.recommendation === "optional" && x.auth !== "none" && x.auth !== "email") },
    { label: "More sources", items: choices.filter((x) => x.recommendation === "optional" && (x.auth === "none" || x.auth === "email")) },
  ].filter((g) => g.items.length);

  const submit = async () => {
    if (!p || missing) return;
    setBusy(true);
    setErr("");
    const values = Object.fromEntries(Object.entries(vals).filter(([, v]) => v.trim()).map(([k, v]) => [k, v.trim()]));
    try {
      if (mode === "add") {
        const r = await api.setSource(p.id, { action: "add", values });
        if (r.status !== "enabled") { setErr(`Not added: ${r.note || r.status}`); setBusy(false); return; }
        onDone(p.id, "Added");
      } else {
        if (Object.keys(values).length) await api.saveConfig(values);
        onDone(p.id, "Saved keys for");
      }
    } catch (ex) {
      setErr(String(ex));
      setBusy(false);
    }
  };
  const keyInput = (k: string, req: boolean) => {
    const f = field(k);
    return (
      <label className="field" key={k}>
        <span className="row" style={{ gap: 6 }}>{f?.label ?? k}{!req && <span className="tiny muted">optional</span>}</span>
        <input className="input" type={f?.secret === false ? "text" : "password"} autoComplete="off" value={vals[k] ?? ""}
               placeholder={f?.set ? `saved (${f.value}); type to replace` : req ? "required" : "leave empty to use without"}
               onChange={(e) => setVals({ ...vals, [k]: e.target.value })} />
        {f?.help && <span className="hint">{f.help}</span>}
      </label>
    );
  };
  return (
    <Dialog title={mode === "add" ? "Add a source" : `Keys for ${p?.name ?? ""}`} onClose={onClose} onSubmit={submit}>
      {mode === "add" && (
        <label className="field">Source
          <select className="select" value={id} onChange={(e) => { setId(e.target.value); setVals({}); setErr(""); }} autoFocus>
            {groups.map((g) => (
              <optgroup key={g.label} label={g.label}>
                {g.items.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
              </optgroup>
            ))}
          </select>
        </label>
      )}
      {p && (
        <div className="source-card">
          <div>{p.about}</div>
          <div className="row" style={{ gap: 5, flexWrap: "wrap", marginTop: 6 }}>
            {p.capabilities.map((c) => <span key={c} className="badge">{c}</span>)}
          </div>
          {p.impact && <div className="small muted" style={{ marginTop: 6 }}>Without it: {p.impact}.</div>}
          {p.auth === "email" && <div className="small muted" style={{ marginTop: 6 }}>Uses the contact email from Admin → Server.</div>}
        </div>
      )}
      {p && required.map((k) => keyInput(k, true))}
      {p && optional.map((k) => keyInput(k, false))}
      {p?.key_url && (required.length > 0 || optional.length > 0) && (
        <a className="small" href={p.key_url} target="_blank" rel="noreferrer"><Icon name="open" size={13} /> Get a key from {new URL(p.key_url).host}</a>
      )}
      {p && !required.length && !optional.length && mode === "add" && <div className="small muted">No key needed.</div>}
      {err && <div className="small" style={{ color: "var(--danger)" }}>{err}</div>}
      <div className="actions">
        <button type="button" className="btn ghost" onClick={onClose}>Cancel</button>
        <button type="submit" className="btn primary" disabled={!p || missing || busy}>{busy ? "Saving…" : mode === "add" ? "Add source" : "Save keys"}</button>
      </div>
    </Dialog>
  );
}
