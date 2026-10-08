import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useApp } from "../app/context";
import { api } from "../lib/api";
import { Icon } from "../ui/Icon";
import { useOverlays } from "../ui/overlays";

export default function MapsPage() {
  const { project, tick } = useApp();
  const { form, menu, ask, confirm, toast } = useOverlays();
  const [maps, setMaps] = useState<{ id: string; title: string; updated_at: string; nodes: number }[]>([]);
  const [params, setParams] = useSearchParams();
  const nav = useNavigate();
  const reload = () => api.maps().then(setMaps);
  useEffect(() => { reload(); }, [project, tick]);

  const create = async () => {
    const r = await form({ title: "New mind map", submit: "Create", fields: [
      { name: "title", label: "Title", required: true, placeholder: "e.g. Delta-rule memory" },
      { name: "outline", label: "Start from an outline (optional)", type: "textarea",
        placeholder: "- topic:delta-rule-memory\n  - yang2024gated adds gating\n  - question: does decay hurt recall?" },
    ] });
    if (!r) return;
    const res = r.outline.trim()
      ? { map: (await api.tool("map_edit", { title: r.title, outline: r.outline })).split(" ")[1] }
      : await api.editMap({ title: r.title, ops: [] });
    if (!res.map) return toast("Could not create the map", true);
    nav(`/maps/${encodeURIComponent(res.map)}`);
  };
  useEffect(() => { if (params.get("new") === "1") { setParams({}); create(); } }, [params]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="page">
      <div className="page-head">
        <h1>Mind maps</h1>
        <button className="btn primary" onClick={create}><Icon name="plus" />New map</button>
      </div>
      <p className="muted" style={{ marginTop: -6, maxWidth: "72ch" }}>
        Nodes can be papers, methods, datasets, benchmarks, results, questions, ideas, hypotheses, experiments, notes, tags or other maps — or plain text.
        Agents read a map as an outline and can build one from a paper's extracted knowledge.
      </p>
      {maps.length === 0 ? (
        <div className="empty"><b>No maps yet</b>Create one, or open a paper's Knowledge tab and choose Map it.</div>
      ) : (
        <table className="list"><tbody>
          {maps.map((m) => (
            <tr key={m.id} onClick={() => nav(`/maps/${encodeURIComponent(m.id)}`)} style={{ cursor: "pointer" }}
              onContextMenu={(e) => menu(e, [
                { label: "Open", icon: "open", onClick: () => nav(`/maps/${encodeURIComponent(m.id)}`) },
                { label: "Rename…", onClick: async () => { const t = await ask("Rename map", { label: "Title", value: m.title, required: true }, "Rename"); if (t) { await api.editMap({ map: m.id, title: t, ops: [] }); reload(); } } },
                { sep: true },
                { label: "Delete", icon: "trash", danger: true, onClick: async () => {
                  if (await confirm(`Delete “${m.title}”?`, <p className="muted" style={{ margin: 0 }}>The papers and items in it are not affected.</p>, "Delete", true)) {
                    await api.deleteMap(m.id); reload();
                  }
                } },
              ])}>
              <td><div style={{ fontWeight: 600, fontSize: 15 }}>{m.title}</div><div className="w-meta"><span>{m.nodes} nodes</span><span>updated {m.updated_at.slice(0, 10)}</span></div></td>
              <td style={{ width: 40 }}><Icon name="chevron" /></td>
            </tr>
          ))}
        </tbody></table>
      )}
    </div>
  );
}
