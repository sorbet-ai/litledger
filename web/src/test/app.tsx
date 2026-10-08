// The app shell around a page under test: who is signed in, the project, and the overlays (dialogs, menus, toasts).
import { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import { AppCtx, Ctx } from "../app/context";
import { OverlayProvider } from "../ui/overlays";

export function appCtx(over: Partial<Ctx> = {}): Ctx {
  return {
    me: { name: "ada", kind: "human", role: "admin", admin: true, project: "default" }, project: "default", setProject: vi.fn(),
    toast: vi.fn(), tick: 0, lastEvent: null, subscribe: () => () => undefined, refresh: vi.fn(), theme: "light", setTheme: vi.fn(),
    ...over,
  };
}

export function InApp({ children, ctx = appCtx() }: { children: ReactNode; ctx?: Ctx }) {
  return (
    <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <OverlayProvider>
        <AppCtx.Provider value={ctx}>{children}</AppCtx.Provider>
      </OverlayProvider>
    </MemoryRouter>
  );
}
