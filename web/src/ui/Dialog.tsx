// The one modal frame: a dimmed backdrop that closes on a click outside or Escape. It is a real modal for keyboards and
// screen readers: aria-modal, Tab stays inside, focus moves in when it opens and back to where it was when it closes.
import { FormEvent, KeyboardEvent, ReactNode, useEffect, useRef, useState } from "react";

type Props = {
  onClose: () => void;
  children: ReactNode;
  title?: string;
  wide?: boolean;
  /** "palette" for the search boxes; "dialog" otherwise. */
  look?: "dialog" | "palette";
  /** Given: the dialog is a form. */
  onSubmit?: () => void;
  onKeyDown?: (e: KeyboardEvent) => void;
};

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), ' +
  'textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

export function focusables(root: HTMLElement): HTMLElement[] {
  return Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE)).filter((el) => !el.hidden && el.getAttribute("aria-hidden") !== "true");
}

export function Dialog({ onClose, children, title, wide, look = "dialog", onSubmit, onKeyDown }: Props) {
  const close = useRef(onClose);
  close.current = onClose;
  const box = useRef<HTMLElement | null>(null);
  // What had focus before this dialog rendered (an autoFocus field inside takes it during the first commit).
  const [opener] = useState(() => document.activeElement as HTMLElement | null);
  useEffect(() => {
    const root = box.current;
    // Children focus their own first field in their effects; otherwise focus the first control, or the dialog itself.
    if (root && !root.contains(document.activeElement)) (focusables(root)[0] ?? root).focus();
    const k = (e: globalThis.KeyboardEvent) => {
      if (e.key === "Escape") { close.current(); return; }
      if (e.key !== "Tab" || !box.current) return;
      const items = focusables(box.current);
      if (!items.length) { e.preventDefault(); box.current.focus(); return; }
      const first = items[0], last = items[items.length - 1];
      const at = document.activeElement as HTMLElement | null;
      if (!at || !box.current.contains(at)) { e.preventDefault(); first.focus(); }
      else if (e.shiftKey && at === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && at === last) { e.preventDefault(); first.focus(); }
    };
    window.addEventListener("keydown", k);
    return () => {
      window.removeEventListener("keydown", k);
      if (opener && opener.isConnected && typeof opener.focus === "function") opener.focus();
    };
  }, [opener]);
  const inner = {
    className: look === "palette" ? "palette" : "dialog" + (wide ? " wide" : ""),
    onMouseDown: (e: { stopPropagation: () => void }) => e.stopPropagation(),
    onKeyDown,
    role: "dialog",
    "aria-modal": true,
    "aria-label": title,
    tabIndex: -1,
  };
  const body = <>{title && <h2>{title}</h2>}{children}</>;
  return (
    <div className="overlay" onMouseDown={() => close.current()}>
      {onSubmit
        ? <form {...inner} ref={(el) => { box.current = el; }} onSubmit={(e: FormEvent) => { e.preventDefault(); onSubmit(); }}>{body}</form>
        : <div {...inner} ref={(el) => { box.current = el; }}>{body}</div>}
    </div>
  );
}
