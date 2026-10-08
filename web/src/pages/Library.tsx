import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useApp } from "../app/context";
import { api, Tag, WorkBrief } from "../lib/api";
import { authorsFull, authorsShort } from "../lib/format";
import { READ_LEVELS } from "../lib/kinds";
import { Dialog } from "../ui/Dialog";
import { Icon } from "../ui/Icon";
import { MenuButton, MenuItem, rowKeys, useOverlays } from "../ui/overlays";
import { TagChip, usePicker } from "../ui/pickers";
import { exportItems, useWorkMenu } from "../works/workMenu";

function AddPapers({ close, done }: { close: () => void; done: () => void }) {
  const { toast } = useOverlays();
  const [items, setItems] = useState("");
  const [tags, setTags] = useState("");
  const [why, setWhy] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    setBusy(true);
    try {
      const tagList = tags.split(",").map((t) => t.trim()).filter(Boolean);
      if (file) {
        const r = await api.upload(file, { tags: tagList.join(","), why });
        toast(r.work ? `Attached ${file.name} to ${r.work}` : `Imported ${file.name}: ${r.added ?? 0} added, ${r.already ?? 0} already here`);
      }
      const list = items.split("\n").map((s) => s.trim()).filter(Boolean);
      if (list.length) toast(await api.tool("resolve", { items: list, add: true, tags: tagList, why: why || undefined }));
      done();
      close();
    } catch (e) {
      toast(String(e), true);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog title="Add papers" wide onClose={close} onSubmit={submit}>
      <label className="field">One per line
        <textarea autoFocus className="textarea" rows={7} value={items} onChange={(e) => setItems(e.target.value)}
          placeholder={"2412.06464\n10.18653/v1/N19-1423\nhttps://proceedings.mlr.press/v162/baevski22a.html\nAttention Is All You Need"} />
        <span className="hint">arXiv IDs, DOIs, URLs, titles or BibTeX. Anything already in the library is reused, not duplicated.</span>
      </label>
      <div className="row">
        <label className="field grow">Tags<input className="input" placeholder="phase:related-work, method:linear-attention" value={tags} onChange={(e) => setTags(e.target.value)} /></label>
        <label className="field grow">Why<input className="input" placeholder="one line" value={why} onChange={(e) => setWhy(e.target.value)} /></label>
      </div>
      <label className="field">Or a file
        <input type="file" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
        <span className="hint">A PDF is attached as full text; .bib, RIS, CSL-JSON or a list of IDs is imported.</span>
      </label>
      <div className="actions">
        <button type="button" className="btn ghost" onClick={close}>Cancel</button>
        <button type="submit" className="btn primary" disabled={busy || (!items.trim() && !file)}>{busy ? "Adding…" : "Add papers"}</button>
      </div>
    </Dialog>
  );
}

function Preview({ w, reload }: { w: WorkBrief; reload: () => void }) {
  const [detail, setDetail] = useState<{ abstract?: string } | null>(null);
  const { toast } = useOverlays();
  const [newTag, setNewTag] = useState("");
  useEffect(() => { api.work(w.citekey).then((d) => setDetail({ abstract: d.csl.abstract as string | undefined })).catch(() => setDetail(null)); }, [w.citekey]);
  const run = async (name: string, args: Record<string, unknown>) => { toast(await api.tool(name, args)); reload(); };
  return (
    <aside className="preview panel pad stack sticky">
      <div className="w-title" style={{ fontSize: 19 }}>{w.title}</div>
      <div className="small muted">{authorsFull(w.csl)}</div>
      <div className="row small wrap"><span>{w.venue}</span><span className="key">{w.citekey}</span></div>
      <div className="row wrap" style={{ gap: 5 }}>
        {(w.tags ?? []).map((t) => <TagChip key={t} name={t} onRemove={() => run("tag", { action: "remove", tags: [t], works: [w.citekey] })} />)}
        <input className="input" style={{ height: 24, width: 130, fontSize: 12 }} placeholder="+ tag" value={newTag} onChange={(e) => setNewTag(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && newTag.trim()) { run("tag", { action: "apply", tags: [newTag.trim()], works: [w.citekey] }); setNewTag(""); } }} />
      </div>
      {w.why && <div className="why">{w.why}</div>}
      {detail?.abstract && <p className="serif" style={{ margin: 0, fontSize: 15, lineHeight: 1.55, color: "var(--ink-2)", maxHeight: 260, overflow: "auto" }}>{detail.abstract}</p>}
      <div className="row">
        <Link className="btn primary sm" to={`/work/${encodeURIComponent(w.citekey)}`}>Open</Link>
        <Link className="btn sm" to={`/work/${encodeURIComponent(w.citekey)}?tab=text`}><Icon name="read" />Read</Link>
        {w.in_project === false ? (  /* a library row from another project: reading is tracked per project */
          <button className="btn sm" onClick={() => run("resolve", { items: [w.citekey], add: true })}><Icon name="plus" />Add to project</button>
        ) : (
          <select className="select" aria-label="Reading" style={{ height: 26, width: 120, fontSize: 12.5 }} value={w.read ?? ""}
            onChange={(e) => run("update_work", { items: [{ work: w.citekey, read: e.target.value }] })}>
            <option value="">Not read</option>
            {READ_LEVELS.map((r) => <option key={r} value={r}>{r}</option>)}
          </select>
        )}
      </div>
    </aside>
  );
}

export default function Library() {
  const { project, tick } = useApp();
  const { ask, confirm, menu, toast } = useOverlays();
  const { pick } = usePicker();
  const workMenu = useWorkMenu();
  const nav = useNavigate();
  const [params, setParams] = useSearchParams();
  const [q, setQ] = useState("");
  const [scope, setScope] = useState("project");
  const [read, setRead] = useState("");
  const [include, setInclude] = useState<string[]>(() => (params.get("tag") ? [params.get("tag")!] : []));
  const [exclude, setExclude] = useState<string[]>([]);
  const [items, setItems] = useState<WorkBrief[]>([]);
  const [total, setTotal] = useState(0);
  const [limit, setLimit] = useState(60);
  const [tags, setTags] = useState<Tag[]>([]);
  const [sel, setSel] = useState<Set<string>>(new Set());
  const [focus, setFocus] = useState<string | null>(null);
  const [version, setVersion] = useState(0);
  const adding = params.get("add") === "1";
  const reload = () => setVersion((v) => v + 1);

  useEffect(() => { if (params.get("tag")) setInclude([params.get("tag")!]); }, [params]);
  const tagExpr = [...include, ...exclude.map((t) => "-" + t)].join(",");
  useEffect(() => {
    const h = window.setTimeout(async () => {
      const res = await api.works({ q, tags: tagExpr, scope, limit, read: read || undefined });
      setItems(res.items);
      setTotal(res.total);
    }, 160);
    return () => window.clearTimeout(h);
  }, [q, tagExpr, scope, limit, read, project, tick, version]);
  useEffect(() => { api.tags().then(setTags); }, [project, tick, version]);

  const groups = useMemo(() => {
    const g: Record<string, Tag[]> = {};
    tags.forEach((t) => { (g[t.name.includes(":") ? t.name.split(":")[0] : "other"] ??= []).push(t); });
    return Object.entries(g).sort(([a], [b]) => (a === "other" ? 1 : b === "other" ? -1 : a.localeCompare(b)));
  }, [tags]);
  const cycle = (name: string) => {
    if (include.includes(name)) { setInclude(include.filter((t) => t !== name)); setExclude([...exclude, name]); }
    else if (exclude.includes(name)) setExclude(exclude.filter((t) => t !== name));
    else setInclude([...include, name]);
  };
  const tagMenu = (t: Tag): MenuItem[] => {
    const run = async (args: Record<string, unknown>) => { toast(await api.tool("tag", args)); reload(); };
    return [
      { label: "Show only this", icon: "check", onClick: () => { setInclude([t.name]); setExclude([]); } },
      { label: "Hide this", icon: "x", onClick: () => { setExclude([...exclude.filter((x) => x !== t.name), t.name]); setInclude(include.filter((x) => x !== t.name)); } },
      { sep: true },
      { label: "Rename…", onClick: async () => { const to = await ask(`Rename ${t.name}`, { label: "New name", value: t.name }, "Rename"); if (to && to !== t.name) run({ action: "rename", tags: [t.name], to }); } },
      { label: "Merge into…", onClick: async () => {
        const to = await ask(`Merge ${t.name} into…`, { label: "Tag", type: "select", value: tags.find((x) => x.name !== t.name)?.name, options: tags.filter((x) => x.name !== t.name).map((x) => ({ value: x.name, label: x.name })) }, "Merge");
        if (to) run({ action: "merge", tags: [t.name], to });
      } },
      { label: "Describe…", onClick: async () => { const d = await ask(`Describe ${t.name}`, { label: "What this tag means (agents read this)", value: t.description ?? "", type: "textarea" }, "Save"); if (d !== null) run({ action: "define", tags: [t.name], description: d }); } },
      { sep: true },
      { label: "Archive", icon: "trash", danger: true, onClick: async () => { if (await confirm(`Archive ${t.name}?`, undefined, "Archive", true)) run({ action: "archive", tags: [t.name] }); } },
    ];
  };
  const selected = [...sel];
  const bulkTag = async (action: "apply" | "remove") => {
    const t = await ask(action === "apply" ? `Tag ${selected.length} papers` : `Untag ${selected.length} papers`, { label: "Tags", placeholder: "phase:baselines" }, action === "apply" ? "Tag" : "Untag");
    if (!t) return;
    toast(await api.tool("tag", { action, tags: t.split(",").map((x) => x.trim()).filter(Boolean), works: selected }));
    reload();
  };
  const focused = items.find((w) => w.citekey === focus) ?? null;

  return (
    <div className="page">
      <div className="page-head">
        <h1>{scope === "project" ? project : "Whole library"}</h1>
        <MenuButton label="Export" icon="download" items={() => [
          { header: sel.size ? `${sel.size} selected` : include.length || exclude.length ? "Current filter" : `All of ${project}` },
          ...exportItems(project, sel.size ? { works: selected } : tagExpr ? { tags: tagExpr.split(",") } : {}, toast),
        ]} />
        <button className="btn primary" onClick={() => setParams({ add: "1" })}><Icon name="plus" />Add papers</button>
      </div>
      <div className="cols-lib" style={focused ? undefined : { gridTemplateColumns: "200px minmax(0,1fr)" }}>
        <div className="facets">
          <select className="select" value={scope} onChange={(e) => setScope(e.target.value)}>
            <option value="project">This project</option>
            <option value="library">Whole library</option>
          </select>
          <select className="select" value={read} onChange={(e) => setRead(e.target.value)} disabled={scope !== "project"}>
            <option value="">Any reading state</option>
            <option value="unread">Not read yet</option>
            {READ_LEVELS.map((r) => <option key={r} value={r}>{r}</option>)}
          </select>
          {groups.map(([ns, list]) => (
            <div className="facet" key={ns}>
              <h4>{ns}</h4>
              {list.map((t) => (
                <div key={t.name} title={t.description ?? "Click to filter, again to exclude. Right-click for more."}
                  className={"t" + (include.includes(t.name) ? " on" : exclude.includes(t.name) ? " off" : "")}
                  onClick={() => cycle(t.name)} onContextMenu={(e) => menu(e, tagMenu(t))}>
                  <span>{t.name.includes(":") ? t.name.split(":").slice(1).join(":") : t.name}{t.scope === "library" ? " ◦" : ""}</span>
                  <span className="muted tiny">{t.count}</span>
                </div>
              ))}
            </div>
          ))}
          {!tags.length && <p className="small muted" style={{ margin: 0 }}>Tags you add appear here as filters.</p>}
        </div>
        <div className="stack" style={{ gap: 12 }}>
          <div className="row">
            <input className="input grow" placeholder="Search titles, authors, abstracts, IDs…" value={q} onChange={(e) => setQ(e.target.value)} />
            <span className="small muted" style={{ whiteSpace: "nowrap" }}>{total} papers</span>
          </div>
          {(include.length > 0 || exclude.length > 0) && (
            <div className="row wrap">
              {include.map((t) => <TagChip key={t} name={t} state="on" onRemove={() => setInclude(include.filter((x) => x !== t))} />)}
              {exclude.map((t) => <TagChip key={t} name={t} state="off" onRemove={() => setExclude(exclude.filter((x) => x !== t))} />)}
            </div>
          )}
          {sel.size > 0 && (
            <div className="row panel" style={{ padding: "6px 10px" }}>
              <b className="small">{sel.size} selected</b>
              <span className="grow" />
              <button className="btn sm" onClick={() => bulkTag("apply")}><Icon name="tag" />Tag</button>
              <button className="btn sm" onClick={() => bulkTag("remove")}>Untag</button>
              <button className="btn sm" onClick={async () => {
                const p = await pick({ title: "Add these papers to which map?", kinds: ["map"] });
                if (p?.ref) { await api.editMap({ map: p.ref, ops: selected.map((k) => ({ op: "add", ref: k })) }); nav(`/maps/${encodeURIComponent(p.ref)}`); }
              }}><Icon name="map" />Add to map</button>
              <button className="btn sm danger" onClick={async () => {
                if (await confirm(`Remove ${sel.size} papers from ${project}?`, undefined, "Remove", true)) {
                  toast(await api.tool("update_work", { items: selected.map((k) => ({ work: k, remove: true })) }));
                  setSel(new Set());
                  reload();
                }
              }}>Remove</button>
              <button className="btn ghost sm" onClick={() => setSel(new Set())}>Clear</button>
            </div>
          )}
          {items.length === 0 ? (
            <div className="empty">
              <b>{q || tagExpr ? "Nothing matches" : "No papers yet"}</b>
              {q || tagExpr ? "Try fewer filters." : "Add some by ID or title, or let an agent capture them with resolve."}
              {!(q || tagExpr) && <div style={{ marginTop: 14 }}><button className="btn primary" onClick={() => setParams({ add: "1" })}><Icon name="plus" />Add papers</button></div>}
            </div>
          ) : (
            <table className="list">
              <tbody>
                {items.map((w) => (
                  <tr key={w.id} className={focus === w.citekey ? "sel" : ""} onClick={() => setFocus(w.citekey)}
                    onDoubleClick={() => nav(`/work/${encodeURIComponent(w.citekey)}`)}
                    onContextMenu={(e) => { setFocus(w.citekey); menu(e, workMenu(w, reload)); }}
                    tabIndex={0} aria-selected={focus === w.citekey}
                    onKeyDown={rowKeys({ open: () => nav(`/work/${encodeURIComponent(w.citekey)}`), select: () => setFocus(w.citekey),
                                         menu: (at) => { setFocus(w.citekey); menu(at, workMenu(w, reload)); } })}>
                    <td style={{ width: 28 }} onClick={(e) => e.stopPropagation()}>
                      <input type="checkbox" checked={sel.has(w.citekey)} onChange={(e) => {
                        const s = new Set(sel);
                        if (e.target.checked) s.add(w.citekey); else s.delete(w.citekey);
                        setSel(s);
                      }} />
                    </td>
                    <td>
                      <div className="w-title">{w.title}</div>
                      <div className="w-meta">
                        <span>{authorsShort(w.csl)}</span>
                        <span>{w.venue}</span>
                        <span className="key">{w.citekey}</span>
                        {w.read && <span className="badge read">{w.read}</span>}
                      </div>
                      {(w.tags?.length || w.why) ? (
                        <div className="row wrap" style={{ gap: 5, marginTop: 6 }}>
                          {w.tags?.map((t) => <TagChip key={t} name={t} />)}
                          {w.why && <span className="why">{w.why}</span>}
                        </div>
                      ) : null}
                    </td>
                    <td style={{ width: 36 }} onClick={(e) => e.stopPropagation()}>
                      <button className="btn ghost sm icon" aria-label="More" onClick={(e) => menu(e, workMenu(w, reload))}><Icon name="more" /></button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {items.length < total && <button className="btn" onClick={() => setLimit(limit + 60)}>Show more ({total - items.length})</button>}
        </div>
        {focused && <Preview w={focused} reload={reload} />}
      </div>
      {adding && <AddPapers close={() => setParams({})} done={reload} />}
    </div>
  );
}
