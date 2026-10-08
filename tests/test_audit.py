"""The audit log is structural: account and security changes write their audit row in the same transaction as the
change (an entry exists exactly when its change does), library writes write the journal, and every mutating route is
accounted for. The test-only guard in Database.tx (conftest sets db.STRICT) refuses a transaction that changed rows
with neither."""
import base64
import hashlib
import json
import secrets
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from fastapi.testclient import TestClient

from litledger import accounts, auth, cli, signin
from litledger import db as dbmod
from litledger.app import create_app
from litledger.db import SYSTEM, Ctx

from conftest import BASE, admin_browser, browser, make_ledger, signup_browser

# Every mutating route, and the audit op its action writes (checked by exercising each one below).
AUDITED = {
    ("POST", "/oauth/register"): "app.register",
    ("POST", "/api/v1/oauth/requests/{rid}"): "app.connect",
    ("POST", "/oauth/revoke"): "app.token_revoke",
    ("POST", "/auth/signup"): "account.join",
    ("POST", "/auth/token"): "signin",
    ("POST", "/auth/password"): "signin",
    ("POST", "/auth/logout"): "signout",
    ("POST", "/auth/forgot"): "password.reset_requested",
    ("POST", "/auth/reset"): "password.reset",
    ("POST", "/auth/invite"): "invite.accept",
    ("PATCH", "/api/v1/me/account"): "account.rename",
    ("PUT", "/api/v1/me/password"): "password.change",
    ("DELETE", "/api/v1/me/sessions/{sid}"): "session.end",
    ("DELETE", "/api/v1/me/sessions"): "sessions.end",
    ("DELETE", "/api/v1/me/identities/{provider}"): "identity.unlink",
    ("POST", "/api/v1/me/tokens"): "token.create",
    ("POST", "/api/v1/logins/{code}"): "terminal.approve",
    ("DELETE", "/api/v1/logins/{code}"): "terminal.deny",
    ("POST", "/api/v1/principals"): "token.create",
    ("PATCH", "/api/v1/principals/{name}"): "role.change",
    ("DELETE", "/api/v1/principals/{name}"): "token.revoke",
    ("POST", "/api/v1/principals/{name}/reset-link"): "password.reset_link",
    ("POST", "/api/v1/principals/{name}/sign-out"): "sessions.end",
    ("POST", "/api/v1/invites"): "invite.create",
    ("DELETE", "/api/v1/invites/{invite_id}"): "invite.revoke",
    ("PUT", "/api/v1/config"): "settings.change",
    ("POST", "/api/v1/sources/{pid}"): "source.remove",
    ("POST", "/api/v1/backups"): "backup.create",
}
# GET routes that change state (opening an emailed link, coming back from Google/GitHub): declared by hand.
AUDITED_GETS = {
    ("GET", "/auth/signup/confirm"): "account.join",
    ("GET", "/auth/email/confirm"): "email.confirm",
    ("GET", "/auth/{provider}/callback"): "signin",
    ("GET", "/auth/invite"): "invite.expired",
}
# Library data: these write the journal (checked below), not the audit log.
JOURNALED = {
    ("POST", "/api/v1/tools/{name}"): "tools journal what they change",
    ("POST", "/api/v1/maps"): "map edits",
    ("DELETE", "/api/v1/maps/{map_id:path}"): "map deletion",
    ("POST", "/api/v1/uploads"): "imports and attached files",
    ("POST", "/mcp"): "MCP tools journal what they change",
}
NOT_AUDITED = {
    ("POST", "/oauth/token"): "issues tokens for a grant whose consent is audited (app.connect); hourly rotation isn't "
                              "recorded, a reused refresh token is (app.token_reuse)",
    ("POST", "/api/v1/login/start"): "only creates an in-memory request; approving or denying it is audited",
    ("POST", "/api/v1/config/test/{provider}"): "one test request to a source; changes nothing",
    ("POST", "/api/v1/mail/test"): "sends a test email; changes nothing",
    ("DELETE", "/mcp"): "ends an MCP session; the server is stateless, nothing is stored",
}


@pytest.fixture
def srv(tmp_path):
    lg = make_ledger(tmp_path)
    app = create_app(ledger=lg)
    with TestClient(app, base_url=BASE) as c:
        c.token = (lg.settings.token_dir / "admin").read_text().strip()
        c.headers["Authorization"] = "Bearer " + c.token
        yield lg, app, c


def rows(lg, since=0) -> list[dict]:
    with lg.db.read() as conn:
        return [dict(r) | {"detail": json.loads(r["detail"])} for r in conn.execute("SELECT * FROM audit WHERE seq>? ORDER BY seq", (since,))]


def top(lg, table) -> int:
    with lg.db.read() as conn:
        return conn.execute(f"SELECT COALESCE(max(seq), 0) FROM {table}").fetchone()[0]


def mail_box(monkeypatch, lg):
    lg.set_config(SYSTEM, {"SMTP_HOST": "smtp.example.com"})
    sent = []
    monkeypatch.setattr(signin, "send_mail", lambda lg, to, subject, body: sent.append((to, subject, body)) or True)
    return sent


def link_token(text: str) -> str:
    return parse_qs(urlsplit(next(w for w in text.split() if "?t=" in w)).query)["t"][0]


def provider_round_trip(client, monkeypatch, who, **start):
    monkeypatch.setattr(signin, "_identity", lambda lg, provider, code, uri: who)
    r = client.get("/auth/github/start", params=start, follow_redirects=False)
    state = parse_qs(urlsplit(r.headers["location"]).query)["state"][0]
    return client.get("/auth/github/callback", params={"code": "c", "state": state}, follow_redirects=False)


def authorize(c, client_id):
    verifier = secrets.token_urlsafe(48)
    q = {"response_type": "code", "client_id": client_id, "redirect_uri": "http://localhost:9/cb", "state": "s",
         "code_challenge": base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode(),
         "code_challenge_method": "S256"}
    r = c.get("/oauth/authorize?" + urlencode(q), follow_redirects=False)
    return parse_qs(urlsplit(r.headers["location"]).query)["request"][0], verifier


# ------------------------------------------------------------------------------------------------ coverage
def test_every_mutating_route_is_accounted_for(srv):
    lg, app, c = srv
    mutating = {(m, r.path) for r in app.routes for m in (getattr(r, "methods", None) or set()) & {"POST", "PUT", "PATCH", "DELETE"}}
    declared = [set(AUDITED), set(JOURNALED), set(NOT_AUDITED)]
    assert not (declared[0] & declared[1]) and not (declared[0] & declared[2]) and not (declared[1] & declared[2])
    assert mutating - set().union(*declared) == set(), "a new mutating route: make its action audit (or journal), or list it"
    assert set().union(*declared) - mutating == set(), "a declared route no longer exists"
    assert all(reason.strip() for reason in NOT_AUDITED.values())


def test_each_route_writes_its_declared_audit_op_or_journal_entry(srv, monkeypatch):
    """Exercise every declared route once and check what it wrote."""
    lg, app, c = srv
    seen: dict[tuple, tuple[list[str], bool]] = {}

    def hit(method, route, fn, ok=(200, 201, 303)):
        a0, j0 = top(lg, "audit"), top(lg, "journal")
        r = fn()
        assert r.status_code in ok, (method, route, r.status_code, r.text[:300])
        seen[(method, route)] = ([x["op"] for x in rows(lg, a0)], top(lg, "journal") > j0)
        return r

    sent = mail_box(monkeypatch, lg)
    lg.set_config(SYSTEM, {"GITHUB_CLIENT_ID": "id", "GITHUB_CLIENT_SECRET": "secret"})
    root = browser(app)
    hit("POST", "/auth/token", lambda: root.post("/auth/token", json={"token": c.token}))
    ada = browser(app)
    hit("POST", "/auth/signup", lambda: ada.post("/auth/signup", json={"name": "ada", "email": "ada@example.com", "password": "ada password"}))
    hit("GET", "/auth/email/confirm", lambda: ada.get("/auth/email/confirm", params={"t": link_token(sent[-1][2])},
                                                      follow_redirects=False))
    hit("PATCH", "/api/v1/me/account", lambda: ada.patch("/api/v1/me/account", json={"name": "ada-l"}))
    hit("PUT", "/api/v1/me/password", lambda: ada.put("/api/v1/me/password", json={"current": "ada password", "new": "ada password 2"}))
    other = browser(app)
    hit("POST", "/auth/password", lambda: other.post("/auth/password", json={"email": "ada@example.com", "password": "ada password 2"}))
    sid = next(s["id"] for s in ada.get("/api/v1/me/sessions").json() if not s["current"])
    hit("DELETE", "/api/v1/me/sessions/{sid}", lambda: ada.delete(f"/api/v1/me/sessions/{sid}"))
    browser(app).post("/auth/password", json={"email": "ada@example.com", "password": "ada password 2"})
    hit("DELETE", "/api/v1/me/sessions", lambda: ada.delete("/api/v1/me/sessions"))
    r = provider_round_trip(ada, monkeypatch, ("7", None, "ada-gh"), link=1)  # linking GitHub from Settings ...
    assert r.headers["location"] == "/settings/profile?linked=github" and rows(lg)[-1]["op"] == "identity.link"
    hit("GET", "/auth/{provider}/callback", lambda: provider_round_trip(browser(app), monkeypatch, ("7", None, "ada-gh")),
        ok=(303,))  # ... and signing in with it
    hit("DELETE", "/api/v1/me/identities/{provider}", lambda: ada.delete("/api/v1/me/identities/github"))
    hit("POST", "/api/v1/me/tokens", lambda: ada.post("/api/v1/me/tokens", json={"name": "ci"}))
    start = c.post("/api/v1/login/start", json={"name": "laptop"}, headers={"Authorization": ""}).json()
    hit("POST", "/api/v1/logins/{code}", lambda: ada.post(f"/api/v1/logins/{start['code']}", json={}))
    start = c.post("/api/v1/login/start", json={"name": "other"}, headers={"Authorization": ""}).json()
    hit("DELETE", "/api/v1/logins/{code}", lambda: ada.delete(f"/api/v1/logins/{start['code']}"))
    hit("POST", "/api/v1/principals", lambda: root.post("/api/v1/principals", json={"name": "ci-bot"}))
    hit("DELETE", "/api/v1/principals/{name}", lambda: root.delete("/api/v1/principals/ci-bot"))
    hit("PATCH", "/api/v1/principals/{name}", lambda: root.patch("/api/v1/principals/ada-l", json={"role": "admin"}))
    hit("POST", "/api/v1/principals/{name}/sign-out", lambda: root.post("/api/v1/principals/ada-l/sign-out"))
    link = hit("POST", "/api/v1/principals/{name}/reset-link", lambda: root.post("/api/v1/principals/ada-l/reset-link")).json()["link"]
    hit("POST", "/auth/reset", lambda: browser(app).post("/auth/reset", json={"token": link_token(link), "password": "ada password 3"}))
    hit("POST", "/auth/forgot", lambda: browser(app).post("/auth/forgot", json={"email": "nobody@example.com"}))
    inv = hit("POST", "/api/v1/invites", lambda: root.post("/api/v1/invites", json={"email": "bo@example.com"})).json()
    bo = browser(app)
    hit("POST", "/auth/invite", lambda: bo.post("/auth/invite", json={"token": link_token(inv["link"]), "password": "bo password"}))
    hit("GET", "/auth/invite", lambda: browser(app).get("/auth/invite", params={"t": link_token(inv["link"])}), ok=(404,))
    root.post("/api/v1/invites", json={"email": "cy@example.com"})
    hit("DELETE", "/api/v1/invites/{invite_id}", lambda: root.delete(f"/api/v1/invites/{root.get('/api/v1/invites').json()[0]['id']}"))
    lg.set_config(SYSTEM, {"SIGNUP": "domains", "SIGNUP_DOMAINS": "uni.edu"})
    browser(app).post("/auth/signup", json={"name": "dee", "email": "dee@uni.edu", "password": "dee password"})
    hit("GET", "/auth/signup/confirm", lambda: browser(app).get("/auth/signup/confirm", params={"t": link_token(sent[-1][2])},
                                                                follow_redirects=False))
    hit("PUT", "/api/v1/config", lambda: root.put("/api/v1/config", json={"values": {"MAX_UPLOAD_MB": "50"}}))
    hit("POST", "/api/v1/sources/{pid}", lambda: root.post("/api/v1/sources/arxiv", json={"action": "remove"}))
    hit("POST", "/api/v1/backups", lambda: root.post("/api/v1/backups"))
    reg = hit("POST", "/oauth/register", lambda: c.post("/oauth/register", json={"redirect_uris": ["http://localhost/cb"],
                                                                                "client_name": "Tool"})).json()
    rid, verifier = authorize(c, reg["client_id"])
    redirect = hit("POST", "/api/v1/oauth/requests/{rid}", lambda: bo.post(f"/api/v1/oauth/requests/{rid}", json={"allow": True})).json()["redirect"]
    tok = c.post("/oauth/token", data={"grant_type": "authorization_code", "code": parse_qs(urlsplit(redirect).query)["code"][0],
                                       "client_id": reg["client_id"], "redirect_uri": "http://localhost:9/cb", "code_verifier": verifier}).json()
    hit("POST", "/oauth/revoke", lambda: c.post("/oauth/revoke", data={"token": tok["refresh_token"]}))
    hit("POST", "/auth/logout", lambda: bo.post("/auth/logout"))
    # library data: the journal
    h = {"X-Litledger-Project": "p1"}
    hit("POST", "/api/v1/tools/{name}", lambda: c.post("/api/v1/tools/tag", json={"action": "define", "tags": ["x"]}, headers=h))
    made = hit("POST", "/api/v1/maps", lambda: c.post("/api/v1/maps", json={"title": "m", "ops": [{"op": "add", "text": "a"}]}, headers=h)).json()
    hit("DELETE", "/api/v1/maps/{map_id:path}", lambda: c.delete(f"/api/v1/maps/{made['map']}", headers=h))
    bib = b"@article{a, title={Attention Is All You Need}, author={Vaswani, Ashish}, year={2017}, eprint={1706.03762}, archivePrefix={arXiv}}\n"
    hit("POST", "/api/v1/uploads", lambda: c.post("/api/v1/uploads", files={"file": ("refs.bib", bib)}, headers=h))
    mcp = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json", **h}
    hit("POST", "/mcp", lambda: c.post("/mcp", headers=mcp, json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                                                  "params": {"name": "tag", "arguments": {"action": "define", "tags": ["y"]}}}))

    for route, op in {**AUDITED, **AUDITED_GETS}.items():
        assert route in seen, f"not exercised: {route}"
        assert op in seen[route][0], (route, op, seen[route][0])
    for route in JOURNALED:
        assert seen[route][1], f"{route} wrote no journal entry"
    assert not any(x["op"].startswith(("config", "work", "map", "tag", "project")) for x in rows(lg)), "library ops stay in the journal"


# ------------------------------------------------------------------------------------------------ the guard and atomicity
def test_the_guard_refuses_an_unrecorded_write_and_internal_says_why(srv):
    lg, app, c = srv
    with pytest.raises(RuntimeError, match="no journal entry or audit row"):
        with lg.db.tx(SYSTEM) as tx:
            tx.execute("INSERT INTO projects(id,title,created_at) VALUES('sneaky','s','x')")
    with lg.db.read() as conn:
        assert not conn.execute("SELECT 1 FROM projects WHERE id='sneaky'").fetchone()  # rolled back
    with lg.db.tx(SYSTEM, internal="test: an explicit reason") as tx:
        tx.execute("INSERT INTO projects(id,title,created_at) VALUES('ok','o','x')")
    with pytest.raises(TypeError):
        lg.db.write()  # a bare write must say why it needs no journal or audit row
    dbmod.STRICT = False  # production never checks
    try:
        with lg.db.tx(SYSTEM) as tx:
            tx.execute("INSERT INTO projects(id,title,created_at) VALUES('prod','p','x')")
    finally:
        dbmod.STRICT = True


def test_no_change_no_entry_and_no_entry_no_change(srv, monkeypatch):
    lg, app, c = srv
    root = admin_browser(app, c.token)
    signup_browser(app, "bo@example.com", "bo password")
    before = top(lg, "audit")
    assert root.patch("/api/v1/principals/admin", json={"role": "member"}).status_code == 400  # refused: nothing recorded
    assert root.patch("/api/v1/principals/bo", json={"role": "member"}).status_code == 200  # no change: nothing recorded
    assert top(lg, "audit") == before
    monkeypatch.setattr(auth, "revoke_kinds", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        accounts.set_disabled(lg.db, SYSTEM, "bo", True)
    assert top(lg, "audit") == before and not accounts.person_by_email(lg.db, "bo@example.com")["disabled"]  # all or nothing


# ------------------------------------------------------------------------------------------------ new events
def test_failed_sign_ins_fold_and_limits_are_recorded(srv):
    lg, app, c = srv
    signup_browser(app, "bo@example.com", "bo password")
    b = browser(app)
    for _ in range(4):
        b.post("/auth/password", json={"email": "bo@example.com", "password": "nope"})
    b.post("/auth/password", json={"email": "eve@example.com", "password": "nope"})
    failed = [r for r in rows(lg) if r["op"] == "signin.failed"]
    assert [(r["target"], r["count"]) for r in failed] == [("bo@example.com", 4), ("eve@example.com", 1)]
    assert failed[0]["subject_id"] and failed[0]["last_at"] and failed[1]["subject_id"] is None
    for _ in range(8):  # the 11th try from this address is refused; more refusals count on one row
        b.post("/auth/password", json={"email": "bo@example.com", "password": "nope"})
    limited = [r for r in rows(lg) if r["op"] == "limit.hit"]
    assert len(limited) == 1 and limited[0]["count"] == 3 and limited[0]["detail"]["what"] == "signin"
    assert browser(app).post("/auth/token", json={"token": "ll_wrong"}).status_code == 401
    assert rows(lg)[-1]["op"] == "signin.failed" and rows(lg)[-1]["target"] == "admin token"


def test_google_github_mismatches_are_recorded(srv, monkeypatch):
    lg, app, c = srv
    lg.set_config(SYSTEM, {"GITHUB_CLIENT_ID": "id", "GITHUB_CLIENT_SECRET": "secret"})
    signup_browser(app, "victim@uni.edu", "mal password", "mal")
    r = provider_round_trip(browser(app), monkeypatch, ("8", "victim@uni.edu", "victim"))
    assert r.headers["location"].startswith("/?error=email_taken")
    assert rows(lg)[-1]["op"] == "signin.failed" and rows(lg)[-1]["detail"]["reason"] == "an unconfirmed account has this email"
    r = browser(app).get("/auth/github/start", follow_redirects=False)
    state = parse_qs(urlsplit(r.headers["location"]).query)["state"][0]
    browser(app).get("/auth/github/callback", params={"code": "c", "state": state}, follow_redirects=False)  # wrong browser
    assert rows(lg)[-1]["detail"]["reason"] == "state mismatch"
    lg.set_config(SYSTEM, {"SIGNUP": "invited"})
    provider_round_trip(browser(app), monkeypatch, ("9", "eve@example.com", "eve"))
    assert rows(lg)[-1]["detail"]["reason"] == "no account" and rows(lg)[-1]["target"] == "eve@example.com"


def test_a_reused_refresh_token_revokes_the_grant_and_is_recorded(srv):
    lg, app, c = srv
    ada = signup_browser(app)
    reg = c.post("/oauth/register", json={"redirect_uris": ["http://localhost/cb"], "client_name": "Tool"}).json()
    rid, verifier = authorize(c, reg["client_id"])
    code = parse_qs(urlsplit(ada.post(f"/api/v1/oauth/requests/{rid}", json={"allow": True}).json()["redirect"]).query)["code"][0]
    first = c.post("/oauth/token", data={"grant_type": "authorization_code", "code": code, "client_id": reg["client_id"],
                                         "redirect_uri": "http://localhost:9/cb", "code_verifier": verifier}).json()
    second = c.post("/oauth/token", data={"grant_type": "refresh_token", "refresh_token": first["refresh_token"],
                                          "client_id": reg["client_id"]}).json()
    assert "access_token" in second and not any(r["op"] == "app.token_reuse" for r in rows(lg))  # rotation: not recorded
    r = c.post("/oauth/token", data={"grant_type": "refresh_token", "refresh_token": first["refresh_token"], "client_id": reg["client_id"]})
    assert r.json()["error"] == "invalid_grant"
    theft = rows(lg)[-1]
    assert theft["op"] == "app.token_reuse" and theft["detail"]["revoked"] == 3 and theft["target"] == "tool-ada"
    assert c.post("/mcp", json={}, headers={"Authorization": f"Bearer {second['access_token']}"}).status_code == 401
    assert accounts.audit_log(lg.db, problems=True)[0]["level"] == "failure"
    # a token of a grant that already ended (here: just revoked for theft; also: disconnected) is refused, not an alarm
    c.post("/oauth/token", data={"grant_type": "refresh_token", "refresh_token": second["refresh_token"], "client_id": reg["client_id"]})
    assert [r["op"] for r in rows(lg)].count("app.token_reuse") == 1


def test_declining_things_and_registering_apps_are_recorded(srv):
    lg, app, c = srv
    ada = signup_browser(app)
    reg = c.post("/oauth/register", json={"redirect_uris": ["http://localhost/cb"], "client_name": "Tool"}).json()
    assert rows(lg)[-1]["op"] == "app.register" and rows(lg)[-1]["detail"]["client_id"] == reg["client_id"]
    rid, _ = authorize(c, reg["client_id"])
    ada.post(f"/api/v1/oauth/requests/{rid}", json={"allow": False})
    assert rows(lg)[-1]["op"] == "app.deny" and rows(lg)[-1]["target"] == "Tool"
    start = c.post("/api/v1/login/start", json={"name": "box"}, headers={"Authorization": ""}).json()
    ada.delete(f"/api/v1/logins/{start['code']}")
    assert rows(lg)[-1]["op"] == "terminal.deny" and rows(lg)[-1]["target"] == "box"


def test_forgot_password_is_recorded_whether_or_not_the_account_exists(srv):
    lg, app, c = srv
    signup_browser(app, "bo@example.com")
    a = browser(app).post("/auth/forgot", json={"email": "bo@example.com"}).json()
    b = browser(app).post("/auth/forgot", json={"email": "nobody@example.com"}).json()
    assert a["sent"] is b["sent"] is False  # the same answer (no SMTP here)
    req = [r for r in rows(lg) if r["op"] == "password.reset_requested"]
    assert [(r["target"], r["detail"]["known"], r["detail"]["sent"]) for r in req] == [
        ("bo@example.com", True, False), ("nobody@example.com", False, False)]


def test_own_sessions_rename_and_sign_out_are_recorded(srv):
    lg, app, c = srv
    bo = signup_browser(app, "bo@example.com", "bo password")
    laptop = browser(app)
    laptop.post("/auth/password", json={"email": "bo@example.com", "password": "bo password"})
    sid = next(s["id"] for s in bo.get("/api/v1/me/sessions").json() if not s["current"])
    bo.delete(f"/api/v1/me/sessions/{sid}")
    assert rows(lg)[-1]["op"] == "session.end" and laptop.get("/api/v1/me").status_code == 401
    bo.patch("/api/v1/me/account", json={"name": "bobby"})
    assert (rows(lg)[-1]["op"], rows(lg)[-1]["target"], rows(lg)[-1]["detail"]["old"]) == ("account.rename", "bobby", "bo")
    bo.post("/auth/logout")
    assert rows(lg)[-1]["op"] == "signout" and rows(lg)[-1]["actor_id"] == rows(lg)[-1]["subject_id"]


def test_shell_actions_are_recorded_as_the_shell(srv, monkeypatch, capsys):
    lg, app, c = srv
    signup_browser(app, "bo@example.com")
    monkeypatch.setenv("LITLEDGER_DATA", str(lg.settings.data_dir))
    root = admin_browser(app, c.token)
    root.post("/api/v1/principals", json={"name": "ci-bot"})
    cli.main(["token", "revoke", "ci-bot"])
    cli.main(["admin", "promote", "bo@example.com"])
    cli.main(["reset-password", "bo@example.com"])
    shell = [(r["op"], r["target"]) for r in rows(lg) if r["detail"].get("via") == "cli"]
    assert shell == [("token.revoke", "ci-bot"), ("role.change", "bo"), ("password.reset_link", "bo")]
    assert all(r["actor_id"] is None for r in rows(lg) if r["detail"].get("via") == "cli")


def test_audit_log_filters(srv):
    lg, app, c = srv
    root = admin_browser(app, c.token)
    bo = signup_browser(app, "bo@example.com", "bo password")
    token = bo.post("/api/v1/me/tokens", json={"name": "ci"}).json()["token"]
    browser(app).post("/auth/password", json={"email": "bo@example.com", "password": "nope"})
    assert bo.get("/api/v1/audit").status_code == 403
    root.patch("/api/v1/principals/bo", json={"role": "admin"})
    assert c.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    mine = root.get("/api/v1/audit", params={"person": "bo"}).json()
    assert {r["op"] for r in mine} >= {"account.join", "token.create", "signin.failed", "role.change"}
    assert all(r["actor"] in ("bo", "admin", None) or r["subject"] == "bo" for r in mine)
    signins = {r["op"] for r in root.get("/api/v1/audit", params={"kind": "signin"}).json()}
    assert {"signin", "signin.failed"} <= signins <= {"signin", "signin.failed", "signin.refused", "signout", "session.end",
                                                     "sessions.end", "limit.hit"}
    assert {r["op"] for r in root.get("/api/v1/audit", params={"kind": "tokens"}).json()} == {"token.create"}
    problems = root.get("/api/v1/audit", params={"problems": True}).json()
    assert {r["op"] for r in problems} == {"signin.failed", "role.change"}
    assert {r["level"] for r in problems} == {"failure", "security"}


def test_the_audit_row_carries_the_request(srv):
    lg, app, c = srv
    b = browser(app)
    b.headers["User-Agent"] = "Mozilla/5.0 test"
    b.post("/auth/signup", json={"name": "bo", "email": "bo@example.com", "password": "bo password"})
    row = rows(lg)[-1]
    assert row["op"] == "account.join" and row["ua"] == "Mozilla/5.0 test" and row["ip"] and row["actor_id"] == row["subject_id"]
    ctx = Ctx(via="startup")
    with lg.db.tx(SYSTEM, ctx) as tx:
        tx.audit("x.test", "t")
    assert rows(lg)[-1]["detail"] == {"via": "startup"}


def test_the_audit_page_names_exactly_the_ops_the_server_writes():
    """Admin → Audit log has a verb for every op an action records, and none for ops nothing writes."""
    import re
    from pathlib import Path
    root = Path(__file__).parent.parent
    code = "\n".join(p.read_text(encoding="utf-8") for p in (root / "src" / "litledger").rglob("*.py"))
    written = set(re.findall(r'tx\.audit\(\s*"([a-z_.]+)"', code))
    written |= set(re.findall(r'(?:accounts\.)?record\(\s*\w+(?:\.\w+)?,\s*\w+,\s*"([a-z_.]+)"', code))
    # ops chosen at run time (tx.audit(op, ...)): remove(), create_token() and set_config()/set_source()
    written |= {"person.remove", "app.disconnect", "token.revoke", "token.rotate", "token.create", "terminal.approve",
                "settings.change", "source.add", "source.remove"}
    page = (root / "web" / "src" / "pages" / "admin" / "Audit.tsx").read_text(encoding="utf-8")
    block = page[page.index("const WHAT"):page.index("};", page.index("const WHAT"))]
    named = set(re.findall(r'"([a-z_]+(?:\.[a-z_]+)?)":', block))
    assert named == written, (sorted(named - written), sorted(written - named))
