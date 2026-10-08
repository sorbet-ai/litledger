import cytoscape from "cytoscape";
import fcose from "cytoscape-fcose";
import layoutUtilities from "cytoscape-layout-utilities";
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useApp } from "../app/context";
import { api } from "../lib/api";
import { cssVar as css, plural } from "../lib/format";
import { COLUMN_ORDER, kindStyle, THINKING } from "../lib/kinds";
import { store } from "../lib/storage";
import { Icon } from "../ui/Icon";
import { MenuButton, useOverlays } from "../ui/overlays";
import { useWorkMenu } from "../works/workMenu";

cytoscape.use(fcose);
cytoscape.use(layoutUtilities);

const READ: Record<string, string> = { full: "#2b7a4b", sections: "#4f9a6a", skimmed: "#2342c4", abstract: "#6e86d6", metadata: "#9aa6c4" };

type LayoutName = "force" | "spacious" | "columns";
const LAYOUTS: Record<LayoutName, string> = { force: "Force", spacious: "Force, spacious", columns: "Columns by kind" };

// Papers in the middle column, each kind in its own column either side; rows ordered by their neighbours' rows
// (a few barycenter sweeps) so links mostly run straight across.
function columnPositions(nodes: cytoscape.NodeCollection): Record<string, cytoscape.Position> {
  const kinds = [...new Set(nodes.map((n) => n.data("kind") as string))].filter((k) => k !== "work")
    .sort((a, b) => (COLUMN_ORDER.indexOf(a) + 100) % 200 - (COLUMN_ORDER.indexOf(b) + 100) % 200);
  const half = Math.ceil(kinds.length / 2);
  const cols = [...kinds.slice(0, half).reverse(), "work", ...kinds.slice(half)];
  const colOf: Record<string, string[]> = {};
  nodes.forEach((n) => { (colOf[n.data("kind")] ??= []).push(n.id()); });
  const row: Record<string, number> = {};
  cols.forEach((k) => (colOf[k] ?? []).forEach((id, i) => { row[id] = i; }));
  for (let sweep = 0; sweep < 4; sweep++) {
    cols.forEach((k) => {
      const ids = colOf[k] ?? [];
      const bary = (id: string) => {
        const nb = nodes.getElementById(id).neighborhood("node").filter((m) => m.style("display") !== "none" && m.data("kind") !== k);
        return nb.length ? nb.reduce((sum, m) => sum + row[m.id()] / Math.max(1, (colOf[m.data("kind")] ?? []).length), 0) / nb.length : 0.5;
      };
      const score = Object.fromEntries(ids.map((id) => [id, bary(id)]));
      ids.sort((a, b) => score[a] - score[b]).forEach((id, i) => { row[id] = i; });
    });
  }
  const tallest = Math.max(...cols.map((k) => (colOf[k] ?? []).length));
  const pos: Record<string, cytoscape.Position> = {};
  cols.forEach((k, c) => {
    const ids = colOf[k] ?? [];
    const gap = Math.max(58, ((tallest - 1) * 58) / Math.max(1, ids.length - 1));
    const top = -((ids.length - 1) * gap) / 2;
    ids.forEach((id, i) => { pos[id] = { x: c * 230, y: top + i * gap }; });
  });
  return pos;
}

// Layouts size nodes with their labels, so long titles push their neighbours away instead of overlapping them.
function layoutOptions(name: LayoutName, randomize: boolean, nodes: cytoscape.NodeCollection): cytoscape.LayoutOptions {
  const common = { animate: !randomize, animationDuration: 450, fit: true, padding: 40, nodeDimensionsIncludeLabels: true };
  if (name === "columns") {
    const pos = columnPositions(nodes);
    return { name: "preset", positions: (n: cytoscape.NodeSingular) => pos[n.id()], ...common } as cytoscape.LayoutOptions;
  }
  const spread = name === "spacious" ? 1.8 : 1;
  return {
    name: "fcose", quality: "proof", randomize, ...common,
    nodeRepulsion: () => 12000 * spread, idealEdgeLength: (e: cytoscape.EdgeSingular) => (e.data("kind") === "cites" ? 140 : 90) * spread,
    edgeElasticity: () => 0.35, nodeSeparation: 110 * spread, gravity: 0.2, gravityRange: 4, numIter: 4000,
    packComponents: true, tile: true, tilingPaddingVertical: 24, tilingPaddingHorizontal: 24,
  } as cytoscape.LayoutOptions;
}

export default function GraphPage() {
  const { project, theme, subscribe } = useApp();
  const { menu } = useOverlays();
  const workMenu = useWorkMenu();
  const box = useRef<HTMLDivElement>(null);
  const cy = useRef<cytoscape.Core | null>(null);
  const [tagInput, setTagInput] = useState("");
  const [tags, setTags] = useState("");
  useEffect(() => { const t = window.setTimeout(() => setTags(tagInput), 400); return () => window.clearTimeout(t); }, [tagInput]);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [hidden, setHidden] = useState<Set<string>>(() => new Set(store.get<string[]>("litledger.graph.hidden", ["result"])));
  const [layout, setLayout] = useState<LayoutName>(() => store.get<LayoutName>("litledger.graph.layout", "force"));
  const [stats, setStats] = useState({ nodes: 0, edges: 0 });
  const [version, setVersion] = useState(0);
  const nav = useNavigate();
  const posKey = `litledger.graph.pos.${project}.${tags}`;
  const hiddenRef = useRef(hidden);
  hiddenRef.current = hidden;
  const layoutRef = useRef(layout);
  layoutRef.current = layout;

  const savePositions = () => {
    const c = cy.current;
    if (!c) return;
    const pos: Record<string, cytoscape.Position> = {};
    c.nodes().forEach((n) => { pos[n.id()] = n.position(); });
    store.set(posKey, pos);
  };

  const run = (name: LayoutName, randomize: boolean) => {
    const c = cy.current;
    if (!c) return;
    const visible = c.elements().filter((el) => el.style("display") !== "none");
    const l = visible.layout(layoutOptions(name, randomize, visible.nodes()));
    l.one("layoutstop", savePositions);
    l.run();
  };

  const applyHidden = () => {
    const c = cy.current;
    if (!c) return;
    c.batch(() => {
      c.nodes().forEach((n) => { n.style("display", hiddenRef.current.has(n.data("kind")) ? "none" : "element"); });
    });
    const shown = c.nodes().filter((n) => n.style("display") !== "none");
    setStats({ nodes: shown.length, edges: shown.edgesWith(shown).length });
  };

  useEffect(() => {
    let cancelled = false;
    api.network(tags).then((net) => {
      if (cancelled || !box.current) return;
      const degree: Record<string, number> = {};
      net.edges.forEach((e) => { degree[e.source] = (degree[e.source] ?? 0) + 1; degree[e.target] = (degree[e.target] ?? 0) + 1; });
      const linked = net.entities.filter((e) => degree[e.id]);
      const ids = new Set([...net.nodes.map((n) => n.id), ...linked.map((e) => e.id)]);
      const kinds: Record<string, number> = { work: net.nodes.length };
      linked.forEach((e) => { kinds[e.kind] = (kinds[e.kind] ?? 0) + 1; });
      setCounts(kinds);
      const saved = store.get<Record<string, cytoscape.Position>>(posKey, {});
      const elements: cytoscape.ElementDefinition[] = [
        ...net.nodes.map((n) => ({ data: { id: n.id, label: n.citekey, title: n.title, kind: "work", read: n.read ?? "", deg: degree[n.id] ?? 0, key: n.citekey, tags: n.tags },
                                    position: saved[n.id] })),
        ...linked.map((e) => ({ data: { id: e.id, label: e.title, title: e.title, kind: e.kind, deg: degree[e.id] ?? 0, thinking: THINKING.has(e.kind) ? 1 : 0 },
                                position: saved[e.id] })),
        ...net.edges.filter((e) => ids.has(e.source) && ids.has(e.target))
          .map((e, i) => ({ data: { id: `e${i}`, source: e.source, target: e.target, kind: e.kind, label: e.kind === "cites" ? "cites" : e.kind.replace(/_/g, " ") } })),
      ];
      // Nodes new since the last layout start next to a neighbour that has a position, so the layout only tidies.
      const known = elements.filter((el) => !el.data.source && el.position).length;
      const fresh = known < ids.size * 0.8;
      const placed = known < ids.size;
      if (!fresh) {
        const posOf = (id: string) => saved[id];
        elements.forEach((el) => {
          if (el.data.source || el.position) return;
          const nb = net.edges.find((e) => (e.source === el.data.id && posOf(e.target)) || (e.target === el.data.id && posOf(e.source)));
          const p = nb ? posOf(nb.source === el.data.id ? nb.target : nb.source) : undefined;
          el.position = p ? { x: p.x + (Math.random() - 0.5) * 80, y: p.y + (Math.random() - 0.5) * 80 } : { x: 0, y: 0 };
        });
      }

      cy.current?.destroy();
      const ink = css("--ink"), muted = css("--muted"), pen = css("--pen"), line = css("--line-strong"), marker = css("--marker"), surface = css("--surface");
      const fill = (k: string) => css(`--k-${k}`) || muted;
      cy.current = cytoscape({
        container: box.current, elements, wheelSensitivity: 0.25, maxZoom: 2.5, minZoom: 0.08,
        layout: { name: "preset", fit: true, padding: 40 },
        style: [
          { selector: "node", style: { label: "data(label)", "font-family": "Instrument Sans, sans-serif", "font-size": 11, color: ink,
            "text-valign": "bottom", "text-margin-y": 4, "text-wrap": "ellipsis", "text-max-width": "150px",
            "text-background-color": surface, "text-background-opacity": 0.85, "text-background-padding": "2px", "text-background-shape": "roundrectangle",
            "min-zoomed-font-size": 7,
            width: "mapData(deg, 0, 16, 12, 40)", height: "mapData(deg, 0, 16, 12, 40)", "background-color": muted, "border-width": 0,
            "transition-property": "opacity", "transition-duration": 150 } },
          { selector: "node[kind = 'work']", style: { "font-weight": 600 } },
          ...Object.entries(READ).map(([k, c]) => ({ selector: `node[kind = "work"][read = "${k}"]`, style: { "background-color": c } })),
          { selector: "node[kind != 'work']", style: { shape: "round-rectangle", "background-color": (el: cytoscape.NodeSingular) => fill(el.data("kind")) } },
          { selector: "node[thinking = 1]", style: { "background-color": marker, "border-width": 1, "border-color": ink } },
          { selector: "edge", style: { width: 1, "line-color": line, "target-arrow-color": line, "target-arrow-shape": "triangle", "curve-style": "bezier",
            "arrow-scale": 0.7, opacity: 0.7, "font-size": 9.5, color: muted, "text-background-color": surface, "text-background-opacity": 0.9,
            "text-background-padding": "1px", "text-rotation": "autorotate", "min-zoomed-font-size": 7 } },
          { selector: "edge[kind != 'cites']", style: { "line-color": pen, "target-arrow-color": pen, width: 1.3, opacity: 0.55 } },
          // Hovering or selecting a node brings out its neighbourhood and the names of its links; everything else fades.
          { selector: ".faded", style: { opacity: 0.1, "text-opacity": 0 } },
          { selector: "edge.hl", style: { label: "data(label)", opacity: 1, width: 2, "z-index": 10 } },
          { selector: "node.hl", style: { "z-index": 10, "text-max-width": "260px" } },
          { selector: "node.focus", style: { "text-wrap": "wrap", "text-max-width": "260px", "font-size": 12, "z-index": 20 } },
          { selector: "node:selected", style: { "border-width": 3, "border-color": pen } },
        ],
      });
      const c = cy.current;
      applyHidden();
      if (fresh || placed) run(layoutRef.current, fresh);

      const highlight = (n: cytoscape.NodeSingular | null) => {
        c.batch(() => {
          c.elements().removeClass("faded hl focus");
          if (!n) return;
          const near = n.closedNeighborhood();
          c.elements().not(near).addClass("faded");
          near.addClass("hl");
          n.addClass("focus");
        });
      };
      let pinned: cytoscape.NodeSingular | null = null;
      c.on("mouseover", "node", (evt) => { if (!pinned) highlight(evt.target); });
      c.on("mouseout", "node", () => { if (!pinned) highlight(null); });
      c.on("tap", "node", (evt) => { pinned = evt.target; highlight(pinned); });
      c.on("tap", (evt) => { if (evt.target === c) { pinned = null; highlight(null); } });
      c.on("dragfree", "node", savePositions);
      c.on("dbltap", "node", (evt) => {
        const d = evt.target.data();
        nav(d.kind === "work" ? `/work/${encodeURIComponent(d.key)}` : `/knowledge?item=${encodeURIComponent(d.id)}`);
      });
      c.on("cxttap", "node", (evt) => {
        const n = evt.target as cytoscape.NodeSingular;
        const d = n.data();
        const oe = evt.originalEvent as MouseEvent;
        const focus = { label: "Show only this and its neighbours", icon: "fit", onClick: () => {
          c.elements().not(n.closedNeighborhood()).style("display", "none");
          run(layoutRef.current, false);
        } };
        const expand = { label: "Show its neighbours too", onClick: () => {
          n.closedNeighborhood().style("display", "element");
          run(layoutRef.current, false);
        } };
        if (d.kind === "work") menu(oe, [...workMenu({ citekey: d.key, tags: d.tags, in_project: true }, () => setVersion((v) => v + 1)), { sep: true }, focus, expand]);
        else menu(oe, [{ label: "Open", icon: "open", onClick: () => nav(`/knowledge?item=${encodeURIComponent(d.id)}`) }, focus, expand,
                       { label: `Hide this`, icon: "x", onClick: () => n.style("display", "none") }]);
      });
      c.on("cxttap", (evt) => {
        if (evt.target !== c) return;
        menu(evt.originalEvent as MouseEvent, [
          { label: "Fit to screen", icon: "fit", onClick: () => c.fit(undefined, 40) },
          { label: "Show everything", onClick: () => { applyHidden(); run(layoutRef.current, false); } },
          { sep: true },
          ...(Object.keys(LAYOUTS) as LayoutName[]).map((k) => ({ label: `Arrange: ${LAYOUTS[k]}`, icon: "layout", onClick: () => pick(k) })),
        ]);
      });
    });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project, tags, nav, theme, version, menu, workMenu]);
  // Rebuild only when something the graph shows changed, at most every couple of seconds; saved positions keep the layout.
  useEffect(() => {
    let timer: number | undefined;
    const off = subscribe((e) => {
      if (!/^(project\.|link\.|entity\.|document\.|work\.|tag\.)/.test(e.op)) return;
      window.clearTimeout(timer);
      timer = window.setTimeout(() => setVersion((v) => v + 1), 2000);
    });
    return () => { window.clearTimeout(timer); off(); };
  }, [subscribe]);
  useEffect(() => () => cy.current?.destroy(), []);

  const pick = (k: LayoutName) => {
    setLayout(k);
    store.set("litledger.graph.layout", k);
    run(k, k !== "columns");
  };

  const toggle = (kind: string) => {
    const next = new Set(hidden);
    if (next.has(kind)) next.delete(kind); else next.add(kind);
    setHidden(next);
    hiddenRef.current = next;
    store.set("litledger.graph.hidden", [...next]);
    applyHidden();
    run(layout, false);
  };

  const kinds = useMemo(() => Object.entries(counts).sort(([a], [b]) => (a === "work" ? -1 : b === "work" ? 1 : (THINKING.has(a) ? 1 : 0) - (THINKING.has(b) ? 1 : 0) || a.localeCompare(b))), [counts]);

  return (
    <div className="page full">
      <div className="topbar">
        <h2 style={{ marginRight: 8 }}>Graph</h2>
        <div className="kind-toggles">
          {kinds.map(([k, n]) => (
            <button key={k} className={`ktoggle${hidden.has(k) ? " off" : ""}`} title={hidden.has(k) ? `Show ${plural(k).toLowerCase()}` : `Hide ${plural(k).toLowerCase()}`}
                    style={k === "work" ? { ["--kc" as string]: "var(--pen)" } : kindStyle(k)} onClick={() => toggle(k)}>
              <span className="sw" />{plural(k)}<span className="n">{n}</span>
            </button>
          ))}
        </div>
        <span className="grow" />
        <span className="small muted">{stats.nodes} nodes · {stats.edges} links</span>
        <input className="input" style={{ width: 220 }} placeholder="Filter by tag" value={tagInput} onChange={(e) => setTagInput(e.target.value)} />
        <MenuButton label={`Arrange: ${LAYOUTS[layout]}`} icon="layout" className="btn sm"
                    items={(Object.keys(LAYOUTS) as LayoutName[]).map((k) => ({ label: LAYOUTS[k], icon: k === layout ? "check" : undefined, onClick: () => pick(k) }))} />
        <button className="btn sm icon" title="Fit to screen" aria-label="Fit to screen" onClick={() => cy.current?.fit(undefined, 40)}><Icon name="fit" /></button>
      </div>
      <div className="stage canvas-bg">
        <div ref={box} className="fill" />
        <div className="floatbar small muted graph-legend" style={{ left: 12, bottom: 12 }}>
          {Object.entries(READ).map(([k, c]) => <span key={k}><span className="dot" style={{ ["--c" as string]: c }} />{k}</span>)}
          <span><span className="dot" style={{ ["--c" as string]: "var(--muted)" }} />not read</span>
          <span className="sepv" />
          Grey arrows are citations, blue are typed links. Click a node to focus it, double-click to open, right-click for more.
        </div>
      </div>
    </div>
  );
}
