// The map as the editor holds it: a forest of nodes (any number of top-level items) with free positions, plus
// cross-links. Changes are diffed against the last saved state and sent as map_edit ops.
import type { MapData } from "../lib/api";

type Sty = { color?: string; mark?: string; expanded?: false };
export type MNode = {
  id: string;
  parent: string | null;
  seq: number;
  ref: string | null; // citekey, entity id, tag:…, map:…, n:… — null for plain text
  kind: string; // "text" for plain nodes
  label: string; // the referenced item's title (derived, never stored)
  sub: string; // secondary line: citekey · year, "note on …", …
  text: string; // plain text, or the user's caption on a referenced node
  x: number | null;
  y: number | null;
  style: Sty;
};
export type MEdge = { id: string; src: string; dst: string; label: string; promoted: boolean };
export type Doc = { nodes: Record<string, MNode>; edges: Record<string, MEdge> };
export type Size = { width: number; height: number };
export type Op = Record<string, unknown>;

export const newId = () => (Date.now().toString(36) + Math.random().toString(36).slice(2, 8)).slice(-12);

export function fromServer(m: MapData): Doc {
  const nodes: Record<string, MNode> = {};
  m.nodes.forEach((n, i) => {
    const s = (n.style ?? {}) as Record<string, unknown>;
    const color = s.color as string | undefined;
    const mark = s.mark as string | undefined;
    let sub = "";
    if (n.kind === "work") sub = [n.ref, n.year].filter(Boolean).join(" · ");
    else if (n.kind === "note") sub = `note on ${n.subject ?? "?"}${n.verified ? " · verified quote" : ""}`;
    else if (n.kind === "tag") sub = `${n.count ?? 0} papers`;
    else if (n.kind === "map") sub = "map";
    nodes[n.id] = {
      id: n.id, parent: n.parent, seq: n.seq ?? i, ref: n.ref, kind: n.ref ? n.kind : "text",
      label: n.ref ? (n.title ?? n.ref) : "", sub, text: n.text ?? "", x: n.x, y: n.y,
      style: { ...(color ? { color } : {}), ...(mark ? { mark } : {}), ...(s.expanded === false ? { expanded: false } : {}) },
    };
  });
  Object.values(nodes).forEach((n) => { if (n.parent && !nodes[n.parent]) n.parent = null; });
  // Old data may hold a parent loop: the node where it closes becomes a top-level item.
  Object.values(nodes).forEach((n) => { if (wouldLoop(nodes, n.id, n.parent)) n.parent = null; });
  const edges: Record<string, MEdge> = {};
  m.edges.forEach((e) => { if (nodes[e.src] && nodes[e.dst]) edges[e.id] = { id: e.id, src: e.src, dst: e.dst, label: e.label ?? "", promoted: e.promoted }; });
  return { nodes, edges };
}

const styleKey = (s: Sty) => JSON.stringify([s.color ?? null, s.mark ?? null, s.expanded ?? null]);
// Null clears a key on the server (and the old editor's keys are cleared once a node is restyled).
const storedStyle = (s: Sty) => ({ color: s.color ?? null, mark: s.mark ?? null, expanded: s.expanded ?? null, style: null, icons: null, branchColor: null });

/** Ops that turn `prev` into `cur`, and the state they leave the server in. New nodes without text yet (an idea
 * still being typed) wait, with their branches and lines, until they have some. */
export function diff(prev: Doc, cur: Doc, opts: { keepWaiting?: boolean } = {}): { ops: Op[]; sent: Doc } {
  const waiting = new Set<string>();
  const kidsAll = kidsOf(cur);
  if (!opts.keepWaiting) Object.values(cur.nodes).forEach((n) => {
    if (!prev.nodes[n.id] && !n.ref && !n.text) [n.id, ...descendants(cur, n.id, kidsAll)].forEach((x) => { if (!prev.nodes[x]) waiting.add(x); });
  });
  if (waiting.size) {
    const nodes = Object.fromEntries(Object.entries(cur.nodes).filter(([k]) => !waiting.has(k)));
    const edges = Object.fromEntries(Object.entries(cur.edges).filter(([, e]) => !waiting.has(e.src) && !waiting.has(e.dst)));
    cur = { nodes, edges };
  }
  const ops: Op[] = [];
  // Parents before children, so a new subtree can be added in one call.
  const order = topo(cur);
  for (const id of order) {
    const c = cur.nodes[id];
    const p = prev.nodes[id];
    if (!p) {
      ops.push({ op: "add", id, parent: c.parent, seq: c.seq, ...(c.ref ? { ref: c.ref } : {}), ...(c.text ? { text: c.text } : {}),
                 style: storedStyle(c.style), ...(c.x != null ? { x: Math.round(c.x), y: Math.round(c.y ?? 0) } : {}) });
      continue;
    }
    const u: Record<string, unknown> = {};
    if (p.parent !== c.parent) u.parent = c.parent;
    if (p.seq !== c.seq) u.seq = c.seq;
    if (p.text !== c.text) u.text = c.text;
    if (p.ref !== c.ref) u.ref = c.ref ?? "";
    if (styleKey(p.style) !== styleKey(c.style)) u.style = storedStyle(c.style);
    if (Object.keys(u).length) ops.push({ op: "update", id, ...u });
    if (c.x != null && (p.x !== c.x || p.y !== c.y)) ops.push({ op: "move", id, x: Math.round(c.x), y: Math.round(c.y ?? 0) });
  }
  for (const id of Object.keys(prev.nodes)) if (!cur.nodes[id]) ops.push({ op: "delete", id });
  for (const e of Object.values(cur.edges)) {
    const p = prev.edges[e.id];
    if (!p) ops.push({ op: "edge", id: e.id, src: e.src, dst: e.dst, label: e.label });
    else if (p.label !== e.label) ops.push({ op: "edge_update", id: e.id, label: e.label });
  }
  for (const id of Object.keys(prev.edges)) if (!cur.edges[id] && cur.nodes[prev.edges[id].src] && cur.nodes[prev.edges[id].dst]) ops.push({ op: "unedge", id });
  return { ops, sent: cur };
}

// ------------------------------------------------------------------------------------------------ applying & rebasing
export type Applied = {
  doc: Doc;
  /** Ops that no longer fit (their node or edge is gone, or a new parent would make a loop). */
  dropped: Op[];
  /** Nodes left without their parent by a delete, now top-level items (e.g. an agent's addition under a node deleted here). */
  rescued: string[];
};

const toSty = (v: unknown): Sty => {
  const s = (v ?? {}) as Record<string, unknown>;
  return { ...(typeof s.color === "string" ? { color: s.color } : {}), ...(typeof s.mark === "string" ? { mark: s.mark } : {}),
           ...(s.expanded === false ? { expanded: false as const } : {}) };
};
const numOr = (v: unknown, d: number | null) => (typeof v === "number" ? v : d);

/** Apply map_edit ops (as `diff` makes them) to a doc. Where `source` holds the node or edge, its values are used
 * (an op only says which fields changed; the source doc has them exactly, labels included). A delete removes just
 * that node: its remaining children become top-level, so nothing the ops don't name is lost. With `resurrect`, a
 * text or ref change to a node that is gone brings it back from `source`; otherwise such ops are `dropped`. */
export function applyOps(doc: Doc, ops: Op[], source?: Doc, opts: { resurrect?: boolean } = {}): Applied {
  const nodes = { ...doc.nodes };
  const edges = { ...doc.edges };
  const dropped: Op[] = [];
  const rescued = new Set<string>();
  const placeable = (n: MNode): MNode => (n.parent && (!nodes[n.parent] || wouldLoop(nodes, n.id, n.parent)) ? { ...n, parent: null } : n);
  for (const op of ops) {
    const id = String(op.id ?? "");
    const src = source?.nodes[id];
    switch (op.op) {
      case "add": {
        const n: MNode = src ? { ...src } : {
          id, parent: typeof op.parent === "string" ? op.parent : null, seq: numOr(op.seq, 0)!, ref: typeof op.ref === "string" && op.ref ? op.ref : null,
          kind: op.ref ? "item" : "text", label: typeof op.ref === "string" ? op.ref : "", sub: "", text: typeof op.text === "string" ? op.text : "",
          x: numOr(op.x, null), y: numOr(op.y, null), style: toSty(op.style),
        };
        nodes[id] = placeable(n);
        break;
      }
      case "update": {
        const cur = nodes[id];
        if (!cur) {
          if (opts.resurrect && src && ("text" in op || "ref" in op)) nodes[id] = placeable({ ...src });
          else dropped.push(op);
          break;
        }
        const next = { ...cur };
        if ("parent" in op) {
          const p = src ? src.parent : typeof op.parent === "string" ? op.parent : null;
          if (p && (!nodes[p] || wouldLoop(nodes, id, p))) dropped.push({ op: "update", id, parent: p });
          else next.parent = p;
        }
        if ("seq" in op) next.seq = src ? src.seq : numOr(op.seq, next.seq)!;
        if ("text" in op) next.text = src ? src.text : typeof op.text === "string" ? op.text : "";
        if ("ref" in op) {
          if (src) Object.assign(next, { ref: src.ref, kind: src.kind, label: src.label, sub: src.sub });
          else if (typeof op.ref === "string" && op.ref) Object.assign(next, { ref: op.ref, kind: next.ref ? next.kind : "item", label: op.ref });
          else Object.assign(next, { ref: null, kind: "text", label: "", sub: "" });
        }
        if ("style" in op) next.style = src ? src.style : toSty(op.style);
        nodes[id] = next;
        break;
      }
      case "move": {
        const cur = nodes[id];
        if (!cur) { dropped.push(op); break; }
        nodes[id] = { ...cur, x: src ? src.x : numOr(op.x, cur.x), y: src ? src.y : numOr(op.y, cur.y) };
        break;
      }
      case "delete": {
        if (!nodes[id]) break;
        delete nodes[id];
        Object.values(nodes).forEach((n) => { if (n.parent === id) { nodes[n.id] = { ...n, parent: null }; rescued.add(n.id); } });
        Object.values(edges).forEach((e) => { if (e.src === id || e.dst === id) delete edges[e.id]; });
        break;
      }
      case "edge": {
        const e = source?.edges[id];
        const s = e?.src ?? String(op.src ?? ""), d = e?.dst ?? String(op.dst ?? "");
        if (!nodes[s] || !nodes[d]) { dropped.push(op); break; }
        edges[id] = e ? { ...e } : { id, src: s, dst: d, label: typeof op.label === "string" ? op.label : "", promoted: edges[id]?.promoted ?? false };
        break;
      }
      case "edge_update": {
        const cur = edges[id], e = source?.edges[id];
        if (!cur) {
          if (opts.resurrect && e && nodes[e.src] && nodes[e.dst]) edges[id] = { ...e, promoted: false };
          else dropped.push(op);
          break;
        }
        edges[id] = { ...cur, label: e ? e.label : typeof op.label === "string" ? op.label : cur.label };
        break;
      }
      case "unedge":
        delete edges[id];
        break;
      default:
        break; // meta, promote, expand: nothing the doc holds
    }
  }
  return { doc: { nodes, edges }, dropped, rescued: [...rescued].filter((x) => nodes[x]) };
}

/** The local doc moved onto a newer server state: `server` with the local pending changes (`base` → `local`)
 * re-applied. Pending changes win field by field; remote additions stay; ideas still being typed are kept.
 * `skip` leaves out ops the server refused, which reverts them. */
export function rebase(base: Doc, server: Doc, local: Doc, skip?: (op: Op) => boolean): Applied {
  const { ops } = diff(base, local, { keepWaiting: true });
  return applyOps(server, skip ? ops.filter((o) => !skip(o)) : ops, local, { resurrect: true });
}

/** Undo/redo snapshots moved onto the server state the same way, so stepping through them later sends only the
 * user's own changes. Null when one no longer fits (it would edit something removed elsewhere): drop the history. */
export function rebaseHistory(base: Doc, server: Doc, snaps: Doc[]): Doc[] | null {
  const out: Doc[] = [];
  for (const s of snaps) {
    const r = applyOps(server, diff(base, s, { keepWaiting: true }).ops, s);
    if (r.dropped.length) return null;
    out.push(r.doc);
  }
  return out;
}

// ------------------------------------------------------------------------------------------------ tree helpers
export function kidsOf(doc: Doc): Record<string, string[]> {
  const kids: Record<string, string[]> = {};
  Object.values(doc.nodes).sort((a, b) => a.seq - b.seq).forEach((n) => { (kids[n.parent ?? ""] ??= []).push(n.id); });
  return kids;
}

function topo(doc: Doc): string[] {
  const kids = kidsOf(doc);
  const out: string[] = [];
  const seen = new Set<string>();
  const walk = (id: string) => { if (seen.has(id)) return; seen.add(id); out.push(id); (kids[id] ?? []).forEach(walk); };
  (kids[""] ?? []).forEach(walk);
  Object.keys(doc.nodes).forEach(walk); // anything under a missing parent (never dropped from a diff)
  return out;
}

export function descendants(doc: Doc, id: string, kids = kidsOf(doc)): string[] {
  const out: string[] = [];
  const seen = new Set<string>([id]);
  const walk = (x: string) => (kids[x] ?? []).forEach((k) => { if (seen.has(k)) return; seen.add(k); out.push(k); walk(k); });
  walk(id);
  return out;
}

/** Would making `parent` the parent of `id` close a loop? */
function wouldLoop(nodes: Record<string, MNode>, id: string, parent: string | null): boolean {
  const seen = new Set<string>();
  for (let cur = parent; cur && !seen.has(cur); cur = nodes[cur]?.parent ?? null) {
    if (cur === id) return true;
    seen.add(cur);
  }
  return false;
}

export function hiddenSet(doc: Doc, kids = kidsOf(doc)): Set<string> {
  const hidden = new Set<string>();
  Object.values(doc.nodes).forEach((n) => { if (n.style.expanded === false) descendants(doc, n.id, kids).forEach((d) => hidden.add(d)); });
  return hidden;
}

export function removeSubtree(doc: Doc, ids: string[]): Doc {
  const kids = kidsOf(doc);
  const doomed = new Set<string>();
  ids.forEach((id) => { doomed.add(id); descendants(doc, id, kids).forEach((d) => doomed.add(d)); });
  const nodes = { ...doc.nodes };
  doomed.forEach((id) => delete nodes[id]);
  const edges = Object.fromEntries(Object.entries(doc.edges).filter(([, e]) => !doomed.has(e.src) && !doomed.has(e.dst)));
  return { nodes, edges };
}

// ------------------------------------------------------------------------------------------------ layout
const GAP_X = 56;
const GAP_Y = 12;
const DEFAULT: Size = { width: 180, height: 40 };

/** Tidy one tree as a horizontal mind map. Top-level items with several children spread to both sides; a branch
 * grows away from its parent. The node `id` keeps its current position. */
export function tidy(doc: Doc, id: string, size: (id: string) => Size, kids = kidsOf(doc)): Record<string, { x: number; y: number }> {
  const visible = (x: string) => (doc.nodes[x]?.style.expanded === false ? [] : kids[x] ?? []);
  const sz = (x: string) => size(x) ?? DEFAULT;
  const memo: Record<string, number> = {};
  const height = (x: string): number => {
    if (memo[x] != null) return memo[x];
    const ks = visible(x);
    const block = ks.reduce((s, k) => s + height(k), 0) + GAP_Y * Math.max(0, ks.length - 1);
    return (memo[x] = Math.max(sz(x).height, block));
  };
  const out: Record<string, { x: number; y: number }> = {};
  // Lay out `ks` as a block on one side of a parent whose box is (px, py, pw, ph).
  const block = (ks: string[], px: number, py: number, pw: number, ph: number, dir: 1 | -1) => {
    const total = ks.reduce((s, k) => s + height(k), 0) + GAP_Y * Math.max(0, ks.length - 1);
    let top = py + ph / 2 - total / 2;
    for (const k of ks) {
      const h = height(k);
      const { width: w, height: kh } = sz(k);
      const kx = dir > 0 ? px + pw + GAP_X : px - GAP_X - w;
      const ky = top + h / 2 - kh / 2;
      out[k] = { x: kx, y: ky };
      block(visible(k), kx, ky, w, kh, dir);
      top += h + GAP_Y;
    }
  };
  const n = doc.nodes[id];
  const { width, height: h } = sz(id);
  const x = n.x ?? 0, y = n.y ?? 0;
  const ks = visible(id);
  const parent = n.parent ? doc.nodes[n.parent] : null;
  if (parent) {
    const dir = (n.x ?? 0) + width / 2 >= (parent.x ?? 0) + sz(parent.id).width / 2 ? 1 : -1;
    block(ks, x, y, width, h, dir);
  } else if (ks.length >= 4) {
    // Split children between the two sides with roughly equal heights, keeping their order.
    const total = ks.reduce((s, k) => s + height(k), 0);
    let acc = 0, cut = ks.length;
    for (let i = 0; i < ks.length; i++) { acc += height(ks[i]); if (acc >= total / 2) { cut = i + 1; break; } }
    block(ks.slice(0, cut), x, y, width, h, 1);
    block(ks.slice(cut), x, y, width, h, -1);
  } else {
    block(ks, x, y, width, h, 1);
  }
  return out;
}

type Box = { x: number; y: number; w: number; h: number };
const boxOf = (ids: string[], pos: (id: string) => { x: number; y: number }, size: (id: string) => Size): Box => {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  ids.forEach((id) => { const p = pos(id); const s = size(id) ?? DEFAULT; x0 = Math.min(x0, p.x); y0 = Math.min(y0, p.y); x1 = Math.max(x1, p.x + s.width); y1 = Math.max(y1, p.y + s.height); });
  return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
};

/** Apply positions; returns a new doc. */
export function withPositions(doc: Doc, pos: Record<string, { x: number; y: number }>): Doc {
  if (!Object.keys(pos).length) return doc;
  const nodes = { ...doc.nodes };
  Object.entries(pos).forEach(([id, p]) => { if (nodes[id]) nodes[id] = { ...nodes[id], x: Math.round(p.x), y: Math.round(p.y) }; });
  return { ...doc, nodes };
}

/** Tidy every tree, then move whole trees down where they would overlap. */
export function tidyAll(doc: Doc, size: (id: string) => Size): Doc {
  const kids = kidsOf(doc);
  const hidden = hiddenSet(doc, kids);
  let d = doc;
  const roots = kids[""] ?? [];
  roots.forEach((r) => { d = withPositions(d, tidy(d, r, size, kids)); });
  const trees = roots.map((r) => {
    const ids = [r, ...descendants(d, r, kids).filter((x) => !hidden.has(x))];
    return { r, ids, box: boxOf(ids, (x) => ({ x: d.nodes[x].x ?? 0, y: d.nodes[x].y ?? 0 }), size) };
  }).sort((a, b) => a.box.y - b.box.y);
  const placed: Box[] = [];
  const shift: Record<string, { x: number; y: number }> = {};
  for (const t of trees) {
    let dy = 0;
    for (let guard = 0; guard < 200; guard++) {
      const hit = placed.find((p) => t.box.x < p.x + p.w + 40 && p.x < t.box.x + t.box.w + 40 && t.box.y + dy < p.y + p.h + 48 && p.y < t.box.y + dy + t.box.h + 48);
      if (!hit) break;
      dy = hit.y + hit.h + 48 - t.box.y;
    }
    placed.push({ ...t.box, y: t.box.y + dy });
    if (dy) t.ids.forEach((x) => { shift[x] = { x: d.nodes[x].x ?? 0, y: (d.nodes[x].y ?? 0) + dy }; });
  }
  return withPositions(d, shift);
}

/** Give positions to nodes that have none (made by agents, by "expand", or on another device). */
export function placeNew(doc: Doc, size: (id: string) => Size): Doc {
  const unplaced = Object.values(doc.nodes).filter((n) => n.x == null);
  if (!unplaced.length) return doc;
  const kids = kidsOf(doc);
  let d = doc;
  const placedIds = Object.values(doc.nodes).filter((n) => n.x != null).map((n) => n.id);
  // New top-level trees go below everything else.
  const newRoots = unplaced.filter((n) => !n.parent);
  if (newRoots.length) {
    let { y, h, x } = placedIds.length ? boxOf(placedIds, (i) => ({ x: d.nodes[i].x ?? 0, y: d.nodes[i].y ?? 0 }), size) : { x: 0, y: 0, h: -80 };
    for (const r of newRoots) {
      const anchor = { x: placedIds.length ? x + 280 : 0, y: y + h + 80 };
      d = withPositions(d, { [r.id]: anchor });
      d = withPositions(d, tidy(d, r.id, size, kids));
      const ids = [r.id, ...descendants(d, r.id, kids)];
      const b = boxOf(ids, (i) => ({ x: d.nodes[i].x ?? 0, y: d.nodes[i].y ?? 0 }), size);
      // the tree may extend left and up of its anchor: keep it below what's there
      if (b.y < anchor.y) d = withPositions(d, Object.fromEntries(ids.map((i) => [i, { x: d.nodes[i].x ?? 0, y: (d.nodes[i].y ?? 0) + anchor.y - b.y }])));
      const nb = boxOf(ids, (i) => ({ x: d.nodes[i].x ?? 0, y: d.nodes[i].y ?? 0 }), size);
      y = nb.y; h = nb.h; x = placedIds.length ? x : nb.x;
    }
  }
  // New children of placed nodes: tidy that branch.
  const anchors = new Set<string>();
  Object.values(d.nodes).filter((n) => n.x == null && n.parent && d.nodes[n.parent]?.x != null).forEach((n) => anchors.add(n.parent!));
  anchors.forEach((a) => { d = withPositions(d, tidy(d, a, size, kids)); });
  // Anything still unplaced (e.g. under a collapsed node) sits on its parent.
  const rest: Record<string, { x: number; y: number }> = {};
  Object.values(d.nodes).forEach((n) => { if (n.x == null) { const p = n.parent ? d.nodes[n.parent] : null; rest[n.id] = { x: (p?.x ?? 0) + 40, y: (p?.y ?? 0) + 40 }; } });
  return withPositions(d, rest);
}

/** Where a new child of `parentId` goes: beside the parent, below its last visible child. */
export function childSpot(doc: Doc, parentId: string, size: (id: string) => Size): { x: number; y: number } {
  const p = doc.nodes[parentId];
  const kids = (kidsOf(doc)[parentId] ?? []).map((k) => doc.nodes[k]).filter((k) => k.x != null);
  const ps = size(parentId) ?? DEFAULT;
  const grand = p.parent ? doc.nodes[p.parent] : null;
  const dir = grand && (grand.x ?? 0) > (p.x ?? 0) ? -1 : 1;
  if (kids.length) {
    const last = kids.reduce((a, b) => ((a.y ?? 0) > (b.y ?? 0) ? a : b));
    return { x: last.x ?? 0, y: (last.y ?? 0) + (size(last.id) ?? DEFAULT).height + GAP_Y };
  }
  return { x: dir > 0 ? (p.x ?? 0) + ps.width + GAP_X : (p.x ?? 0) - GAP_X - DEFAULT.width, y: p.y ?? 0 };
}
