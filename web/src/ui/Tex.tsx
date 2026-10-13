import { useEffect, useState } from "react";

/** Text with TeX in it, as papers' extracted text and quotes have: $…$, $$…$$, \(…\) and \[…\] render as maths.
 * KaTeX loads only once some text on the page has maths. Each formula keeps its source in data-tex, so a selection
 * (see selectedSource) still matches the stored text a quote is checked against. */

export type Piece = { text: string } | { tex: string; src: string; display: boolean };

// $…$ only when it reads as maths: no space just inside the dollars, not followed by a digit (so "$5 and $10" stays
// text), on one line; the display forms may span lines
const MATH = /\$\$([^$]+?)\$\$|\\\[([\s\S]+?)\\\]|\\\(([\s\S]+?)\\\)|\$(?=[^\s$])((?:\\.|[^$\\\n]){1,400}?)(?<=[^\s\\$])\$(?!\d)/g;

export function splitTex(text: string): Piece[] {
  const out: Piece[] = [];
  let at = 0;
  for (const m of text.matchAll(MATH)) {
    const tex = m[1] ?? m[2] ?? m[3] ?? m[4];
    if (m.index! > at) out.push({ text: text.slice(at, m.index) });
    out.push({ tex: tex.trim(), src: m[0], display: m[1] !== undefined || m[2] !== undefined });
    at = m.index! + m[0].length;
  }
  if (at < text.length) out.push({ text: text.slice(at) });
  return out;
}

type Katex = typeof import("katex").default;
let katex: Katex | null = null;
let loading: Promise<Katex> | null = null;
const load = () => (loading ??= Promise.all([import("katex"), import("katex/dist/katex.min.css")]).then(([m]) => (katex = m.default)));

const cache = new Map<string, string | null>();
function html(k: Katex, tex: string, display: boolean): string | null {
  const key = (display ? "D" : "i") + tex;
  if (!cache.has(key)) {
    try {
      cache.set(key, k.renderToString(tex, { displayMode: display, throwOnError: true, output: "html", strict: false, trust: false }));
    } catch {
      cache.set(key, null); // not TeX after all (or KaTeX can't): show it as written
    }
  }
  return cache.get(key)!;
}

export function Tex({ text }: { text: string | null | undefined }) {
  const pieces = splitTex(text ?? "");
  const hasMath = pieces.some((p) => "tex" in p);
  const [k, setK] = useState<Katex | null>(katex);
  useEffect(() => { if (hasMath && !k) load().then(setK).catch(() => {}); }, [hasMath, k]);
  if (!hasMath) return <>{text}</>;
  return (
    <>
      {pieces.map((p, i) => {
        if ("text" in p) return <span key={i}>{p.text}</span>;
        const out = k && html(k, p.tex, p.display);
        return out
          ? <span key={i} className={`tex${p.display ? " tex-display" : ""}`} data-tex={p.src} dangerouslySetInnerHTML={{ __html: out }} />
          : <span key={i}>{p.src}</span>;
      })}
    </>
  );
}

/** The selection as the stored text has it: rendered formulas give back their TeX source. */
export function selectedSource(sel: Selection | null): string {
  if (!sel || sel.rangeCount === 0) return "";
  const range = sel.getRangeAt(0).cloneRange();
  const formula = (n: Node) => (n instanceof Element ? n : n.parentElement)?.closest<HTMLElement>("[data-tex]");
  // a selection that starts or ends inside a formula takes the whole formula
  const first = formula(range.startContainer), last = formula(range.endContainer);
  if (first) range.setStartBefore(first);
  if (last) range.setEndAfter(last);
  const div = document.createElement("div");
  div.appendChild(range.cloneContents());
  if (!div.querySelector("[data-tex]")) return sel.toString();
  div.querySelectorAll<HTMLElement>("[data-tex]").forEach((el) => el.replaceWith(el.dataset.tex ?? ""));
  return div.textContent ?? "";
}
