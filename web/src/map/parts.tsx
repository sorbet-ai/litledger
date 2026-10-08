// Node cards and edges for the map canvas. Edges attach to whichever sides of the two nodes face each other, so
// branches can grow in any direction and nodes can be dragged anywhere.
import { BaseEdge, EdgeLabelRenderer, getBezierPath, Handle, Position, useConnection, useInternalNode } from "@xyflow/react";
import type { Edge, EdgeProps, InternalNode, Node, NodeProps } from "@xyflow/react";
import { createContext, KeyboardEvent, useContext, useEffect, useRef, useState } from "react";
import { kindColor, THINKING } from "../lib/kinds";
import type { MNode } from "./model";

type CardData = { n: MNode; branch: string; hiddenKids: number; kids: number; top: boolean };
export type CardNode = Node<CardData, "card">;
type TreeEdge = Edge<{ color: string; depth: number }, "tree">;
type LinkEdge = Edge<{ label: string; promoted: boolean }, "link">;

export const COLORS: { key: string; label: string }[] = [
  { key: "", label: "Default" }, { key: "marker", label: "Highlighter" }, { key: "pen", label: "Ink blue" },
  { key: "green", label: "Green" }, { key: "red", label: "Red" }, { key: "grey", label: "Grey" },
];
export const MARKS = ["⭐", "❓", "✅", "⚠️", "💡", "🔥", "📌", "🧪"];
export const BRANCH = ["#2342c4", "#0f7b6c", "#a6581b", "#8a4c9b", "#a8386a", "#3c6fa6", "#2b7a4b", "#6b56b8"];
export const BRANCH_DARK = ["#8ea3ff", "#4fc7b3", "#e5a066", "#d39be3", "#ee8fb8", "#86b4e8", "#7bcf99", "#b4a3f2"];

type Ctx = {
  editing: string | null;
  setEditing: (id: string | null) => void;
  commitText: (id: string, text: string | null) => void;
  toggleFold: (id: string) => void;
  dropTarget: string | null;
  editEdge: (id: string) => void;
  /** Keys typed between creating a node and its editor appearing; `done` if Enter/Tab came too. */
  takeTypeAhead: () => { text: string; done: boolean | null } | null;
};
export const MapCtx = createContext<Ctx>(null as unknown as Ctx);

const kindName = (k: string) => (k === "work" ? "paper" : k);

export function Card({ id, data, selected }: NodeProps<CardNode>) {
  const ctx = useContext(MapCtx);
  const connection = useConnection();
  const { n } = data;
  const editing = ctx.editing === id;
  const thinking = THINKING.has(n.kind);
  const typed = n.kind !== "text";
  const title = n.ref ? n.label : n.text;
  const caption = n.ref && n.text ? n.text : "";
  const cls = ["mcard", typed ? "typed" : "plain", thinking ? "thinking" : "", data.top ? "top" : "", selected ? "sel" : "",
               ctx.dropTarget === id ? "drop" : "", n.style.color ? `c-${n.style.color}` : ""].filter(Boolean).join(" ");
  const connectingHere = connection.inProgress && connection.fromNode?.id !== id;
  return (
    <div className={cls} style={{ ["--kc" as string]: kindColor(n.kind), ["--bc" as string]: data.branch }}>
      {typed && (
        <div className="mk">
          <span className="kchip">{kindName(n.kind)}</span>
          {n.sub && <span className="msub">{n.sub}</span>}
        </div>
      )}
      {editing ? (
        <Editor value={n.text} placeholder={n.ref ? "Add a caption…" : "Idea"} typeAhead={ctx.takeTypeAhead} onDone={(v) => ctx.commitText(id, v)} />
      ) : (
        <div className="mt">{n.style.mark && <span className="mmark">{n.style.mark}</span>}{title || <span className="muted">Untitled</span>}</div>
      )}
      {!editing && caption && <div className="mcap">{caption}</div>}
      {data.kids > 0 && (
        <button className={`fold${data.hiddenKids ? " closed" : ""}`} title={data.hiddenKids ? "Show branch" : "Collapse branch"}
                onMouseDown={(e) => e.stopPropagation()} onClick={(e) => { e.stopPropagation(); ctx.toggleFold(id); }}>
          {data.hiddenKids ? data.hiddenKids : "–"}
        </button>
      )}
      <Handle type="source" position={Position.Right} id="r" className="mh" />
      <Handle type="source" position={Position.Left} id="l" className="mh" />
      <Handle type="target" position={Position.Left} id="tl" className="mh-hidden" isConnectable={false} />
      {connectingHere && <Handle type="target" position={Position.Top} id="full" className="mh-full" />}
    </div>
  );
}

function Editor({ value, placeholder, onDone, typeAhead }: { value: string; placeholder: string; onDone: (v: string | null) => void;
                                                          typeAhead: Ctx["takeTypeAhead"] }) {
  const [ahead] = useState(() => typeAhead());
  const [v, setV] = useState(ahead ? value + ahead.text : value);
  const ref = useRef<HTMLTextAreaElement>(null);
  const done = useRef(false);
  const finish = (val: string | null) => { if (!done.current) { done.current = true; onDone(val); } };
  // A new node stays invisible until React Flow has measured it, and focus() does nothing until then. Keys typed
  // in that moment are buffered so nothing is lost.
  useEffect(() => {
    if (ahead?.done !== null && ahead?.done !== undefined) { finish(ahead.done ? (value + ahead.text).trim() : null); return; }
    let tries = 0, raf = 0, typed = ahead?.text ?? "";
    const early = (e: globalThis.KeyboardEvent) => {
      if (document.activeElement === ref.current) return;
      if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) { typed += e.key; setV((x) => x + e.key); e.preventDefault(); e.stopPropagation(); }
      else if (e.key === "Backspace") { typed = typed.slice(0, -1); setV((x) => x.slice(0, -1)); e.preventDefault(); e.stopPropagation(); }
      else if (e.key === "Enter" || e.key === "Tab") { e.preventDefault(); e.stopPropagation(); finish(typed.trim()); }
      else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); finish(null); }
    };
    window.addEventListener("keydown", early, true);
    const focus = () => {
      const el = ref.current;
      if (!el) return;
      el.focus();
      if (document.activeElement === el) {
        window.removeEventListener("keydown", early, true);
        if (typed) el.setSelectionRange(el.value.length, el.value.length); else el.select();
      } else if (tries++ < 60) raf = requestAnimationFrame(focus);
    };
    focus();
    return () => { cancelAnimationFrame(raf); window.removeEventListener("keydown", early, true); };
  }, []);
  const onKey = (e: KeyboardEvent) => {
    e.stopPropagation();
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); finish(v.trim()); }
    if (e.key === "Escape") { e.preventDefault(); finish(null); }
    if (e.key === "Tab") { e.preventDefault(); finish(v.trim()); }
  };
  return <textarea ref={ref} className="medit nodrag nopan" rows={1} value={v} placeholder={placeholder}
                   onChange={(e) => setV(e.target.value)} onKeyDown={onKey} onBlur={() => finish(v.trim())} />;
}

// --------------------------------------------------------------------------------------------- edge geometry
type Rect = { x: number; y: number; w: number; h: number };
const rectOf = (n: InternalNode): Rect => ({ x: n.internals.positionAbsolute.x, y: n.internals.positionAbsolute.y,
                                              w: n.measured.width ?? 160, h: n.measured.height ?? 40 });
function anchor(r: Rect, side: Position): { x: number; y: number } {
  if (side === Position.Right) return { x: r.x + r.w, y: r.y + r.h / 2 };
  if (side === Position.Left) return { x: r.x, y: r.y + r.h / 2 };
  if (side === Position.Top) return { x: r.x + r.w / 2, y: r.y };
  return { x: r.x + r.w / 2, y: r.y + r.h };
}
const opposite = { [Position.Left]: Position.Right, [Position.Right]: Position.Left, [Position.Top]: Position.Bottom, [Position.Bottom]: Position.Top };

function sides(a: Rect, b: Rect, horizontalOnly: boolean): [Position, Position] {
  const dx = b.x + b.w / 2 - (a.x + a.w / 2);
  const dy = b.y + b.h / 2 - (a.y + a.h / 2);
  // Overlapping horizontally (one above the other): vertical sides read better, except for tree branches.
  const overlapX = a.x < b.x + b.w && b.x < a.x + a.w;
  if (!horizontalOnly && overlapX && Math.abs(dy) > Math.abs(dx) * 0.4) return dy > 0 ? [Position.Bottom, Position.Top] : [Position.Top, Position.Bottom];
  const s = dx >= 0 ? Position.Right : Position.Left;
  return [s, opposite[s]];
}

export function TreeLine({ id, source, target, data, selected }: EdgeProps<TreeEdge>) {
  const s = useInternalNode(source);
  const t = useInternalNode(target);
  if (!s || !t) return null;
  const a = rectOf(s), b = rectOf(t);
  const [sp, tp] = sides(a, b, true);
  const p1 = anchor(a, sp), p2 = anchor(b, tp);
  const [path] = getBezierPath({ sourceX: p1.x, sourceY: p1.y, sourcePosition: sp, targetX: p2.x, targetY: p2.y, targetPosition: tp, curvature: 0.35 });
  return <BaseEdge id={id} path={path} interactionWidth={10}
                   style={{ stroke: data?.color, strokeWidth: selected ? 3.5 : data?.depth === 0 ? 2.6 : 1.8, opacity: selected ? 1 : 0.85 }} />;
}

export function LinkLine({ id, source, target, data, selected, markerEnd }: EdgeProps<LinkEdge>) {
  const ctx = useContext(MapCtx);
  const s = useInternalNode(source);
  const t = useInternalNode(target);
  if (!s || !t) return null;
  const a = rectOf(s), b = rectOf(t);
  const [sp, tp] = sides(a, b, false);
  const p1 = anchor(a, sp), p2 = anchor(b, tp);
  const [path, lx, ly] = getBezierPath({ sourceX: p1.x, sourceY: p1.y, sourcePosition: sp, targetX: p2.x, targetY: p2.y, targetPosition: tp });
  const promoted = data?.promoted;
  return (
    <>
      <BaseEdge id={id} path={path} markerEnd={markerEnd} interactionWidth={16}
                style={{ stroke: promoted ? "var(--pen)" : "var(--ink-2)", strokeWidth: selected ? 2.6 : promoted ? 2 : 1.4,
                         strokeDasharray: promoted ? undefined : "5 4", opacity: selected ? 1 : 0.8 }} />
      {(data?.label || selected) && (
        <EdgeLabelRenderer>
          <div className={`elabel nodrag nopan${promoted ? " promoted" : ""}${selected ? " sel" : ""}`}
               style={{ transform: `translate(-50%, -50%) translate(${lx}px, ${ly}px)` }}
               onDoubleClick={(e) => { e.stopPropagation(); ctx.editEdge(id); }}>
            {data?.label ? data.label.replace(/_/g, " ") : <span className="muted">label</span>}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
}
