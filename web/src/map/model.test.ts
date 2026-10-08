import { describe, expect, it } from "vitest";
import type { MapData } from "../lib/api";
import { applyOps, diff, Doc, fromServer, MEdge, MNode, rebase, rebaseHistory, removeSubtree } from "./model";

const node = (id: string, parent: string | null = null, extra: Partial<MNode> = {}): MNode => ({
  id, parent, seq: 0, ref: null, kind: "text", label: "", sub: "", text: id.toUpperCase(), x: 0, y: 0, style: {}, ...extra,
});
const edge = (id: string, src: string, dst: string, label = ""): MEdge => ({ id, src, dst, label, promoted: false });
const doc = (nodes: MNode[], edges: MEdge[] = []): Doc => ({
  nodes: Object.fromEntries(nodes.map((n) => [n.id, n])), edges: Object.fromEntries(edges.map((e) => [e.id, e])),
});
const withNode = (d: Doc, n: MNode): Doc => ({ ...d, nodes: { ...d.nodes, [n.id]: n } });
const kinds = (ops: Record<string, unknown>[]) => ops.map((o) => `${o.op}:${o.id}`);

describe("diff", () => {
  it("holds back new empty nodes with their branches and lines until they have text", () => {
    const prev = doc([node("a")]);
    const cur = doc([node("a"), node("w", "a", { text: "" }), node("w2", "w", { text: "typed under an empty one" }), node("t", "a")],
                    [edge("e1", "a", "w2"), edge("e2", "a", "t")]);
    const { ops, sent } = diff(prev, cur);
    expect(kinds(ops)).toEqual(["add:t", "edge:e2"]);
    expect(Object.keys(sent.nodes).sort()).toEqual(["a", "t"]);
    expect(Object.keys(sent.edges)).toEqual(["e2"]);
    // once the idea has text, the whole branch goes, parents first
    const typed = withNode(cur, { ...cur.nodes.w, text: "now named" });
    expect(kinds(diff(sent, typed).ops)).toEqual(["add:w", "add:w2", "edge:e1"]);
    // an existing node emptied is an ordinary update, and keepWaiting shows everything
    const emptied = withNode(prev, { ...prev.nodes.a, text: "" });
    expect(diff(prev, emptied).ops).toEqual([{ op: "update", id: "a", text: "" }]);
    expect(kinds(diff(prev, cur, { keepWaiting: true }).ops)).toEqual(["add:w", "add:w2", "add:t", "edge:e1", "edge:e2"]);
  });

  it("acknowledging what was sent leaves nothing pending, even with fractional positions", () => {
    const prev = doc([node("a")]);
    const cur = doc([node("a", null, { x: 10.4, y: 3.6 }), node("b", "a", { x: 1.5, y: 2.5, style: { color: "red" } })]);
    const { ops, sent } = diff(prev, cur);
    const acked = applyOps(prev, ops, sent).doc;
    expect(diff(acked, cur).ops).toEqual([]);
    // only the confirmed ops move `synced`: a failed add stays pending
    const partial = applyOps(prev, ops.filter((o) => o.id !== "b"), sent).doc;
    expect(kinds(diff(partial, cur).ops)).toEqual(["add:b"]);
  });
});

describe("applyOps / rebase", () => {
  const base = doc([node("a"), node("b"), node("c", "a")], [edge("e", "a", "b")]);

  it("keeps local adds and moves, and remote (agent) additions and edits", () => {
    let local = withNode(base, node("n", "a", { text: "mine" }));
    local = withNode(local, { ...local.nodes.a, x: 100, y: 50 });
    let server = withNode(base, node("g", "b", { text: "agent", x: null, y: null }));
    server = withNode(server, { ...server.nodes.b, text: "B edited by agent" });
    const r = rebase(base, server, local);
    expect(r.dropped).toEqual([]);
    expect(r.doc.nodes.n).toMatchObject({ parent: "a", text: "mine" });
    expect(r.doc.nodes.a).toMatchObject({ x: 100, y: 50 });
    expect(r.doc.nodes.g).toMatchObject({ parent: "b", text: "agent" });
    expect(r.doc.nodes.b.text).toBe("B edited by agent");
    // what is then sent: only the local changes, nothing that would undo the agent's work
    expect(kinds(diff(server, r.doc).ops)).toEqual(["move:a", "add:n"]);
  });

  it("local edits win field by field over remote edits of the same node", () => {
    const local = withNode(base, { ...base.nodes.c, text: "local text" });
    const server = withNode(base, { ...base.nodes.c, text: "remote text", style: { color: "green" } });
    const r = rebase(base, server, local);
    expect(r.doc.nodes.c).toMatchObject({ text: "local text", style: { color: "green" } });
  });

  it("a local delete removes what the user saw; remote additions under it survive as top-level items", () => {
    const local = removeSubtree(base, ["a"]);
    let server = withNode(base, { ...base.nodes.c, text: "C edited by agent" });
    server = withNode(server, node("d", "a", { text: "agent child" }));
    server = withNode(server, node("g", null, { text: "agent elsewhere" }));
    const r = rebase(base, server, local);
    expect(Object.keys(r.doc.nodes).sort()).toEqual(["b", "d", "g"]);
    expect(r.doc.nodes.d.parent).toBeNull();
    expect(r.rescued).toEqual(["d"]);
    expect(r.doc.edges).toEqual({});
    const ops = diff(server, r.doc).ops;
    // the rescue is sent before the delete, so the server's subtree delete can't take the agent's node
    expect(kinds(ops)).toEqual(["update:d", "delete:a", "delete:c"]);
    expect(ops[0]).toEqual({ op: "update", id: "d", parent: null });
  });

  it("a remote delete loses to a local text edit, but not to a local move", () => {
    const local = withNode(withNode(base, { ...base.nodes.b, text: "B rewritten" }), { ...base.nodes.c, x: 400 });
    const server = removeSubtree(base, ["a", "b"]);
    const r = rebase(base, server, local);
    expect(r.doc.nodes.b.text).toBe("B rewritten");
    expect(r.doc.nodes.c).toBeUndefined();
    expect(r.dropped).toEqual([{ op: "move", id: "c", x: 400, y: 0 }]);
    expect(kinds(diff(server, r.doc).ops)).toEqual(["add:b"]); // re-adding a deleted id replaces it on the server
  });

  it("refuses a reparent that would make a loop with a remote reparent", () => {
    const flat = doc([node("x"), node("y")]);
    const local = withNode(flat, { ...flat.nodes.x, parent: "y" });
    const server = withNode(flat, { ...flat.nodes.y, parent: "x" });
    const r = rebase(flat, server, local);
    expect(r.doc.nodes.x.parent).toBeNull();
    expect(r.doc.nodes.y.parent).toBe("x");
    expect(r.dropped).toEqual([{ op: "update", id: "x", parent: "y" }]);
  });

  it("keeps an idea still being typed, and skips ops the server refused", () => {
    const local = withNode(withNode(base, node("w", "b", { text: "" })), { ...base.nodes.b, text: "refused" });
    const r = rebase(base, base, local, (op) => op.op === "update" && op.id === "b");
    expect(r.doc.nodes.w).toMatchObject({ parent: "b", text: "" });
    expect(r.doc.nodes.b.text).toBe("B");
  });

  it("applies plain ops without a source doc", () => {
    const d = applyOps(doc([]), [
      { op: "add", id: "p", text: "P", seq: 0, style: { color: "pen", mark: null }, x: 4, y: 5 },
      { op: "add", id: "k", text: "K", parent: "p", seq: 0 },
      { op: "edge", id: "e", src: "k", dst: "p", label: "rel" },
      { op: "update", id: "k", ref: "topic:x" },
      { op: "edge_update", id: "e", label: "rel2" },
    ]).doc;
    expect(d.nodes.p).toMatchObject({ text: "P", x: 4, y: 5, style: { color: "pen" } });
    expect(d.nodes.k).toMatchObject({ parent: "p", ref: "topic:x" });
    expect(d.edges.e.label).toBe("rel2");
  });
});

describe("undo after a rebase", () => {
  it("history moved onto the server state undoes only the user's own step", () => {
    const s0 = doc([node("a")]);
    const s1 = withNode(s0, node("n", "a"));           // the user added n; this is synced
    const server = withNode(s1, node("g", null, { text: "agent" }));
    const past = rebaseHistory(s1, server, [s0]);
    expect(past).not.toBeNull();
    const undone = past![0];
    expect(Object.keys(undone.nodes).sort()).toEqual(["a", "g"]);
    expect(kinds(diff(server, undone).ops)).toEqual(["delete:n"]);
    // and redo (the current doc, rebased the same way) brings n back without touching g
    const redo = rebaseHistory(s1, server, [s1])![0];
    expect(kinds(diff(undone, redo).ops)).toEqual(["add:n"]);
  });

  it("history that would edit something removed elsewhere is dropped instead of resurrecting it", () => {
    const s0 = doc([node("a", null, { text: "old" })]);
    const s1 = doc([node("a", null, { text: "new" })]);
    const server = doc([]);
    expect(rebaseHistory(s1, server, [s0])).toBeNull();
    // unrelated history survives the same removal
    const t0 = doc([node("a"), node("b", null, { text: "old" })]);
    const t1 = doc([node("a"), node("b", null, { text: "new" })]);
    const kept = rebaseHistory(t1, doc([node("b", null, { text: "new" })]), [t0]);
    expect(kept && Object.keys(kept[0].nodes)).toEqual(["b"]);
  });
});

describe("fromServer", () => {
  it("breaks parent loops and orphans so every node can be reached", () => {
    const n = (id: string, parent: string | null) => ({ id, ref: null, title: null, text: id, kind: "text", parent, seq: null, by: "x",
                                                       x: null, y: null, style: {} });
    const m = { id: "map:t", title: "T", updated_at: "", meta: {}, edges: [], version: 0,
                nodes: [n("a", "b"), n("b", "a"), n("c", "gone")] } as MapData;
    const d = fromServer(m);
    expect(d.nodes.c.parent).toBeNull();
    expect([d.nodes.a.parent, d.nodes.b.parent].filter((p) => p === null)).toHaveLength(1);
    const { ops } = diff(doc([]), d);
    expect(kinds(ops).sort()).toEqual(["add:a", "add:b", "add:c"]);
  });
});
