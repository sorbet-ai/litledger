// The map editor's Export menu: the canvas as an image, or the map in a text format from the server.
import { getNodesBounds, getViewportForBounds } from "@xyflow/react";
import type { ReactFlowInstance } from "@xyflow/react";
import { toPng, toSvg } from "html-to-image";
import { ReactNode } from "react";
import { api } from "../lib/api";
import { cssVar as css } from "../lib/format";
import { download, MenuItem } from "../ui/overlays";

async function exportImage(id: string, rf: ReactFlowInstance, kind: "png" | "svg") {
  const nodes = rf.getNodes().filter((n) => !n.hidden);
  if (!nodes.length) return;
  const b = getNodesBounds(nodes);
  const scale = Math.min(2, 4000 / Math.max(b.width, b.height));
  const W = Math.round((b.width + 120) * Math.min(1, scale)), H = Math.round((b.height + 120) * Math.min(1, scale));
  const vp = getViewportForBounds(b, W, H, 0.1, 2, 0.04);
  const el = document.querySelector(".react-flow__viewport") as HTMLElement;
  const opts = { backgroundColor: css("--bg"), width: W, height: H, pixelRatio: kind === "png" ? 2 : 1,
                 style: { width: `${W}px`, height: `${H}px`, transform: `translate(${vp.x}px, ${vp.y}px) scale(${vp.zoom})` } };
  const url = kind === "png" ? await toPng(el, opts) : await toSvg(el, opts);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${id.replace("map:", "")}.${kind}`;
  a.click();
}

/** `flush` saves pending edits first, so what the server exports includes them. */
export function exportMap(id: string, rf: ReactFlowInstance, flush: () => Promise<void>,
                          show: (title: string, body: ReactNode, wide?: boolean) => Promise<void>): MenuItem[] {
  const text = async (format: string, ext: string, type = "text/plain") => {
    await flush();
    download(`${id.replace("map:", "")}.${ext}`, await api.tool("map_get", { map: id, format }), type);
  };
  return [
    { label: "PNG image", onClick: () => exportImage(id, rf, "png") },
    { label: "SVG image", onClick: () => exportImage(id, rf, "svg") },
    { sep: true },
    { label: "Mermaid", onClick: () => text("mermaid", "mmd") },
    { label: "JSON Canvas (Obsidian)", onClick: () => text("canvas", "canvas", "application/json") },
    { label: "OPML", onClick: () => text("opml", "opml", "text/x-opml") },
    { sep: true },
    { label: "Show what agents see", icon: "robot", onClick: async () => { await flush(); show("Agent view (map_get)", <pre>{await api.tool("map_get", { map: id })}</pre>, true); } },
  ];
}
