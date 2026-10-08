// @vitest-environment jsdom
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Person } from "../../lib/api";
import { InApp } from "../../test/app";
import { mockFetch } from "../../test/fetchMock";
import { PeopleTab } from "./People";

const person = (name: string, extra: Partial<Person> = {}): Person => ({
  name, email: `${name}@example.com`, role: "member", disabled: false, root: false, email_verified: true, has_password: true,
  identities: [], agents: 0, projects: null, created_at: "2026-01-01T00:00:00+00:00", last_seen_at: null, ...extra,
});
const PEOPLE = [person("admin", { root: true, role: "admin", email: null, has_password: false }), person("ada", { role: "admin" }), person("bo")];

function setup() {
  const calls = mockFetch({
    "GET /api/v1/principals": () => ({ people: PEOPLE, agents: [] }),
    "GET /api/v1/invites": () => [],
    "PATCH /api/v1/principals/bo": () => ({ ok: true }),
    "DELETE /api/v1/principals/bo": () => ({ removed: "bo" }),
  });
  render(<InApp><PeopleTab /></InApp>);
  return { calls, user: userEvent.setup() };
}

const openMenu = async (user: ReturnType<typeof userEvent.setup>, name: string) => {
  await user.click(await screen.findByRole("button", { name: `Actions for ${name}` }));
  return screen.getByRole("menu");
};

describe("Admin → People", () => {
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

  it("makes a member an admin", async () => {
    const { calls, user } = setup();
    await user.click(within(await openMenu(user, "bo")).getByRole("menuitem", { name: "Make admin" }));
    await vi.waitFor(() => expect(calls.some((c) => c.method === "PATCH")).toBe(true));
    const patch = calls.find((c) => c.method === "PATCH")!;
    expect([patch.path, patch.body]).toEqual(["/api/v1/principals/bo", { role: "admin" }]);
  });

  it("removes someone only after confirming", async () => {
    const { calls, user } = setup();
    await user.click(within(await openMenu(user, "bo")).getByRole("menuitem", { name: "Remove…" }));
    const dialog = await screen.findByRole("dialog", { name: "Remove bo?" });
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);
    await user.click(within(dialog).getByRole("button", { name: "Remove" }));
    await vi.waitFor(() => expect(calls.find((c) => c.method === "DELETE")?.path).toBe("/api/v1/principals/bo"));
  });

  it("sends only what an edit changed (never an untouched email)", async () => {
    const { calls, user } = setup();
    await user.click(within(await openMenu(user, "bo")).getByRole("menuitem", { name: "Edit email and projects…" }));
    const dialog = await screen.findByRole("dialog", { name: "Edit bo" });
    await user.type(within(dialog).getByPlaceholderText("all"), "thesis");
    await user.click(within(dialog).getByRole("button", { name: "Save" }));
    await vi.waitFor(() => expect(calls.some((c) => c.method === "PATCH")).toBe(true));
    expect(calls.find((c) => c.method === "PATCH")!.body).toEqual({ projects: ["thesis"] });
  });

  it("works from the keyboard: the menu takes focus, arrows move, Enter picks", async () => {
    const { calls, user } = setup();
    const more = await screen.findByRole("button", { name: "Actions for bo" });
    more.focus();
    await user.keyboard("{Enter}");
    const menu = screen.getByRole("menu");
    expect(document.activeElement?.textContent).toBe("Make admin");
    await user.keyboard("{ArrowDown}");
    expect(document.activeElement?.textContent).toBe("Edit email and projects…");
    await user.keyboard("{ArrowUp}{Enter}");
    expect(menu.isConnected).toBe(false);
    await vi.waitFor(() => expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ role: "admin" }));
  });
});
