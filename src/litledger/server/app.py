"""The HTTP server: MCP (streamable HTTP) at /mcp, REST at /api/v1, live events at /events, sign-in at /auth, OAuth for
MCP apps at /oauth, and the web UI at /. Every request to /mcp, /api and /events is authenticated here first."""
from __future__ import annotations

import contextlib
import logging
from pathlib import Path

import anyio
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from mcp.server.streamable_http_manager import StreamableHTTPASGIApp, StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.routing import Route
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from .. import __version__, accounts, auth, backup, jobs, works
from ..access import register_access
from ..args import project_slug
from ..config import Settings
from ..ledger import Ledger
from ..oauth import challenge, register_oauth, resource_url
from ..signin import COOKIE, register_signin
from .mcp import build_mcp
from .rest import register_rest

log = logging.getLogger("litledger")
WEB_DIR = Path(__file__).parent.parent / "web"
# Reachable without signing in: health, the start of `litledger login` (it only creates a request a person must
# approve), and the API docs. /auth/*, /oauth/* and /.well-known/* handle their own checks.
LOOPBACK = ("127.0.0.1", "::1", "localhost")
PUBLIC = ("/api/v1/health", "/favicon.ico", "/api/v1/login/", "/api/docs", "/api/openapi.json")
SAFE_METHODS = ("GET", "HEAD", "OPTIONS")


class AuthMiddleware(BaseHTTPMiddleware):
    """Bearer tokens (API tokens; OAuth access tokens on /mcp only) or the browser's session cookie.

    Cookie-authenticated writes must carry X-Litledger-CSRF: a cross-site page can't add that header without a CORS
    preflight, which this server never grants. Principals limited to some projects can't reach others."""

    def __init__(self, app, ledger: Ledger):
        super().__init__(app)
        self.lg = ledger
        self.warned_scheme = False

    def _check_scheme(self, request: Request) -> None:
        """Once: PUBLIC_URL says https but a proxied request arrived as http, so the proxy isn't trusted (its
        X-Forwarded-* headers are ignored: every client then shares the proxy's address and rate limits)."""
        if self.warned_scheme or request.url.scheme != "http" or not self.lg.settings.get("PUBLIC_URL").startswith("https://"):
            return
        host = request.client.host if request.client else ""
        if request.headers.get("x-forwarded-for") or request.headers.get("x-forwarded-proto") or host not in LOOPBACK:
            self.warned_scheme = True
            log.warning("PUBLIC_URL is https but requests arrive over http from %s. If a reverse proxy is in front, set "
                        "LITLEDGER_TRUSTED_PROXIES to its address (now %s) so client addresses and https are seen.",
                        host or "?", self.lg.base_settings.trusted_proxies)

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        self._check_scheme(request)
        if path.startswith(("/mcp", "/api/", "/events")) and not path.startswith(PUBLIC):
            header = request.headers.get("authorization", "")
            bearer = header[7:].strip() if header.lower().startswith("bearer ") else None
            is_mcp = path.startswith("/mcp")
            cookie = None if bearer or is_mcp else request.cookies.get(COOKIE)
            # OAuth access tokens are issued for the MCP endpoint only (their audience); API tokens work everywhere.
            # On /mcp both the configured address and the one this request came in on count, so a token bound to the
            # address a client still uses keeps working after PUBLIC_URL changes; a refresh re-binds it (oauth.py).
            resource = (resource_url(self.lg, request), f"{request.url.scheme}://{request.url.netloc}/mcp") if is_mcp \
                else "urn:litledger:api"
            actor = await anyio.to_thread.run_sync(auth.verify, self.lg.db, bearer or cookie, resource)
            if not actor:
                body = {"error": "unauthorized", "hint": "sign in on the web page, or send Authorization: Bearer <token> "
                        "(MCP apps: add the server URL and sign in through the browser when asked)"}
                return JSONResponse(body, status_code=401, headers=challenge(self.lg, request) if is_mcp else {"WWW-Authenticate": "Bearer"})
            if cookie and request.method not in SAFE_METHODS and request.headers.get("x-litledger-csrf") != "1":
                return JSONResponse({"error": "csrf", "hint": "browser writes need the X-Litledger-CSRF: 1 header"}, status_code=403)
            if actor.projects is not None:
                project = request.headers.get("x-litledger-project") or request.query_params.get("project")
                if project and project_slug(project) not in actor.projects:
                    return JSONResponse({"error": "forbidden", "hint": f"this sign-in may only use: {', '.join(actor.projects)}"},
                                        status_code=403)
            request.state.actor = actor
            works.VISIBLE.set(actor.projects)  # a limited caller's lookups see only its projects' works (works.py)
        return await call_next(request)


def _startup_log(lg: Ledger) -> None:
    if accounts.bootstrap(lg.db, lg.settings.token_dir):
        log.warning("First start: the admin token is in %s. Sign in with it on the web UI (Sign in with admin token).",
                    lg.settings.token_dir / "admin")
    warnings = lg.providers.warnings()
    if not lg.settings.quiet_recommendations:
        for w in warnings:
            steps = "\n".join(f"        {i}. {step}" for i, step in enumerate(w["steps"], 1))
            log.warning("%s\n      Impact: %s.\n      To enable:\n%s", w["headline"], w["impact"], steps)
    n_on = sum(st.enabled for st in lg.providers.states.values())
    log.info("litledger %s: %d sources enabled%s; MCP at /mcp, UI at /", __version__, n_on,
             f"; {len(warnings)} setup warnings above (set keys under Admin → Sources on the web UI)"
             if warnings and not lg.settings.quiet_recommendations else "")


def create_app(settings: Settings | None = None, ledger: Ledger | None = None) -> FastAPI:
    lg = ledger or Ledger(settings, handlers=jobs.HANDLERS)
    server = build_mcp(lg)
    # Bearer tokens are required on every request, so a DNS-rebinding page cannot act without one.
    security = TransportSecuritySettings(enable_dns_rebinding_protection=False)
    manager = StreamableHTTPSessionManager(app=server, stateless=True, json_response=True, security_settings=security)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        lock = backup.DataLock(lg.settings.data_dir)  # `litledger restore` refuses while this is held
        if not lock.acquire():
            log.warning("Another litledger server holds %s: two servers on one data folder don't share sign-in codes, "
                        "OAuth requests or rate limits.", lock.path)
        _startup_log(lg)
        lg.start_worker()
        try:
            async with manager.run(), anyio.create_task_group() as tg:
                tg.start_soon(backup.schedule, lg)
                yield
                tg.cancel_scope.cancel()
        finally:
            lg.stop_worker()
            lock.release()

    app = FastAPI(title="litledger", version=__version__, lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.state.ledger = lg
    app.state.mcp_server = server
    app.add_middleware(AuthMiddleware, ledger=lg)
    # Outermost: behind a trusted reverse proxy (LITLEDGER_TRUSTED_PROXIES), the client's address and scheme come from
    # X-Forwarded-For/-Proto, so rate limits, the audit log and secure cookies see the real client.
    app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=lg.base_settings.trusted_proxies)
    app.router.routes.append(Route("/mcp", endpoint=StreamableHTTPASGIApp(manager), methods=["GET", "POST", "DELETE"]))
    register_oauth(app, lg)
    register_signin(app, lg)
    register_access(app, lg)
    register_rest(app, lg)

    # Static web UI (built into src/litledger/web by the Docker image / `npm run build`). Registered last: catch-all.
    @app.get("/{path:path}", include_in_schema=False)
    async def ui(path: str):
        if path.startswith(("api/", "mcp", "events", "oauth/", "auth/", ".well-known/")):
            raise HTTPException(404)
        candidate = (WEB_DIR / path).resolve() if path else None
        if candidate and candidate.is_file() and str(candidate).startswith(str(WEB_DIR.resolve())):
            return FileResponse(candidate)
        index = WEB_DIR / "index.html"
        if index.exists():
            return FileResponse(index)
        return HTMLResponse("<h1>litledger</h1><p>The web UI is not built. Use the API at <a href='/api/docs'>/api/docs</a>.</p>")

    return app
