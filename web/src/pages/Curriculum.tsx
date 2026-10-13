import cytoscape from "cytoscape";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useApp } from "../app/context";
import { api, CReading, CStage, CStep, Curriculum } from "../lib/api";
import { cssVar as css } from "../lib/format";
import { Icon } from "../ui/Icon";
import { MenuItem, useOverlays } from "../ui/overlays";
import { hours } from "./Curricula";
import { Tex } from "../ui/Tex";

// done is the learner's own (agents reading a paper raise the library's read level, not anyone's progress);
// finishing a reading also marks the paper read this far in the library
const NEED: Record<string, string> = { skim: "skimmed", read: "sections", study: "full" };
const done = (r: CReading) => !!r.done;

const RELATION_WORD: Record<string, string> = { extends: "extends", generalizes: "generalizes", simplifies: "simplifies", fixes: "fixes",
  reinterprets: "reinterprets", outperforms: "outperforms", contradicts: "contradicts", compares_to: "compares to", instance_of: "is a kind of",
  motivates: "motivates" };

export default function CurriculumPage() {
  const id = decodeURIComponent(useParams().id ?? "");
  const { tick, subscribe } = useApp();
  const { menu, confirm, form, ask, toast } = useOverlays();
  const nav = useNavigate();
  const [c, setC] = useState<Curriculum | null>(null);
  const [tab, setTab] = useState<"syllabus" | "lineage">("syllabus");
  const load = useCallback(() => api.curriculum(id).then(setC).catch((e) => toast(String(e), true)), [id, toast]);
  useEffect(() => { load(); }, [load, tick]);
  // follow an agent building it: reload shortly after it writes
  useEffect(() => {
    let t: number | undefined;
    const off = subscribe((e) => {
      if (!/^(curriculum\.|link\.|project\.read)/.test(e.op)) return;
      window.clearTimeout(t);
      t = window.setTimeout(load, 800);
    });
    return () => { window.clearTimeout(t); off(); };
  }, [subscribe, load]);

  const edit = async (ops: Record<string, unknown>[]) => {
    try {
      const r = await api.editCurriculum({ curriculum: id, ops });
      if (r.problems.length) toast(r.problems.join("; "), true);
    } catch (e) { toast(String(e), true); }
    load();
  };

  if (!c) return <div className="page"><div className="empty">Loading…</div></div>;

  const readings = c.stages.flatMap((s) => s.readings);
  const minutes = readings.reduce((a, r) => a + (r.minutes ?? 0), 0);
  const finished = readings.filter(done).length;

  const finish = async (r: CReading, value: boolean) => {
    await api.curriculumProgress(id, r.id, value).catch((e) => toast(String(e), true));
    load();
  };

  const download = async () => {
    const text = await api.curriculumMarkdown(id);
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([text], { type: "text/markdown" }));
    a.download = `${id.replace(/^cur:/, "")}.md`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  const readingMenu = (s: CStage, r: CReading, i: number): MenuItem[] => [
    { label: "Edit…", onClick: async () => {
      const v = await form({ title: r.work?.citekey ?? "Reading", submit: "Save", wide: true, fields: [
        { name: "why", label: "Why read it", type: "textarea", value: r.why ?? "" },
        { name: "focus", label: "What to focus on", value: r.focus ?? "" },
        { name: "depth", label: "Depth", type: "select", value: r.depth ?? "read",
          options: [{ value: "skim", label: "Skim" }, { value: "read", label: "Read" }, { value: "study", label: "Study" }] },
        { name: "minutes", label: "Minutes", value: String(r.minutes ?? "") },
      ] });
      if (v) edit([{ op: "read_update", id: r.id, ...v, minutes: Number(v.minutes) || 0 }]);
    } },
    { label: "Move up", disabled: i === 0, onClick: () => edit([{ op: "move", id: r.id, before: s.readings[i - 1].id }]) },
    { label: "Move down", disabled: i === s.readings.length - 1, onClick: () => edit([{ op: "move", id: s.readings[i + 1].id, before: r.id }]) },
    { label: "Move to stage", items: c.stages.filter((x) => x.id !== s.id).map((x) => ({ label: x.title, onClick: () => edit([{ op: "move", id: r.id, stage: x.id }]) })) },
    { sep: true },
    { label: "Remove from the curriculum", icon: "trash", danger: true, onClick: () => edit([{ op: "remove", id: r.id }]) },
  ];

  const stageMenu = (s: CStage, i: number): MenuItem[] => [
    { label: "Rename…", onClick: async () => { const t = await ask("Rename stage", { label: "Title", value: s.title, required: true }, "Rename"); if (t) edit([{ op: "stage_update", id: s.id, title: t }]); } },
    { label: "Move up", disabled: i === 0, onClick: () => edit([{ op: "move", id: s.id, before: c.stages[i - 1].id }]) },
    { label: "Move down", disabled: i === c.stages.length - 1, onClick: () => edit([{ op: "move", id: c.stages[i + 1].id, before: s.id }]) },
    { sep: true },
    { label: "Delete stage", icon: "trash", danger: true, disabled: s.readings.length > 0, onClick: () => edit([{ op: "remove", id: s.id }]) },
  ];

  return (
    <div className="page curriculum">
      <div className="crumbs small"><Link to="/curricula"><Icon name="course" />Curricula</Link></div>
      <div className="page-head">
        <h1>{c.title}</h1>
        <div className="row" style={{ gap: 6 }}>
          <button className="btn sm" onClick={download}><Icon name="download" />Syllabus (Markdown)</button>
          <button className="btn sm icon" aria-label="More" title="More" onClick={(e) => menu(e, [
            { label: "Delete curriculum", icon: "trash", danger: true, onClick: async () => {
              if (await confirm(`Delete “${c.title}”?`, <p className="muted" style={{ margin: 0 }}>The papers and their links stay in the library.</p>, "Delete", true)) {
                await api.deleteCurriculum(c.id); nav("/curricula");
              }
            } },
          ])}><Icon name="more" /></button>
        </div>
      </div>
      {c.idea && <p className="lede"><Tex text={c.idea} /></p>}
      <div className="cur-stats">
        {c.audience && <span>For {c.audience}</span>}
        <span>{c.stages.length} stages · {readings.length} readings{minutes ? ` · ${hours(minutes)}` : ""}</span>
        {readings.length > 0 && (
          <span className="progress" title={`${finished} of ${readings.length} read as deeply as suggested`}>
            <span className="bar"><span style={{ width: `${(100 * finished) / readings.length}%` }} /></span>{finished}/{readings.length} done
          </span>
        )}
      </div>
      {c.intro && <div className="cur-intro"><Tex text={c.intro} /></div>}

      <div className="seg" style={{ margin: "18px 0 14px" }}>
        <button className={tab === "syllabus" ? "on" : ""} onClick={() => setTab("syllabus")}>Syllabus</button>
        <button className={tab === "lineage" ? "on" : ""} onClick={() => setTab("lineage")}>Lineage{c.lineage.length ? ` (${c.lineage.length})` : ""}</button>
      </div>

      {tab === "syllabus" && (
        <ol className="stages">
          {c.stages.map((s, i) => (
            <li key={s.id} className="stage">
              <div className="stage-head">
                <span className="stage-n">{i + 1}</span>
                <div className="grow">
                  <h2>{s.title}</h2>
                  {s.goal && <div className="muted"><Tex text={s.goal} /></div>}
                </div>
                <button className="btn ghost sm icon" aria-label={`More for ${s.title}`} onClick={(e) => menu(e, stageMenu(s, i))}><Icon name="more" /></button>
              </div>
              <ol className="readings">
                {s.readings.map((r, j) => (
                  <li key={r.id} className={`reading${done(r) ? " done" : ""}`}>
                    <span className="reading-n">{i + 1}.{j + 1}</span>
                    <div className="grow">
                      <div className="reading-title">
                        {r.work ? <Link to={`/work/${encodeURIComponent(r.work.citekey)}`}>{r.work.title}</Link> : <i>missing work</i>}
                      </div>
                      {r.work && <div className="w-meta"><span>{r.work.authors}</span>{r.work.year && <span>{r.work.year}</span>}
                        {r.work.venue && <span>{r.work.venue}</span>}<span className="mono">{r.work.citekey}</span></div>}
                      {r.why && <p className="why"><Tex text={r.why} /></p>}
                      {r.focus && <div className="focus"><b>Focus</b> <Tex text={r.focus} /></div>}
                      {!!r.concepts?.length && <div className="concepts">{r.concepts.map((k) => <span key={k} className="chip">{k}</span>)}</div>}
                    </div>
                    <div className="reading-side">
                      <span className={`depth d-${r.depth ?? "read"}`}>{r.depth ?? "read"}</span>
                      {r.minutes ? <span className="small muted">{r.minutes} min</span> : null}
                      <button className={`btn sm${done(r) ? " ok" : ""}`}
                              title={done(r) ? "Finished. Click to undo" : `Mark done; the paper also counts as read (${NEED[r.depth ?? "read"]}) in the library`}
                              onClick={() => finish(r, !done(r))}>
                        <Icon name="check" />{done(r) ? "Done" : "Mark done"}
                      </button>
                      <button className="btn ghost sm icon" aria-label="More" onClick={(e) => menu(e, readingMenu(s, r, j))}><Icon name="more" /></button>
                    </div>
                  </li>
                ))}
              </ol>
              {!!s.checkpoints?.length && (
                <div className="checkpoints">
                  <b>Check yourself</b>
                  <ul>{s.checkpoints.map((q) => <li key={q}><Tex text={q} /></li>)}</ul>
                </div>
              )}
            </li>
          ))}
          {c.stages.length === 0 && <div className="empty">No stages yet. An agent is probably still reading; this page updates as it writes.</div>}
        </ol>
      )}

      {tab === "lineage" && <Lineage c={c} />}
    </div>
  );
}

/** How the idea evolved: works by year left to right, in their stage's lane; arrows from the older work to the newer. */
function Lineage({ c }: { c: Curriculum }) {
  const box = useRef<HTMLDivElement>(null);
  const [picked, setPicked] = useState<{ step?: CStep; work?: string } | null>(null);
  const stageOf = useMemo(() => {
    const m: Record<string, number> = {};
    c.stages.forEach((s, i) => s.readings.forEach((r) => { if (r.work) m[r.work.id] = i; }));
    return m;
  }, [c]);
  const why = useMemo(() => Object.fromEntries(c.stages.flatMap((s) => s.readings.filter((r) => r.work).map((r) => [r.work!.id, r.why ?? ""]))), [c]);
  const steps = useMemo(() => [...c.lineage].sort((a, b) => (c.works[a.work]?.year ?? 0) - (c.works[b.work]?.year ?? 0)), [c]);

  useEffect(() => {
    if (!box.current) return;
    // a column per year, a lane per stage; a lane is as tall as its most crowded year, so lanes never overlap
    const ids = Object.keys(c.works);
    const years = [...new Set(ids.map((w) => c.works[w].year ?? 0))].sort((a, b) => a - b);
    const COL = 210, ROW = 50, GAP = 34;
    const col = (y: number) => years.indexOf(y) * COL;
    const cell = (w: string) => `${c.works[w].year ?? 0}:${stageOf[w] ?? 0}`;
    const crowd: Record<string, number> = {};
    ids.forEach((w) => { crowd[cell(w)] = (crowd[cell(w)] ?? 0) + 1; });
    const laneTop: number[] = [];
    let top = 0;
    c.stages.forEach((_, i) => {
      laneTop[i] = top;
      top += Math.max(1, ...Object.entries(crowd).filter(([k]) => k.endsWith(`:${i}`)).map(([, n]) => n)) * ROW + GAP;
    });
    const used: Record<string, number> = {};
    const pos = (w: string) => {
      const n = (used[cell(w)] = (used[cell(w)] ?? -1) + 1);
      // zigzag within a crowded year, so an arrow between two works in it doesn't run through the ones between
      return { x: col(c.works[w].year ?? 0) + (n % 2) * 56, y: (laneTop[stageOf[w] ?? 0] ?? 0) + n * ROW };
    };
    const short = (t: string) => { const head = t.split(/[:—(]/)[0].trim(); return head.length > 26 ? head.slice(0, 25) + "…" : head; };
    const lineagePairs = new Set(c.lineage.map((e) => `${e.builds_on}>${e.work}`));
    const ink = css("--ink"), pen = css("--pen"), line = css("--line-strong"), surface = css("--surface"), muted = css("--muted");
    const cy = cytoscape({
      container: box.current, userZoomingEnabled: false, userPanningEnabled: false, boxSelectionEnabled: false, autoungrabify: true,
      elements: [
        ...years.map((y) => ({ data: { id: `year${y}`, label: y ? String(y) : "?", year: 1 }, position: { x: col(y), y: -50 }, selectable: false, grabbable: false })),
        ...c.stages.map((st, i) => ({ data: { id: `lane${i}`, label: `${i + 1}  ${short(st.title)}`, year: 1, lane: 1 },
          position: { x: -70, y: laneTop[i] }, selectable: false, grabbable: false })),
        ...ids.map((w) => ({ data: { id: w, label: c.works[w].citekey }, position: pos(w) })),
        ...c.cites.filter((e) => !lineagePairs.has(`${e.to}>${e.from}`)).map((e, i) => ({ data: { id: `c${i}`, source: e.to, target: e.from, cite: 1 } })),
        ...c.lineage.map((e) => ({ data: { id: e.id, source: e.builds_on, target: e.work, label: RELATION_WORD[e.relation] ?? e.relation, step: 1 } })),
      ],
      layout: { name: "preset", fit: false },
      style: [
        { selector: "node", style: { label: "data(label)", "font-family": "Nunito Variable, Nunito, sans-serif", "font-size": 11, "font-weight": 600, color: ink,
          "text-valign": "bottom", "text-margin-y": 4, width: 16, height: 16, "background-color": pen,
          "text-background-color": surface, "text-background-opacity": 0.85, "text-background-padding": "2px" } },
        { selector: "node[year = 1]", style: { "background-opacity": 0, "font-size": 13, color: muted, "text-valign": "center", width: 1, height: 1, events: "no" } },
        { selector: "node[lane = 1]", style: { "text-halign": "left", "font-size": 12, "text-background-opacity": 0 } },
        { selector: "edge", style: { "curve-style": "bezier", width: 1.6, "line-color": pen, "target-arrow-color": pen, "target-arrow-shape": "triangle",
          "font-size": 11, "font-weight": 600, color: ink, "text-rotation": "autorotate", "text-background-color": surface, "text-background-opacity": 0.95,
          "text-background-padding": "2px", opacity: 0.55 } },
        // a step's words show when you point at it or at either of its works
        { selector: "edge.hl", style: { label: "data(label)", width: 2.6, opacity: 1, "z-index": 10 } },
        { selector: "node.hl", style: { "border-width": 3, "border-color": surface, "z-index": 11 } },
        { selector: "edge[cite = 1]", style: { "line-style": "dashed", "line-color": line, "target-arrow-color": line, width: 0.8, label: "", opacity: 0.6 } },
        { selector: ":selected", style: { "overlay-color": pen, "overlay-opacity": 0.15 } },
      ],
    });
    // readable rather than squeezed: zoom no lower than 0.8, and the box as big as the graph (it scrolls sideways when wider)
    const PAD = 36, el = box.current, room = el.parentElement?.clientWidth ?? el.clientWidth;
    const bb = cy.elements().boundingBox({});
    const z = Math.min(1, Math.max(0.8, (room - 2 * PAD) / bb.w));
    el.style.width = `${Math.max(room, Math.ceil(bb.w * z + 2 * PAD))}px`;
    el.style.height = `${Math.ceil(bb.h * z + 2 * PAD)}px`;
    cy.resize();
    cy.zoom(z);
    cy.pan({ x: PAD - bb.x1 * z, y: PAD - bb.y1 * z });
    cy.on("tap", "edge[step = 1]", (e) => setPicked({ step: c.lineage.find((s) => s.id === e.target.id()) }));
    cy.on("tap", "node[^year]", (e) => setPicked({ work: e.target.id() }));
    cy.on("tap", (e) => { if (e.target === cy) setPicked(null); });
    cy.on("mouseover", "edge[step = 1]", (e) => e.target.addClass("hl").connectedNodes().addClass("hl"));
    cy.on("mouseover", "node[^year]", (e) => e.target.addClass("hl").connectedEdges("[step = 1]").addClass("hl"));
    cy.on("mouseout", "edge, node", () => cy.elements(".hl").removeClass("hl"));
    return () => cy.destroy();
  }, [c, stageOf]);

  const key = (w: string) => c.works[w]?.citekey ?? w;
  return (
    <div className="lineage">
      <div className="lineage-scroll"><div className="lineage-graph" ref={box} /></div>
      {picked?.step && (
        <div className="panel pad picked">
          <b>{key(picked.step.work)}</b> {RELATION_WORD[picked.step.relation] ?? picked.step.relation} <b>{key(picked.step.builds_on)}</b>
          {picked.step.change && <p><Tex text={picked.step.change} /></p>}
          {picked.step.evidence ? <blockquote><Tex text={picked.step.evidence} /></blockquote> : <div className="small muted">No quote recorded for this step.</div>}
        </div>
      )}
      {picked?.work && (
        <div className="panel pad picked">
          <Link to={`/work/${encodeURIComponent(key(picked.work))}`}><b>{c.works[picked.work]?.title}</b></Link>
          <div className="small muted">{c.works[picked.work]?.authors} {c.works[picked.work]?.year}</div>
          {why[picked.work] && <p><Tex text={why[picked.work]} /></p>}
        </div>
      )}
      <ol className="steps">
        {steps.map((e) => (
          <li key={e.id}>
            <span className="yr">{c.works[e.work]?.year ?? ""}</span>
            <div>
              <div><b>{key(e.work)}</b> {RELATION_WORD[e.relation] ?? e.relation} <b>{key(e.builds_on)}</b>{e.change && <>: <Tex text={e.change} /></>}</div>
              {e.evidence ? <blockquote><Tex text={e.evidence} /></blockquote> : <span className="small muted">no quote recorded</span>}
            </div>
          </li>
        ))}
        {steps.length === 0 && <div className="empty">No lineage recorded yet.</div>}
      </ol>
    </div>
  );
}
