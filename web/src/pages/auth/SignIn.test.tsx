// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { mockFetch, reply } from "../../test/fetchMock";
import SignIn from "./SignIn";

const METHODS = { signup: "anyone", password_signup: true, domains: [], github: false, google: false, email: false };

async function signIn(onDone = vi.fn()) {
  const user = userEvent.setup();
  render(<SignIn next="/" onDone={onDone} />);
  await user.type(await screen.findByLabelText("Email"), "ada@example.com");
  await user.type(screen.getByLabelText("Password"), "not the password");
  await user.click(screen.getByRole("button", { name: "Sign in" }));
  return onDone;
}

describe("sign-in errors", () => {
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); window.history.replaceState(null, "", "/"); });

  it("says a wrong password plainly and stays on the page", async () => {
    const calls = mockFetch({ "GET /auth/methods": () => METHODS,
                              "POST /auth/password": () => reply(401, { detail: "Wrong email or password." }) });
    const onDone = await signIn();
    expect((await screen.findByRole("alert")).textContent).toBe("Wrong email or password.");
    expect(onDone).not.toHaveBeenCalled();
    expect(calls.find((c) => c.path === "/auth/password")?.body).toEqual({ email: "ada@example.com", password: "not the password" });
    expect(screen.getByRole("button", { name: "Sign in" })).toHaveProperty("disabled", false);  // can try again
  });

  it("says when sign-in is rate limited", async () => {
    mockFetch({ "GET /auth/methods": () => METHODS,
                "POST /auth/password": () => reply(429, { detail: "Too many attempts. Wait a few minutes." }) });
    await signIn();
    expect((await screen.findByRole("alert")).textContent).toBe("Too many attempts. Wait a few minutes.");
  });

  it("explains a refused Google/GitHub round trip that came back limited", async () => {
    window.history.replaceState(null, "", "/?error=limited&provider=github");
    mockFetch({ "GET /auth/methods": () => METHODS });
    render(<SignIn next="/" onDone={vi.fn()} />);
    expect((await screen.findByRole("alert")).textContent).toBe("Too many attempts. Wait a few minutes.");
  });

  it("signs in when the password is right", async () => {
    mockFetch({ "GET /auth/methods": () => METHODS, "POST /auth/password": () => ({ name: "ada" }) });
    const onDone = await signIn();
    await vi.waitFor(() => expect(onDone).toHaveBeenCalledOnce());
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
