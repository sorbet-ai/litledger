"""MCP OAuth end to end, driven by the official MCP SDK client the way Claude Code and Codex do it: discovery from the
401, client registration (metadata document or dynamic), PKCE, a person's consent, tokens bound to /mcp, refresh with
rotation, and a real tool call. Sign-in and accounts are in test_accounts.py."""
from urllib.parse import parse_qs, urlsplit

import anyio
import httpx2
import pytest
from fastapi.testclient import TestClient
from mcp.client.auth.oauth2 import OAuthClientProvider
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.auth import AuthorizationCodeResult, OAuthClientInformationFull, OAuthClientMetadata, OAuthToken

from litledger import accounts
from litledger.app import create_app
from litledger.oauth import Clients

from conftest import CSRF, make_ledger, signup_browser

BASE = "http://127.0.0.1:8765"


class Memory:
    def __init__(self):
        self.tokens: OAuthToken | None = None
        self.client: OAuthClientInformationFull | None = None

    async def get_tokens(self):
        return self.tokens

    async def set_tokens(self, tokens):
        self.tokens = tokens

    async def get_client_info(self):
        return self.client

    async def set_client_info(self, info):
        self.client = info


def _consent(app, session: str, allow: bool = True):
    """What a person does in their signed-in browser: open the authorize URL, land on the consent page, approve."""
    got: dict = {}

    async def redirect_handler(url: str) -> None:
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url=BASE) as c:
            r = await c.get(url, follow_redirects=False)
            assert r.status_code == 302, r.text
            loc = r.headers["location"]
            assert loc.startswith("/authorize?request="), loc
            rid = parse_qs(urlsplit(loc).query)["request"][0]
            h = {**CSRF, "Cookie": f"litledger_session={session}"}
            info = (await c.get(f"/api/v1/oauth/requests/{rid}", headers=h)).json()
            got["info"] = info
            r = await c.post(f"/api/v1/oauth/requests/{rid}", json={"allow": allow}, headers=h)
            got["redirect"] = r.json()["redirect"]

    async def callback_handler() -> AuthorizationCodeResult:
        q = parse_qs(urlsplit(got["redirect"]).query)
        if "error" in q:
            raise RuntimeError(q["error"][0])
        return AuthorizationCodeResult(code=q["code"][0], state=q.get("state", [None])[0], iss=q.get("iss", [None])[0])

    return redirect_handler, callback_handler, got


async def _session_tools(app, provider) -> list[str]:
    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url=BASE, auth=provider) as http:
        async with streamable_http_client(f"{BASE}/mcp", http_client=http) as streams:
            read, write = streams[0], streams[1]
            async with ClientSession(read, write) as s:
                await s.initialize()
                return [t.name for t in (await s.list_tools()).tools]


@pytest.fixture
def setup(tmp_path):
    lg = make_ledger(tmp_path)
    app = create_app(ledger=lg)
    with TestClient(app, base_url=BASE) as c:  # runs the lifespan (bootstrap admin token, MCP session manager)
        admin = (lg.settings.token_dir / "admin").read_text().strip()
        ada = signup_browser(app).cookies["litledger_session"]  # a member who signed up, signed in
        yield lg, app, c, admin, ada


def _run(fn):
    return anyio.run(fn)


def test_mcp_oauth_with_dynamic_registration(setup):
    lg, app, c, admin, ada = setup
    # Unauthenticated MCP request: 401 pointing at the protected-resource metadata
    r = c.post("/mcp", json={})
    assert r.status_code == 401 and 'resource_metadata="http://127.0.0.1:8765/.well-known/oauth-protected-resource/mcp"' in r.headers["www-authenticate"]
    prm = c.get("/.well-known/oauth-protected-resource/mcp").json()
    assert prm["resource"] == f"{BASE}/mcp" and prm["authorization_servers"] == [BASE]
    meta = c.get("/.well-known/oauth-authorization-server").json()
    assert meta["code_challenge_methods_supported"] == ["S256"] and meta["client_id_metadata_document_supported"] is True

    redirect, callback, got = _consent(app, ada)
    store = Memory()
    provider = OAuthClientProvider(f"{BASE}/mcp", OAuthClientMetadata(
        redirect_uris=["http://localhost:3030/callback"], token_endpoint_auth_method="none",
        grant_types=["authorization_code", "refresh_token"], client_name="Test Agent"), store, redirect, callback)

    async def go():
        return await _session_tools(app, provider)

    tools = _run(go)
    assert "find" in tools and got["info"]["client_name"] == "Test Agent" and got["info"]["person"] == "ada"
    assert store.tokens and store.tokens.refresh_token
    # The app is an agent of its own, owned by the person; its writes show as "test-agent via ada"
    agents = c.get("/api/v1/principals", headers={"Authorization": f"Bearer {admin}"}).json()["agents"]
    app_row = next(p for p in agents if p["origin"] == "oauth")
    assert app_row["name"] == "test-agent-ada" and app_row["owner"] == "ada" and app_row["by"] == "test-agent via ada"
    assert app_row["client_name"] == "Test Agent" and app_row["access"] == "library"
    # Access tokens are for /mcp only (their audience), not the REST API
    assert c.get("/api/v1/me", headers={"Authorization": f"Bearer {store.tokens.access_token}"}).status_code == 401
    # Refresh rotates; reusing the old refresh token revokes the whole family
    old = store.tokens.refresh_token
    r = c.post("/oauth/token", data={"grant_type": "refresh_token", "refresh_token": old, "client_id": store.client.client_id})
    assert r.status_code == 200 and r.json()["refresh_token"] != old
    new_access = r.json()["access_token"]
    assert c.post("/mcp", json={}, headers={"Authorization": f"Bearer {new_access}"}).status_code != 401
    r = c.post("/oauth/token", data={"grant_type": "refresh_token", "refresh_token": old, "client_id": store.client.client_id})
    assert r.json()["error"] == "invalid_grant"
    assert c.post("/mcp", json={}, headers={"Authorization": f"Bearer {new_access}"}).status_code == 401


def test_mcp_oauth_with_client_metadata_document_and_loopback_port(setup, monkeypatch):
    lg, app, c, admin, ada = setup
    cimd = "https://claude.ai/oauth/claude-code-client-metadata"
    doc = {"client_id": cimd, "client_name": "Claude Code", "redirect_uris": ["http://localhost/callback", "http://127.0.0.1/callback"],
           "token_endpoint_auth_method": "none", "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"]}
    monkeypatch.setattr(Clients, "_fetch_document", lambda self, cid: doc if cid == cimd else None)
    redirect, callback, got = _consent(app, ada)
    store = Memory()
    provider = OAuthClientProvider(f"{BASE}/mcp", OAuthClientMetadata(
        redirect_uris=["http://localhost:51234/callback"], token_endpoint_auth_method="none",
        grant_types=["authorization_code", "refresh_token"], client_name="Claude Code"), store, redirect, callback,
        client_metadata_url=cimd)
    tools = _run(lambda: _session_tools(app, provider))
    assert "find" in tools and got["info"]["verified"] is True and got["info"]["loopback"] is True
    assert store.client.client_id == cimd


def test_authorize_rejects_bad_requests(setup, monkeypatch):
    lg, app, c, admin, ada = setup
    reg = c.post("/oauth/register", json={"redirect_uris": ["http://localhost/cb"], "client_name": "x"}).json()
    cid = reg["client_id"]
    q = {"client_id": cid, "redirect_uri": "http://localhost:9/cb", "response_type": "code", "state": "s"}
    r = c.get("/oauth/authorize", params=q, follow_redirects=False)  # no PKCE
    assert r.status_code == 302 and "error=invalid_request" in r.headers["location"] and "state=s" in r.headers["location"]
    r = c.get("/oauth/authorize", params={**q, "redirect_uri": "https://evil.example/cb", "code_challenge": "x",
                                          "code_challenge_method": "S256"}, follow_redirects=False)
    assert r.status_code == 400  # never redirect to an unregistered address
    r = c.get("/oauth/authorize", params={**q, "code_challenge": "x", "code_challenge_method": "S256",
                                          "resource": "https://other.example/mcp"}, follow_redirects=False)
    assert "error=invalid_target" in r.headers["location"]
    assert c.post("/oauth/register", json={"redirect_uris": ["javascript:alert(1)"]}).status_code == 400
    # an agent can't approve, not even a full-access one; a person can deny
    r = c.get("/oauth/authorize", params={**q, "code_challenge": "x", "code_challenge_method": "S256"}, follow_redirects=False)
    rid = parse_qs(urlsplit(r.headers["location"]).query)["request"][0]
    for token in (accounts.create_token(lg.db, "bot").token, accounts.create_token(lg.db, "ci", admin=True).token):
        assert c.get(f"/api/v1/oauth/requests/{rid}", headers={"Authorization": f"Bearer {token}"}).status_code == 403
    out = c.post(f"/api/v1/oauth/requests/{rid}", json={"allow": False}, headers={**CSRF, "Cookie": f"litledger_session={ada}"}).json()
    assert "error=access_denied" in out["redirect"] and out["redirect"].startswith("http://localhost:9/cb?")


def test_events_stream_does_not_hold_worker_threads(setup):
    """Live-event subscribers wait on an asyncio queue, not in a worker thread: with more of them waiting than the
    thread pool has threads, no thread is borrowed and other requests still get one at once."""
    lg, app, c, admin, ada = setup

    async def go():
        limiter = anyio.to_thread.current_default_thread_limiter()
        subs = [lg.db.events.subscribe_async() for _ in range(int(limiter.total_tokens) + 10)]
        got: list[dict] = []

        async def wait(q):
            got.append(await q.get())

        try:
            async with anyio.create_task_group() as tg:
                for q in subs:
                    tg.start_soon(wait, q)
                await anyio.sleep(0.05)
                assert limiter.borrowed_tokens == 0  # every subscriber is waiting without a thread
                with anyio.fail_after(5):
                    assert await anyio.to_thread.run_sync(lambda: 42) == 42  # the pool is free for other work
                lg.db.events.publish([{"op": "x", "project": "default", "seq": 1}])  # from this thread, as a commit does
        finally:
            for q in subs:
                lg.db.events.unsubscribe_async(q)
        return got, len(subs)

    got, n = _run(go)
    assert len(got) == n > 40 and all(e["op"] == "x" for e in got)


def test_registration_and_authorization_are_rate_limited(setup, monkeypatch):
    """Anyone can call /oauth/register and /oauth/authorize, so a burst from one address gets 429 and leaves bounded
    state: one folded audit row, a capped clients table and capped in-memory stores."""
    from litledger import oauth
    lg, app, c, admin, ada = setup
    body = {"redirect_uris": ["http://localhost/cb"], "client_name": "burst"}
    codes = [c.post("/oauth/register", json={**body, "client_name": f"burst {i}"}).status_code for i in range(25)]
    assert codes[:oauth.REGISTER_LIMIT[0]] == [201] * oauth.REGISTER_LIMIT[0] and set(codes[oauth.REGISTER_LIMIT[0]:]) == {429}
    with lg.db.read() as conn:
        clients = conn.execute("SELECT count(*) FROM oauth_clients").fetchone()[0]
        audit = conn.execute("SELECT op, count FROM audit WHERE op IN ('app.register', 'limit.hit') ORDER BY seq").fetchall()
    assert clients == oauth.REGISTER_LIMIT[0]
    assert [tuple(r) for r in audit] == [("app.register", oauth.REGISTER_LIMIT[0]), ("limit.hit", 5)]  # two rows, not 25
    cid = c.post("/oauth/register", json=body, headers={"X-Forwarded-For": "203.0.113.7"})  # untrusted: still limited
    assert cid.status_code == 429
    # authorization requests: limited per address too
    reg = oauth.Clients(lg).register(body, accounts.SHELL)
    q = {"client_id": reg["client_id"], "redirect_uri": "http://localhost:9/cb", "response_type": "code",
         "code_challenge": "x", "code_challenge_method": "S256"}
    got = [c.get("/oauth/authorize", params=q, follow_redirects=False).status_code for _ in range(oauth.AUTHORIZE_LIMIT[0] + 3)]
    assert got.count(302) == oauth.AUTHORIZE_LIMIT[0] and got[-1] == 429


def test_unused_registrations_and_waiting_requests_are_capped(setup, monkeypatch):
    from litledger import auth as authmod
    from litledger import oauth
    lg, app, c, admin, ada = setup
    monkeypatch.setattr(oauth, "MAX_CLIENTS", 10)
    clients = oauth.Clients(lg)
    for i in range(30):
        clients.register({"redirect_uris": ["http://localhost/cb"], "client_name": f"c{i}"}, accounts.Ctx(ip=f"10.0.0.{i}"))
    with lg.db.read() as conn:
        assert conn.execute("SELECT count(*) FROM oauth_clients").fetchone()[0] <= 12
    store = authmod.TTLStore(600, cap=100)
    keys = [store.put(i) for i in range(250)]
    assert len(store) == 100 and store.get(keys[0]) is None and store.get(keys[-1]) == 249  # the oldest went first
    short = authmod.TTLStore(0.05, cap=100)
    for i in range(50):
        short.put(i)
    import time
    time.sleep(0.1)
    assert len(short) == 0  # expired items are pruned off the front


def test_tokens_follow_a_change_of_public_address(setup):
    """Grants are bound to the /mcp URL they were issued for. After PUBLIC_URL changes, a token still works through
    the address the app uses, and a refresh re-binds the grant to the new address."""
    from conftest import oauth_grant
    from litledger.db import SYSTEM
    lg, app, c, admin, ada = setup
    person = TestClient(app, base_url=BASE)
    person.cookies.set("litledger_session", ada)
    person.headers.update(CSRF)
    client_id, tok = oauth_grant(c, person)
    mcp = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    ping = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}

    def tools_list(client, access):
        return client.post("/mcp", json=ping, headers={**mcp, "Authorization": f"Bearer {access}"}).status_code

    assert tools_list(c, tok["access_token"]) == 200
    lg.set_config(SYSTEM, {"PUBLIC_URL": "https://lit.example.com"})
    assert tools_list(c, tok["access_token"]) == 200  # the old address still reaches the server: still valid
    public = TestClient(app, base_url="https://lit.example.com")
    assert tools_list(public, tok["access_token"]) == 401  # bound to the old address
    fresh = public.post("/oauth/token", data={"grant_type": "refresh_token", "refresh_token": tok["refresh_token"],
                                              "client_id": client_id}).json()
    assert tools_list(public, fresh["access_token"]) == 200  # re-bound to https://lit.example.com/mcp
    with lg.db.read() as conn:
        newest = conn.execute("SELECT resource FROM credentials WHERE kind='access' ORDER BY rowid DESC LIMIT 1").fetchone()[0]
    assert newest == "https://lit.example.com/mcp"
