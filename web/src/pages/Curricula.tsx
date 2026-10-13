import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useApp } from "../app/context";
import { api, CurriculumSummary } from "../lib/api";
import { CopyBlock } from "../ui/CopyBlock";
import { Icon } from "../ui/Icon";
import { useOverlays } from "../ui/overlays";

export const hours = (minutes: number) => (minutes < 60 ? `${minutes} min` : `${Math.round(minutes / 6) / 10} h`);

export default function CurriculaPage() {
  const { project, tick } = useApp();
  const { menu, confirm, toast } = useOverlays();
  const [items, setItems] = useState<CurriculumSummary[] | null>(null);
  const nav = useNavigate();
  const reload = () => api.curricula().then(setItems).catch((e) => toast(String(e), true));
  useEffect(() => { reload(); }, [project, tick]); // eslint-disable-line react-hooks/exhaustive-deps

  const actions = (c: CurriculumSummary) => [
    { label: "Open", icon: "open", onClick: () => nav(`/curricula/${encodeURIComponent(c.id)}`) },
    { sep: true as const },
    { label: "Delete", icon: "trash", danger: true, onClick: async () => {
      if (await confirm(`Delete “${c.title}”?`, <p className="muted" style={{ margin: 0 }}>The papers and their links stay in the library.</p>, "Delete", true)) {
        await api.deleteCurriculum(c.id); reload();
      }
    } },
  ];

  return (
    <div className="page">
      <div className="page-head">
        <h1>Curricula</h1>
      </div>
      <div className="panel pad stack" style={{ gap: 8, marginBottom: 18 }}>
        <b>Build one with an agent</b>
        <div className="small muted">In Claude Code, with a strong model; it reads the papers, traces how the idea evolved and writes the stages here.</div>
        <CopyBlock title="Claude Code" text="/litledger:curriculum delta rule" note="Add who it's for or a time budget after the idea, e.g. “for ML engineers, about 10 hours”." />
      </div>
      {items && items.length === 0 && <div className="empty"><b>No curricula yet</b></div>}
      {items && items.length > 0 && (
        <table className="list"><tbody>
          {items.map((c) => (
            <tr key={c.id} onClick={() => nav(`/curricula/${encodeURIComponent(c.id)}`)} style={{ cursor: "pointer" }} onContextMenu={(e) => menu(e, actions(c))}>
              <td>
                <div style={{ fontWeight: 600, fontSize: 15 }}>{c.title}</div>
                {c.idea && <div className="small muted clamp2">{c.idea}</div>}
                <div className="w-meta"><span>{c.stages} stage{c.stages === 1 ? "" : "s"}</span><span>{c.readings} reading{c.readings === 1 ? "" : "s"}</span>
                  {c.minutes > 0 && <span>{hours(c.minutes)}</span>}<span>updated {c.updated_at.slice(0, 10)}</span></div>
              </td>
              <td style={{ width: 44 }}>
                <button className="btn ghost sm icon" aria-label={`More for ${c.title}`} title="More"
                        onClick={(e) => { e.stopPropagation(); menu(e, actions(c)); }}><Icon name="more" /></button>
              </td>
            </tr>
          ))}
        </tbody></table>
      )}
    </div>
  );
}
