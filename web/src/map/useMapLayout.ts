// The doc drawn on the canvas: React Flow nodes and edges, branch colours, selection, measuring and placing new nodes.
import { applyNodeChanges, MarkerType, useNodesInitialized, useReactFlow } from "@xyflow/react";
import type { Edge, NodeChange } from "@xyflow/react";
import { MutableRefObject, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useApp } from "../app/context";
import { cssVar as css } from "../lib/format";
import { descendants, Doc, hiddenSet, kidsOf, placeNew, Size } from "./model";
import { BRANCH, BRANCH_DARK, CardNode } from "./parts";

const DEFAULT_SIZE: Size = { width: 180, height: 40 };

export function useMapLayout(doc: Doc | null, docRef: MutableRefObject<Doc | null>, setDoc: (next: Doc, history?: boolean) => void) {
  const { theme: appTheme } = useApp();
  const rf = useReactFlow();
  const initialized = useNodesInitialized();
  const dark = document.documentElement.dataset.theme === "dark";
  const [rfNodes, setRfNodes] = useState<CardNode[]>([]);
  const [selEdge, setSelEdge] = useState<string | null>(null);
  /** Nodes to select once the next doc is on the canvas (a node just added). */
  const pendingSelect = useRef<string[] | null>(null);
  const fitted = useRef(false);

  const branchOf = useMemo(() => {
    const out: Record<string, string> = {};
    if (!doc) return out;
    const kids = kidsOf(doc);
    const pal = dark ? BRANCH_DARK : BRANCH;
    const walk = (x: string, c: string) => { out[x] = c; (kids[x] ?? []).forEach((k) => walk(k, c)); };
    (kids[""] ?? []).forEach((r, ri) => {
      out[r] = pal[ri % pal.length];
      (kids[r] ?? []).forEach((k, i) => walk(k, pal[i % pal.length]));
    });
    return out;
  }, [doc, dark]);

  useEffect(() => {
    if (!doc) return;
    const kids = kidsOf(doc);
    const hidden = hiddenSet(doc, kids);
    const select = pendingSelect.current;
    pendingSelect.current = null;
    setRfNodes((prev) => {
      const old = new Map(prev.map((p) => [p.id, p]));
      return Object.values(doc.nodes).map((n) => {
        const o = old.get(n.id);
        return {
          id: n.id, type: "card" as const, position: { x: n.x ?? 0, y: n.y ?? 0 },
          data: { n, branch: branchOf[n.id] ?? "var(--muted)", kids: kids[n.id]?.length ?? 0,
                  hiddenKids: n.style.expanded === false ? descendants(doc, n.id, kids).length : 0, top: !n.parent },
          hidden: hidden.has(n.id), selected: select ? select.includes(n.id) : o?.selected ?? false,
          measured: o?.measured, className: n.x == null ? "unplaced" : undefined,
        };
      });
    });
    if (select) setSelEdge(null);
  }, [doc, branchOf]);

  const edges = useMemo<Edge[]>(() => {
    if (!doc) return [];
    const hidden = hiddenSet(doc);
    const out: Edge[] = [];
    Object.values(doc.nodes).forEach((n) => {
      if (n.parent && doc.nodes[n.parent] && !hidden.has(n.id))
        out.push({ id: `t:${n.id}`, source: n.parent, target: n.id, type: "tree", data: { color: branchOf[n.id], depth: doc.nodes[n.parent].parent ? 1 : 0 },
                   selectable: false, focusable: false });
    });
    const pen = css("--pen"), ink = css("--ink-2");
    Object.values(doc.edges).forEach((e) => {
      if (hidden.has(e.src) || hidden.has(e.dst)) return;
      out.push({ id: e.id, source: e.src, target: e.dst, type: "link", data: { label: e.label, promoted: e.promoted }, selected: selEdge === e.id,
                 markerEnd: { type: MarkerType.ArrowClosed, color: e.promoted ? pen : ink, width: 14, height: 14 } });
    });
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [doc, branchOf, selEdge, appTheme]);

  const onNodesChange = useCallback((changes: NodeChange<CardNode>[]) => setRfNodes((ns) => applyNodeChanges(changes, ns)), []);

  const sizeOf = useCallback((nid: string): Size => {
    const n = rf.getInternalNode(nid);
    return n?.measured?.width ? { width: n.measured.width, height: n.measured.height ?? 40 } : DEFAULT_SIZE;
  }, [rf]);

  // Nodes without a position (from agents, "expand", other devices) are placed once they have been measured.
  useEffect(() => {
    const d = docRef.current;
    if (!d) return;
    const waiting = rfNodes.filter((n) => n.className === "unplaced" && !n.hidden);
    if (waiting.length && waiting.every((n) => n.measured?.width)) setDoc(placeNew(d, sizeOf), false);
    else if (!waiting.length && Object.values(d.nodes).some((n) => n.x == null)) setDoc(placeNew(d, sizeOf), false);
  }, [rfNodes, setDoc, sizeOf, docRef]);

  useEffect(() => {
    if (fitted.current || !doc || !initialized || rfNodes.some((n) => n.className === "unplaced")) return;
    fitted.current = true;
    window.requestAnimationFrame(() => rf.fitView({ padding: 0.15, maxZoom: 1, minZoom: 0.4 }));
  }, [doc, initialized, rfNodes, rf]);

  const selected = () => rf.getNodes().filter((n) => n.selected && !n.hidden).map((n) => n.id);
  const select = (ids: string[]) => { setRfNodes((ns) => ns.map((n) => ({ ...n, selected: ids.includes(n.id) }))); setSelEdge(null); };

  return { dark, rfNodes, setRfNodes, onNodesChange, edges, selEdge, setSelEdge, pendingSelect, sizeOf, selected, select };
}
