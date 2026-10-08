// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { mockFetch } from "../test/fetchMock";
import Authorize from "./Authorize";

const REQUEST = { client_name: "Claude Code", client_id: "https://claude.ai/oauth/claude-code-client-metadata", verified: true,
  redirect_host: "localhost:51234", scope: "litledger", client_uri: null, person: "ada", loopback: true };
const PROJECTS = [{ id: "thesis", works: 12 }, { id: "secret", works: 3 }, { id: "default", works: 0 }];

describe("the consent page", () => {
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

  it("allows an app for only some projects", async () => {
    const calls = mockFetch({ "GET /api/v1/oauth/requests/r1": () => REQUEST,
                              "POST /api/v1/oauth/requests/r1": () => ({ redirect: "#done" }) });
    const user = userEvent.setup();
    render(<MemoryRouter initialEntries={["/authorize?request=r1"]} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}><Authorize projects={PROJECTS} /></MemoryRouter>);
    expect(await screen.findByRole("heading", { name: "Connect Claude Code?" })).toBeTruthy();
    await user.click(screen.getByLabelText("Only some projects"));
    const allow = screen.getByRole("button", { name: "Allow" });
    expect(allow).toHaveProperty("disabled", true);  // pick at least one first
    await user.click(screen.getByText("thesis"));
    expect(allow).toHaveProperty("disabled", false);
    await user.click(allow);
    expect(await screen.findByRole("heading", { name: "Claude Code is connected" })).toBeTruthy();
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({ allow: true, projects: ["thesis"] });
  });

  it("allows all projects by default", async () => {
    const calls = mockFetch({ "GET /api/v1/oauth/requests/r1": () => REQUEST,
                              "POST /api/v1/oauth/requests/r1": () => ({ redirect: "#done" }) });
    const user = userEvent.setup();
    render(<MemoryRouter initialEntries={["/authorize?request=r1"]} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}><Authorize projects={PROJECTS} /></MemoryRouter>);
    await user.click(await screen.findByRole("button", { name: "Allow" }));
    await screen.findByRole("heading", { name: "Claude Code is connected" });
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({ allow: true });
  });
});
