// In-app replacements for prompt()/confirm(), right-click menus, and toasts.
import { createContext, KeyboardEvent as ReactKeyboardEvent, ReactNode, useCallback, useContext, useEffect, useLayoutEffect, useRef,
  useState } from "react";
import { Dialog } from "./Dialog";
import { Icon } from "./Icon";

// ---------------------------------------------------------------------------------------------- dialogs
type Field = {
  name: string;
  label: string;
  value?: string;
  placeholder?: string;
  type?: "text" | "textarea" | "select" | "password";
  options?: { value: string; label: string }[];
  hint?: string;
  required?: boolean;
};
type FormSpec = { title: string; body?: ReactNode; fields?: Field[]; submit?: string; danger?: boolean; wide?: boolean; cancel?: string | null };
type Pending = { spec: FormSpec; resolve: (v: Record<string, string> | null) => void };

export type MenuItem =
  | { label: string; icon?: string; shortcut?: string; danger?: boolean; disabled?: boolean; onClick?: () => void; items?: MenuItem[] }
  | { sep: boolean }
  | { header: string };

type Overlays = {
  form: (spec: FormSpec) => Promise<Record<string, string> | null>;
  ask: (title: string, field: Omit<Field, "name">, submit?: string) => Promise<string | null>;
  confirm: (title: string, body?: ReactNode, submit?: string, danger?: boolean) => Promise<boolean>;
  show: (title: string, body: ReactNode, wide?: boolean) => Promise<void>;
  menu: (e: { clientX: number; clientY: number; preventDefault?: () => void }, items: MenuItem[]) => void;
  toast: (msg: string, error?: boolean) => void;
};
const Ctx = createContext<Overlays>(null as unknown as Overlays);
export const useOverlays = () => useContext(Ctx);

function FormDialog({ pending, close }: { pending: Pending; close: (v: Record<string, string> | null) => void }) {
  const { spec } = pending;
  const [vals, setVals] = useState<Record<string, string>>(() => Object.fromEntries((spec.fields ?? []).map((f) => [f.name, f.value ?? ""])));
  const first = useRef<HTMLInputElement & HTMLTextAreaElement & HTMLSelectElement>(null);
  useEffect(() => { first.current?.focus(); first.current?.select?.(); }, []);
  const missing = (spec.fields ?? []).some((f) => f.required && !vals[f.name]?.trim());
  const submit = () => { if (!missing) close(vals); };
  return (
    <Dialog title={spec.title} wide={spec.wide} onClose={() => close(null)} onSubmit={submit}
      onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) submit(); }}>
      {spec.body}
      {(spec.fields ?? []).map((f, i) => (
        <label className="field" key={f.name}>
          {f.label}
          {f.type === "textarea" ? (
            <textarea ref={i === 0 ? first : undefined} className="textarea" rows={4} placeholder={f.placeholder} value={vals[f.name]}
              onChange={(e) => setVals({ ...vals, [f.name]: e.target.value })} />
          ) : f.type === "select" ? (
            <select ref={i === 0 ? first : undefined} className="select" value={vals[f.name]} onChange={(e) => setVals({ ...vals, [f.name]: e.target.value })}>
              {f.options?.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
          ) : (
            <input ref={i === 0 ? first : undefined} className="input" type={f.type === "password" ? "password" : "text"} placeholder={f.placeholder}
              value={vals[f.name]} onChange={(e) => setVals({ ...vals, [f.name]: e.target.value })} />
          )}
          {f.hint && <span className="hint">{f.hint}</span>}
        </label>
      ))}
      <div className="actions">
        {spec.cancel !== null && <button type="button" className="btn ghost" onClick={() => close(null)}>{spec.cancel ?? "Cancel"}</button>}
        <button type="submit" className={"btn " + (spec.danger ? "danger" : "primary")} disabled={missing}>{spec.submit ?? "OK"}</button>
      </div>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------------------------- menus
type MenuListProps = { items: MenuItem[]; x: number; y: number; close: () => void; nested?: boolean; focusFirst?: boolean;
  onBack?: () => void };

/** A menu, usable by mouse and keyboard: it takes focus when it opens; Up/Down (Home/End) move, Enter or Space picks,
 * Right opens a submenu, Left (in a submenu) goes back, Escape closes (OverlayProvider) and focus returns to where it was. */
export function MenuList({ items, x, y, close, nested, focusFirst = true, onBack }: MenuListProps) {
  const ref = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState({ x, y });
  const [open, setOpen] = useState<{ i: number; kbd: boolean } | null>(null);
  const itemEls = useRef<(HTMLDivElement | null)[]>([]);
  useLayoutEffect(() => {
    if (nested || !ref.current) return;
    const r = ref.current.getBoundingClientRect();
    setPos({ x: Math.min(x, window.innerWidth - r.width - 8), y: Math.min(y, window.innerHeight - r.height - 8) });
  }, [x, y, nested]);
  const entries = () => Array.from(ref.current?.querySelectorAll<HTMLElement>(":scope > [role=menuitem]:not([aria-disabled=true])") ?? []);
  useEffect(() => { if (focusFirst) entries()[0]?.focus(); }, [focusFirst]);
  const pick = (it: Exclude<MenuItem, { sep: boolean } | { header: string }>, i: number, kbd: boolean) => {
    if (it.disabled) return;
    if (it.items) { setOpen({ i, kbd }); return; }
    close();
    it.onClick?.();
  };
  const onKey = (e: ReactKeyboardEvent) => {
    const list = entries();
    const at = list.indexOf(document.activeElement as HTMLElement);
    const go = (n: number) => { e.preventDefault(); e.stopPropagation(); list[(n + list.length) % list.length]?.focus(); };
    if (e.key === "ArrowDown") go(at + 1);
    else if (e.key === "ArrowUp") go(at < 0 ? list.length - 1 : at - 1);
    else if (e.key === "Home") go(0);
    else if (e.key === "End") go(list.length - 1);
    else if (e.key === "ArrowLeft" && onBack) { e.preventDefault(); e.stopPropagation(); onBack(); }
    else if (e.key === "Tab") { e.preventDefault(); close(); }
  };
  return (
    <div ref={ref} className={"menu" + (nested ? " sub" : "")} style={nested ? undefined : { left: pos.x, top: pos.y }}
      onMouseDown={(e) => e.stopPropagation()} onContextMenu={(e) => e.preventDefault()} role="menu" onKeyDown={onKey}>
      {items.map((it, i) => {
        if ("sep" in it) return <div key={i} className="sep" role="separator" />;
        if ("header" in it) return <div key={i} className="hd" role="presentation">{it.header}</div>;
        return (
          <div key={i} role="menuitem" tabIndex={-1} aria-disabled={it.disabled || undefined}
            aria-haspopup={it.items ? "menu" : undefined} aria-expanded={it.items ? open?.i === i : undefined}
            className={"mi" + (it.danger ? " danger" : "") + (it.disabled ? " disabled" : "") + (open?.i === i ? " active" : "")}
            onMouseEnter={() => setOpen(it.items ? { i, kbd: false } : null)}
            onClick={(e) => { if (e.target === e.currentTarget || !it.items) pick(it, i, false); }}
            onKeyDown={(e) => {
              if (e.target !== e.currentTarget) return;  // keys inside an open submenu belong to it
              if (e.key === "Enter" || e.key === " " || (e.key === "ArrowRight" && it.items)) {
                e.preventDefault();
                e.stopPropagation();
                pick(it, i, true);
              }
            }}
            ref={(el) => { itemEls.current[i] = el; }}>
            {it.icon && <Icon name={it.icon} />}
            <span>{it.label}</span>
            {it.shortcut && <span className="sc">{it.shortcut}</span>}
            {it.items && <span className="sc"><Icon name="chevron" size={12} /></span>}
            {it.items && open?.i === i && (
              <MenuList items={it.items} x={0} y={0} close={close} nested focusFirst={open.kbd}
                onBack={() => { setOpen(null); itemEls.current[i]?.focus(); }} />
            )}
          </div>
        );
      })}
    </div>
  );
}

export function OverlayProvider({ children }: { children: ReactNode }) {
  const [pending, setPending] = useState<Pending[]>([]);
  const [menu, setMenu] = useState<{ x: number; y: number; items: MenuItem[] } | null>(null);
  const menuOpener = useRef<HTMLElement | null>(null);
  const [toasts, setToasts] = useState<{ id: number; msg: string; err: boolean }[]>([]);

  const form = useCallback((spec: FormSpec) => new Promise<Record<string, string> | null>((resolve) => setPending((p) => [...p, { spec, resolve }])), []);
  const close = (v: Record<string, string> | null) => setPending((p) => { p[p.length - 1]?.resolve(v); return p.slice(0, -1); });
  const ask = useCallback(async (title: string, field: Omit<Field, "name">, submit?: string) => {
    const r = await form({ title, fields: [{ ...field, name: "v" }], submit });
    return r ? r.v : null;
  }, [form]);
  const confirm = useCallback(async (title: string, body?: ReactNode, submit?: string, danger?: boolean) =>
    (await form({ title, body, submit: submit ?? "Confirm", danger })) !== null, [form]);
  const show = useCallback(async (title: string, body: ReactNode, wide?: boolean) => { await form({ title, body, submit: "Close", cancel: null, wide }); }, [form]);
  const openMenu = useCallback((e: { clientX: number; clientY: number; preventDefault?: () => void }, items: MenuItem[]) => {
    e.preventDefault?.();
    menuOpener.current = document.activeElement as HTMLElement | null;
    setMenu({ x: e.clientX, y: e.clientY, items });
  }, []);
  const toast = useCallback((msg: string, err = false) => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t, { id, msg, err }]);
    window.setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), err ? 9000 : 5000);
  }, []);
  useEffect(() => {
    if (!menu) {  // closed: focus goes back where it was, unless something it opened (a dialog) took it
      const back = menuOpener.current;
      menuOpener.current = null;
      if (back?.isConnected && (!document.activeElement || document.activeElement === document.body)) back.focus();
      return;
    }
    const off = () => setMenu(null);
    const key = (e: KeyboardEvent) => e.key === "Escape" && off();
    window.addEventListener("mousedown", off);
    window.addEventListener("blur", off);
    window.addEventListener("keydown", key);
    window.addEventListener("wheel", off, { passive: true });
    return () => { window.removeEventListener("mousedown", off); window.removeEventListener("blur", off); window.removeEventListener("keydown", key); window.removeEventListener("wheel", off); };
  }, [menu]);

  return (
    <Ctx.Provider value={{ form, ask, confirm, show, menu: openMenu, toast }}>
      {children}
      {pending.length > 0 && <FormDialog key={pending.length} pending={pending[pending.length - 1]} close={close} />}
      {menu && <MenuList items={menu.items} x={menu.x} y={menu.y} close={() => setMenu(null)} />}
      <div className="toasts">
        {toasts.map((t) => <div key={t.id} className={"toast" + (t.err ? " err" : "")} onClick={() => setToasts((x) => x.filter((y) => y.id !== t.id))}>{t.msg}</div>)}
      </div>
    </Ctx.Provider>
  );
}

/** Keyboard for a clickable table row (give it tabIndex 0): Enter opens it, Space selects it, the menu key or
 * Shift+F10 opens its right-click menu. Keys pressed in a control inside the row (a checkbox) are left alone. */
export function rowKeys(on: { open?: () => void; select?: () => void; menu?: (at: { clientX: number; clientY: number }) => void }) {
  return (e: ReactKeyboardEvent<HTMLElement>) => {
    if (e.target !== e.currentTarget) return;
    const enter = on.open ?? on.select;
    if (e.key === "Enter" && enter) { e.preventDefault(); enter(); }
    else if (e.key === " " && on.select) { e.preventDefault(); on.select(); }
    else if ((e.key === "ContextMenu" || (e.key === "F10" && e.shiftKey)) && on.menu) {
      e.preventDefault();
      const r = e.currentTarget.getBoundingClientRect();
      on.menu({ clientX: r.left + 24, clientY: r.top + r.height / 2 });
    }
  };
}

// A button that opens a menu below itself (Export ▾, Add ▾ …).
export function MenuButton({ label, icon, items, className }: { label: string; icon?: string; items: MenuItem[] | (() => MenuItem[]); className?: string }) {
  const { menu } = useOverlays();
  return (
    <button className={className ?? "btn"} onMouseDown={(e) => e.stopPropagation()} onClick={(e) => {
      const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
      menu({ clientX: r.left, clientY: r.bottom + 4 }, typeof items === "function" ? items() : items);
    }}>
      {icon && <Icon name={icon} />}{label}<Icon name="down" size={13} />
    </button>
  );
}

export function download(filename: string, data: Blob | string, type = "text/plain") {
  const blob = typeof data === "string" ? new Blob([data], { type: `${type};charset=utf-8` }) : data;
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 2000);
}
