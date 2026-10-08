// @vitest-environment jsdom
import { act, cleanup, renderHook } from "@testing-library/react";
import { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { MapData } from "../lib/api";
import { InApp } from "../test/app";

const server = vi.hoisted(() => ({ map: vi.fn(), editMap: vi.fn(), maps: vi.fn() }));
vi.mock("../lib/api", () => ({ api: server }));
import { useMapSync } from "./useMapSync";

const node = (id: string, text: string, parent: string | null = null): MapData["nodes"][number] => ({
  id, ref: null, title: null, text, kind: "text", parent, seq: 0, by: "t", x: 0, y: 0, style: {},
});
const mapAt = (version: number, nodes: MapData["nodes"]): MapData => ({ id: "map:m", title: "M", updated_at: "", version, meta: {}, nodes, edges: [] });
const wrapper = ({ children }: { children: ReactNode }) => <InApp>{children}</InApp>;

describe("useMapSync", () => {
  beforeEach(() => { server.map.mockReset(); server.editMap.mockReset(); server.maps.mockReset(); });
  afterEach(cleanup);

  it("on a conflict reply, keeps the local edit and brings in the other writer's change", async () => {
    server.map.mockResolvedValueOnce(mapAt(1, [node("a", "A")]));
    const { result } = renderHook(() => useMapSync("map:m", null), { wrapper });
    await vi.waitFor(() => expect(result.current.doc?.nodes.a.text).toBe("A"));
    // meanwhile an agent added b (version 2); our save lands on top of it as version 3 and says so
    server.editMap.mockResolvedValueOnce({ map: "map:m", added: [], changed: 1, problems: [], results: [{ i: 0, ok: true }],
                                           version: 3, conflict: true });
    server.map.mockResolvedValueOnce(mapAt(3, [node("a", "A mine"), node("b", "B from the agent", "a")]));
    act(() => {
      const d = result.current.docRef.current!;
      result.current.setDoc({ ...d, nodes: { ...d.nodes, a: { ...d.nodes.a, text: "A mine" } } });
    });
    expect(result.current.status).toBe("unsaved");
    await act(() => result.current.flush());
    expect(server.editMap).toHaveBeenCalledWith({ map: "map:m", ops: [{ op: "update", id: "a", text: "A mine" }], base_version: 1 });
    expect(server.map).toHaveBeenCalledTimes(2);  // the conflict made it look at the server again
    const doc = result.current.doc!;
    expect(doc.nodes.a.text).toBe("A mine");
    expect(doc.nodes.b).toMatchObject({ text: "B from the agent", parent: "a" });
    expect(result.current.status).toBe("saved");
    // nothing left to send: the next flush is a no-op
    await act(() => result.current.flush());
    expect(server.editMap).toHaveBeenCalledTimes(1);
  });

  it("a save that fails is retried later and the edit is kept", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      server.map.mockResolvedValue(mapAt(1, [node("a", "A")]));
      const { result } = renderHook(() => useMapSync("map:m", null), { wrapper });
      await vi.waitFor(() => expect(result.current.doc?.nodes.a.text).toBe("A"));
      server.editMap.mockRejectedValueOnce(new Error("offline"));
      server.editMap.mockResolvedValueOnce({ map: "map:m", added: [], changed: 1, problems: [], results: [{ i: 0, ok: true }], version: 2 });
      act(() => {
        const d = result.current.docRef.current!;
        result.current.setDoc({ ...d, nodes: { ...d.nodes, a: { ...d.nodes.a, text: "A2" } } });
      });
      await act(() => result.current.flush());
      expect(result.current.status).toBe("error");
      expect(result.current.doc!.nodes.a.text).toBe("A2");
      await act(async () => { await vi.advanceTimersByTimeAsync(2500); });
      await vi.waitFor(() => expect(result.current.status).toBe("saved"));
      expect(server.editMap).toHaveBeenLastCalledWith({ map: "map:m", ops: [{ op: "update", id: "a", text: "A2" }], base_version: 1 });
    } finally {
      vi.useRealTimers();
    }
  });
});
