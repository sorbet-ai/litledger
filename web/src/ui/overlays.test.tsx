// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MenuItem, OverlayProvider, useOverlays } from "./overlays";

function Opener({ items, onConfirm }: { items: MenuItem[]; onConfirm?: (ok: boolean) => void }) {
  const { menu, confirm } = useOverlays();
  return (
    <>
      <button onClick={(e) => { const r = e.currentTarget.getBoundingClientRect(); menu({ clientX: r.left, clientY: r.bottom }, items); }}>Open menu</button>
      <button onClick={async () => onConfirm?.(await confirm("Delete it?", <a href="#x">details</a>, "Delete", true))}>Open dialog</button>
    </>
  );
}

describe("menus and dialogs from the keyboard", () => {
  afterEach(cleanup);

  it("a menu moves with the arrows, opens submenus, and gives focus back on Escape", async () => {
    const picked = vi.fn();
    const user = userEvent.setup();
    render(<OverlayProvider><Opener items={[
      { label: "First", onClick: () => picked("first") },
      { label: "Off", disabled: true },
      { sep: true },
      { label: "More", items: [{ label: "Deep", onClick: () => picked("deep") }, { label: "Deeper", onClick: () => picked("deeper") }] },
    ]} /></OverlayProvider>);
    const opener = screen.getByRole("button", { name: "Open menu" });
    opener.focus();
    await user.keyboard("{Enter}");
    expect(document.activeElement?.textContent).toBe("First");
    await user.keyboard("{ArrowDown}");  // skips the disabled item and the separator
    expect(document.activeElement?.textContent).toContain("More");
    await user.keyboard("{ArrowDown}");  // wraps around
    expect(document.activeElement?.textContent).toBe("First");
    await user.keyboard("{End}{ArrowRight}");
    expect(document.activeElement?.textContent).toBe("Deep");
    await user.keyboard("{ArrowDown}");
    expect(document.activeElement?.textContent).toBe("Deeper");
    await user.keyboard("{ArrowLeft}");
    expect(document.activeElement?.textContent).toContain("More");
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("menu")).toBeNull();
    expect(document.activeElement).toBe(opener);
    await user.keyboard("{Enter}{End}{ArrowRight}{ArrowDown}{Enter}");
    expect(picked).toHaveBeenCalledWith("deeper");
  });

  it("a dialog is modal: focus moves in, Tab stays inside, Escape closes and focus returns", async () => {
    const answer = vi.fn();
    const user = userEvent.setup();
    render(<OverlayProvider><Opener items={[]} onConfirm={answer} /></OverlayProvider>);
    const opener = screen.getByRole("button", { name: "Open dialog" });
    await user.click(opener);
    const dialog = screen.getByRole("dialog", { name: "Delete it?" });
    expect(dialog.getAttribute("aria-modal")).toBe("true");
    expect(dialog.contains(document.activeElement)).toBe(true);
    const stops = ["details", "Cancel", "Delete"];
    const seen: string[] = [];
    for (let i = 0; i < 5; i++) {
      seen.push(document.activeElement?.textContent ?? "");
      await user.tab();
      expect(dialog.contains(document.activeElement)).toBe(true);
    }
    expect(new Set(seen)).toEqual(new Set(stops));
    await user.tab({ shift: true });
    expect(dialog.contains(document.activeElement)).toBe(true);
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    await vi.waitFor(() => expect(answer).toHaveBeenCalledWith(false));
    expect(document.activeElement).toBe(opener);
  });
});
