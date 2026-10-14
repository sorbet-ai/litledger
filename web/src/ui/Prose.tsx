import enUs from "hyphenation.en-us";
import { useLayoutEffect, useRef } from "react";
import { breakLines, createHyphenator, type InputItem } from "tex-linebreak";
import { loadKatex, mathHtml, splitTex } from "./Tex";

/**
 * Reading text set the way TeX sets it, as the billing documents are: Knuth–Plass chooses all of a paragraph's line
 * breaks together, spaces stretch by ½ and shrink by ⅓ of their width, and words hyphenate with TeX's US-English
 * patterns. Formulas render with KaTeX and travel as unbreakable boxes; display formulas get their own centred line.
 *
 * The element shows the plain text at once, then swaps in the set lines (and sets them again when its width changes).
 * What it renders still selects and copies as the stored text: formulas carry their TeX in data-tex, the hyphens added
 * at line ends are .hy (not selectable, dropped by selectedSource), and every line keeps its closing space.
 */

const hyphenate = createHyphenator(enUs);
const HYPHEN_COST = 50, EXPLICIT_COST = 50;   // TeX's \hyphenpenalty and \exhyphenpenalty
const LEFT_MIN = 2, RIGHT_MIN = 3;            // \lefthyphenmin, \righthyphenmin
const MIN_WIDTH = 320;                        // narrower than this, ragged right reads better than any justification

type Box = { kind: "text"; text: string; tight?: boolean } | { kind: "math"; html: string; src: string };
type Item = InputItem & { box?: Box; hyphen?: boolean };

// widths: canvas for words (fast), scaled by how this font's canvas width compares with the page's; the DOM for formulas
const ctx = document.createElement("canvas").getContext("2d");
const widths = new Map<string, number>();
const scales = new Map<string, number>();
function textWidth(text: string, font: string, probe: HTMLElement): number {
  if (!ctx) return text.length * 8;
  let scale = scales.get(font);
  if (scale === undefined) {
    const sample = "The quick brown fox jumps over the lazy dog, 0123456789.";
    ctx.font = font;
    const span = document.createElement("span");
    span.textContent = sample;
    span.style.whiteSpace = "pre";
    probe.appendChild(span);
    scale = span.getBoundingClientRect().width / ctx.measureText(sample).width || 1;
    span.remove();
    scales.set(font, scale);
  }
  const key = font + "\u0001" + text;
  let w = widths.get(key);
  if (w === undefined) {
    ctx.font = font;
    w = ctx.measureText(text).width * scale;
    widths.set(key, w);
  }
  return w;
}

function syllables(word: string): { parts: string[]; explicit: boolean } {
  if (word.includes("-") && word.length > 3 && !/^-|-$/.test(word)) return { parts: word.split(/(?<=-)/), explicit: true };
  const m = /^([^\p{L}]*)(\p{Ll}[\p{Ll}'’]*)([^\p{L}]*)$/u.exec(word);
  if (!m || m[2].length < LEFT_MIN + RIGHT_MIN + 1) return { parts: [word], explicit: false };
  const parts = hyphenate(m[2]);
  while (parts.length > 1 && parts[parts.length - 1].length < RIGHT_MIN) parts[parts.length - 2] += parts.pop();
  while (parts.length > 1 && parts[0].length < LEFT_MIN) parts[0] += parts.splice(1, 1)[0];
  parts[0] = m[1] + parts[0];
  parts[parts.length - 1] += m[3];
  return { parts, explicit: false };
}

const esc = (s: string) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
const attr = (s: string) => esc(s).replace(/"/g, "&quot;");

/** A paragraph's runs: text, and inline formulas measured as they render here. */
type Run = Box[];

function items(run: Run, font: string, probe: HTMLElement): Item[] {
  const out: Item[] = [];
  const space = textWidth(" ", font, probe);
  const glue = (): Item => ({ type: "glue", width: space, stretch: space / 2, shrink: space / 3 });
  for (const b of run) {
    if (b.kind === "math") {
      const el = document.createElement("span");
      el.className = "tex";
      el.innerHTML = b.html;
      probe.appendChild(el);
      const w = el.getBoundingClientRect().width;
      el.remove();
      out.push({ type: "box", width: w, box: b });
      continue;
    }
    const tokens = b.text.split(/(\s+)/);
    tokens.forEach((token, t) => {
      if (!token) return;
      if (/^\s+$/.test(token)) {
        // extracted text puts spaces inside citations and before punctuation ("[ 27 , 12 ]"): keep each such space in
        // the text, but hairline-thin and unbreakable, so no line starts with a comma or ends with a bracket
        if (/^[,.;:!?)\]}]/.test(tokens[t + 1] ?? "") || /[([{]$/.test(tokens[t - 1] ?? "")) {
          out.push({ type: "box", width: space * 0.15, box: { kind: "text", text: " ", tight: true } });
        } else if (out.length && out[out.length - 1].type !== "glue") out.push(glue());
        return;
      }
      const { parts, explicit } = syllables(token);
      parts.forEach((part, i) => {
        out.push({ type: "box", width: textWidth(part, font, probe), box: { kind: "text", text: part } });
        if (i < parts.length - 1) {
          out.push(explicit ? { type: "penalty", width: 0, cost: EXPLICIT_COST, flagged: true }
                            : { type: "penalty", width: textWidth("-", font, probe), cost: HYPHEN_COST, flagged: true, hyphen: true });
        }
      });
    });
  }
  while (out.length && out[0].type === "glue") out.shift();
  while (out.length && out[out.length - 1].type === "glue") out.pop();
  out.push({ type: "glue", width: 0, stretch: 1e5, shrink: 0 });   // \parfillskip: the last line runs ragged
  out.push({ type: "penalty", width: 0, cost: -1000, flagged: false });
  return out;
}

const boxHtml = (b: Box) => (b.kind === "math" ? `<span class="tex" data-tex="${attr(b.src)}">${b.html}</span>`
  : b.tight ? '<span class="tsp"> </span>' : esc(b.text));

/** The run as set lines, or null when it can't be set well at this width (the browser's own wrapping is used then). */
function setLines(run: Run, width: number, font: string, probe: HTMLElement): string | null {
  const its = items(run, font, probe);
  if (its.length < 4) return null;
  let breaks: number[];
  try {
    breaks = breakLines(its, width, { maxAdjustmentRatio: null, doubleHyphenPenalty: 300, adjacentLooseTightPenalty: 300 });
  } catch {
    return null;
  }
  const lines: string[] = [];
  for (let b = 1; b < breaks.length; b++) {
    const start = b === 1 ? 0 : breaks[b - 1] + 1, end = breaks[b];
    let html = "", natural = 0, shrink = 0, spaces = 0, started = false;
    for (let i = start; i < end; i++) {
      const it = its[i];
      if (it.type === "glue") {
        if (!started || it.stretch > 1000) continue;
        html += " ";
        natural += it.width;
        shrink += it.shrink;
        spaces++;
      } else if (it.type === "box" && it.box) {
        html += boxHtml(it.box);
        natural += it.width;
        started = true;
      }
    }
    html = html.replace(/ +$/, () => { spaces = Math.max(0, spaces - 1); return ""; });
    const last = b === breaks.length - 1;
    const brk = its[end];
    const hy = brk.type === "penalty" && brk.hyphen;
    const joined = brk.type === "penalty";  // broken inside a word (at a hyphen already there, or one we add)
    if (hy) natural += brk.width;
    // a tight line shrinks its spaces first (a hair more than measured, so measuring differences can't wrap it)
    const squeeze = !last && spaces > 0 && natural > width - 1 ? (Math.max(-shrink, Math.min(0, width - natural)) - 1) / spaces : 0;
    // the closing space keeps words apart when lines are copied; a broken word needs none
    const tail = last ? "" : hy ? '<span class="hy" aria-hidden="true">-</span>' : joined ? "" : " ";
    lines.push(`<span class="tl${last ? " last" : ""}"${squeeze ? ` style="word-spacing:${squeeze.toFixed(3)}px"` : ""}>${html}${tail}</span>`);
  }
  return lines.join("");
}

function plain(text: string): string {
  return esc(text);
}

/** Paragraph text set with Knuth–Plass. `as` is the element; class names style it as before. */
export function Prose({ text, className, as: Tag = "div" }: { text: string | null | undefined; className?: string; as?: "div" | "p" | "span" | "blockquote" }) {
  const ref = useRef<HTMLElement>(null);
  const value = text ?? "";

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.innerHTML = plain(value);
    let cancelled = false, lastWidth = -1, frame = 0;
    const pieces = splitTex(value);
    const needsMath = pieces.some((p) => "tex" in p);

    const typeset = () => {
      if (cancelled || !el.isConnected) return;
      const style = getComputedStyle(el);
      const width = el.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
      if (width <= 0 || Math.abs(width - lastWidth) < 0.5) return;
      lastWidth = width;
      const font = `${style.fontStyle} ${style.fontWeight} ${style.fontSize} ${style.fontFamily}`;
      // blocks: paragraphs of text and inline maths, split at blank lines and display formulas
      const blocks: (Run | { display: string; src: string })[] = [];
      let run: Run = [];
      const flush = () => { if (run.length) blocks.push(run); run = []; };
      for (const p of pieces) {
        if ("text" in p) {
          p.text.split(/\n\s*\n/).forEach((chunk, i) => { if (i) flush(); if (chunk) run.push({ kind: "text", text: chunk }); });
        } else {
          const html = mathHtml(p.tex, p.display);
          if (!html) run.push({ kind: "text", text: p.src });
          else if (p.display) { flush(); blocks.push({ display: html, src: p.src }); }
          else run.push({ kind: "math", html, src: p.src });
        }
      }
      flush();
      const narrow = width < MIN_WIDTH;
      el.innerHTML = blocks.map((b) => {
        if (!Array.isArray(b)) return `<span class="tex tex-display" data-tex="${attr(b.src)}">${b.display}</span>`;
        const set = narrow ? null : setLines(b, width, font, el);
        return `<span class="para${set ? " typeset" : ""}">${set ?? b.map(boxHtml).join("")}</span>`;
      }).join("");
    };

    const schedule = () => { cancelAnimationFrame(frame); frame = requestAnimationFrame(typeset); };
    // set again whenever fonts arrive (the page's, then KaTeX's once its stylesheet is in): widths change with them
    const again = () => { lastWidth = -1; schedule(); };
    const katexFonts = () => Promise.all(['1em KaTeX_Main', 'italic 1em KaTeX_Math', '1em KaTeX_AMS', '1em KaTeX_Size1']
      .map((f) => document.fonts?.load(f).catch(() => null)));
    Promise.all([document.fonts?.ready, needsMath ? loadKatex().then(katexFonts).catch(() => null) : null]).then(again);
    document.fonts?.addEventListener?.("loadingdone", again);
    const ro = new ResizeObserver(schedule);
    ro.observe(el);
    return () => { cancelled = true; cancelAnimationFrame(frame); ro.disconnect(); document.fonts?.removeEventListener?.("loadingdone", again); };
  }, [value]);

  return <Tag ref={ref as never} className={className} />;
}
