// Small display helpers shared by the pages.
import type { WorkBrief } from "./api";

type Csl = WorkBrief["csl"];

export function authorsShort(csl: Csl, max = 3): string {
  const a = csl.author ?? [];
  const names = a.map((x) => x.family ?? x.literal ?? "").filter(Boolean);
  if (names.length <= max) return names.join(", ");
  return `${names.slice(0, max).join(", ")} +${names.length - max}`;
}
export function authorsFull(csl: Csl): string {
  return (csl.author ?? []).map((x) => (x.family ? `${x.given ?? ""} ${x.family}`.trim() : x.literal ?? "")).join(", ");
}

/** Only http(s) URLs become links: a stored `javascript:` or `data:` URL must never be clickable. */
function safeUrl(value: string | null | undefined): string | null {
  if (!value) return null;
  try {
    const u = new URL(value);
    return u.protocol === "http:" || u.protocol === "https:" ? u.href : null;
  } catch {
    return null;
  }
}

export function idLink(scheme: string, value: string): string | null {
  switch (scheme) {
    case "arxiv":
      return `https://arxiv.org/abs/${value}`;
    case "doi":
      return `https://doi.org/${value}`;
    case "dblp":
      return `https://dblp.org/rec/${value}`;
    case "openreview":
      return `https://openreview.net/forum?id=${value}`;
    case "acl":
      return `https://aclanthology.org/${value}`;
    case "pmid":
      return `https://pubmed.ncbi.nlm.nih.gov/${value}`;
    case "openalex":
      return `https://openalex.org/${value}`;
    case "s2":
      return `https://www.semanticscholar.org/paper/${value}`;
    case "url":
      return safeUrl(value);
    default:
      return null;
  }
}

export const ago = (iso: string | null | undefined) => {
  if (!iso) return "never";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 90) return "just now";
  if (s < 5400) return `${Math.round(s / 60)} min ago`;
  if (s < 129600) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} days ago`;
};

/** A CSS custom property's current value (canvas libraries need real colours, not var()). */
export const cssVar = (name: string) => getComputedStyle(document.documentElement).getPropertyValue(name).trim() || "#888";

/** "method" → "Methods", "work" → "Papers". */
export const plural = (k: string) =>
  k === "work" ? "Papers" : k === "hypothesis" ? "Hypotheses" : k[0].toUpperCase() + k.slice(1) + (k.endsWith("s") ? "es" : "s");
