"""Offline, deterministic tests: HTTP is replayed from tests/fixtures/http (record with LITLEDGER_RECORD=1)."""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
import pytest

from litledger import db as dbmod
from litledger import tools
from litledger.config import Settings
from litledger.db import Actor, now
from litledger.jobs import HANDLERS
from litledger.ledger import Ledger
from litledger.textutil import norm_title

FIXTURES = Path(__file__).parent / "fixtures" / "http"
# Every transaction that changes rows must write a journal entry or an audit row, or say why it needs neither.
dbmod.STRICT = True
RECORD = os.environ.get("LITLEDGER_RECORD") == "1"
KEEP_HEADERS = ("content-type", "x-ratelimit-remaining-usd", "x-ratelimit-reset", "retry-after")


def _key(request: httpx.Request) -> str:
    parts = urlsplit(str(request.url))
    query = urlencode(sorted(parse_qsl(parts.query, keep_blank_values=True)))
    canon = f"{request.method} {urlunsplit((parts.scheme, parts.netloc, parts.path, query, ''))}"
    body = request.content or b""
    return hashlib.sha256(canon.encode() + b"\n" + body).hexdigest()[:24]


class ReplayTransport(httpx.BaseTransport):
    def __init__(self):
        self.real = httpx.HTTPTransport() if RECORD else None
        self.missing: list[str] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        path = FIXTURES / f"{_key(request)}.json"
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            return httpx.Response(data["status"], headers=data["headers"], content=base64.b64decode(data["body"]),
                                  request=request)
        if not self.real:
            self.missing.append(str(request.url))
            return httpx.Response(599, content=b"no recorded fixture", request=request)  # not retried
        resp = self.real.handle_request(request)
        resp.read()
        content = resp.content
        if len(content) < 3_000_000:
            FIXTURES.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"url": str(request.url), "status": resp.status_code,
                                        "headers": {k: v for k, v in resp.headers.items() if k.lower() in KEEP_HEADERS},
                                        "body": base64.b64encode(content).decode()}), encoding="utf-8")
        return httpx.Response(resp.status_code, headers={k: v for k, v in resp.headers.items() if k.lower() in KEEP_HEADERS},
                              content=content, request=request)


def make_ledger(tmp_path: Path, **config) -> Ledger:
    lg = Ledger(Settings.from_env({"LITLEDGER_DATA": str(tmp_path / "data")}), transport=ReplayTransport(), run_jobs_inline=True,
                handlers=HANDLERS)
    if config:  # runtime settings are saved exactly as the web UI saves them
        lg.set_config(Actor(name="test", kind="human"), {k.upper(): v for k, v in config.items()})
    lg.http.pace = RECORD  # polite pacing only when hitting real APIs
    if not RECORD:  # the SSRF guard resolves hosts; offline tests must not touch DNS
        lg.http.guard.resolver = fake_resolver
    return lg


def fake_resolver(host: str, port: int) -> list[str]:
    """Every name resolves to a public documentation-style address unless a test maps it elsewhere."""
    return ["93.184.216.34"]


@pytest.fixture
def lg(tmp_path):
    return make_ledger(tmp_path)


@pytest.fixture
def actor():
    return Actor(principal_id=None, name="tester", kind="agent", project="t")


def offline_ledger(tmp_path: Path, name: str = "data") -> Ledger:
    """A ledger that never reaches a provider (imports are stored from what was given)."""
    return Ledger(Settings.from_env({"LITLEDGER_DATA": str(tmp_path / name), "LITLEDGER_OFFLINE": "1"}),
                  transport=ReplayTransport(), run_jobs_inline=True, handlers=HANDLERS)


@pytest.fixture
def off(tmp_path):
    return offline_ledger(tmp_path)


ACTOR = Actor(principal_id=None, name="tester", kind="agent", project="t")


def call(lg: Ledger, actor: Actor, name: str, **args) -> str:
    return tools.call(lg, actor, name, args)


def add_work(conn, wid: str, title: str, wtype: str = "article-journal", csl: dict | None = None, citekey: str | None = None,
             stamp: str | None = None) -> None:
    """A bare works row (no providers, no journal), for tests that build a library by hand."""
    stamp = stamp or now()
    conn.execute("INSERT INTO works(id,type,title,csl,citekey,norm_title,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                 (wid, wtype, title, json.dumps(csl or {"title": title}), citekey or wid, norm_title(title), stamp, stamp))


def item(title, family, year, **extra):
    return {"csl": {"type": "article", "title": title, "author": [{"family": family, "given": "A"}],
                    "issued": {"date-parts": [[year]]}, **extra}}


CSRF = {"X-Litledger-CSRF": "1"}
BASE = "http://127.0.0.1:8765"


def browser(app):
    """A fresh browser: keeps cookies and sends the CSRF header like the web app."""
    from fastapi.testclient import TestClient
    b = TestClient(app, base_url=BASE)
    b.headers.update(CSRF)
    return b


def admin_browser(app, token):
    """The root admin, signed in with the admin token."""
    b = browser(app)
    r = b.post("/auth/token", json={"token": token})
    assert r.status_code == 200, r.text
    return b


def signup_browser(app, email="ada@example.com", password="correct horse", name=None):
    """Someone who created their own account (a member)."""
    b = browser(app)
    r = b.post("/auth/signup", json={"name": name or email.split("@")[0], "email": email, "password": password})
    assert r.status_code == 200, r.text
    return b


def invited_browser(app, admin, email, role="member", password="member pass 1", name=None, projects=None):
    """Someone an admin (a browser or a client with a token) invited, after accepting with a password."""
    from urllib.parse import parse_qs, urlsplit
    made = admin.post("/api/v1/invites", json={"email": email, "role": role, "projects": projects}, headers=CSRF)
    assert made.status_code == 200, made.text
    b = browser(app)
    t = parse_qs(urlsplit(made.json()["link"]).query)["t"][0]
    r = b.post("/auth/invite", json={"token": t, "name": name or email.split("@")[0], "password": password})
    assert r.status_code == 200, r.text
    return b


def wid(lg, key):
    with lg.db.read() as conn:
        return conn.execute("SELECT id FROM works WHERE citekey=?", (key,)).fetchone()["id"]


def oauth_grant(client, person, name="Tool", redirect="http://localhost:9/cb"):
    """A person connects an app the way an MCP client does (dynamic registration, PKCE, consent, code exchange).
    Returns (client_id, tokens)."""
    import base64
    import secrets
    from urllib.parse import parse_qs, urlencode, urlsplit
    reg = client.post("/oauth/register", json={"redirect_uris": ["http://localhost/cb"], "client_name": name}).json()
    verifier = secrets.token_urlsafe(48)
    q = {"response_type": "code", "client_id": reg["client_id"], "redirect_uri": redirect, "state": "s",
         "code_challenge": base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode(),
         "code_challenge_method": "S256"}
    r = client.get("/oauth/authorize?" + urlencode(q), follow_redirects=False)
    rid = parse_qs(urlsplit(r.headers["location"]).query)["request"][0]
    back = person.post(f"/api/v1/oauth/requests/{rid}", json={"allow": True}).json()["redirect"]
    tok = client.post("/oauth/token", data={"grant_type": "authorization_code", "code": parse_qs(urlsplit(back).query)["code"][0],
                                            "client_id": reg["client_id"], "redirect_uri": redirect, "code_verifier": verifier}).json()
    return reg["client_id"], tok


def link_token(text: str) -> str:
    """The one-time token in an emailed (or admin-made) link."""
    from urllib.parse import parse_qs, urlsplit
    return parse_qs(urlsplit(next(w for w in text.split() if "?t=" in w)).query)["t"][0]
