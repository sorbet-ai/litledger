import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useApp } from "../app/context";
import { api, Entity, KindInfo, ResultRow } from "../lib/api";
import { plural } from "../lib/format";
import { THINKING } from "../lib/kinds";
import { useCopy } from "../ui/CopyBlock";
import { Icon } from "../ui/Icon";
import { MenuItem, rowKeys, useOverlays } from "../ui/overlays";
import { KindLabel, usePicker } from "../ui/pickers";

function Leaderboard({ query }: { query: string }) {
  const [rows, setRows] = useState<ResultRow[]>([]);
  const { tick } = useApp();
  useEffect(() => { api.results({ benchmark: query || undefined }).then((r) => setRows(r.rows)); }, [query, tick]);
  const boards = useMemo(() => {
    const g: Record<string, ResultRow[]> = {};
    rows.forEach((r) => { (g[`${r.benchmark || "?"}\u0000${r.metric || "?"}`] ??= []).push(r); });
    return Object.entries(g).map(([key, list]) => {
      const [bench, metric] = key.split("\u0000");
      const lower = list.some((r) => r.higher_is_better === false || String(r.higher_is_better) === "false");
      const col = (r: ResultRow) => [r.setting, r.split].filter(Boolean).join(" ") || "value";
      const cols = [...new Set(list.map(col))];
      const methods = [...new Set(list.map((r) => `${r.method ?? "?"}${r.model_size ? ` (${r.model_size})` : ""}`))];
      const cell: Record<string, ResultRow> = {};
      list.forEach((r) => { cell[`${r.method ?? "?"}${r.model_size ? ` (${r.model_size})` : ""}\u0000${col(r)}`] = r; });
      const best: Record<string, number> = {};
      cols.forEach((c) => {
        const vals = methods.map((m) => parseFloat(String(cell[`${m}\u0000${c}`]?.value ?? "NaN"))).filter((v) => !Number.isNaN(v));
        if (vals.length > 1) best[c] = lower ? Math.min(...vals) : Math.max(...vals);
      });
      const papers = [...new Set(list.map((r) => r.paper).filter(Boolean))] as string[];
      return { bench, metric, lower, cols, methods, cell, best, papers };
    });
  }, [rows]);
  if (!rows.length) return <div className="empty"><b>No results yet</b>Ask an agent to extract a paper.</div>;
  return (
    <div className="stack" style={{ gap: 18 }}>
      {boards.map((b) => (
        <div key={b.bench + b.metric} className="panel" style={{ overflowX: "auto" }}>
          <div className="row" style={{ padding: "10px 12px", borderBottom: "1px solid var(--line)" }}>
            <b className="grow">{b.bench}</b>
            <span className="small muted">{b.metric}{b.lower ? ", lower is better" : ""}</span>
            {b.papers.map((p) => <Link key={p} to={`/work/${encodeURIComponent(p)}`} className="key">{p}</Link>)}
          </div>
          <table className="list board">
            <thead><tr><th>Method</th>{b.cols.map((c) => <th key={c} style={{ textAlign: "right" }}>{c}</th>)}</tr></thead>
            <tbody>
              {b.methods.map((m) => {
                const role = b.cols.map((c) => b.cell[`${m}\u0000${c}`]?.role).find(Boolean);
                return (
                  <tr key={m}>
                    <td><b>{m}</b>{role && <span className="badge" style={{ marginLeft: 8 }}>{role}</span>}</td>
                    {b.cols.map((c) => {
                      const r = b.cell[`${m}\u0000${c}`];
                      const v = r ? parseFloat(String(r.value)) : NaN;
                      return <td key={c} className="v" title={r?.paper ?? ""}>{r ? <span className={v === b.best[c] ? "best" : ""}>{String(r.value)}</span> : <span className="muted">–</span>}</td>;
                    })}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ))}
    </div>
  );
}

function Detail({ e, kinds, reload }: { e: Entity; kinds: KindInfo[]; reload: () => void }) {
  const { form, confirm, toast } = useOverlays();
  const { pick } = usePicker();
  const nav = useNavigate();
  const info = kinds.find((k) => k.name === e.kind);
  const edit = async () => {
    const fields = [{ name: "body", label: "Description", type: "textarea" as const, value: e.body },
                    { name: "aliases", label: "Aliases", value: e.aliases.join(", "), hint: "Comma-separated" },
                    ...(info?.fields ?? []).filter((f) => f !== "aliases").map((f) => ({ name: "d:" + f, label: f.replace("_", " "), value: e.data[f] == null ? "" : String(e.data[f]) }))];
    const r = await form({ title: `Edit ${e.title}`, fields, submit: "Save", wide: true });
    if (!r) return;
    const data: Record<string, string> = {};
    Object.entries(r).forEach(([k, v]) => { if (k.startsWith("d:") && v !== "") data[k.slice(2)] = v; });
    toast(await api.tool("entity", { items: [{ id: e.id, kind: e.kind, title: e.title, body: r.body, aliases: r.aliases.split(",").map((x) => x.trim()).filter(Boolean), data }] }));
    reload();
  };
  const link = async () => {
    const target = await pick({ title: `Link ${e.title} to…` });
    if (!target?.ref) return;
    const rels = (await api.kinds()).relations;
    const r = await form({ title: "Relation", fields: [{ name: "rel", label: `${e.title} → ${target.label}`, type: "select", value: "about", options: rels.map((x) => ({ value: x.name, label: `${x.name.replace("_", " ")} — ${x.meaning}` })) }], submit: "Link" });
    if (!r) return;
    toast(await api.tool("link", { items: [{ source: e.id, target: target.ref, relation: r.rel }] }));
    reload();
  };
  return (
    <aside className="drawer panel pad stack sticky">
      <KindLabel kind={e.kind} />
      <div className="w-title" style={{ fontSize: 20 }}>{e.title}</div>
      {e.aliases.length > 0 && <div className="small muted">Also: {e.aliases.join(", ")}</div>}
      {e.body && <p className="serif" style={{ margin: 0, fontSize: 15.5 }}>{e.body}</p>}
      {Object.keys(e.data).length > 0 && (
        <dl className="fields">{Object.entries(e.data).flatMap(([k, v]) => [<dt key={k + "t"}>{k.replace("_", " ")}</dt>, <dd key={k + "d"}>{String(v)}</dd>])}</dl>
      )}
      {e.papers.length > 0 && (
        <div className="stack" style={{ gap: 4 }}>
          <div className="section-title" style={{ margin: 0 }}>Papers</div>
          {e.papers.map((p) => <Link key={p} to={`/work/${encodeURIComponent(p)}`} className="key">{p}</Link>)}
        </div>
      )}
      <div className="small muted">{e.scope === "library" ? "Shared across all projects" : "Only in this project"} · {e.links} links · by {e.by}</div>
      <div className="row wrap">
        <button className="btn sm" onClick={edit}>Edit</button>
        <button className="btn sm" onClick={link}><Icon name="link" />Link to…</button>
        <button className="btn sm" onClick={async () => { const res = await api.editMap({ title: e.title, ops: [{ op: "expand", ref: e.id }] }); nav(`/maps/${encodeURIComponent(res.map)}`); }}><Icon name="map" />Map it</button>
        <button className="btn sm danger" onClick={async () => {
          if (await confirm(`Delete ${e.title}?`, <p className="muted" style={{ margin: 0 }}>It's also removed from maps.</p>, "Delete", true)) {
            toast(await api.tool("entity", { action: "delete", ids: [e.id] }));
            reload();
          }
        }}>Delete</button>
      </div>
    </aside>
  );
}

export default function Knowledge() {
  const { project, tick } = useApp();
  const { ask, form, menu, toast, confirm } = useOverlays();
  const copy = useCopy();
  const [params, setParams] = useSearchParams();
  const [kinds, setKinds] = useState<KindInfo[]>([]);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [items, setItems] = useState<Entity[]>([]);
  const [q, setQ] = useState("");
  const [version, setVersion] = useState(0);
  const item = params.get("item");
  const kind = params.get("kind") ?? (item ? item.split(":")[0] : "method");
  const reload = () => setVersion((v) => v + 1);

  useEffect(() => { api.kinds().then((k) => setKinds(k.kinds)); }, []);
  const load = useCallback(async () => {
    const res = await api.entities({ kind: kind === "leaderboard" ? "result" : kind, q });
    setItems(res.items);
    setCounts(res.counts);
  }, [kind, q]);
  useEffect(() => { load(); }, [load, project, tick, version]);
  const focused = items.find((e) => e.id === item) ?? null;
  const literature = kinds.filter((k) => k.scope === "library");
  const thinking = kinds.filter((k) => k.scope === "project");

  const create = async () => {
    const info = kinds.find((k) => k.name === kind);
    const r = await form({ title: `New ${kind}`, submit: "Create", fields: [
      { name: "title", label: "Title", required: kind !== "result" },
      { name: "body", label: "Description", type: "textarea" },
      ...(info?.fields ?? []).filter((f) => f !== "aliases").slice(0, 6).map((f) => ({ name: "d:" + f, label: f.replace("_", " ") })),
    ] });
    if (!r) return;
    const data: Record<string, string> = {};
    Object.entries(r).forEach(([k, v]) => { if (k.startsWith("d:") && v) data[k.slice(2)] = v; });
    toast(await api.tool("entity", { items: [{ kind, title: r.title, body: r.body, data }] }));
    reload();
  };
  const rowMenu = (e: Entity): MenuItem[] => [
    { label: "Rename…", onClick: async () => { const t = await ask(`Rename ${e.title}`, { label: "Title", value: e.title }, "Rename"); if (t) { toast(await api.tool("entity", { items: [{ id: e.id, kind: e.kind, rename: t }] })); reload(); } } },
    { label: "Copy id", icon: "copy", shortcut: e.id, onClick: () => copy(e.id) },
    { sep: true },
    { label: "Delete", icon: "trash", danger: true, onClick: async () => { if (await confirm(`Delete ${e.title}?`, undefined, "Delete", true)) { toast(await api.tool("entity", { action: "delete", ids: [e.id] })); reload(); } } },
  ];

  const kindLink = (k: KindInfo) => (
    <a key={k.name} href="#" className={kind === k.name && !params.get("board") ? "on" : ""} onClick={(ev) => { ev.preventDefault(); setParams({ kind: k.name }); }}>
      <KindLabel kind={k.name}>{plural(k.name)}</KindLabel><span className="tiny muted">{counts[k.name] ?? 0}</span>
    </a>
  );
  const board = params.get("board") === "1";
  return (
    <div className="page">
      <div className="page-head">
        <h1>Knowledge</h1>
        {!board && <button className="btn primary" onClick={create}><Icon name="plus" />New {kind}</button>}
      </div>
      <div className="cols-k" style={focused ? undefined : { gridTemplateColumns: "190px minmax(0,1fr)" }}>
        <nav className="kinds">
          <a href="#" className={board ? "on" : ""} onClick={(ev) => { ev.preventDefault(); setParams({ kind: "result", board: "1" }); }}>
            <span className="row" style={{ gap: 6 }}><Icon name="layout" size={14} />Leaderboards</span><span className="tiny muted">{counts.result ?? 0}</span>
          </a>
          <div className="grp">From the literature</div>
          {literature.map(kindLink)}
          <div className="grp">Your thinking</div>
          {thinking.map(kindLink)}
        </nav>
        <div className="stack">
          <input className="input" placeholder={board ? "Filter by benchmark…" : `Search ${plural(kind).toLowerCase()}…`} value={q} onChange={(e) => setQ(e.target.value)} />
          {board ? <Leaderboard query={q} /> : items.length === 0 ? (
            <div className="empty">
              <b>No {plural(kind).toLowerCase()} yet</b>
              {THINKING.has(kind) ? "Add your own, or create them while mapping." : "Agents add these when they extract a paper (paper menu → Extract knowledge with an agent)."}
            </div>
          ) : (
            <table className="list"><tbody>
              {items.map((e) => (
                <tr key={e.id} className={item === e.id ? "sel" : ""} onClick={() => setParams({ kind, item: e.id })} onContextMenu={(ev) => menu(ev, rowMenu(e))}
                  tabIndex={0} aria-selected={item === e.id}
                  onKeyDown={rowKeys({ select: () => setParams({ kind, item: e.id }), menu: (at) => menu(at, rowMenu(e)) })}>
                  <td>
                    <div style={{ fontWeight: 600 }}>{e.title}</div>
                    <div className="w-meta">
                      {Object.entries(e.data).slice(0, 4).map(([k, v]) => <span key={k}>{k.replace("_", " ")}: {String(v)}</span>)}
                      {e.aliases.length > 0 && <span>aka {e.aliases.slice(0, 3).join(", ")}</span>}
                    </div>
                  </td>
                  <td className="small" style={{ width: 220 }}>{e.papers.slice(0, 3).map((p) => <div key={p} className="key">{p}</div>)}{e.papers.length > 3 && <span className="muted">+{e.papers.length - 3}</span>}</td>
                </tr>
              ))}
            </tbody></table>
          )}
        </div>
        {focused && <Detail e={focused} kinds={kinds} reload={reload} />}
      </div>
    </div>
  );
}
