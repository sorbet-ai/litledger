"""OAuth 2.1 authorization server for MCP clients (Claude Code, Codex, …), per the MCP authorization spec.

A client adds `<server>/mcp` with no token. On its first request it gets 401 with a pointer to the protected-resource
metadata, discovers this authorization server, registers (a Client ID Metadata Document such as
https://claude.ai/oauth/claude-code-client-metadata, or dynamic registration), and opens the browser on
/oauth/authorize. A signed-in person approves on the consent page (/authorize in the web app), the client swaps the
code (PKCE S256) for a one-hour access token bound to the /mcp resource plus a rotating refresh token, and every write
it makes is attributed to an agent principal like "claude-code-<person>".

The audit log records each step that matters, inside its transaction: a client registering itself, a person allowing
(app.connect) or declining an app, an app revoking its own tokens, and a used refresh token coming back (it was copied:
the whole grant is revoked). Routine hourly rotation of a live grant isn't recorded; the grant's start and end are."""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from datetime import timedelta
from urllib.parse import urlencode, urlsplit, urlunsplit

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

from . import accounts, auth
from .auth import Denied, Limiter, TTLStore
from .db import SYSTEM, Ctx, now
from .ledger import Ledger
from .pages import page
from .server.rest import ctx_of, read_body, require_person, run

SCOPE = "litledger"
LOOPBACK = {"localhost", "127.0.0.1", "::1"}
CIMD_TTL = 24 * 3600
REQUEST_TTL = 600
CODE_TTL = 300
MAX_CLIENTS = 500  # registered (DCR) clients never used for a token; the oldest beyond this are forgotten
MAX_WAITING = 1000  # consent requests and unexchanged codes held in memory (each store)
REGISTER_LIMIT = (20, 3600)  # dynamic registrations per address
AUTHORIZE_LIMIT = (30, 600)  # authorization requests per address


def base_url(lg: Ledger, request: Request) -> str:
    """The server's public origin: the PUBLIC_URL setting, else the URL this request came in on."""
    configured = (lg.settings.get("PUBLIC_URL") or "").strip().rstrip("/")
    if configured:
        return configured
    return f"{request.url.scheme}://{request.url.netloc}"


def resource_url(lg: Ledger, request: Request) -> str:
    return base_url(lg, request) + "/mcp"


def metadata_url(lg: Ledger, request: Request) -> str:
    return base_url(lg, request) + "/.well-known/oauth-protected-resource/mcp"


def challenge(lg: Ledger, request: Request) -> dict[str, str]:
    return {"WWW-Authenticate": f'Bearer resource_metadata="{metadata_url(lg, request)}", scope="{SCOPE}"'}


def _redirect_ok(registered: list[str], given: str) -> bool:
    """Exact match, except that loopback redirects may use any port (RFC 8252 §7.3)."""
    if given in registered:
        return True
    g = urlsplit(given)
    if g.scheme != "http" or (g.hostname or "") not in LOOPBACK:
        return False
    for r in registered:
        u = urlsplit(r)
        if u.scheme == "http" and (u.hostname or "") in LOOPBACK and (u.path or "/") == (g.path or "/"):
            return True
    return False


def _valid_redirect(uri: str) -> bool:
    u = urlsplit(uri)
    if u.fragment:
        return False
    if u.scheme == "https" and u.hostname:
        return True
    if u.scheme == "http" and (u.hostname or "") in LOOPBACK:
        return True
    # Native apps may use private-use schemes (com.example.app:/cb); never script-ish ones.
    return bool(u.scheme) and u.scheme not in ("http", "https", "javascript", "data", "file", "vbscript") and "." in u.scheme


class Clients:
    """Registered (DCR) clients live in oauth_clients; metadata-document clients are fetched and cached there."""

    def __init__(self, lg: Ledger):
        self.lg = lg

    def get(self, client_id: str) -> dict | None:
        with self.lg.db.read() as conn:
            row = conn.execute("SELECT * FROM oauth_clients WHERE client_id=?", (client_id,)).fetchone()
        meta = json.loads(row["meta"]) if row else {}
        if client_id.startswith("https://"):
            fresh = row and time.time() - meta.get("fetched", 0) < CIMD_TTL
            if not fresh:
                doc = self._fetch_document(client_id)
                if doc is None:
                    return self._row(row) if row else None
                self._save(client_id, doc.get("client_name") or urlsplit(client_id).hostname or "app",
                           doc["redirect_uris"], {"cimd": True, "fetched": time.time(), "client_uri": doc.get("client_uri")})
                with self.lg.db.read() as conn:
                    row = conn.execute("SELECT * FROM oauth_clients WHERE client_id=?", (client_id,)).fetchone()
        return self._row(row) if row else None

    @staticmethod
    def _row(row) -> dict:
        return {"client_id": row["client_id"], "name": row["name"], "redirect_uris": json.loads(row["redirect_uris"]),
                "meta": json.loads(row["meta"] or "{}")}

    def _fetch_document(self, client_id: str) -> dict | None:
        host = urlsplit(client_id).hostname or ""
        allowed = [h.strip().lower() for h in self.lg.settings.get("OAUTH_CLIENT_HOSTS").split(",") if h.strip()]
        if "*" not in allowed and host.lower() not in allowed:
            raise HTTPException(400, f"client metadata host {host} is not allowed (Admin → Sign-in: Trusted app hosts)")
        if not urlsplit(client_id).path.strip("/"):
            return None
        try:
            from .providers.netguard import BlockedFetch, guarded_transport
            guard = self.lg.http.guard
            guard.check(client_id)  # never fetch an internal address, even for an allowed host name
            with httpx.Client(timeout=8, follow_redirects=False, transport=guarded_transport(guard)) as c:
                r = c.get(client_id, headers={"Accept": "application/json"})
            if r.status_code != 200 or len(r.content) > 64_000:
                return None
            doc = r.json()
        except (httpx.HTTPError, ValueError, BlockedFetch):
            return None
        if not isinstance(doc, dict) or doc.get("client_id") != client_id or not isinstance(doc.get("redirect_uris"), list):
            return None
        doc["redirect_uris"] = [u for u in doc["redirect_uris"] if isinstance(u, str) and _valid_redirect(u)]
        return doc if doc["redirect_uris"] else None

    def register(self, body: dict, ctx: Ctx) -> dict:
        """Dynamic client registration (recorded: anyone can register, so the log shows who did, from where)."""
        uris = body.get("redirect_uris")
        if not isinstance(uris, list) or not uris or not all(isinstance(u, str) and _valid_redirect(u) for u in uris):
            raise ValueError("redirect_uris must be https, loopback http, or a private-use scheme")
        method = body.get("token_endpoint_auth_method") or "none"
        if method != "none":
            raise ValueError("only public clients (token_endpoint_auth_method=none) are supported; use PKCE")
        name = str(body.get("client_name") or "MCP client")[:80]
        client_id = "llc_" + secrets.token_urlsafe(16)
        with self.lg.db.tx(SYSTEM, ctx) as tx:
            pruned = 0
            n = tx.execute("SELECT count(*) FROM oauth_clients WHERE last_used_at IS NULL").fetchone()[0]
            if n >= MAX_CLIENTS:  # forget the oldest never-used registrations (no app ever got a token with them)
                pruned = tx.execute(
                    "DELETE FROM oauth_clients WHERE client_id IN (SELECT client_id FROM oauth_clients WHERE last_used_at IS NULL "
                    "AND client_id NOT IN (SELECT client_id FROM principals WHERE client_id IS NOT NULL) "
                    "ORDER BY created_at LIMIT ?)", (n - MAX_CLIENTS + MAX_CLIENTS // 5,)).rowcount
            _put_client(tx, client_id, name, uris, {"dcr": True, "client_uri": body.get("client_uri")})
            # Anyone may register, so a burst from one address is one row with a count (×N), not N rows.
            tx.audit("app.register", name, fold=accounts.FOLD, fold_any_target=True, client_id=client_id,
                     redirects=sorted({urlsplit(u).netloc or u for u in uris}), pruned=pruned or None)
        return {"client_id": client_id, "client_id_issued_at": int(time.time()), "client_name": name, "redirect_uris": uris,
                "token_endpoint_auth_method": "none", "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"], "scope": SCOPE}

    def _save(self, client_id: str, name: str, uris: list[str], meta: dict) -> None:
        with self.lg.db.write("client metadata document cache") as conn:
            _put_client(conn, client_id, name, uris, meta)

    def touch(self, client_id: str) -> None:
        with self.lg.db.write("app last used") as conn:
            conn.execute("UPDATE oauth_clients SET last_used_at=? WHERE client_id=?", (now(), client_id))


def _put_client(conn, client_id: str, name: str, uris: list[str], meta: dict) -> None:
    conn.execute("INSERT INTO oauth_clients(client_id,name,redirect_uris,meta,created_at) VALUES(?,?,?,?,?) "
                 "ON CONFLICT(client_id) DO UPDATE SET name=excluded.name, redirect_uris=excluded.redirect_uris, meta=excluded.meta",
                 (client_id, name, json.dumps(uris), json.dumps(meta), now()))


def _pair(tx, pid: str, client_id: str, scope: str, resource: str, family: str | None = None) -> dict:
    family = family or "f" + secrets.token_hex(8)
    access = auth.issue(tx, pid, "access", timedelta(seconds=auth.ACCESS_SECONDS), client_id, family, resource)
    refresh = auth.issue(tx, pid, "refresh", timedelta(days=auth.REFRESH_DAYS), client_id, family, resource)
    return {"access_token": access, "token_type": "Bearer", "expires_in": auth.ACCESS_SECONDS, "refresh_token": refresh,
            "scope": scope}


def grant_tokens(db, pid: str, client_id: str, scope: str, resource: str) -> dict:
    """A consented code for the grant's first token pair (the consent recorded app.connect)."""
    with db.tx(SYSTEM, internal="the first tokens of a grant whose consent is audited (app.connect)") as tx:
        return _pair(tx, pid, client_id, scope, resource)


def refresh_tokens(db, raw: str, client_id: str, ctx: Ctx, resource: str | None = None) -> dict:
    """Rotate a refresh token: the old one dies, a new pair is issued. A used one coming back while its grant is still
    live means it was copied (or a client misbehaved): the whole family is revoked and recorded as app.token_reuse. A
    token of a grant that already ended (disconnected, revoked by the app) is just refused. Returns tokens, or
    {"error": …} for the client. The new pair is bound to `resource` (the server's current /mcp URL), so grants follow a
    change of PUBLIC_URL instead of breaking."""
    with db.tx(SYSTEM, ctx, internal="routine rotation of an audited grant") as tx:
        row = auth.find(tx, raw, "refresh")
        if not row or row["client_id"] != client_id:
            return {"error": "unknown refresh token"}
        if not row["revoked_at"] and (row["blocked"] or (row["expires_at"] and row["expires_at"] < now())):
            return {"error": "refresh token expired; sign in again"}
        if row["revoked_at"] or not auth.revoke_credential(tx, raw):  # used before, or a moment ago by another request
            n = auth.revoke_credential(tx, family=row["family"])
            if n:
                tx.audit("app.token_reuse", row["name"], subject=row["principal_id"], actor_id=None, client_id=client_id,
                         revoked=n)
            return {"error": "refresh token was already used; sign in again"}
        return _pair(tx, row["principal_id"], client_id, SCOPE, resource or row["resource"], row["family"])


def revoke_token(db, raw: str, ctx: Ctx) -> None:
    """RFC 7009: an app revokes its own token (and with it, its grant's family)."""
    with db.tx(SYSTEM, ctx) as tx:
        for kind in ("refresh", "access"):
            row = auth.find(tx, raw, kind)
            if row:
                n = auth.revoke_credential(tx, family=row["family"]) if row["family"] else auth.revoke_credential(tx, raw)
                if n:
                    tx.audit("app.token_revoke", row["name"], subject=row["principal_id"], actor_id=row["principal_id"],
                             client_id=row["client_id"], revoked=n)
                return


def decide(db, person, req: dict, allow: bool, projects, ctx: Ctx) -> str | None:
    """A person's answer on the consent page: the app's agent (connected and recorded), or None (declined, recorded)."""
    if not allow:
        accounts.record(db, ctx, "app.deny", req["client_name"], client_id=req["client_id"])
        return None
    projects = [str(p) for p in projects] if isinstance(projects, list) and projects else None
    if person.projects is not None:
        projects = [p for p in (projects or person.projects) if p in person.projects] or list(person.projects)
    return accounts.app_principal(db, person, req["client_id"], req["client_name"], projects, ctx)


def _err(error: str, desc: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": error, "error_description": desc}, status_code=status,
                        headers={"Cache-Control": "no-store", "Access-Control-Allow-Origin": "*"})


def _redirect(uri: str, params: dict, iss: str, state: str | None) -> str:
    """The client's redirect_uri with the authorization response (RFC 6749 §4.1.2, RFC 9207 iss) appended."""
    u = urlsplit(uri)
    params = {**params, "iss": iss, **({"state": state} if state is not None else {})}
    return urlunsplit((u.scheme, u.netloc, u.path, (u.query + "&" if u.query else "") + urlencode(params), ""))


def register_oauth(app: FastAPI, lg: Ledger) -> None:
    clients = Clients(lg)
    # waiting for consent; issued codes (bounded: a flood evicts the oldest)
    requests, codes = TTLStore(REQUEST_TTL, 18, MAX_WAITING), TTLStore(CODE_TTL, 32, MAX_WAITING)
    register_limit, authorize_limit = Limiter(*REGISTER_LIMIT), Limiter(*AUTHORIZE_LIMIT)

    def limited(limiter: Limiter, what: str, request: Request) -> str | None:
        """None, or the refusal (recorded once per window as limit.hit)."""
        ctx = ctx_of(request)
        try:
            accounts._limit(lg.db, limiter, ["ip:" + (ctx.ip or "?")], what, what, ctx)
        except Denied as exc:
            return str(exc)
        return None
    cors = {"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "*", "Access-Control-Allow-Methods": "GET, POST, OPTIONS"}

    def as_metadata(request: Request) -> dict:
        base = base_url(lg, request)
        return {
            "issuer": base,
            "authorization_endpoint": base + "/oauth/authorize",
            "token_endpoint": base + "/oauth/token",
            "registration_endpoint": base + "/oauth/register",
            "revocation_endpoint": base + "/oauth/revoke",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none"],
            "revocation_endpoint_auth_methods_supported": ["none"],
            "scopes_supported": [SCOPE],
            "client_id_metadata_document_supported": True,
            "authorization_response_iss_parameter_supported": True,
            "service_documentation": base + "/settings/apps",
        }

    def pr_metadata(request: Request) -> dict:
        base = base_url(lg, request)
        return {"resource": base + "/mcp", "authorization_servers": [base], "scopes_supported": [SCOPE],
                "bearer_methods_supported": ["header"], "resource_name": "litledger"}

    @app.get("/.well-known/oauth-protected-resource")
    @app.get("/.well-known/oauth-protected-resource/mcp")
    async def protected_resource(request: Request):
        return JSONResponse(pr_metadata(request), headers={**cors, "Cache-Control": "public, max-age=3600"})

    @app.get("/.well-known/oauth-authorization-server")
    @app.get("/.well-known/openid-configuration")
    async def authorization_server(request: Request):
        return JSONResponse(as_metadata(request), headers={**cors, "Cache-Control": "public, max-age=3600"})

    @app.options("/oauth/{path:path}")
    @app.options("/.well-known/{path:path}")
    async def preflight(path: str):
        return Response(status_code=204, headers=cors)

    @app.post("/oauth/register")
    async def register(request: Request):
        refused = await run(limited, register_limit, "app register", request)
        if refused:
            return _err("too_many_requests", refused, 429)
        try:
            body = await request.json()
        except ValueError:
            return _err("invalid_client_metadata", "body must be JSON")
        try:
            out = await run(clients.register, body if isinstance(body, dict) else {}, ctx_of(request))
        except ValueError as exc:
            return _err("invalid_redirect_uri" if "redirect" in str(exc) else "invalid_client_metadata", str(exc))
        return JSONResponse(out, status_code=201, headers={**cors, "Cache-Control": "no-store"})

    @app.get("/oauth/authorize")
    async def authorize(request: Request):
        refused = await run(limited, authorize_limit, "app authorize", request)
        if refused:
            return page("Too many sign-in attempts", refused, 429)
        q = request.query_params
        client_id, redirect_uri = q.get("client_id", ""), q.get("redirect_uri", "")
        try:
            client = await run(clients.get, client_id) if client_id else None
        except HTTPException as exc:
            return page("Can't connect this app", str(exc.detail))
        if not client:
            return page("Unknown app", "This sign-in link comes from an app litledger doesn't know. Start again from the app.")
        if not redirect_uri and len(client["redirect_uris"]) == 1:
            redirect_uri = client["redirect_uris"][0]
        if not _redirect_ok(client["redirect_uris"], redirect_uri):
            return page("Can't connect this app", "The app asked to return to an address it didn't register.")
        iss = base_url(lg, request)

        def back(**params) -> RedirectResponse:
            return RedirectResponse(_redirect(redirect_uri, params, iss, q.get("state")), status_code=302)

        if q.get("response_type") != "code":
            return back(error="unsupported_response_type", error_description="use response_type=code")
        if not q.get("code_challenge") or q.get("code_challenge_method") != "S256":
            return back(error="invalid_request", error_description="PKCE with code_challenge_method=S256 is required")
        resource = q.get("resource") or resource_url(lg, request)
        if not auth.same_resource(resource, resource_url(lg, request)) and not auth.same_resource(resource, iss):
            return back(error="invalid_target", error_description=f"this server only issues tokens for {resource_url(lg, request)}")
        scopes = [s for s in (q.get("scope") or SCOPE).split() if s]
        rid = requests.put({"client_id": client_id, "client_name": client["name"], "redirect_uri": redirect_uri,
                                   "state": q.get("state"), "challenge": q["code_challenge"], "scope": " ".join(scopes),
                                   "resource": resource_url(lg, request), "iss": iss,
                                   "client_uri": client["meta"].get("client_uri"), "verified": client_id.startswith("https://")})
        return RedirectResponse(f"/authorize?request={rid}", status_code=302)

    # -- the consent page (web app) talks to these; a signed-in person is required -----------------------
    @app.get("/api/v1/oauth/requests/{rid}")
    async def consent_info(rid: str, request: Request):
        person = require_person(request)
        req = requests.get(rid)
        if not req:
            raise HTTPException(404, "This approval request expired. Start the sign-in again from the app.")
        return {"client_name": req["client_name"], "client_id": req["client_id"], "verified": req["verified"],
                "redirect_host": urlsplit(req["redirect_uri"]).netloc or req["redirect_uri"], "scope": req["scope"],
                "client_uri": req["client_uri"], "person": person.name,
                "loopback": (urlsplit(req["redirect_uri"]).hostname or "") in LOOPBACK}

    @app.post("/api/v1/oauth/requests/{rid}")
    async def consent_decide(rid: str, request: Request):
        person = require_person(request)
        body = await read_body(request)
        req = requests.pop(rid)
        if not req:
            raise HTTPException(404, "This approval request expired. Start the sign-in again from the app.")
        def target(params: dict) -> str:
            return _redirect(req["redirect_uri"], params, req["iss"], req["state"])

        pid = decide(lg.db, person, req, bool(body.get("allow")), body.get("projects"), ctx_of(request, person))
        if not pid:
            return {"redirect": target({"error": "access_denied", "error_description": "the person declined"})}
        code = codes.put({"principal_id": pid, "client_id": req["client_id"], "redirect_uri": req["redirect_uri"],
                                 "challenge": req["challenge"], "scope": req["scope"], "resource": req["resource"]})
        return {"redirect": target({"code": code})}

    # -- token endpoint ------------------------------------------------------------------------------------
    @app.post("/oauth/token")
    async def token(request: Request):
        form = dict(await request.form())
        header = request.headers.get("authorization", "")
        if header.lower().startswith("basic "):  # tolerate clients that send client_id via Basic with an empty secret
            try:
                form.setdefault("client_id", base64.b64decode(header[6:]).decode().split(":", 1)[0])
            except Exception:
                pass
        grant, client_id = form.get("grant_type"), str(form.get("client_id") or "")
        headers = {"Cache-Control": "no-store", "Pragma": "no-cache", "Access-Control-Allow-Origin": "*"}
        if grant == "authorization_code":
            data = codes.pop(str(form.get("code") or ""))
            if not data or data["client_id"] != client_id:
                return _err("invalid_grant", "the code is invalid, expired or was already used")
            if str(form.get("redirect_uri") or data["redirect_uri"]) != data["redirect_uri"]:
                return _err("invalid_grant", "redirect_uri does not match the authorization request")
            verifier = str(form.get("code_verifier") or "")
            digest = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
            if not verifier or digest != data["challenge"]:
                return _err("invalid_grant", "PKCE verification failed")
            res = form.get("resource")
            if res and not auth.same_resource(str(res), data["resource"]) and not auth.same_resource(str(res), base_url(lg, request)):
                return _err("invalid_target", f"tokens are only issued for {data['resource']}")
            clients.touch(client_id)
            return JSONResponse(grant_tokens(lg.db, data["principal_id"], client_id, data["scope"], data["resource"]), headers=headers)
        if grant == "refresh_token":
            out = refresh_tokens(lg.db, str(form.get("refresh_token") or ""), client_id, ctx_of(request), resource_url(lg, request))
            if "error" in out:
                return _err("invalid_grant", out["error"])
            clients.touch(client_id)
            return JSONResponse(out, headers=headers)
        return _err("unsupported_grant_type", "use authorization_code or refresh_token")

    @app.post("/oauth/revoke")
    async def revoke(request: Request):
        form = dict(await request.form())
        revoke_token(lg.db, str(form.get("token") or ""), ctx_of(request))
        return Response(status_code=200, headers={"Access-Control-Allow-Origin": "*"})
