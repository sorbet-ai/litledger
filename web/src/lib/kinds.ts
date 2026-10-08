// Kinds of knowledge items, papers' reading levels and note kinds, in the order the UI lists them.

/** Kinds agents extract from papers (shared across projects). */
export const LIT_KINDS = ["concept", "method", "model", "task", "dataset", "benchmark", "metric", "claim", "limitation"];
/** The project's own thinking. */
export const THINK_KINDS = ["topic", "question", "idea", "hypothesis", "gap", "experiment", "finding"];
export const THINKING = new Set(THINK_KINDS);
/** Graph columns, outwards from the papers in the middle. */
export const COLUMN_ORDER = ["concept", "task", "method", "model", "dataset", "benchmark", "metric", "result", "claim", "limitation",
                             "topic", "question", "gap", "idea", "hypothesis", "experiment", "finding"];
export const NOTE_KINDS = ["note", "quote", "summary", "inference", "result", "critique", "question", "todo"];
export const READ_LEVELS = ["metadata", "abstract", "skimmed", "sections", "full"];

export const kindColor = (kind: string) => (THINKING.has(kind) ? "var(--marker)" : `var(--k-${kind}, var(--muted))`);
export const kindStyle = (kind: string) => ({ ["--kc" as string]: kindColor(kind) });
