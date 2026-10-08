// Reference picker (anything in the ledger, or create/capture) and the Ctrl+K command palette.
import { createContext, ReactNode, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { api, RefResults } from "../lib/api";
import { kindStyle, THINKING } from "../lib/kinds";
import { Dialog } from "./Dialog";
import { Icon } from "./Icon";
import { useOverlays } from "./overlays";

export type Picked = { ref: string | null; label: string; kind: string; text?: string };
type PickOpts = { title?: string; create?: string[]; text?: boolean; capture?: boolean; kinds?: string[] };
type Item = { key: string; group: string; label: string; kind: string; sub?: string; run: () => Promise<Picked | null> };

const Ctx = createContext<{ pick: (o?: PickOpts) => Promise<Picked | null>; palette: () => void }>(null as never);
export const usePicker = () => useContext(Ctx);

function useRefItems(q: string, opts: PickOpts, onCreate: (kind: string) => Promise<Picked | null>): Item[] {
  const { toast } = useOverlays();
  const [res, setRes] = useState<RefResults | null>(null);
  useEffect(() => {
    if (q.trim().length < 2) { setRes(null); return; }
    const h = window.setTimeout(() => api.refs(q).then(setRes).catch(() => setRes(null)), 160);
    return () => window.clearTimeout(h);
  }, [q]);
  return useMemo(() => {
    const t = q.trim();
    const items: Item[] = [];
    if (res) {
      if (res.capture && opts.capture !== false) items.push({ key: "cap", group: "Capture", label: `Add ${res.capture.handle} to this project`, kind: "work", run: async () => {
        const out = await api.tool("resolve", { items: [res.capture!.input], add: true });
        const m = out.match(/keys: (\S+)/);
        if (!m) { toast(out, true); return null; }
        return { ref: m[1], label: m[1], kind: "work" };
      } });
      const allow = (k: string) => !opts.kinds || opts.kinds.includes(k);
      if (allow("work")) res.works.forEach((w) => items.push({ key: w.ref, group: "Papers", label: w.title, kind: "work", sub: `${w.ref}${w.year ? " " + w.year : ""}`, run: async () => ({ ref: w.ref, label: w.title, kind: "work" }) }));
      res.entities.filter((e) => allow(e.kind)).forEach((e) => items.push({ key: e.ref, group: "Knowledge", label: e.title, kind: e.kind, sub: e.kind, run: async () => ({ ref: e.ref, label: e.title, kind: e.kind }) }));
      if (allow("note")) res.notes.forEach((n) => items.push({ key: n.ref, group: "Notes", label: n.text, kind: "note", sub: n.subject, run: async () => ({ ref: n.ref, label: n.text, kind: "note" }) }));
      if (allow("tag")) res.tags.forEach((g) => items.push({ key: g.ref, group: "Tags", label: g.title, kind: "tag", sub: `${g.count} tagged`, run: async () => ({ ref: g.ref, label: g.title, kind: "tag" }) }));
      if (allow("map")) res.maps.forEach((m) => items.push({ key: m.ref, group: "Maps", label: m.title, kind: "map", run: async () => ({ ref: m.ref, label: m.title, kind: "map" }) }));
    }
    if (t.length >= 2) {
      (opts.create ?? []).forEach((k) => items.push({ key: `new-${k}`, group: "Create", label: `New ${k}: “${t}”`, kind: k, run: () => onCreate(k) }));
      if (opts.text) items.push({ key: "text", group: "Create", label: `Plain text: “${t}”`, kind: "text", run: async () => ({ ref: null, label: t, kind: "text", text: t }) });
    }
    return items;
  }, [res, q, opts, onCreate, toast]);
}

function Results({ items, active, setActive, choose }: { items: Item[]; active: number; setActive: (i: number) => void; choose: (it: Item) => void }) {
  let last = "";
  return (
    <div className="res">
      {items.map((it, i) => {
        const head = it.group !== last ? <div className="grp">{it.group}</div> : null;
        last = it.group;
        return (
          <div key={it.key}>
            {head}
            <div className={"it" + (i === active ? " on" : "")} onMouseEnter={() => setActive(i)} onMouseDown={(e) => { e.preventDefault(); choose(it); }}>
              <KindLabel kind={it.kind} />
              <span className="lbl">{it.label}</span>
              {it.sub && <span className="sub">{it.sub}</span>}
            </div>
          </div>
        );
      })}
    </div>
  );
}

export type Action = { label: string; icon: string; run: () => void; keywords?: string };

/** The search box behind both the reference picker and the command palette; `done` gets what was chosen. */
function Finder({ opts, actions = [], placeholder, foot, done }: { opts: PickOpts; actions?: Action[]; placeholder: string; foot: ReactNode;
                                                                  done: (p: Picked | null) => void }) {
  const [q, setQ] = useState("");
  const [active, setActive] = useState(0);
  const { toast } = useOverlays();
  const create = useCallback(async (kind: string) => {
    const out = await api.tool("entity", { items: [{ kind, title: q.trim() }] });
    const m = out.match(new RegExp(`${kind}:[a-z0-9-]+`));
    if (!m) { toast(out, true); return null; }
    return { ref: m[0], label: q.trim(), kind };
  }, [q, toast]);
  const refItems = useRefItems(q, opts, create);
  const actionItems: Item[] = actions
    .filter((a) => !q.trim() || (a.label + " " + (a.keywords ?? "")).toLowerCase().includes(q.trim().toLowerCase()))
    .map((a) => ({ key: "a-" + a.label, group: "Actions", label: a.label, kind: "action", run: async () => { a.run(); return null; } }));
  const items = [...refItems, ...actionItems];
  const choose = async (it: Item) => done(await it.run());
  return (
    <Dialog look="palette" onClose={() => done(null)}>
      <input autoFocus placeholder={placeholder} value={q}
        onChange={(e) => { setQ(e.target.value); setActive(0); }}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") { e.preventDefault(); setActive((a) => Math.min(a + 1, items.length - 1)); }
          if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)); }
          if (e.key === "Enter" && items[active]) choose(items[active]);
        }} />
      <Results items={items} active={active} setActive={setActive} choose={choose} />
      <div className="foot">{foot}</div>
    </Dialog>
  );
}

export function PickerProvider({ children, actions, onOpen }: { children: ReactNode; actions: Action[]; onOpen: (p: Picked) => void }) {
  const [pending, setPending] = useState<{ opts: PickOpts; resolve: (p: Picked | null) => void } | null>(null);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const pick = useCallback((opts: PickOpts = {}) => new Promise<Picked | null>((resolve) => setPending({ opts, resolve })), []);
  const actionsRef = useRef(actions);
  actionsRef.current = actions;
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); setPaletteOpen((o) => !o); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  return (
    <Ctx.Provider value={{ pick, palette: () => setPaletteOpen(true) }}>
      {children}
      {pending && <Finder opts={pending.opts} placeholder={pending.opts.title ?? "Find a paper, method, dataset, note, tag, map — or paste an arXiv ID / DOI"}
                          foot={<><span><kbd>↑</kbd> <kbd>↓</kbd> move</span><span><kbd>Enter</kbd> pick</span><span><kbd>Esc</kbd> close</span></>}
                          done={(p) => { pending.resolve(p); setPending(null); }} />}
      {paletteOpen && <Finder opts={{ capture: true }} actions={actionsRef.current} placeholder="Jump to a paper, method, dataset, map… or run an action"
                              foot={<><span><kbd>Enter</kbd> open</span><span><kbd>Esc</kbd> close</span><span>Paste an arXiv ID or DOI to capture it</span></>}
                              done={(p) => { setPaletteOpen(false); if (p) onOpen(p); }} />}
    </Ctx.Provider>
  );
}

export function TagChip({ name, onRemove, state }: { name: string; onRemove?: () => void; state?: "on" | "off" }) {
  const i = name.indexOf(":");
  return (
    <span className={"chip" + (state ? " " + state : "")}>
      {i > 0 && <span className="ns">{name.slice(0, i + 1)}</span>}
      {i > 0 ? name.slice(i + 1) : name}
      {onRemove && <button onClick={(e) => { e.stopPropagation(); onRemove(); }} aria-label={`remove ${name}`}><Icon name="x" size={11} /></button>}
    </span>
  );
}

export function KindLabel({ kind, children }: { kind: string; children?: ReactNode }) {
  return <span className={"kind" + (THINKING.has(kind) ? " thinking" : "")} style={kindStyle(kind)}>{children ?? kind}</span>;
}
