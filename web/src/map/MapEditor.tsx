// The mind-map page: the canvas, its toolbar and floating bars. Syncing lives in useMapSync, drawing in
// useMapLayout, and what the user can do in useMapCommands.
import { Background, BackgroundVariant, ConnectionMode, Controls, MiniMap, ReactFlow, ReactFlowProvider, useReactFlow } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { cssVar as css } from "../lib/format";
import { THINKING } from "../lib/kinds";
import { Icon } from "../ui/Icon";
import { MenuButton, useOverlays } from "../ui/overlays";
import { exportMap } from "./exportMap";
import type { MNode } from "./model";
import { Card, LinkLine, MapCtx, TreeLine } from "./parts";
import { useMapCommands } from "./useMapCommands";
import { useMapLayout } from "./useMapLayout";
import { useMapSync } from "./useMapSync";

const nodeTypes = { card: Card };
const edgeTypes = { tree: TreeLine, link: LinkLine };

const SHORTCUTS = [["Double-click canvas", "new idea there"], ["Drag empty canvas / scroll", "pan"], ["Ctrl + scroll, pinch", "zoom"],
  ["Shift + drag", "select several"], ["Tab", "add child"], ["Enter", "add sibling (below a top-level item: a new one)"],
  ["F2 / Space / double-click", "edit text"], ["Arrow keys", "move the selection"], ["Del", "delete (with its branch)"],
  ["Ctrl Z / Ctrl Y", "undo / redo"], ["Drag a node", "moves its branch too; Alt + drag moves it alone"],
  ["Drop a node onto another", "makes it a child of that node"],
  ["Drag from a node's side dot", "onto a node: a line between them; onto empty canvas: a new child"],
  ["Right-click", "papers, typed items, colours, marks, expand, connect, tidy"]];

export default function MapEditor() {
  // A fresh editor per map, so nothing pending for one map is ever sent to another.
  const id = useParams().id ?? "";
  return <ReactFlowProvider><Editor key={id} /></ReactFlowProvider>;
}

function Editor() {
  const id = decodeURIComponent(useParams().id ?? "");
  const { ask, show, toast } = useOverlays();
  const rf = useReactFlow();
  const [editing, setEditing] = useState<string | null>(null);
  const sync = useMapSync(id, editing);
  const layout = useMapLayout(sync.doc, sync.docRef, sync.setDoc);
  const cmd = useMapCommands(sync, layout, editing, setEditing);
  const { doc, title, status } = sync;
  const { dark, setSelEdge } = layout;
  const { linking, setLinking, defaultAt } = cmd;

  const linkingNode = linking ? sync.docRef.current?.nodes[linking] : null;
  const selectedLink = layout.selEdge ? doc?.edges[layout.selEdge] : null;
  const empty = doc && !Object.keys(doc.nodes).length;

  return (
    <div className="page full">
      <div className="topbar">
        <Link to="/maps" className="btn ghost sm"><Icon name="map" />Maps</Link>
        <span className="map-title" title="Rename" role="button" tabIndex={0} onKeyDown={(e) => { if (e.key === "Enter") (e.currentTarget as HTMLElement).click(); }} onClick={async () => {
          const t = await ask("Rename map", { label: "Title", value: title, required: true }, "Rename");
          if (!t) return;
          await sync.rename(t).catch((e) => toast(String(e), true));
        }}>{title}</span>
        {status === "error"
          ? <button className="btn sm danger" title="Try again" onClick={sync.retry}>Couldn't save — retry</button>
          : <span className="tiny muted">{status === "saving" ? "Saving…" : status === "unsaved" ? "Unsaved" : "Saved"}</span>}
        <span className="grow" />
        <button className="btn sm" onClick={() => cmd.pickAt(defaultAt())}><Icon name="search" />Add from the ledger</button>
        <MenuButton className="btn sm" label="New" icon="plus" items={() => [
          { label: "Idea (plain text)", onClick: () => cmd.newItemAt("text", defaultAt()) }, { sep: true },
          ...cmd.kindItems((k) => cmd.newItemAt(k, defaultAt())),
        ]} />
        <button className="btn sm icon" title="Undo (Ctrl Z)" aria-label="Undo (Ctrl Z)" onClick={sync.undo}><Icon name="undo" /></button>
        <button className="btn sm icon" title="Redo (Ctrl Y)" aria-label="Redo (Ctrl Y)" onClick={sync.redo}><Icon name="redo" /></button>
        <button className="btn sm" title="Tidy layout" onClick={cmd.tidyEverything}><Icon name="layout" />Tidy</button>
        <MenuButton className="btn sm" label="Export" icon="download" items={() => exportMap(id, rf, sync.flush, show)} />
        <button className="btn sm ghost" onClick={() => show("Map shortcuts", (
          <table className="list small"><tbody>
            {SHORTCUTS.map(([k, v]) => <tr key={k}><td><kbd>{k}</kbd></td><td>{v}</td></tr>)}
          </tbody></table>
        ))}>Shortcuts</button>
      </div>
      <div className="stage canvas-bg mapcanvas" onDoubleClick={(e) => {
        if (!(e.target as HTMLElement).classList.contains("react-flow__pane")) return;
        const p = rf.screenToFlowPosition({ x: e.clientX, y: e.clientY });
        cmd.addNode({ parent: null, x: p.x - 60, y: p.y - 18 }, true);
      }}>
        <MapCtx.Provider value={cmd.cardCtx}>
          <ReactFlow
            nodes={layout.rfNodes} edges={layout.edges} nodeTypes={nodeTypes} edgeTypes={edgeTypes}
            onNodesChange={layout.onNodesChange} {...cmd.flow}
            onNodeDoubleClick={(_e, n) => setEditing(n.id)}
            onEdgeClick={(_e, edge) => { if (edge.type === "link") setSelEdge(edge.id); }}
            onEdgeDoubleClick={(_e, edge) => { if (edge.type === "link") cmd.editEdge(edge.id); }}
            onPaneClick={() => { setSelEdge(null); setLinking(null); }}
            connectionMode={ConnectionMode.Loose} connectionRadius={36} deleteKeyCode={null} zoomOnDoubleClick={false}
            panOnScroll panOnDrag={[0, 1]} selectionKeyCode="Shift" multiSelectionKeyCode={["Control", "Meta"]} selectNodesOnDrag={false}
            minZoom={0.1} maxZoom={2.5} nodeDragThreshold={2} colorMode={dark ? "dark" : "light"} attributionPosition="top-right"
            connectionLineStyle={{ stroke: css("--pen"), strokeWidth: 1.6, strokeDasharray: "5 4" }}
          >
            <Background variant={BackgroundVariant.Dots} gap={22} size={1.3} color={css("--line-strong")} />
            <Controls showInteractive={false} position="bottom-left" />
            <MiniMap pannable zoomable position="bottom-right" nodeBorderRadius={4}
                     nodeColor={(n) => { const k = (n.data as { n?: MNode }).n?.kind ?? "text"; return k === "text" ? css("--line-strong") : THINKING.has(k) ? css("--marker") : css(`--k-${k}`) || css("--muted"); }} />
          </ReactFlow>
        </MapCtx.Provider>
        {empty && (
          <div className="map-empty">
            <b>An empty map</b>
            Double-click anywhere to write an idea, or right-click to add papers, concepts, questions and other items.
            Add as many top-level items as you like and connect them by dragging from the dot on a node's side.
          </div>
        )}
        {linkingNode && (
          <div className="floatbar" style={{ top: 12, left: "50%", transform: "translateX(-50%)" }}>
            <span className="small">Click the node to connect <b>{linkingNode.label || linkingNode.text || "this node"}</b> to</span>
            <button className="btn sm" onClick={() => setLinking(null)}>Cancel</button>
          </div>
        )}
        {selectedLink && (
          <div className="floatbar" style={{ top: 12, left: "50%", transform: "translateX(-50%)" }}>
            <span className="small">{selectedLink.promoted ? "Typed link" : "Line"}{selectedLink.label ? `: ${selectedLink.label.replace(/_/g, " ")}` : ""}</span>
            <button className="btn sm" onClick={() => cmd.editEdge(selectedLink.id)}>Label…</button>
            {!selectedLink.promoted && <button className="btn sm" onClick={() => cmd.promote(selectedLink.id)}><Icon name="link" />Record as a typed link</button>}
            <button className="btn sm danger" onClick={() => cmd.deleteEdge(selectedLink.id)}>Delete</button>
          </div>
        )}
      </div>
    </div>
  );
}
