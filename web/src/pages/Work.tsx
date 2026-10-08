import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useApp } from "../app/context";
import { api, WorkDetail } from "../lib/api";
import { authorsFull, idLink } from "../lib/format";
import { NOTE_KINDS, READ_LEVELS } from "../lib/kinds";
import { useCopy } from "../ui/CopyBlock";
import { Icon } from "../ui/Icon";
import { useOverlays } from "../ui/overlays";
import { KindLabel, TagChip } from "../ui/pickers";
import { AgentBrief, useWorkMenu } from "../works/workMenu";

type FullText = Awaited<ReturnType<typeof api.passages>>;

function useQuoteNote(work: string, after: () => void) {
  const { form, toast } = useOverlays();
  return useCallback(async (quote: string) => {
    const r = await form({
      title: "Quote in a note", submit: "Save note",
      body: <blockquote className="serif" style={{ margin: 0, paddingLeft: 12, borderLeft: "3px solid var(--marker)", fontSize: 15 }}>{quote}</blockquote>,
      fields: [{ name: "text", label: "Your note (optional)", type: "textarea", placeholder: "Why this matters…" },
               { name: "kind", label: "Kind", type: "select", value: "quote", options: NOTE_KINDS.map((k) => ({ value: k, label: k })) }],
    });
    if (!r) return;
    toast(await api.tool("note", { items: [{ work, quote, text: r.text || undefined, kind: r.kind }] }));
    after();
  }, [form, toast, work, after]);
}

function Reader({ work, onChange }: { work: WorkDetail; onChange: () => void }) {
  const { toast, menu } = useOverlays();
  const copy = useCopy();
  const [doc, setDoc] = useState<FullText["document"]>(null);
  const [passages, setPassages] = useState<FullText["passages"]>([]);
  const [busy, setBusy] = useState(false);
  const [sel, setSel] = useState<{ text: string; x: number; y: number } | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const quoteNote = useQuoteNote(work.citekey, () => { onChange(); load(passages[0]?.seq ?? 1); });

  const load = useCallback(async (start = 1) => {
    const res = await api.passages(work.citekey, start, start + 39);
    setDoc(res.document);
    setPassages(res.passages);
  }, [work.citekey]);
  useEffect(() => { if (work.document) load(1); }, [work.document, load]);
  useEffect(() => {
    const onUp = () => {
      const s = window.getSelection();
      const text = s?.toString().trim() ?? "";
      if (text.length > 12 && box.current && s?.anchorNode && box.current.contains(s.anchorNode)) {
        const r = s.getRangeAt(0).getBoundingClientRect();
        setSel({ text, x: r.left + r.width / 2, y: r.top });
      } else setSel(null);
    };
    document.addEventListener("mouseup", onUp);
    return () => document.removeEventListener("mouseup", onUp);
  }, []);

  if (!work.document) {
    return (
      <div className="empty">
        <b>No full text stored yet</b>
        litledger tries arXiv's HTML first, then open-access PDFs. You can also upload a PDF.
        <div style={{ marginTop: 14 }}>
          <button className="btn primary" disabled={busy} onClick={async () => { setBusy(true); toast((await api.tool("read", { work: work.citekey })).split("\n")[0]); setBusy(false); onChange(); }}>
            {busy ? "Fetching…" : "Fetch full text"}
          </button>
        </div>
      </div>
    );
  }
  let last = "";
  return (
    <div className="reader">
      <nav className="toc">
        <div className="tiny muted" style={{ padding: "0 6px 6px" }}>{doc?.source.replace("_", " ")} · {doc?.passages} passages</div>
        {doc?.outline.map((s) => <a key={s.first} href="#" onClick={(e) => { e.preventDefault(); load(s.first); }}>{s.section}</a>)}
      </nav>
      <div ref={box} style={{ paddingLeft: 46 }}>
        {passages.map((p) => {
          const head = p.section !== last ? <div className="sec-head">{p.section}</div> : null;
          last = p.section;
          return (
            <div key={p.id}>
              {head}
              <div className={"passage" + (p.note ? " noted" : "")}
                onContextMenu={(e) => {
                  const chosen = window.getSelection()?.toString().trim();
                  menu(e, [
                    ...(chosen ? [{ label: "Quote selection in a note…", icon: "quote", onClick: () => quoteNote(chosen) }] : []),
                    { label: "Quote this passage in a note…", icon: "quote", onClick: () => quoteNote(p.text.slice(0, 600)) },
                    { label: "Copy passage", icon: "copy", onClick: () => copy(p.text) },
                    { label: `Copy reference (${work.citekey} p${p.seq})`, icon: "copy", onClick: () => copy(`${work.citekey} p${p.seq}${p.page ? ` page ${p.page}` : ""}`) },
                  ]);
                }}>
                <span className="pno">p{p.seq}</span>
                <span className="body">{p.text}</span>
              </div>
            </div>
          );
        })}
        {passages.length > 0 && passages[passages.length - 1].seq < (doc?.passages ?? 0) && (
          <button className="btn" onClick={() => load(passages[passages.length - 1].seq + 1)}>Next passages</button>
        )}
      </div>
      {sel && (
        <div className="quote-pop" style={{ left: sel.x, top: sel.y - 40, transform: "translateX(-50%)" }}>
          <button className="btn primary sm" onMouseDown={(e) => { e.preventDefault(); const t = sel.text; setSel(null); quoteNote(t); }}><Icon name="quote" />Quote in note</button>
        </div>
      )}
    </div>
  );
}

export default function WorkPage() {
  const ref = decodeURIComponent(useParams()["*"] ?? "");
  const { tick } = useApp();
  const { toast, menu, ask, show } = useOverlays();
  const workMenu = useWorkMenu();
  const nav = useNavigate();
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") ?? "notes";
  const [w, setW] = useState<WorkDetail | null>(null);
  const [err, setErr] = useState("");
  const [note, setNote] = useState({ text: "", quote: "", kind: "note" });
  const [newTag, setNewTag] = useState("");

  const load = useCallback(() => {
    api.work(ref).then((d) => { setW(d); setErr(""); }).catch((e) => setErr(String(e.message ?? e)));
  }, [ref]);
  useEffect(load, [load, tick]);

  if (err) return <div className="page"><div className="warn">{err}</div></div>;
  if (!w) return <div className="page muted">Loading…</div>;
  const act = async (name: string, args: Record<string, unknown>) => { toast(await api.tool(name, args)); load(); };
  const csl = w.csl;
  const setTab = (t: string) => setParams({ tab: t });
  const knowledge = w.knowledge ?? [];
  const byKind: Record<string, typeof knowledge> = {};
  knowledge.forEach((k) => { (byKind[k.kind] ??= []).push(k); });

  return (
    <div className="page">
      <div className="row small" style={{ marginBottom: 10 }}>
        <Link to="/">Library</Link><span className="muted">/</span><span className="key">{w.citekey}</span>
        <span className="grow" />
        <button className="btn ghost sm icon" aria-label="More" onClick={(e) => menu(e, workMenu(w, load))}><Icon name="more" /></button>
      </div>
      <div className="cols-2">
        <div>
          <h1 className="serif" style={{ fontSize: 30, fontWeight: 500, lineHeight: 1.18, letterSpacing: "-0.01em" }}>{w.title}</h1>
          <div style={{ color: "var(--ink-2)", marginTop: 8 }}>{authorsFull(csl)}</div>
          <div className="row wrap small" style={{ marginTop: 8, gap: 12 }}>
            <span>{String(csl["container-title"] ?? w.type)} {w.year}</span>
            {Object.entries(w.ids).map(([s, v]) => {
              const href = idLink(s, v);
              return href ? <a key={s} href={href} target="_blank" rel="noreferrer">{s}:{v.length > 36 ? v.slice(0, 36) + "…" : v}</a> : <span key={s} className="muted">{s}:{v}</span>;
            })}
            {(w.flags ?? []).map((f) => <span key={f} className="badge" style={{ background: "var(--danger-soft)", color: "var(--danger)" }}>{f}</span>)}
          </div>
          {typeof csl.abstract === "string" && <p className="abstract" style={{ marginTop: 18 }}>{csl.abstract}</p>}
          <div className="tabs">
            {[["notes", "Notes", w.counts.notes], ["text", "Full text", 0], ["knowledge", "Knowledge", knowledge.length], ["links", "Links", w.counts.links],
              ["cited", "Cited by", w.counts.cited_by_local], ["history", "History", 0]].map(([k, label, n]) => (
              <button key={k as string} className={tab === k ? "on" : ""} onClick={() => setTab(k as string)}>{label}{n ? <span className="n">{n}</span> : null}</button>
            ))}
          </div>
          {tab === "notes" && (
            <div className="stack">
              <form className="panel pad stack" onSubmit={async (e) => {
                e.preventDefault();
                await act("note", { items: [{ work: w.citekey, text: note.text, quote: note.quote || undefined, kind: note.kind }] });
                setNote({ text: "", quote: "", kind: "note" });
              }}>
                <textarea className="textarea" rows={2} placeholder="Write a note…" value={note.text} onChange={(e) => setNote({ ...note, text: e.target.value })} />
                <textarea className="textarea serif" rows={2} style={{ fontSize: 15 }} placeholder="Quote (optional)"
                  value={note.quote} onChange={(e) => setNote({ ...note, quote: e.target.value })} />
                <div className="row">
                  <select className="select" style={{ width: 140 }} value={note.kind} onChange={(e) => setNote({ ...note, kind: e.target.value })}>
                    {NOTE_KINDS.map((k) => <option key={k}>{k}</option>)}
                  </select>
                  <span className="grow small muted">Tip: select text in Full text to quote it.</span>
                  <button className="btn primary" disabled={!note.text && !note.quote}>Add note</button>
                </div>
              </form>
              {(w.notes ?? []).map((n) => (
                <div key={n.id} className="note">
                  <div className="row small">
                    <span className="badge">{n.kind}</span>
                    {n.verification === "verified" && <span className="badge verified">quote verified</span>}
                    <span className="muted">{n.by ?? ""}</span><span className="muted">{n.created_at.slice(0, 10)}</span>
                  </div>
                  {n.text && n.text !== n.quote && <div style={{ marginTop: 6 }}>{n.text}</div>}
                  {n.quote && <blockquote>{n.quote}</blockquote>}
                </div>
              ))}
              {!w.notes?.length && <p className="muted">No notes in this project yet.</p>}
            </div>
          )}
          {tab === "text" && <Reader work={w} onChange={load} />}
          {tab === "knowledge" && (
            <div className="stack">
              <div className="row">
                <span className="grow" />
                <button className="btn sm" onClick={async () => show(`Extract ${w.citekey} with an agent`, <AgentBrief brief={await api.brief("extract_paper", w.citekey)} work={w.citekey} />, true)}>
                  <Icon name="robot" />Extract with an agent
                </button>
                <button className="btn sm" onClick={async () => { const res = await api.editMap({ title: w.citekey, ops: [{ op: "expand", ref: w.citekey }] }); nav(`/maps/${encodeURIComponent(res.map)}`); }}>
                  <Icon name="map" />Map it
                </button>
              </div>
              {Object.entries(byKind).map(([kind, list]) => (
                <div key={kind} className="panel pad">
                  <div className="row" style={{ marginBottom: 6 }}><KindLabel kind={kind}>{kind}</KindLabel><span className="tiny muted">{list.length}</span></div>
                  {list.map((k) => (
                    <div key={k.id} className="row small" style={{ padding: "3px 0" }}>
                      <Link to={`/knowledge?item=${encodeURIComponent(k.id)}`} className="grow">{k.title}</Link>
                      <span className="muted tiny">{k.relation.replace("_", " ")}</span>
                    </div>
                  ))}
                </div>
              ))}
              {!knowledge.length && <div className="empty"><b>Nothing extracted yet</b>Ask an agent to read the paper and fill this in.</div>}
            </div>
          )}
          {tab === "links" && (
            <div className="stack">
              {(w.links ?? []).map((l) => (
                <div key={l.id} className="row small">
                  <Link to={l.source.includes(":") ? `/knowledge?item=${encodeURIComponent(l.source)}` : `/work/${encodeURIComponent(l.source)}`}>{l.source}</Link>
                  <span className="badge">{l.relation.replace("_", " ")}{l.qualifier ? ` / ${l.qualifier}` : ""}</span>
                  <Link to={l.target.includes(":") ? `/knowledge?item=${encodeURIComponent(l.target)}` : `/work/${encodeURIComponent(l.target)}`}>{l.target}</Link>
                  {l.evidence && <span className="badge verified">evidence</span>}
                </div>
              ))}
              {!w.links?.length && <p className="muted">No links yet.</p>}
            </div>
          )}
          {tab === "cited" && (
            <div className="stack">
              {(w.cited_by ?? []).map((c, i) => (
                <div key={i} className="note"><Link to={`/work/${encodeURIComponent(c.citekey)}`} className="key">{c.citekey}</Link><blockquote>{c.sentence}</blockquote></div>
              ))}
              {!w.cited_by?.length && <p className="muted">No papers in your library cite this one yet.</p>}
            </div>
          )}
          {tab === "history" && (
            <table className="list"><tbody>
              {(w.history ?? []).map((h, i) => (
                <tr key={i}><td className="small muted" style={{ width: 140 }}>{h.ts.slice(0, 16).replace("T", " ")}</td><td className="small">{h.op}</td><td className="small muted">{h.by ?? ""}{h.agent ? ` · ${h.agent}` : ""}{h.project ? ` · ${h.project}` : ""}</td></tr>
              ))}
            </tbody></table>
          )}
        </div>
        <aside className="stack sticky">
          <div className="panel pad stack">
            <div className="section-title" style={{ margin: 0 }}>In this project</div>
            {!w.in_project && <button className="btn" onClick={() => act("resolve", { items: [w.citekey], add: true })}><Icon name="plus" />Add to project</button>}
            <div className="row wrap" style={{ gap: 5 }}>
              {(w.tags ?? []).map((t) => <TagChip key={t} name={t} onRemove={() => act("tag", { action: "remove", tags: [t], works: [w.citekey] })} />)}
              <input className="input" style={{ height: 24, width: 140, fontSize: 12 }} placeholder="+ tag" value={newTag} onChange={(e) => setNewTag(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter" && newTag.trim()) { act("tag", { action: "apply", tags: [newTag.trim()], works: [w.citekey] }); setNewTag(""); } }} />
            </div>
            {w.in_project && <>  {/* why and reading are this project's: set once the paper is in it */}
              <label className="field">Why it matters
                <div className="why" style={{ cursor: "text", minHeight: 22 }} onClick={async () => {
                  const why = await ask("Why it matters", { label: "One line", type: "textarea", value: w.why ?? "" }, "Save");
                  if (why !== null) act("update_work", { items: [{ work: w.citekey, why }] });
                }}>{w.why || <span className="muted" style={{ fontStyle: "normal" }}>Add a reason…</span>}</div>
              </label>
              <label className="field">Reading
                <select className="select" value={w.read ?? ""} onChange={(e) => act("update_work", { items: [{ work: w.citekey, read: e.target.value }] })}>
                  <option value="">Not read</option>
                  {READ_LEVELS.map((r) => <option key={r}>{r}</option>)}
                </select>
              </label>
            </>}
          </div>
          <div className="panel pad stack small">
            <div className="section-title" style={{ margin: 0 }}>Library</div>
            <div>Projects: {(w.projects ?? []).join(", ") || "none"}</div>
            {w.versions.length > 0 && <div>Versions: {w.versions.map((v) => v.label).join("; ")}</div>}
            {w.document && <div>Full text: {w.document.source.replace("_", " ")}, {w.document.passages} passages</div>}
            <button className="btn sm" onClick={async () => {
              const t = await api.tool("export", { works: [w.citekey], format: "bibtex", inline: true });
              show("BibTeX", <pre>{t.slice(t.indexOf("\n") + 1)}</pre>, true);
            }}>Show BibTeX</button>
          </div>
        </aside>
      </div>
    </div>
  );
}
