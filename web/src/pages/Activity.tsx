import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useApp } from "../app/context";
import { api, JournalEntry } from "../lib/api";
import { useOverlays } from "../ui/overlays";

function describe(e: JournalEntry): string {
  const p = e.payload as Record<string, unknown>;
  switch (e.op) {
    case "project.add": return `captured a paper${p.why ? ` — ${p.why}` : ""}`;
    case "tag.apply": return `tagged ${(p.targets as string[] | undefined)?.length ?? 0} ${p.type}${(p.targets as string[] | undefined)?.length === 1 ? "" : "s"} ${p.tag}`;
    case "search": return `searched “${p.query}”: ${p.hits} hits, ${p.new} new`;
    case "note.add": return `added a ${p.kind}${p.verification === "verified" ? " (quote verified)" : ""}`;
    case "work.create": return `new paper ${p.citekey}`;
    case "work.merge": return "merged a duplicate";
    case "work.enrich": return `found the published version: ${p.venue}`;
    case "document.create": return `stored full text (${String(p.source).replace("_", " ")}, ${p.passages} passages)`;
    case "entity.upsert": return `${p.change} ${p.kind} “${p.title}”`;
    case "entity.delete": return `deleted ${p.entity}`;
    case "link.add": return `linked ${p.relation}`;
    case "map.edit": return `edited ${p.map}`;
    case "map.create": return `created ${p.map}`;
    case "config.set": return `changed settings: ${(p.keys as string[]).join(", ")}`;
    default: return e.op.replace(".", " ");
  }
}

export default function Activity() {
  const { project, subscribe, me } = useApp();
  const { toast } = useOverlays();
  const [journal, setJournal] = useState<JournalEntry[]>([]);
  const [searches, setSearches] = useState<{ at: string; query: string; sources: string[]; new: number; hits: number }[]>([]);
  const [snow, setSnow] = useState<{ items: { handle: string; title: string; year: number | null; seeds: string[]; direction: string; score: number }[]; total: number; counts: Record<string, number> } | null>(null);
  const [jobs, setJobs] = useState<{ id: string; kind: string; state: string; error: string | null; created_at: string }[]>([]);

  useEffect(() => {
    api.journal(0, 150).then(setJournal);
    api.searches().then(setSearches);
    api.snowball().then(setSnow);
    api.jobs().then(setJobs);
  }, [project]);
  useEffect(() => subscribe((e) => {
    setJournal((j) => (j.some((x) => x.seq === e.seq) ? j : [e, ...j].slice(0, 300)));
    if (e.op === "search") api.searches().then(setSearches);
    if (e.op?.startsWith("snowball")) api.snowball().then(setSnow);
  }), [subscribe]);

  const decide = async (handle: string, state: "in" | "out") => {
    toast(await api.tool("snowball", { action: "decide", decisions: [{ handle, state }] }));
    api.snowball().then(setSnow);
  };

  return (
    <div className="page">
      <div className="page-head"><h1>Activity</h1><span className="small muted">{project}</span></div>
      <div className="cols-2">
        <div className="panel">
          <table className="list"><tbody>
            {journal.map((e) => (
              <tr key={e.seq}>
                <td className="small muted" style={{ width: 110, whiteSpace: "nowrap" }}>{e.ts.slice(5, 16).replace("T", " ")}</td>
                <td className="small" style={{ width: 170 }}>{e.by ?? e.principal ?? "system"}{e.agent ? <div><span className="badge agent">{e.agent.slice(0, 24)}</span></div> : null}</td>
                <td>{describe(e)}</td>
              </tr>
            ))}
          </tbody></table>
        </div>
        <div className="stack">
          <div className="panel pad stack">
            <div className="section-title" style={{ margin: 0 }}>Searches</div>
            {searches.slice(0, 12).map((s, i) => (
              <div key={i} className="small"><b>{s.query}</b><div className="muted">{s.at.slice(0, 10)} · {s.hits} hits, {s.new} new · {s.sources.join(", ")}</div></div>
            ))}
            {!searches.length && <span className="small muted">No external searches yet.</span>}
          </div>
          <div className="panel pad stack">
            <div className="section-title" style={{ margin: 0 }}>Citation frontier</div>
            {snow && <div className="small muted">{Object.entries(snow.counts).map(([k, v]) => `${v} ${k}`).join(", ") || "Empty — agents fill it with snowball expand."}</div>}
            {snow?.items.slice(0, 20).map((i) => (
              <div key={i.handle} className="small">
                <div>{i.title || i.handle} <span className="muted">{i.year ?? ""}</span></div>
                <div className="row"><span className="muted grow">{i.seeds.length} seed{i.seeds.length === 1 ? "" : "s"}</span>
                  <button className="btn sm" onClick={() => decide(i.handle, "in")}>Add</button>
                  <button className="btn sm ghost" onClick={() => decide(i.handle, "out")}>Skip</button>
                </div>
              </div>
            ))}
          </div>
          <div className="panel pad stack">
            <div className="section-title" style={{ margin: 0 }}>Background jobs</div>
            {jobs.slice(0, 10).map((j) => (
              <div key={j.id} className="small row"><span className="grow">{j.kind.replace("_", " ")}</span><span className="badge">{j.state}</span></div>
            ))}
            {!jobs.length && <span className="small muted">None.</span>}
          </div>
          {me.admin && <Link to="/admin/sources" className="small">Sources & settings</Link>}
        </div>
      </div>
    </div>
  );
}
