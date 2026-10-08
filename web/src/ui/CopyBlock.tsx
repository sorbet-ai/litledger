// Copying to the clipboard, and a titled block of text with a Copy button (commands, tokens, briefs).
import { ReactNode, useCallback } from "react";
import { Icon } from "./Icon";
import { useOverlays } from "./overlays";

export function useCopy() {
  const { toast } = useOverlays();
  return useCallback((text: string, what = "Copied") => {
    navigator.clipboard?.writeText(text).then(() => toast(what), () => toast("Copy failed: select the text and copy it by hand", true));
  }, [toast]);
}

/** `pre` shows long text (a brief) as a scrolling block instead of a one-line command. */
export function CopyBlock({ title, text, note, pre }: { title: string; text: string; note?: ReactNode; pre?: boolean }) {
  const copy = useCopy();
  return (
    <div className="stack" style={{ gap: 6 }}>
      <div className="row"><b className="small grow">{title}</b><button type="button" className="btn sm" onClick={() => copy(text)}><Icon name="copy" />Copy</button></div>
      {pre ? <pre>{text}</pre> : <code className="cmd">{text}</code>}
      {note && <div className="tiny muted">{note}</div>}
    </div>
  );
}
