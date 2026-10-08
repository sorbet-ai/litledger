// What this browser remembers: the chosen project, the theme and a few view settings. Storage can be blocked
// (private mode, strict settings), so every access falls back quietly.

function read(key: string, fallback = ""): string {
  try {
    return localStorage.getItem(key) ?? fallback;
  } catch {
    return fallback;
  }
}
function write(key: string, value: string) {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* private mode: session only */
  }
}

/** JSON values (graph layout, hidden kinds, node positions). */
export const store = {
  get<T>(key: string, fallback: T): T { try { const v = localStorage.getItem(key); return v ? (JSON.parse(v) as T) : fallback; } catch { return fallback; } },
  set(key: string, value: unknown) { write(key, JSON.stringify(value)); },
};

export const session = {
  get project() {
    return read("litledger.project", "default");
  },
  set project(v: string) {
    write("litledger.project", v);
  },
};

const THEME_KEY = "litledger.theme";
export const savedTheme = () => read(THEME_KEY, "system");
export const saveTheme = (t: string) => write(THEME_KEY, t);
export function applyTheme(t: string) {
  const dark = t === "dark" || (t === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.dataset.theme = dark ? "dark" : "light";
}
