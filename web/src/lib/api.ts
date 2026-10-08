// Thin client over litledger's REST API. The browser is signed in with an HttpOnly session cookie, so no secret is
// ever readable by page scripts; writes carry X-Litledger-CSRF. Only the chosen project lives in localStorage.

import { session } from "./storage";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function req<T>(path: string, init: RequestInit = {}, project?: string): Promise<T> {
  const headers: Record<string, string> = {
    "X-Litledger-Project": project ?? session.project,
    "X-Litledger-CSRF": "1",
    ...(init.headers as Record<string, string>),
  };
  if (init.body && !(init.body instanceof FormData)) headers["Content-Type"] = "application/json";
  const r = await fetch(path, { ...init, headers, credentials: "same-origin" });
  if (!r.ok) {
    let msg = r.statusText;
    try {
      const j = await r.json();
      msg = j.detail ?? j.error ?? msg;
    } catch {
      /* not json */
    }
    throw new ApiError(r.status, msg);
  }
  const ct = r.headers.get("content-type") ?? "";
  return (ct.includes("json") ? r.json() : r.text()) as Promise<T>;
}

const qs = (p: Record<string, unknown>) =>
  "?" +
  Object.entries(p)
    .filter(([, v]) => v !== undefined && v !== null && v !== "")
    .map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`)
    .join("&");

type Csl = {
  author?: { family?: string; given?: string; literal?: string }[];
  "container-title"?: string;
  type?: string;
  abstract?: string;
  URL?: string;
  issued?: { "date-parts": number[][] };
  [k: string]: unknown;
};
export type WorkBrief = {
  id: string;
  citekey: string;
  title: string;
  year: number | null;
  type: string;
  csl: Csl;
  ids: Record<string, string>;
  venue?: string;
  in_project?: boolean;
  why?: string | null;
  read?: string | null;
  tags?: string[];
  added_at?: string;
};
export type Note = {
  id: string;
  subject: string;
  kind: string;
  text: string;
  quote: string | null;
  verification: string;
  page: number | null;
  by: string | null;
  created_at: string;
};
export type WorkDetail = WorkBrief & {
  versions: { kind: string; label: string; ids: Record<string, string>; date: string | null }[];
  counts: { notes: number; links: number; cited_by_local: number; projects: number };
  document: { id: string; source: string; passages: number; chars: number } | null;
  notes?: Note[];
  links?: { id: string; source: string; target: string; relation: string; qualifier: string | null; evidence: string | null }[];
  outline?: { section: string; first: number; n: number; chars: number }[];
  projects?: string[];
  cited_by?: { citekey: string; sentence: string }[];
  history?: { ts: string; op: string; agent: string | null; principal_id: string | null; project: string; by?: string | null }[];
  sources?: Record<string, unknown>;
  knowledge?: { id: string; kind: string; title: string; data: Record<string, unknown>; relation: string }[];
  flags?: string[];
};
export type Entity = {
  id: string;
  kind: string;
  title: string;
  body: string;
  scope: "library" | "project";
  data: Record<string, unknown>;
  aliases: string[];
  papers: string[];
  links: number;
  by: string;
  updated_at: string;
};
export type KindInfo = { name: string; scope: string; meaning: string; fields: string[]; group: string };
export type ResultRow = {
  id: string; benchmark: string; metric: string | null; method: string | null; value: unknown; split: string | null;
  setting: string | null; role: string | null; model_size: string | null; higher_is_better: unknown; paper: string | null;
};
export type Tag = { name: string; scope: string; count: number; description: string | null; color: string | null };
export type MapData = {
  id: string;
  title: string;
  updated_at: string;
  version?: number;
  meta: Record<string, unknown>;
  nodes: {
    id: string;
    ref: string | null;
    title: string | null;
    text: string | null;
    kind: string;
    parent: string | null;
    seq: number | null;
    by: string;
    x: number | null;
    y: number | null;
    style: Record<string, unknown>;
    subject?: string;
    verified?: boolean;
    count?: number;
    year?: number;
    data?: Record<string, unknown>;
  }[];
  edges: { id: string; src: string; dst: string; label: string; promoted: boolean; by: string; style: Record<string, unknown> }[];
};
export type JournalEntry = {
  seq: number;
  ts: string;
  op: string;
  project: string;
  agent: string | null;
  principal: string | null;
  by: string | null; // "claude-code via ada" for an agent a person owns
  payload: Record<string, unknown>;
};

export const api = {
  me: () => req<Me & { project: string; warnings: Warning[] }>("/api/v1/me"),
  projects: () => req<{ id: string; title: string; works: number }[]>("/api/v1/projects"),
  works: (p: { q?: string; tags?: string; scope?: string; limit?: number; offset?: number; read?: string; year?: string }) =>
    req<{ items: WorkBrief[]; total: number }>("/api/v1/works" + qs(p)),
  work: (ref: string) => req<WorkDetail>(`/api/v1/works/${encodeURIComponent(ref)}`),
  passages: (ref: string, start: number, end: number) =>
    req<{
      document: { id: string; source: string; passages: number; outline: { section: string; first: number; n: number; chars: number }[] } | null;
      passages: { id: number; seq: number; section: string; page: number | null; text: string; note: string | null }[];
    }>(`/api/v1/works/${encodeURIComponent(ref)}/passages` + qs({ start, end })),
  tags: () => req<Tag[]>("/api/v1/tags"),
  network: (tags?: string) =>
    req<{
      nodes: { id: string; citekey: string; title: string; year: number | null; type: string; tags: string[]; read: string | null }[];
      entities: { id: string; kind: string; title: string }[];
      edges: { source: string; target: string; kind: string }[];
    }>("/api/v1/network" + qs({ tags })),
  refs: (q: string) => req<RefResults>("/api/v1/refs" + qs({ q })),
  entities: (p: { kind?: string; q?: string; work?: string; limit?: number }) =>
    req<{ items: Entity[]; total: number; counts: Record<string, number> }>("/api/v1/entities" + qs(p)),
  results: (p: { benchmark?: string; metric?: string; method?: string }) => req<{ rows: ResultRow[] }>("/api/v1/results" + qs(p)),
  kinds: () => req<{ kinds: KindInfo[]; relations: { name: string; meaning: string }[] }>("/api/v1/kinds"),
  brief: (name: string, work: string, focus = "") => req<string>(`/api/v1/briefs/${name}` + qs({ work, focus })),
  maps: () => req<{ id: string; title: string; updated_at: string; nodes: number; version?: number }[]>("/api/v1/maps"),
  deleteMap: (id: string) => req<{ deleted: boolean }>(`/api/v1/maps/${encodeURIComponent(id)}`, { method: "DELETE" }),
  map: (id: string) => req<MapData>(`/api/v1/maps/${encodeURIComponent(id)}`),
  editMap: (body: { map?: string | null; title?: string; ops: Record<string, unknown>[]; base_version?: number }) =>
    req<{ map: string; added: string[]; changed: number; problems: string[]; results?: { i: number; ok: boolean; error?: string }[];
          version?: number; conflict?: boolean }>("/api/v1/maps", { method: "POST", body: JSON.stringify(body) }),
  searches: () => req<{ at: string; query: string; sources: string[]; new: number; hits: number }[]>("/api/v1/searches"),
  snowball: (state = "pending") =>
    req<{ items: { handle: string; title: string; year: number | null; seeds: string[]; direction: string; score: number }[]; total: number; counts: Record<string, number> }>(
      "/api/v1/snowball" + qs({ state, limit: 100 }),
    ),
  providers: () => req<{ providers: Provider[]; warnings: Warning[] }>("/api/v1/providers"),
  config: () => req<{ fields: ConfigField[]; editable: boolean; warnings: Warning[] }>("/api/v1/config"),
  saveConfig: (values: Record<string, string | null>) =>
    req<{ changed: string[]; fields: ConfigField[]; warnings: Warning[] }>("/api/v1/config", { method: "PUT", body: JSON.stringify({ values }) }),
  testSource: (id: string) => req<{ ok: boolean; message: string }>(`/api/v1/config/test/${id}`, { method: "POST" }),
  setSource: (id: string, body: { action: "add" | "remove"; values?: Record<string, string>; forget_keys?: boolean }) =>
    req<{ changed: string[]; status: string; note: string; warnings: Warning[] }>(`/api/v1/sources/${id}`, { method: "POST", body: JSON.stringify(body) }),
  backups: () => req<{ folder: string; backups: { name: string; size: number; at: string }[] }>("/api/v1/backups"),
  backupNow: () => req<{ name: string; size: number }>("/api/v1/backups", { method: "POST" }),
  oauthRequest: (id: string) => req<OAuthRequest>(`/api/v1/oauth/requests/${encodeURIComponent(id)}`),
  oauthDecide: (id: string, allow: boolean, projects?: string[]) =>
    req<{ redirect: string }>(`/api/v1/oauth/requests/${encodeURIComponent(id)}`, { method: "POST", body: JSON.stringify({ allow, projects }) }),
  // signing in (no session needed)
  authMethods: () => req<AuthMethods>("/auth/methods"),
  signUp: (name: string, email: string, password: string) =>
    req<{ name?: string; confirm?: string }>("/auth/signup", { method: "POST", body: JSON.stringify({ name, email, password }) }),
  tokenSignIn: (token: string) => req<{ name: string }>("/auth/token", { method: "POST", body: JSON.stringify({ token }) }),
  signIn: (email: string, password: string) => req<{ name: string }>("/auth/password", { method: "POST", body: JSON.stringify({ email, password }) }),
  forgot: (email: string) => req<{ sent: boolean; command?: string }>("/auth/forgot", { method: "POST", body: JSON.stringify({ email }) }),
  resetInfo: (t: string) => req<{ name: string }>("/auth/reset" + qs({ t })),
  reset: (token: string, password: string) =>
    req<{ name: string; notice?: string }>("/auth/reset", { method: "POST", body: JSON.stringify({ token, password }) }),
  inviteInfo: (t: string) => req<{ email: string; role: string; invited_by: string; expires_at: string }>("/auth/invite" + qs({ t })),
  acceptInvite: (token: string, name: string, password: string) =>
    req<{ name: string }>("/auth/invite", { method: "POST", body: JSON.stringify({ token, name, password }) }),
  logout: () => req<{ ok: boolean }>("/auth/logout", { method: "POST" }),
  // your account
  account: () => req<Account>("/api/v1/me/account"),
  updateAccount: (body: { name?: string; email?: string }) =>
    req<{ name?: string; email?: string; confirm?: string }>("/api/v1/me/account", { method: "PATCH", body: JSON.stringify(body) }),
  changePassword: (current: string, next: string) =>
    req<{ ok: boolean }>("/api/v1/me/password", { method: "PUT", body: JSON.stringify({ current, new: next }) }),
  mySessions: () => req<Session[]>("/api/v1/me/sessions"),
  endSession: (id: string) => req<{ ok: boolean }>(`/api/v1/me/sessions/${encodeURIComponent(id)}`, { method: "DELETE" }),
  endOtherSessions: () => req<{ ended: number }>("/api/v1/me/sessions", { method: "DELETE" }),
  unlinkIdentity: (provider: string) => req<{ removed: number }>(`/api/v1/me/identities/${encodeURIComponent(provider)}`, { method: "DELETE" }),
  myAgents: () => req<Agent[]>("/api/v1/me/agents"),
  createMyToken: (body: TokenRequest) => req<{ name: string; token: string }>("/api/v1/me/tokens", { method: "POST", body: JSON.stringify(body) }),
  logins: () => req<PendingLogin[]>("/api/v1/logins"),
  approveLogin: (code: string) => req<{ name: string }>(`/api/v1/logins/${encodeURIComponent(code)}`, { method: "POST", body: "{}" }),
  denyLogin: (code: string) => req<{ denied: boolean }>(`/api/v1/logins/${encodeURIComponent(code)}`, { method: "DELETE" }),
  // tokens and apps (their owner or an admin), people (admins)
  updatePrincipal: (name: string, body: { role?: string; disabled?: boolean; email?: string; projects?: string[] | null }) =>
    req<{ ok: boolean }>(`/api/v1/principals/${encodeURIComponent(name)}`, { method: "PATCH", body: JSON.stringify(body) }),
  removePrincipal: (name: string) => req<{ removed: string }>(`/api/v1/principals/${encodeURIComponent(name)}`, { method: "DELETE" }),
  principals: () => req<{ people: Person[]; agents: Agent[] }>("/api/v1/principals"),
  createSharedToken: (body: TokenRequest) => req<{ name: string; token: string }>("/api/v1/principals", { method: "POST", body: JSON.stringify(body) }),
  resetLink: (name: string) => req<{ link: string; minutes: number }>(`/api/v1/principals/${encodeURIComponent(name)}/reset-link`, { method: "POST" }),
  signOutPerson: (name: string) => req<{ ended: number }>(`/api/v1/principals/${encodeURIComponent(name)}/sign-out`, { method: "POST" }),
  invites: () => req<Invite[]>("/api/v1/invites"),
  invite: (email: string, role: string, projects?: string[]) =>
    req<{ link: string; sent: boolean; days: number }>("/api/v1/invites", { method: "POST", body: JSON.stringify({ email, role, projects }) }),
  revokeInvite: (id: string) => req<{ ok: boolean }>(`/api/v1/invites/${encodeURIComponent(id)}`, { method: "DELETE" }),
  audit: (p: { before?: number; person?: string; kind?: string; problems?: boolean }) =>
    req<AuditEntry[]>("/api/v1/audit" + qs({ ...p, problems: p.problems ? "true" : undefined, limit: 100 })),
  mailTest: () => req<{ ok: boolean; message: string }>("/api/v1/mail/test", { method: "POST" }),
  jobs: () => req<{ id: string; kind: string; state: string; error: string | null; created_at: string }[]>("/api/v1/jobs"),
  journal: (since = 0, limit = 100) => req<JournalEntry[]>("/api/v1/journal" + qs({ since, limit })),
  tool: (name: string, args: Record<string, unknown>) =>
    req<string>(`/api/v1/tools/${name}`, { method: "POST", body: JSON.stringify(args), headers: { Accept: "text/plain" } }),
  upload: (file: File, form: Record<string, string>) => {
    const fd = new FormData();
    fd.append("file", file);
    Object.entries(form).forEach(([k, v]) => fd.append(k, v));
    return req<Record<string, unknown>>("/api/v1/uploads", { method: "POST", body: fd });
  },
};

export type RefResults = {
  works: { ref: string; title: string; year: number | null; in_project: boolean }[];
  entities: { ref: string; kind: string; title: string }[];
  notes: { ref: string; text: string; kind: string; subject: string }[];
  tags: { ref: string; title: string; count: number }[];
  maps: { ref: string; title: string }[];
  capture: { handle: string; input: string } | null;
};
export type Me = { name: string; kind: string; role: "admin" | "member"; admin: boolean };
export type AuthMethods = { signup: "invited" | "domains" | "anyone"; password_signup: boolean; domains: string[]; github: boolean; google: boolean;
  email: boolean };
export type Identity = { provider: string; email: string | null; login: string | null };
export type Account = { name: string; email: string | null; role: string; projects: string[] | null; created_at: string; has_password: boolean;
  email_verified: boolean; root: boolean; identities: Identity[]; email_change: "direct" | "confirm" | "admin" | "none" };
export type Session = { id: string; created_at: string; used_at: string | null; expires_at: string; current: boolean; how?: string; agent?: string; ip?: string };
export type Person = { name: string; email: string | null; role: string; disabled: boolean; root: boolean; email_verified: boolean;
  has_password: boolean; identities: Identity[];
  agents: number; projects: string[] | null; created_at: string; last_seen_at: string | null };
export type Agent = { name: string; by: string; label: string; owner: string | null; origin: "token" | "login" | "oauth" | "bootstrap" | null;
  client_name: string | null; projects: string[] | null; access: "library" | "full"; created_at: string; last_seen_at: string | null;
  expires_at: string | null; expired: boolean };
export type TokenRequest = { name: string; expires: string; projects?: string[]; access?: "library" | "full" };
export type Invite = { id: string; email: string; role: string; projects: string[] | null; invited_by: string; created_at: string; expires_at: string };
export type AuditEntry = { seq: number; ts: string; op: string; target: string | null; ip: string | null; ua: string | null; actor: string | null;
  subject: string | null; count: number; last_at: string | null; level: "info" | "failure" | "security"; detail: Record<string, unknown> };
export type OAuthRequest = { client_name: string; client_id: string; verified: boolean; redirect_host: string; scope: string;
  client_uri: string | null; person: string; loopback: boolean };
export type PendingLogin = { code: string; name: string; ip: string; client: string; age: number };
export type ConfigField = {
  key: string;
  label: string;
  secret: boolean;
  provider: string | null;
  recommendation?: string;
  help: string;
  default?: string;
  choices?: string[];
  multi?: boolean;
  group?: string;
  type?: string;
  set: boolean;
  value: string;
};
export type Warning = { provider: string; headline: string; impact: string; steps: string[]; short: string };
export type Provider = {
  id: string;
  name: string;
  status: string;
  note: string;
  capabilities: string[];
  discover_default: boolean;
  about: string;
  default: string;
  auth: string;
  recommendation: string;
  impact: string;
  keys: string[];
  optional_keys: string[];
  key_url: string | null;
  keyless_ok: boolean;
  stats: { ok: number; errors: number; last_error: string | null; last_error_at: string | null; last_ok_at: string | null } | null;
};

/** The live journal of the current project. The session cookie authenticates it. EventSource's own reconnects resume
 * with the Last-Event-ID header; when the browser gives up (the server answered an error), it is reopened after a pause
 * with ?last_id=, so the events in between are replayed either way. */
export function eventStream(onEvent: (e: JournalEntry) => void, retryMs = 5000): () => void {
  let es: EventSource | null = null;
  let last = 0;
  let stopped = false;
  let timer: number | undefined;
  const open = () => {
    es = new EventSource(`/events?project=${encodeURIComponent(session.project)}` + (last ? `&last_id=${last}` : ""));
    es.onmessage = (m) => {
      const id = Number(m.lastEventId);
      if (id > last) last = id;
      try {
        onEvent(JSON.parse(m.data));
      } catch {
        /* ignore */
      }
    };
    es.onerror = () => {
      if (!stopped && es?.readyState === EventSource.CLOSED) timer = window.setTimeout(open, retryMs);
    };
  };
  open();
  return () => { stopped = true; window.clearTimeout(timer); es?.close(); };
}
