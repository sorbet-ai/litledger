// A fetch stand-in for component tests: routes "METHOD /path" to a handler, and records every call.
import { vi } from "vitest";

export type Call = { method: string; path: string; query: string; body: unknown };
type Reply = { status: number; data: unknown };
type Handler = (body: unknown, call: Call) => unknown;

/** A non-200 answer from a route handler, e.g. reply(401, { detail: "Wrong email or password." }). */
export const reply = (status: number, data: unknown): Reply => ({ status, data });
const isReply = (x: unknown): x is Reply => !!x && typeof x === "object" && "status" in x && "data" in x;

export function mockFetch(routes: Record<string, Handler>): Call[] {
  const calls: Call[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const [path, query = ""] = String(input).split("?");
    const method = (init.method ?? "GET").toUpperCase();
    const body = typeof init.body === "string" && init.body ? JSON.parse(init.body) : init.body ?? null;
    const call = { method, path, query, body };
    calls.push(call);
    const handler = routes[`${method} ${path}`];
    const out = handler ? await handler(body, call) : reply(404, { detail: `not mocked: ${method} ${path}` });
    const { status, data } = isReply(out) ? out : { status: 200, data: out };
    return new Response(JSON.stringify(data), { status, headers: { "content-type": "application/json" } });
  }));
  return calls;
}
