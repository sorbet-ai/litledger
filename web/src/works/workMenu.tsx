// What can be done with a paper from anywhere it shows up: its right-click menu, the agent brief, exports.
import { useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { useApp } from "../app/context";
import { api } from "../lib/api";
import { READ_LEVELS } from "../lib/kinds";
import { CopyBlock, useCopy } from "../ui/CopyBlock";
import { download, MenuItem, useOverlays } from "../ui/overlays";

const EXPORTS = [
  { format: "bibtex", label: "BibTeX", ext: "bib" },
  { format: "biblatex", label: "BibLaTeX", ext: "bib" },
  { format: "csl", label: "CSL-JSON", ext: "json" },
  { format: "ris", label: "RIS", ext: "ris" },
  { format: "snapshot", label: "Project snapshot (JSON)", ext: "json" },
];

/** One menu item per export format; `args` picks the papers (works, tags, or the whole project). */
export function exportItems(project: string, args: Record<string, unknown>, toast: (msg: string) => void): MenuItem[] {
  return EXPORTS.map(({ format, label, ext }) => ({ label, onClick: async () => {
    const text = await api.tool("export", { format, inline: true, ...args });
    const body = text.startsWith("%") ? text.slice(text.indexOf("\n") + 1) : text;
    download(`${project}-${format}.${ext}`, body, format === "csl" || format === "snapshot" ? "application/json" : "text/plain");
    toast("Exported " + text.split("\n", 1)[0].replace(/^%\s*/, ""));
  } }));
}

/** Shared right-click menu for a paper (Library rows, preview, graph nodes, maps). */
export function useWorkMenu() {
  const { ask, confirm, show, toast } = useOverlays();
  const { project } = useApp();
  const nav = useNavigate();
  const copy = useCopy();
  return useCallback((w: { citekey: string; tags?: string[]; in_project?: boolean }, after: () => void): MenuItem[] => {
    const run = async (name: string, args: Record<string, unknown>) => { toast(await api.tool(name, args)); after(); };
    return [
      { label: "Open", icon: "open", onClick: () => nav(`/work/${encodeURIComponent(w.citekey)}`) },
      { label: "Read", icon: "read", onClick: () => nav(`/work/${encodeURIComponent(w.citekey)}?tab=text`) },
      { sep: true },
      { label: "Copy citekey", icon: "copy", shortcut: w.citekey, onClick: () => copy(w.citekey, `Copied ${w.citekey}`) },
      { label: "Copy BibTeX", icon: "copy", onClick: async () => {
        const t = await api.tool("export", { works: [w.citekey], format: "bibtex", inline: true });
        copy(t.slice(t.indexOf("\n") + 1), "Copied BibTeX");
      } },
      { sep: true },
      { label: "Add tag…", icon: "tag", onClick: async () => {
        const t = await ask("Add tag", { label: "Tag", placeholder: "phase:baselines", hint: "Comma-separated" }, "Add");
        if (t) run("tag", { action: "apply", tags: t.split(",").map((x) => x.trim()).filter(Boolean), works: [w.citekey] });
      } },
      ...(w.tags?.length ? [{ label: "Remove tag", icon: "tag", items: w.tags.map((t) => ({ label: t, onClick: () => run("tag", { action: "remove", tags: [t], works: [w.citekey] }) })) }] : []),
      // Reading and why belong to this project: offered once the paper is in it ("Add to …" below).
      ...(w.in_project === false ? [] : [
        { label: "Reading", icon: "read", items: READ_LEVELS.map((r) => ({ label: r, onClick: () => run("update_work", { items: [{ work: w.citekey, read: r }] }) })) },
        { label: "Why it matters…", icon: "note", onClick: async () => {
          const why = await ask("Why it matters", { label: "One line", type: "textarea" }, "Save");
          if (why !== null) run("update_work", { items: [{ work: w.citekey, why }] });
        } },
      ]),
      { label: "Add a note…", icon: "note", onClick: async () => {
        const text = await ask(`Note on ${w.citekey}`, { label: "Note", type: "textarea" }, "Add note");
        if (text) run("note", { items: [{ work: w.citekey, text }] });
      } },
      { sep: true },
      { label: "Extract knowledge with an agent…", icon: "robot", onClick: async () => {
        const brief = await api.brief("extract_paper", w.citekey);
        show(`Extract ${w.citekey} with an agent`, <AgentBrief brief={brief} work={w.citekey} />, true);
      } },
      { label: "Add to map…", icon: "map", onClick: async () => {
        const maps = await api.maps();
        const r = await ask("Add to map", { label: "Map", type: "select", value: maps[0]?.id ?? "__new",
          options: [...maps.map((m) => ({ value: m.id, label: m.title })), { value: "__new", label: "New map…" }] }, "Add");
        if (!r) return;
        const res = await api.editMap(r === "__new" ? { title: w.citekey, ops: [{ op: "expand", ref: w.citekey }] } : { map: r, ops: [{ op: "add", ref: w.citekey }] });
        nav(`/maps/${encodeURIComponent(res.map)}`);
      } },
      { sep: true },
      w.in_project === false
        ? { label: `Add to ${project}`, icon: "plus", onClick: () => run("resolve", { items: [w.citekey], add: true }) }
        : { label: "Remove from project", icon: "trash", danger: true, onClick: async () => {
          if (await confirm(`Remove ${w.citekey} from ${project}?`, <p className="muted" style={{ margin: 0 }}>It stays in the library and in other projects.</p>, "Remove", true))
            run("update_work", { items: [{ work: w.citekey, remove: true }] });
        } },
    ];
  }, [ask, confirm, show, toast, nav, project, copy]);
}

export function AgentBrief({ brief, work }: { brief: string; work: string }) {
  return (
    <div className="stack">
      <p className="small muted" style={{ margin: 0 }}>
        Hand this to any agent connected to litledger (it needs the <b>graph</b> toolset, or the CLI). It reads the paper and records
        methods, datasets, benchmarks, metrics, results, claims and limitations, linked to the paper. MCP clients also see it as the
        prompt <b>extract_paper</b>.
      </p>
      <CopyBlock title="From a terminal" text={`claude -p "$(litledger brief extract_paper ${work})"`} />
      <CopyBlock title="The brief" text={brief} pre />
    </div>
  );
}
