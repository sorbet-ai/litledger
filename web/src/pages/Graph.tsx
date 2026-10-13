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

// How far a paper has been read shows as a ring; its fill and size show how connected it is.
const READ: Record<string, string> = { full: "#2b7a4b", sections: "#4f9a6a", skimmed: "#d08a1e", abstract: "#c9a64a", metadata: "#b9b2a0" };

type LayoutName = "force" | "spacious" | "columns";
const LAYOUTS: Record<LayoutName, string> = { force: "Force", spacious: "Force, spacious", columns: "Columns by kind" };
const ALWAYS_LABELLED = 12;  // the most connected papers are named at any zoom; more names appear as you zoom in

type Hub = { id: string; key: string; title: string; kind: string; deg: number; citedBy: number; cites: number };

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

// Well-connected nodes push harder, so the hubs spread out with their neighbourhoods around them instead of
// piling into the middle of one ball.
function layoutOptions(name: LayoutName, randomize: boolean, nodes: cytoscape.NodeCollection): cytoscape.LayoutOptions {
  const common = { animate: !randomize, animationDuration: 450, fit: true, padding: 40, nodeDimensionsIncludeLabels: false };
  if (name === "columns") {
    const pos = columnPositions(nodes);
    return { name: "preset", positions: (n: cytoscape.NodeSingular) => pos[n.id()], ...common } as cytoscape.LayoutOptions;
  }
  const spread = name === "spacious" ? 1.8 : 1;
  return {
    name: "fcose", quality: "proof", randomize, ...common,
    nodeRepulsion: (n: cytoscape.NodeSingular) => (4500 + 900 * Math.min(60, n.data("deg"))) * spread,
    idealEdgeLength: (e: cytoscape.EdgeSingular) => (e.data("kind") === "cites" ? 80 : 60) * spread,
    edgeElasticity: () => 0.3, nodeSeparation: 70 * spread, gravity: 0.35, gravityRange: 3.8, numIter: 5000,
    packComponents: true, tile: true, tilingPaddingVertical: 24, tilingPaddingHorizontal: 24,
  } as cytoscape.LayoutOptions;
}

/** A colour between `a` and `b` (#rrggbb), `t` in 0..1. */
function mix(a: string, b: string, t: number): string {
  const p = (h: string) => { const m = /^#?([0-9a-f]{6})$/i.exec(h.trim()); const n = m ? parseInt(m[1], 16) : 0x888888; return [n >> 16, (n >> 8) & 255, n & 255]; };
  const [x, y] = [p(a), p(b)];
  return "#" + x.map((v, i) => Math.round(v + (y[i] - v) * t).toString(16).padStart(2, "0")).join("");
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
  const [showLoose, setShowLoose] = useState(() => store.get<boolean>("litledger.graph.loose", false));
  const [minLinks, setMinLinks] = useState(1);
  const [maxDeg, setMaxDeg] = useState(1);
  const [loose, setLoose] = useState(0);
  const [layout, setLayout] = useState<LayoutName>(() => store.get<LayoutName>("litledger.graph.layout", "force"));
  const [stats, setStats] = useState({ nodes: 0, edges: 0 });
  const [hubs, setHubs] = useState<Hub[]>([]);
  const [panel, setPanel] = useState(() => store.get<boolean>("litledger.graph.panel", true));
  const [active, setActive] = useState<string | null>(null);
  const [version, setVersion] = useState(0);
  const nav = useNavigate();
  const posKey = `litledger.graph.pos2.${project}.${tags}`;
  const filters = useRef({ hidden, showLoose, minLinks });
  filters.current = { hidden, showLoose, minLinks };
  const layoutRef = useRef(layout);
  layoutRef.current = layout;
  const highlightRef = useRef<(n: cytoscape.NodeSingular | null, pin?: boolean) => void>(() => {});

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
    l.one("layoutstop", () => { spread(visible.nodes()); savePositions(); relabel(); });
    l.run();
  };

  // Force layouts let big nodes overlap; push overlapping circles apart (bigger ones move less) until none touch.
  const spread = (nodes: cytoscape.NodeCollection) => {
    const ns = nodes.filter((n) => n.style("display") !== "none").toArray() as cytoscape.NodeSingular[];
    const p = ns.map((n) => ({ ...n.position() })), r = ns.map((n) => n.data("size") / 2 + 4);
    for (let it = 0; it < 60; it++) {
      let moved = false;
      for (let i = 0; i < ns.length; i++) for (let j = i + 1; j < ns.length; j++) {
        const dx = p[j].x - p[i].x, dy = p[j].y - p[i].y, dist = Math.hypot(dx, dy) || 0.01, gap = r[i] + r[j] - dist;
        if (gap <= 0) continue;
        moved = true;
        const ux = dx / dist, uy = dy / dist, wi = r[j] / (r[i] + r[j]), wj = r[i] / (r[i] + r[j]);
        p[i].x -= ux * gap * wi; p[i].y -= uy * gap * wi; p[j].x += ux * gap * wj; p[j].y += uy * gap * wj;
      }
      if (!moved) break;
    }
    cy.current?.batch(() => ns.forEach((n, i) => n.position(p[i])));
  };

  // Names go to the most connected nodes only: ALWAYS_LABELLED of them at the fitted view, more as you zoom in. Each
  // sits below, above or beside its node, wherever it covers no other name and no better-connected node; a named node
  // is drawn over the smaller ones, so its name stays readable.
  // With a node picked, the same placement names it first and then its best-connected neighbours.
  const focusRef = useRef<cytoscape.NodeSingular | null>(null);
  const relabel = () => {
    const c = cy.current;
    if (!c) return;
    const focus = focusRef.current;
    const pool = focus ? focus.closedNeighborhood().nodes() : c.nodes();
    const shown = pool.filter((n) => n.style("display") !== "none")
      .sort((a, b) => (focus ? Number(b.same(focus)) - Number(a.same(focus)) : 0) || b.data("deg") - a.data("deg")).nodes();
    if (focus) c.nodes().not(shown).data("shown", "");
    const z = c.zoom(), zoomed = Math.max(1, z / (c.data("fitZoom") || z));
    const limit = focus ? 11 : Math.round(ALWAYS_LABELLED * zoomed * zoomed);
    type Box = { x1: number; y1: number; x2: number; y2: number };
    const circles = shown.map((n) => ({ id: n.id(), deg: n.data("deg") as number, ...n.renderedPosition(), r: (n.data("size") * z) / 2 + 1 }));
    const hits = (b: Box, id: string, deg: number) => circles.some((o) => o.id !== id && o.deg >= deg &&
      Math.hypot(Math.max(b.x1, Math.min(o.x, b.x2)) - o.x, Math.max(b.y1, Math.min(o.y, b.y2)) - o.y) < o.r);
    const boxes: Box[] = [];
    c.batch(() => shown.forEach((n, rank) => {
      let label = "", pos = "below";
      if (rank < limit && n.data("deg") > 0) {
        const at = n.renderedPosition(), fs = n.data("fsize"), half = (n.data("size") * z) / 2;  // names keep their screen size
        const w = n.data("label").length * fs * 0.56 + 6, h = fs + 5;
        const spots: [string, Box][] = [
          ["below", { x1: at.x - w / 2, y1: at.y + half + 2, x2: at.x + w / 2, y2: at.y + half + 2 + h }],
          ["above", { x1: at.x - w / 2, y1: at.y - half - 2 - h, x2: at.x + w / 2, y2: at.y - half - 2 }],
          ["right", { x1: at.x + half + 3, y1: at.y - h / 2, x2: at.x + half + 3 + w, y2: at.y + h / 2 }],
          ["left", { x1: at.x - half - 3 - w, y1: at.y - h / 2, x2: at.x - half - 3, y2: at.y + h / 2 }],
        ];
        const free = spots.find(([, b]) => !boxes.some((o) => b.x1 < o.x2 && b.x2 > o.x1 && b.y1 < o.y2 && b.y2 > o.y1) && !hits(b, n.id(), n.data("deg")));
        if (free) {
          boxes.push(free[1]);
          [pos, label] = [free[0], n.data("label")];
        }
      }
      n.data({ shown: label, pos, z: label ? 1000 + n.data("deg") : n.data("deg"), fs: n.data("fsize") / z, valign: pos === "below" ? "bottom" : pos === "above" ? "top" : "center",
               halign: pos === "right" ? "right" : pos === "left" ? "left" : "center" });
    }));
  };

  const applyFilters = () => {
    const c = cy.current;
    if (!c) return;
    const { hidden: h, showLoose: sl, minLinks: m } = filters.current;
    c.batch(() => {
      c.nodes().forEach((n) => {
        const deg = n.data("deg") as number;
        const off = h.has(n.data("kind")) || (deg === 0 ? !sl : deg < m);
        n.style("display", off ? "none" : "element");
      });
    });
    const shown = c.nodes().filter((n) => n.style("display") !== "none");
    setStats({ nodes: shown.length, edges: shown.edgesWith(shown).length });
    setHubs(shown.filter((n) => n.data("deg") > 0).sort((a, b) => b.data("deg") - a.data("deg")).slice(0, 30)
      .map((n) => ({ id: n.id(), key: n.data("key") ?? "", title: n.data("title") ?? "", kind: n.data("kind"), deg: n.data("deg"),
                     citedBy: n.data("citedBy"), cites: n.data("cites") })));
  };

  useEffect(() => {
    let cancelled = false;
    api.network(tags).then((net) => {
      if (cancelled || !box.current) return;
      const degree: Record<string, number> = {}, citedBy: Record<string, number> = {}, cites: Record<string, number> = {};
      net.edges.forEach((e) => {
        degree[e.source] = (degree[e.source] ?? 0) + 1;
        degree[e.target] = (degree[e.target] ?? 0) + 1;
        if (e.kind === "cites") { cites[e.source] = (cites[e.source] ?? 0) + 1; citedBy[e.target] = (citedBy[e.target] ?? 0) + 1; }
      });
      const linked = net.entities.filter((e) => degree[e.id]);
      const ids = new Set([...net.nodes.map((n) => n.id), ...linked.map((e) => e.id)]);
      const kinds: Record<string, number> = { work: net.nodes.length };
      linked.forEach((e) => { kinds[e.kind] = (kinds[e.kind] ?? 0) + 1; });
      setCounts(kinds);
      setLoose(net.nodes.filter((n) => !degree[n.id]).length);
      const top = Math.max(1, ...Object.values(degree));
      setMaxDeg(top);

      // size grows almost in step with links, and the fill stays pale until a paper is really well connected, so a
      // paper with 60 links stands out from one with 15 and the weakly connected recede
      const pen = css("--pen"), faint = mix(css("--surface"), css("--line-strong"), 0.9), ink = css("--ink");
      const weight = (d: number) => Math.pow(d / top, 0.85);
      const shade = (d: number) => Math.pow(d / top, 1.4);
      const saved = store.get<Record<string, cytoscape.Position>>(posKey, {});
      const elements: cytoscape.ElementDefinition[] = [
        ...net.nodes.map((n) => {
          const d = degree[n.id] ?? 0, w = weight(d);
          return { data: { id: n.id, label: n.citekey, shown: "", title: n.title, kind: "work", read: n.read ?? "", deg: d, w,
                           size: 7 + 50 * w, fill: d ? mix(faint, pen, 0.12 + 0.88 * shade(d)) : faint, fsize: 11 + 5 * w,
                           citedBy: citedBy[n.id] ?? 0, cites: cites[n.id] ?? 0, key: n.citekey, tags: n.tags },
                   position: saved[n.id] };
        }),
        ...linked.map((e) => {
          const d = degree[e.id] ?? 0, w = weight(d);
          return { data: { id: e.id, label: e.title, shown: "", title: e.title, kind: e.kind, deg: d, w, size: 10 + 40 * w, fsize: 11 + 3 * w,
                           citedBy: 0, cites: 0, thinking: THINKING.has(e.kind) ? 1 : 0 },
                   position: saved[e.id] };
        }),
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
      const muted = css("--muted"), line = css("--line-strong"), marker = css("--marker"), surface = css("--surface");
      const kindFill = (k: string) => css(`--k-${k}`) || muted;
      cy.current = cytoscape({
        container: box.current, elements, wheelSensitivity: 0.25, maxZoom: 3, minZoom: 0.08,
        layout: { name: "preset", fit: true, padding: 40 },
        style: [
          { selector: "node", style: { label: "data(shown)", "font-family": "Instrument Sans, sans-serif", "font-size": (n: cytoscape.NodeSingular) => n.data("fs") ?? n.data("fsize"), color: ink,
            "font-weight": 600, "text-valign": ((n: cytoscape.NodeSingular) => n.data("valign") ?? "bottom") as unknown as "bottom",
            "text-halign": ((n: cytoscape.NodeSingular) => n.data("halign") ?? "center") as unknown as "center", "text-margin-y": 0, "text-wrap": "ellipsis", "text-max-width": "400px",
            "text-background-color": surface, "text-background-opacity": 0.85, "text-background-padding": "2px", "text-background-shape": "roundrectangle",
            "min-zoomed-font-size": 0, width: "data(size)", height: "data(size)", "background-color": "data(fill)", "border-width": 0,
            "z-index": (n: cytoscape.NodeSingular) => n.data("z") ?? n.data("deg"), "transition-property": "opacity", "transition-duration": 150 } },
          // the ring: how far the paper has been read
          ...Object.entries(READ).map(([k, c]) => ({ selector: `node[kind = "work"][read = "${k}"]`, style: { "border-width": 1.5, "border-color": c } })),
          { selector: "node[kind != 'work']", style: { shape: "round-rectangle", "background-color": (el: cytoscape.NodeSingular) => kindFill(el.data("kind")) } },
          { selector: "node[thinking = 1]", style: { "background-color": marker, "border-width": 1, "border-color": ink } },
          // links stay in the background until a node is picked
          { selector: "edge", style: { width: 0.6, "line-color": line, "curve-style": "haystack", "haystack-radius": 0, opacity: 0.16,
            "font-size": 9.5, color: muted, "text-background-color": surface, "text-background-opacity": 0.9, "text-background-padding": "1px",
            "text-rotation": "autorotate", "min-zoomed-font-size": 7 } },
          { selector: "edge[kind != 'cites']", style: { "line-color": pen, width: 1.1, opacity: 0.35 } },
          // hovering or picking a node brings out its neighbourhood, its links with their direction, and their names
          { selector: ".faded", style: { opacity: 0.07, "text-opacity": 0 } },
          { selector: "edge.hl", style: { "curve-style": "bezier", "target-arrow-shape": "triangle", "target-arrow-color": "data(arrow)", "arrow-scale": 0.8,
            "line-color": "data(arrow)", opacity: 0.85, width: 1.4, "z-index": 900 } },
          { selector: "node.hl", style: { "z-index": (n: cytoscape.NodeSingular) => (n.data("shown") ? 1000 + n.data("deg") : 950) } },
          { selector: "node.focus", style: { "z-index": 999 } },
          { selector: "node:selected", style: { "overlay-color": pen, "overlay-opacity": 0.18, "overlay-padding": 6 } },
        ],
      });
      const c = cy.current;
      c.edges().forEach((e) => { e.data("arrow", e.data("kind") === "cites" ? ink : pen); });
      applyFilters();
      const afterFit = () => { c.data("fitZoom", c.zoom()); relabel(); };
      if (fresh || placed) {
        run(layoutRef.current, fresh);
        c.one("layoutstop", afterFit);
      } else {
        c.fit(undefined, 40);
        afterFit();
      }
      let frame = 0;
      c.on("zoom", () => { cancelAnimationFrame(frame); frame = requestAnimationFrame(relabel); });

      let pinned: cytoscape.NodeSingular | null = null;
      const highlight = (n: cytoscape.NodeSingular | null, pin = false) => {
        if (pin) pinned = n;
        c.batch(() => {
          c.elements().removeClass("faded hl focus");
          focusRef.current = n;
          if (!n) return;
          const near = n.closedNeighborhood();
          c.elements().not(near).addClass("faded");
          near.addClass("hl");
          n.addClass("focus");

        });
        relabel();
        setActive(n ? n.id() : null);
      };
      highlightRef.current = highlight;
      c.on("mouseover", "node", (evt) => { if (!pinned) highlight(evt.target); });
      c.on("mouseout", "node", () => { if (!pinned) highlight(null); });
      c.on("tap", "node", (evt) => highlight(evt.target, true));
      c.on("tap", (evt) => { if (evt.target === c) highlight(null, true); });
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
          { label: "Show everything", onClick: () => { applyFilters(); run(layoutRef.current, false); } },
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

  const refilter = () => { applyFilters(); relabel(); run(layoutRef.current, false); };

  const toggle = (kind: string) => {
    const next = new Set(hidden);
    if (next.has(kind)) next.delete(kind); else next.add(kind);
    setHidden(next);
    filters.current.hidden = next;
    store.set("litledger.graph.hidden", [...next]);
    refilter();
  };

  const toggleLoose = () => {
    const next = !showLoose;
    setShowLoose(next);
    filters.current.showLoose = next;
    store.set("litledger.graph.loose", next);
    refilter();
  };

  // the slider redraws as it moves, and lays out again once it settles
  const settle = useRef<number>();
  const changeMin = (m: number) => {
    setMinLinks(m);
    filters.current.minLinks = m;
    applyFilters();
    relabel();
    window.clearTimeout(settle.current);
    settle.current = window.setTimeout(() => run(layoutRef.current, false), 500);
  };

  const fly = (id: string) => {
    const c = cy.current;
    const n = c?.getElementById(id);
    if (!c || !n || n.empty()) return;
    highlightRef.current(n, true);
    c.animate({ fit: { eles: n.closedNeighborhood().filter((m) => m.style("display") !== "none"), padding: 60 } }, { duration: 450, complete: relabel });
  };

  const togglePanel = () => { setPanel(!panel); store.set("litledger.graph.panel", !panel); };

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
          {loose > 0 && (
            <button className={`ktoggle${showLoose ? "" : " off"}`} style={{ ["--kc" as string]: "var(--muted)" }} onClick={toggleLoose}
                    title={showLoose ? "Hide papers with no links" : "Show papers with no links"}>
              <span className="sw" />Unlinked<span className="n">{loose}</span>
            </button>
          )}
        </div>
        <span className="grow" />
        {maxDeg > 2 && (
          <label className="small muted minlinks" title="Hide papers with fewer links">
            At least <b>{minLinks}</b> link{minLinks === 1 ? "" : "s"}
            <input type="range" min={1} max={Math.max(2, Math.min(maxDeg, 40))} value={minLinks} onChange={(e) => changeMin(Number(e.target.value))} />
          </label>
        )}
        <span className="small muted">{stats.nodes} nodes · {stats.edges} links</span>
        <input className="input" style={{ width: 180 }} placeholder="Filter by tag" value={tagInput} onChange={(e) => setTagInput(e.target.value)} />
        <MenuButton label={`Arrange: ${LAYOUTS[layout]}`} icon="layout" className="btn sm"
                    items={(Object.keys(LAYOUTS) as LayoutName[]).map((k) => ({ label: LAYOUTS[k], icon: k === layout ? "check" : undefined, onClick: () => pick(k) }))} />
        <button className="btn sm icon" title="Fit to screen" aria-label="Fit to screen" onClick={() => cy.current?.fit(undefined, 40)}><Icon name="fit" /></button>
        <button className={`btn sm${panel ? " on" : ""}`} onClick={togglePanel} aria-pressed={panel}>Most connected</button>
      </div>
      <div className="stage canvas-bg">
        <div ref={box} className="fill" style={panel ? { right: 300 } : undefined} />
        {panel && (
          <aside className="hubs">
            <div className="hubs-head">
              <b>Most connected</b>
              <span className="small muted">links within this project</span>
            </div>
            <ol>
              {hubs.map((h) => (
                <li key={h.id} className={active === h.id ? "on" : ""} onMouseEnter={() => cy.current && highlightRef.current(cy.current.getElementById(h.id))}
                    onMouseLeave={() => highlightRef.current(null)} onClick={() => fly(h.id)}>
                  <span className="bar" style={{ width: `${Math.round((100 * h.deg) / maxDeg)}%` }} />
                  <span className="k">{h.kind === "work" ? h.key : h.title}</span>
                  <span className="n">{h.deg}</span>
                  {h.kind === "work" && <span className="t">{h.title}</span>}
                  {h.kind === "work" && <span className="d small muted">cited by {h.citedBy} · cites {h.cites}</span>}
                </li>
              ))}
              {hubs.length === 0 && <li className="small muted">No links yet.</li>}
            </ol>
          </aside>
        )}
        <div className="floatbar small muted graph-legend" style={{ left: 12, bottom: 12 }}>
          <span><span className="dot big" style={{ ["--c" as string]: "var(--pen)" }} />bigger and darker: more links</span>
          <span className="sepv" />
          ring:
          {Object.entries(READ).map(([k, c]) => <span key={k}><span className="dot ring" style={{ ["--c" as string]: c }} />{k}</span>)}
          <span className="sepv" />
          Click a node to see its links, double-click to open, right-click for more.
        </div>
      </div>
    </div>
  );
}
