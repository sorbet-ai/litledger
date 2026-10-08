// Everything the user does to a map: adding and editing nodes and lines, right-click menus, dragging, connecting, keys.
import { useReactFlow } from "@xyflow/react";
import type { Connection, Edge, FinalConnectionState, NodeMouseHandler, OnConnectEnd } from "@xyflow/react";
import { MouseEvent as ReactMouseEvent, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../lib/api";
import { LIT_KINDS, THINK_KINDS } from "../lib/kinds";
import { MenuItem, useOverlays } from "../ui/overlays";
import { Picked, usePicker } from "../ui/pickers";
import { childSpot, descendants, Doc, kidsOf, MNode, newId, removeSubtree, tidy, tidyAll, withPositions } from "./model";
import { CardNode, COLORS, MARKS } from "./parts";
import type { useMapLayout } from "./useMapLayout";
import type { useMapSync } from "./useMapSync";

type At = { parent: string } | { pos: { x: number; y: number } };

function hrefFor(n: MNode): string | undefined {
  if (!n.ref) return undefined;
  if (n.kind === "work") return `/work/${encodeURIComponent(n.ref)}`;
  if (n.kind === "map") return `/maps/${encodeURIComponent(n.ref)}`;
  if (n.kind === "tag") return `/?tag=${encodeURIComponent(n.ref.slice(4))}`;
  if (n.kind === "note") return undefined;
  return `/knowledge?item=${encodeURIComponent(n.ref)}`;
}

export function useMapCommands(sync: ReturnType<typeof useMapSync>, layout: ReturnType<typeof useMapLayout>,
                               editing: string | null, setEditing: (id: string | null) => void) {
  const { ask, form, menu, toast } = useOverlays();
  const { pick } = usePicker();
  const nav = useNavigate();
  const rf = useReactFlow();
  const { docRef, setDoc, flush, undo, redo } = sync;
  const { setRfNodes, selEdge, setSelEdge, pendingSelect, sizeOf, selected, select } = layout;
  const fresh = useRef<string | null>(null);
  const typeAhead = useRef<{ text: string; done: boolean | null } | null>(null);
  const [dropTarget, setDropTarget] = useState<string | null>(null);
  const [linking, setLinking] = useState<string | null>(null);

  // ------------------------------------------------------------------------------------------- editing helpers
  const startEditing = (nid: string, isFresh: boolean) => {
    fresh.current = isFresh ? nid : null;
    typeAhead.current = { text: "", done: null };
    setEditing(nid);
  };
  useEffect(() => {
    const grab = (e: KeyboardEvent) => {
      const t = typeAhead.current;
      if (!t || document.activeElement?.tagName === "TEXTAREA") return;
      if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) t.text += e.key;
      else if (e.key === "Backspace") t.text = t.text.slice(0, -1);
      else if (e.key === "Enter" || e.key === "Tab") t.done = true;
      else if (e.key === "Escape") t.done = false;
      else return;
      e.preventDefault();
      e.stopPropagation();
    };
    window.addEventListener("keydown", grab, true);
    return () => window.removeEventListener("keydown", grab, true);
  }, []);

  const nextSeq = (d: Doc, parent: string | null) => Math.max(-1, ...Object.values(d.nodes).filter((n) => n.parent === parent).map((n) => n.seq)) + 1;

  const addNode = (init: Partial<MNode> & { parent: string | null; x: number; y: number }, edit = false, base?: Doc): string => {
    const d = base ?? docRef.current!;
    const nid = newId();
    const node: MNode = { id: nid, seq: nextSeq(d, init.parent), ref: null, kind: "text", label: "", sub: "", text: "", style: {}, ...init };
    let nodes = { ...d.nodes, [nid]: node };
    if (init.parent && d.nodes[init.parent]?.style.expanded === false)
      nodes = { ...nodes, [init.parent]: { ...d.nodes[init.parent], style: { ...d.nodes[init.parent].style, expanded: undefined } } };
    pendingSelect.current = [nid];
    setDoc({ ...d, nodes });
    if (edit) startEditing(nid, true);
    return nid;
  };
  const fromPicked = (p: Picked): Partial<MNode> =>
    p.ref ? { ref: p.ref, kind: p.kind, label: p.label, sub: p.kind === "work" ? p.ref : "" } : { text: p.text ?? p.label };

  const addChild = (pid: string, init: Partial<MNode> = {}, edit = true) => {
    const d = docRef.current!;
    const spot = childSpot(d, pid, sizeOf);
    return addNode({ ...init, parent: pid, ...spot }, edit && !init.ref);
  };
  const addSibling = (sid: string) => {
    const d = docRef.current!;
    const n = d.nodes[sid];
    const h = sizeOf(sid).height;
    if (!n.parent) return addNode({ parent: null, x: n.x ?? 0, y: (n.y ?? 0) + h + 48 }, true);
    // Make room: later siblings (and their branches) shift down.
    const kids = kidsOf(d);
    const later = (kids[n.parent] ?? []).filter((k) => d.nodes[k].seq > n.seq);
    const shift: Record<string, { x: number; y: number }> = {};
    later.forEach((k) => [k, ...descendants(d, k, kids)].forEach((x) => { shift[x] = { x: d.nodes[x].x ?? 0, y: (d.nodes[x].y ?? 0) + 52 }; }));
    let next = withPositions(d, shift);
    const nodes = { ...next.nodes };
    later.forEach((k) => { nodes[k] = { ...nodes[k], seq: nodes[k].seq + 1 }; });
    next = { ...next, nodes };
    const nid = newId();
    nodes[nid] = { id: nid, parent: n.parent, seq: n.seq + 1, ref: null, kind: "text", label: "", sub: "", text: "", style: {}, x: n.x ?? 0, y: (n.y ?? 0) + h + 12 };
    pendingSelect.current = [nid];
    setDoc(next);
    startEditing(nid, true);
  };
  const patch = (nid: string, p: Partial<MNode>) => {
    const d = docRef.current!;
    if (!d.nodes[nid]) return;
    setDoc({ ...d, nodes: { ...d.nodes, [nid]: { ...d.nodes[nid], ...p } } });
  };
  const restyle = (ids: string[], s: Partial<MNode["style"]>) => {
    const d = docRef.current!;
    const nodes = { ...d.nodes };
    ids.forEach((x) => { const st = { ...nodes[x].style, ...s }; Object.keys(st).forEach((k) => (st as Record<string, unknown>)[k] === undefined && delete (st as Record<string, unknown>)[k]); nodes[x] = { ...nodes[x], style: st }; });
    setDoc({ ...d, nodes });
  };
  const commitText = (nid: string, text: string | null) => {
    setEditing(null);
    typeAhead.current = null;
    const d = docRef.current!;
    const n = d.nodes[nid];
    const wasFresh = fresh.current === nid;
    fresh.current = null;
    if (!n) return;
    if (wasFresh && !n.ref && !text) {
      // An empty new idea is dropped, and leaves no undo step behind.
      sync.discardStep();
      return;
    }
    if (text !== null && text !== n.text) patch(nid, { text });
  };
  const deleteNodes = (ids: string[]) => { if (ids.length) setDoc(removeSubtree(docRef.current!, ids)); };
  const toggleFold = (nid: string) => {
    const n = docRef.current!.nodes[nid];
    restyle([nid], { expanded: n.style.expanded === false ? undefined : false });
  };
  // Move a node (with its branch) under a new parent, next to that parent's other children.
  const reparented = (d: Doc, nid: string, parent: string | null): Doc => {
    if (parent && (parent === nid || descendants(d, nid).includes(parent))) return d;
    let next: Doc = { ...d, nodes: { ...d.nodes, [nid]: { ...d.nodes[nid], parent, seq: nextSeq(d, parent) } } };
    if (parent) {
      const spot = childSpot({ ...d, nodes: { ...d.nodes, [nid]: { ...d.nodes[nid], parent: "__" } } }, parent, sizeOf);
      const n = d.nodes[nid];
      const dx = spot.x - (n.x ?? 0), dy = spot.y - (n.y ?? 0);
      next = withPositions(next, Object.fromEntries([nid, ...descendants(next, nid)].map((x) => [x, { x: (next.nodes[x].x ?? 0) + dx, y: (next.nodes[x].y ?? 0) + dy }])));
    }
    return next;
  };
  const reparent = (nid: string, parent: string | null) => setDoc(reparented(docRef.current!, nid, parent));
  const tidyBranch = (nid: string) => setDoc(withPositions(docRef.current!, tidy(docRef.current!, nid, sizeOf)));
  const tidyEverything = () => { setDoc(tidyAll(docRef.current!, sizeOf)); window.setTimeout(() => rf.fitView({ padding: 0.15, maxZoom: 1, duration: 300 }), 60); };

  const createItem = async (kind: string): Promise<Partial<MNode> | null> => {
    const t = await ask(`New ${kind}`, { label: "Title", required: true, placeholder: kind === "question" ? "What would we need to know to…?" : "" }, "Add");
    if (!t) return null;
    const out = await api.tool("entity", { items: [{ kind, title: t }] });
    const m = out.match(new RegExp(`${kind}:[a-z0-9+#._-]+`));
    if (!m) { toast(out, true); return null; }
    return { ref: m[0], kind, label: t };
  };
  const placeAt = (at: At, init: Partial<MNode>, edit = false) =>
    "parent" in at ? addChild(at.parent, init, edit) : addNode({ ...init, parent: null, ...at.pos }, edit);
  const newItemAt = async (kind: string, at: At) => {
    if (kind === "text") return placeAt(at, {}, true);
    const init = await createItem(kind);
    if (init) placeAt(at, init);
  };
  const pickAt = async (at: At) => {
    const p = await pick({ create: THINK_KINDS, text: true });
    if (p) placeAt(at, fromPicked(p));
  };
  const viewportCentre = () => {
    const box = document.querySelector(".react-flow")?.getBoundingClientRect();
    const p = rf.screenToFlowPosition({ x: (box?.left ?? 0) + (box?.width ?? 800) / 2, y: (box?.top ?? 0) + (box?.height ?? 600) / 2 });
    return { x: p.x - 90, y: p.y - 20 };
  };
  const defaultAt = (): At => { const s = selected(); return s.length === 1 ? { parent: s[0] } : { pos: viewportCentre() }; };

  const linkTo = async (nid: string) => {
    const p = await pick({ title: "Link this node to…", create: THINK_KINDS, capture: true });
    if (!p?.ref) return;
    const n = docRef.current!.nodes[nid];
    patch(nid, { ref: p.ref, kind: p.kind, label: p.label, sub: p.kind === "work" ? p.ref : "", text: n.ref ? n.text : "" });
  };
  const turnInto = async (nid: string, kind: string) => {
    const n = docRef.current!.nodes[nid];
    const out = await api.tool("entity", { items: [{ kind, title: n.text }] });
    const m = out.match(new RegExp(`${kind}:[a-z0-9+#._-]+`));
    if (!m) { toast(out, true); return; }
    patch(nid, { ref: m[0], kind, label: n.text, text: "" });
  };
  const expandFromLedger = (nid: string) => {
    sync.sendNow([{ op: "expand", id: nid }], (res) => {
      if (res.problems.length) toast(res.problems.join("\n"), true);
      else if (!res.added.length) toast("Nothing new is linked to this item yet.");
      else toast(`Added ${res.added.length} linked items`);
    }).catch((e) => toast(String(e), true));
  };
  const groupUnderNew = (ids: string[]) => {
    const d = docRef.current!;
    const set = new Set(ids);
    const tops = ids.filter((x) => !d.nodes[x].parent || !set.has(d.nodes[x].parent!));
    const xs = tops.map((x) => d.nodes[x].x ?? 0), ys = tops.map((x) => d.nodes[x].y ?? 0);
    const gid = newId();
    const nodes = { ...d.nodes, [gid]: { id: gid, parent: null, seq: nextSeq(d, null), ref: null, kind: "text", label: "", sub: "", text: "", style: {},
                                         x: Math.min(...xs) - 260, y: (Math.min(...ys) + Math.max(...ys)) / 2 } as MNode };
    tops.forEach((x, i) => { nodes[x] = { ...nodes[x], parent: gid, seq: i }; });
    pendingSelect.current = [gid];
    setDoc({ ...d, nodes });
    startEditing(gid, false);
  };
  const addEdge = (src: string, dst: string) => {
    if (src === dst) return;
    const d = docRef.current!;
    const eid = newId();
    setDoc({ ...d, edges: { ...d.edges, [eid]: { id: eid, src, dst, label: "", promoted: false } } });
    setSelEdge(eid);
  };
  const editEdge = async (eid: string) => {
    const e = docRef.current!.edges[eid];
    if (!e) return;
    const v = await ask("Label this line", { label: "Label", value: e.label, placeholder: "e.g. extends, contradicts, motivates" }, "Save");
    if (v === null) return;
    const d = docRef.current!;
    setDoc({ ...d, edges: { ...d.edges, [eid]: { ...d.edges[eid], label: v } } });
  };
  const deleteEdge = (eid: string) => {
    const d = docRef.current!;
    const edgesLeft = { ...d.edges };
    delete edgesLeft[eid];
    setDoc({ ...d, edges: edgesLeft });
    setSelEdge(null);
  };
  const promote = async (eid: string) => {
    const e = docRef.current!.edges[eid];
    const rels = (await api.kinds()).relations;
    const r = await form({ title: "Record as a typed link", submit: "Record link",
      body: <p className="small muted" style={{ margin: 0 }}>Agents and the graph will see this link.</p>,
      fields: [{ name: "rel", label: "Relation", type: "select", value: e.label && rels.some((x) => x.name === e.label) ? e.label : "about",
                 options: rels.map((x) => ({ value: x.name, label: `${x.name.replace(/_/g, " ")} — ${x.meaning}` })) }] });
    if (!r) return;
    await sync.sendNow([{ op: "edge_update", id: eid, label: r.rel }, { op: "promote", edge: eid, relation: r.rel }], (res) => {
      toast(res.problems.length ? res.problems.join("\n") : "Recorded as a typed link", !!res.problems.length);
    }).catch((e) => toast(String(e), true));
  };

  // ------------------------------------------------------------------------------------------- menus
  const kindItems = (go: (k: string) => void): MenuItem[] => [
    { header: "Your thinking" }, ...THINK_KINDS.map((k) => ({ label: k, onClick: () => go(k) })),
    { sep: true }, { header: "From the literature" }, ...LIT_KINDS.map((k) => ({ label: k, onClick: () => go(k) })),
  ];
  const colourItems = (ids: string[]): MenuItem[] => COLORS.map((c) => ({ label: c.label, onClick: () => restyle(ids, { color: c.key || undefined }) }));

  const nodeMenu = (n: MNode): MenuItem[] => {
    const href = hrefFor(n);
    const d = docRef.current!;
    const hasKids = Object.values(d.nodes).some((x) => x.parent === n.id);
    return [
      { label: "Add child", icon: "plus", shortcut: "Tab", onClick: () => addChild(n.id) },
      { label: n.parent ? "Add sibling" : "Add item below", shortcut: "Enter", onClick: () => addSibling(n.id) },
      { label: "Add from the ledger…", icon: "search", onClick: () => pickAt({ parent: n.id }) },
      { label: "Add new", icon: "plus", items: kindItems((k) => newItemAt(k, { parent: n.id })) },
      { sep: true },
      { label: n.ref ? "Edit caption" : "Edit text", shortcut: "F2", onClick: () => setEditing(n.id) },
      ...(n.ref ? [{ label: "Unlink from the ledger", icon: "x", onClick: () => patch(n.id, { ref: null, kind: "text", text: n.text || n.label, label: "", sub: "" }) }]
               : [{ label: "Link to a paper or item…", icon: "link", onClick: () => linkTo(n.id) },
                  { label: "Turn into", items: kindItems((k) => turnInto(n.id, k)) }]),
      ...(n.ref && n.kind !== "tag" && n.kind !== "note" && n.kind !== "map" ? [{ label: "Expand linked items", icon: "expand", onClick: () => expandFromLedger(n.id) }] : []),
      ...(href ? [{ label: "Open", icon: "open", onClick: () => { void flush(); nav(href); } }] : []),
      { label: "Connect to…", icon: "link", onClick: () => { setLinking(n.id); } },
      ...(n.parent ? [{ label: "Detach from its parent", onClick: () => reparent(n.id, null) }] : []),
      { sep: true },
      { label: "Colour", items: colourItems([n.id]) },
      { label: "Mark", items: [...MARKS.map((m) => ({ label: m, onClick: () => restyle([n.id], { mark: m }) })), { label: "No mark", onClick: () => restyle([n.id], { mark: undefined }) }] },
      ...(hasKids ? [{ label: n.style.expanded === false ? "Show branch" : "Collapse branch", onClick: () => toggleFold(n.id) },
                     { label: "Tidy this branch", icon: "layout", onClick: () => tidyBranch(n.id) }] : []),
      { sep: true },
      { label: hasKids ? "Delete with its branch" : "Delete", icon: "trash", danger: true, shortcut: "Del", onClick: () => deleteNodes([n.id]) },
    ];
  };
  const multiMenu = (ids: string[]): MenuItem[] => [
    { label: "Group under a new idea", icon: "plus", onClick: () => groupUnderNew(ids) },
    { label: "Colour", items: colourItems(ids) },
    { label: "Tidy these branches", icon: "layout", onClick: () => { let d = docRef.current!; ids.forEach((x) => { d = withPositions(d, tidy(d, x, sizeOf)); }); setDoc(d); } },
    { sep: true },
    { label: `Delete ${ids.length} items`, icon: "trash", danger: true, shortcut: "Del", onClick: () => deleteNodes(ids) },
  ];

  const onNodeContextMenu: NodeMouseHandler<CardNode> = (e, node) => {
    e.preventDefault();
    let ids = selected();
    if (!ids.includes(node.id)) { select([node.id]); ids = [node.id]; }
    menu(e, ids.length > 1 ? multiMenu(ids) : nodeMenu(docRef.current!.nodes[node.id]));
  };
  const onPaneContextMenu = (e: ReactMouseEvent | MouseEvent) => {
    e.preventDefault();
    const p = rf.screenToFlowPosition({ x: e.clientX, y: e.clientY });
    const at: At = { pos: { x: p.x, y: p.y } };
    menu(e, [
      { label: "Add an idea here", icon: "plus", shortcut: "Double-click", onClick: () => newItemAt("text", at) },
      { label: "Add from the ledger here…", icon: "search", onClick: () => pickAt(at) },
      { label: "Add new here", icon: "plus", items: kindItems((k) => newItemAt(k, at)) },
      { sep: true },
      { label: "Tidy everything", icon: "layout", onClick: tidyEverything },
      { label: "Fit to screen", icon: "fit", onClick: () => rf.fitView({ padding: 0.15, maxZoom: 1, duration: 300 }) },
      { sep: true },
      { label: "Undo", icon: "undo", shortcut: "Ctrl Z", disabled: !sync.canUndo(), onClick: undo },
      { label: "Redo", icon: "redo", shortcut: "Ctrl Y", disabled: !sync.canRedo(), onClick: redo },
    ]);
  };
  const onEdgeContextMenu = (e: ReactMouseEvent, edge: Edge) => {
    e.preventDefault();
    if (edge.type === "tree") {
      menu(e, [{ label: "Detach this branch", onClick: () => reparent(edge.target, null) }]);
      return;
    }
    setSelEdge(edge.id);
    const ed = docRef.current!.edges[edge.id];
    menu(e, [
      { label: "Label…", onClick: () => editEdge(edge.id) },
      ...(ed?.promoted ? [] : [{ label: "Record as a typed link…", icon: "link", onClick: () => promote(edge.id) }]),
      { label: "Reverse direction", onClick: () => { const d = docRef.current!; const x = d.edges[edge.id]; const nid = newId(); const es = { ...d.edges }; delete es[edge.id]; es[nid] = { ...x, id: nid, src: x.dst, dst: x.src, promoted: false }; setDoc({ ...d, edges: es }); } },
      { sep: true },
      { label: "Delete line", icon: "trash", danger: true, onClick: () => deleteEdge(edge.id) },
    ]);
  };

  // ------------------------------------------------------------------------------------------- dragging
  const drag = useRef<{ lead: string; start: { x: number; y: number }; base: Record<string, { x: number; y: number }>; follow: string[]; moving: Set<string> } | null>(null);
  const onNodeDragStart = (e: MouseEvent | TouchEvent, node: CardNode, nodes: CardNode[]) => {
    const d = docRef.current!;
    const kids = kidsOf(d);
    const moving = new Set(nodes.map((n) => n.id));
    // A dragged node takes its branch along (hold Alt to move it alone).
    const follow = e.altKey ? [] : [...new Set(nodes.flatMap((n) => descendants(d, n.id, kids)))].filter((x) => !moving.has(x));
    const base: Record<string, { x: number; y: number }> = {};
    rf.getNodes().forEach((n) => { if (follow.includes(n.id)) base[n.id] = { ...n.position }; });
    drag.current = { lead: node.id, start: { ...node.position }, base, follow, moving: new Set([...moving, ...follow]) };
  };
  const onNodeDrag = (e: MouseEvent | TouchEvent, node: CardNode, nodes: CardNode[]) => {
    const s = drag.current;
    if (!s) return;
    const dx = node.position.x - s.start.x, dy = node.position.y - s.start.y;
    if (s.follow.length) setRfNodes((ns) => ns.map((n) => (s.base[n.id] ? { ...n, position: { x: s.base[n.id].x + dx, y: s.base[n.id].y + dy } } : n)));
    if (nodes.length === 1) {
      const pt = "touches" in e ? e.touches[0] : e;
      if (!pt) return;
      const p = rf.screenToFlowPosition({ x: pt.clientX, y: pt.clientY });
      const hit = rf.getNodes().find((n) => !n.hidden && !s.moving.has(n.id) && n.measured?.width &&
        p.x >= n.position.x && p.x <= n.position.x + n.measured.width && p.y >= n.position.y && p.y <= n.position.y + (n.measured.height ?? 40));
      setDropTarget(hit?.id ?? null);
    }
  };
  const onNodeDragStop = (_e: MouseEvent | TouchEvent, node: CardNode) => {
    const s = drag.current;
    drag.current = null;
    if (!s) return;
    const pos: Record<string, { x: number; y: number }> = {};
    rf.getNodes().forEach((n) => { if (s.moving.has(n.id)) pos[n.id] = n.position; });
    const target = dropTarget;
    setDropTarget(null);
    let next = withPositions(docRef.current!, pos);
    if (target && target !== next.nodes[node.id]?.parent) next = reparented(next, node.id, target);
    setDoc(next);
  };

  // ------------------------------------------------------------------------------------------- connecting
  const onConnect = (c: Connection) => { if (c.source && c.target) addEdge(c.source, c.target); };
  const onConnectEnd: OnConnectEnd = (e, state: FinalConnectionState) => {
    if (state.isValid || !state.fromNode) return;
    // Dropping a connector on empty canvas makes a new child there.
    const ev = "changedTouches" in e ? e.changedTouches[0] : e;
    const p = rf.screenToFlowPosition({ x: ev.clientX, y: ev.clientY });
    addNode({ parent: state.fromNode.id, x: p.x, y: p.y - 18 }, true);
  };
  const onNodeClick: NodeMouseHandler<CardNode> = (_e, node) => {
    setSelEdge(null);
    if (linking && linking !== node.id) { addEdge(linking, node.id); setLinking(null); }
  };

  // ------------------------------------------------------------------------------------------- keyboard
  const keyRef = useRef<(e: KeyboardEvent) => void>(() => {});
  keyRef.current = (e: KeyboardEvent) => {
    if (editing || !docRef.current) return;
    const t = e.target as HTMLElement;
    if (t.closest?.("input, textarea, select, [contenteditable=true]") || document.querySelector(".overlay, [role=menu]")) return;
    const ctrl = e.ctrlKey || e.metaKey;
    const sel = selected();
    const one = sel.length === 1 ? sel[0] : null;
    if (ctrl && e.key.toLowerCase() === "z") { e.preventDefault(); if (e.shiftKey) redo(); else undo(); return; }
    if (ctrl && e.key.toLowerCase() === "y") { e.preventDefault(); redo(); return; }
    if (ctrl && e.key.toLowerCase() === "a") { e.preventDefault(); select(rf.getNodes().filter((n) => !n.hidden).map((n) => n.id)); return; }
    if (e.key === "Escape") { setLinking(null); select([]); return; }
    if ((e.key === "Delete" || e.key === "Backspace") && (selEdge || sel.length)) {
      e.preventDefault();
      if (selEdge) deleteEdge(selEdge); else deleteNodes(sel);
      return;
    }
    if (!one) return;
    if (e.key === "Tab") { e.preventDefault(); addChild(one); }
    else if (e.key === "Enter") { e.preventDefault(); addSibling(one); }
    else if (e.key === "F2" || e.key === " ") { e.preventDefault(); setEditing(one); }
    else if (e.key.startsWith("Arrow")) {
      // Move the selection to the nearest visible node in that direction.
      e.preventDefault();
      const from = rf.getNodes().find((n) => n.id === one);
      if (!from) return;
      const c = (n: CardNode) => ({ x: n.position.x + (n.measured?.width ?? 160) / 2, y: n.position.y + (n.measured?.height ?? 40) / 2 });
      const o = c(from as CardNode);
      const dir = { ArrowRight: [1, 0], ArrowLeft: [-1, 0], ArrowDown: [0, 1], ArrowUp: [0, -1] }[e.key] ?? [0, 0];
      let best: string | null = null, score = Infinity;
      (rf.getNodes() as CardNode[]).forEach((n) => {
        if (n.id === one || n.hidden) return;
        const p = c(n);
        const along = (p.x - o.x) * dir[0] + (p.y - o.y) * dir[1];
        if (along <= 4) return;
        const across = Math.abs((p.x - o.x) * dir[1]) + Math.abs((p.y - o.y) * dir[0]);
        const s = along + across * 2.5;
        if (s < score) { score = s; best = n.id; }
      });
      if (best) select([best]);
    }
  };
  useEffect(() => {
    const h = (e: KeyboardEvent) => keyRef.current(e);
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, []);

  const takeTypeAhead = () => { const t = typeAhead.current; typeAhead.current = null; return t; };
  /** What the node cards and line labels call back into. */
  const cardCtx = useMemo(() => ({ editing, setEditing, commitText, toggleFold, dropTarget, editEdge, takeTypeAhead }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [editing, dropTarget]);

  return {
    cardCtx, linking, setLinking, kindItems, newItemAt, pickAt, defaultAt, tidyEverything, addNode, editEdge, deleteEdge, promote,
    flow: { onNodeDragStart, onNodeDrag, onNodeDragStop, onConnect, onConnectEnd, onNodeClick,
            onNodeContextMenu, onPaneContextMenu, onEdgeContextMenu },
  };
}
