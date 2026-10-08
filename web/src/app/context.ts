// What every page gets from the app shell: who is signed in, the project, live events, theme and toasts.
import { createContext, useContext } from "react";
import type { JournalEntry, Me } from "../lib/api";

export type Ctx = {
  me: Me & { project: string };
  project: string;
  setProject: (p: string) => void;
  toast: (msg: string, error?: boolean) => void;
  tick: number; // bumps on every live journal event for the current project
  lastEvent: JournalEntry | null;
  /** Every live event, one call each (lastEvent alone can skip events that arrive in the same render). */
  subscribe: (fn: (e: JournalEntry) => void) => () => void;
  refresh: () => void;
  theme: string;
  setTheme: (t: string) => void;
};
export const AppCtx = createContext<Ctx>(null as unknown as Ctx);
export const useApp = () => useContext(AppCtx);
