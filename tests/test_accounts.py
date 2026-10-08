"""Accounts (docs/ACCESS.md): the root admin's token sign-in, creating an account, password sign-in, resets,
invitations, Google/GitHub sign-up and linking, roles and what admins may do to others, tokens and apps people own,
disabling, attribution, the audit log, and the rule that every admin endpoint refuses members and agents."""
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from litledger import accounts, auth, cli, signin
from litledger.app import create_app
from litledger.db import SYSTEM

from conftest import BASE, admin_browser, browser, invited_browser, make_ledger, signup_browser


@pytest.fixture
def srv(tmp_path):
    lg = make_ledger(tmp_path)
    app = create_app(ledger=lg)
    with TestClient(app, base_url=BASE) as c:  # c: the root admin's token (/data/tokens/admin)
        c.token = (lg.settings.token_dir / "admin").read_text().strip()
        c.headers["Authorization"] = "Bearer " + c.token
        yield lg, app, c


@pytest.fixture
def team(srv):
    """ada signed up and the root admin made her an admin; bo is a member she invited."""
    lg, app, c = srv
    ada = signup_browser(app)
    admin_browser(app, c.token).patch("/api/v1/principals/ada", json={"role": "admin"}).raise_for_status()
    bo = invited_browser(app, ada, "bo@example.com")
    return lg, app, c, ada, bo


def mail_box(monkeypatch, lg):
    lg.set_config(SYSTEM, {"SMTP_HOST": "smtp.example.com"})
    sent = []
    monkeypatch.setattr(signin, "send_mail", lambda lg, to, subject, body: sent.append((to, subject, body)) or True)
    return sent


def link_token(text: str) -> str:
    url = next(w for w in text.split() if "?t=" in w)
    return parse_qs(urlsplit(url).query)["t"][0]


# ------------------------------------------------------------------------------------------------ the admin, new accounts
def test_the_admin_signs_in_with_the_token(srv):
    lg, app, c = srv
    assert TestClient(app, base_url=BASE).post("/auth/token", json={"token": c.token}).status_code == 403  # CSRF
    assert browser(app).post("/auth/token", json={"token": "ll_nope"}).json() == {"detail": "That token isn't valid."}
    ci = accounts.create_token(lg.db, "ci", admin=True).token  # a full-access shared agent is still not a person
    assert browser(app).post("/auth/token", json={"token": ci}).status_code == 401
    root = browser(app)
    r = root.post("/auth/token", json={"token": c.token})
    assert r.status_code == 200 and r.json() == {"name": "admin"} and c.token not in str(r.cookies)
    assert root.get("/api/v1/me").json() | {"warnings": []} == {"name": "admin", "kind": "human", "role": "admin",
                                                                "admin": True, "project": "default", "warnings": []}
    acct = root.get("/api/v1/me/account").json()
    assert acct["root"] and acct["email"] is None and acct["email_change"] == "none" and not acct["has_password"]
    for change in ({"name": "boss"}, {"email": "a@example.com"}):
        assert root.patch("/api/v1/me/account", json=change).status_code == 400
    assert root.put("/api/v1/me/password", json={"new": "a password"}).status_code == 400
    # nobody can demote, disable or remove it, or reset it, or limit it: there is always this admin
    ada = signup_browser(app)
    root.patch("/api/v1/principals/ada", json={"role": "admin"}).raise_for_status()
    for change in ({"role": "member"}, {"disabled": True}, {"projects": ["x"]}):
        assert ada.patch("/api/v1/principals/admin", json=change).status_code == 400, change
    assert ada.delete("/api/v1/principals/admin").status_code == 400
    assert ada.post("/api/v1/principals/admin/reset-link").status_code == 400
    with pytest.raises(ValueError):
        accounts.remove(lg.db, SYSTEM, "admin")  # what `litledger token revoke admin` would do
    assert accounts.bootstrap(lg.db, lg.settings.token_dir) is None  # it exists, so nothing new is made


def test_people_create_their_own_accounts(srv, monkeypatch):
    lg, app, c = srv
    m = browser(app).get("/auth/methods").json()
    assert m["signup"] == "anyone" and m["password_signup"] is True  # open out of the box
    b = browser(app)
    assert b.post("/auth/signup", json={"email": "ada@example.com", "password": "short"}).status_code == 400
    r = b.post("/auth/signup", json={"name": "Ada", "email": "Ada@Example.com", "password": "correct horse"})
    assert r.status_code == 200 and r.json() == {"name": "ada"}
    assert b.get("/api/v1/me").json()["role"] == "member" and not b.get("/api/v1/me/account").json()["email_verified"]
    taken = browser(app).post("/auth/signup", json={"email": "ada@example.com", "password": "another one"})
    assert taken.status_code == 400 and taken.json()["detail"] == accounts.TAKEN and "your" not in accounts.TAKEN
    # invite only: no sign-up; allowed domains without email: no password sign-up (the address can't be checked)
    lg.set_config(SYSTEM, {"SIGNUP": "invited"})
    assert browser(app).post("/auth/signup", json={"email": "bo@example.com", "password": "bo password"}).status_code == 403
    lg.set_config(SYSTEM, {"SIGNUP": "domains", "SIGNUP_DOMAINS": "uni.edu"})
    assert browser(app).get("/auth/methods").json()["password_signup"] is False
    assert browser(app).post("/auth/signup", json={"email": "bo@uni.edu", "password": "bo password"}).status_code == 403
    # with email set up, a domain sign-up makes the account when the emailed link is opened
    sent = mail_box(monkeypatch, lg)
    assert browser(app).post("/auth/signup", json={"email": "eve@gmail.com", "password": "eve password"}).status_code == 403
    assert browser(app).post("/auth/signup", json={"name": "bo", "email": "bo@uni.edu", "password": "bo password"}).json() == {"confirm": "bo@uni.edu"}
    assert accounts.person_by_email(lg.db, "bo@uni.edu") is None
    bo = browser(app)
    r = bo.get("/auth/signup/confirm", params={"t": link_token(sent[-1][2])}, follow_redirects=False)
    assert r.headers["location"] == "/" and bo.get("/api/v1/me/account").json()["email_verified"] is True
    # with SIGNUP=anyone and email set up, the new address gets a confirmation link
    lg.set_config(SYSTEM, {"SIGNUP": "anyone"})
    cy = signup_browser(app, "cy@example.com")
    assert sent[-1][0] == "cy@example.com" and not cy.get("/api/v1/me/account").json()["email_verified"]
    # an unconfirmed account's link works only where that account is signed in: a stranger with the address resets
    stranger = browser(app).get("/auth/email/confirm", params={"t": link_token(sent[-1][2])})
    assert stranger.status_code == 403 and "Sign in" in stranger.text
    assert not cy.get("/api/v1/me/account").json()["email_verified"]
    cy.get("/auth/email/confirm", params={"t": link_token(sent[-1][2])})
    assert cy.get("/api/v1/me/account").json()["email_verified"] is True


def test_the_admin_promotes_someone_who_then_signs_in_as_an_admin(srv):
    lg, app, c = srv
    signup_browser(app, "bo@example.com", "bo password")
    root = admin_browser(app, c.token)
    assert root.patch("/api/v1/principals/bo", json={"role": "admin"}).status_code == 200
    bo = browser(app)
    bo.post("/auth/password", json={"email": "bo@example.com", "password": "bo password"}).raise_for_status()
    assert bo.get("/api/v1/me").json()["admin"] is True and bo.get("/api/v1/principals").status_code == 200


def test_password_sign_in_is_generic_and_slowed(team):
    lg, app, c, ada, bo = team
    stranger = browser(app)
    wrong = stranger.post("/auth/password", json={"email": "ada@example.com", "password": "wrong one!"})
    nobody = stranger.post("/auth/password", json={"email": "nobody@example.com", "password": "wrong one!"})
    assert wrong.status_code == nobody.status_code == 401 and wrong.json() == nobody.json()  # no account enumeration
    r = stranger.post("/auth/password", json={"email": "ADA@example.com", "password": "correct horse"})
    assert r.status_code == 200 and stranger.get("/api/v1/me").json()["name"] == "ada"


def test_too_many_wrong_passwords_are_slowed(srv):
    lg, app, c = srv
    codes = [browser(app).post("/auth/password", json={"email": "x@example.com", "password": "nope"}).status_code for _ in range(11)]
    assert codes[:10] == [401] * 10 and codes[10] == 429


def test_changing_the_password_signs_out_other_sessions(team):
    lg, app, c, ada, bo = team
    laptop = browser(app)
    laptop.post("/auth/password", json={"email": "bo@example.com", "password": "member pass 1"}).raise_for_status()
    assert len(bo.get("/api/v1/me/sessions").json()) == 2
    assert bo.put("/api/v1/me/password", json={"current": "nope", "new": "brand new pass"}).status_code == 400
    assert bo.put("/api/v1/me/password", json={"current": "member pass 1", "new": "brand new pass"}).json() == {"ok": True}
    assert laptop.get("/api/v1/me").status_code == 401 and bo.get("/api/v1/me").status_code == 200
    assert auth.password_ok(accounts.get(lg.db, accounts.person_by_email(lg.db, "bo@example.com")["id"])["password_hash"],
                            "brand new pass")
    # ending other sessions by hand
    browser(app).post("/auth/password", json={"email": "bo@example.com", "password": "brand new pass"})
    sessions = bo.get("/api/v1/me/sessions").json()
    assert len(sessions) == 2 and sum(s["current"] for s in sessions) == 1
    assert bo.delete("/api/v1/me/sessions").json() == {"ended": 1}


def test_forgot_password_without_email_points_to_an_admin(team):
    lg, app, c, ada, bo = team
    a = browser(app).post("/auth/forgot", json={"email": "bo@example.com"}).json()
    b = browser(app).post("/auth/forgot", json={"email": "nobody@example.com"}).json()
    assert a["sent"] is False and a["command"] == "litledger reset-password bo@example.com"
    assert b == {"sent": False, "command": "litledger reset-password nobody@example.com"}  # same answer either way


def test_forgot_password_with_email_and_the_reset_link(team, monkeypatch):
    lg, app, c, ada, bo = team
    sent = mail_box(monkeypatch, lg)
    assert browser(app).post("/auth/forgot", json={"email": "nobody@example.com"}).json() == {"sent": True}
    assert browser(app).post("/auth/forgot", json={"email": "bo@example.com"}).json() == {"sent": True}
    assert [s[0] for s in sent] == ["bo@example.com"]
    t = link_token(sent[0][2])
    fresh = browser(app)
    assert fresh.get("/auth/reset", params={"t": t}).json() == {"name": "bo"}
    assert fresh.post("/auth/reset", json={"token": t, "password": "short"}).status_code == 400
    assert fresh.post("/auth/reset", json={"token": t, "password": "reset and long"}).status_code == 200
    assert fresh.get("/api/v1/me").json()["name"] == "bo"
    assert bo.get("/api/v1/me").status_code == 401  # every older session ended
    assert fresh.post("/auth/reset", json={"token": t, "password": "again and again"}).status_code == 404  # one use
    assert fresh.get("/auth/reset", params={"t": t}).status_code == 404


def test_admin_reset_link_and_recovery_cli(team, monkeypatch, capsys):
    lg, app, c, ada, bo = team
    assert bo.post("/api/v1/principals/ada/reset-link").status_code == 403
    out = ada.post("/api/v1/principals/bo/reset-link").json()
    assert out["minutes"] == accounts.RESET_MINUTES and "/reset?t=" in out["link"]
    assert browser(app).post("/auth/reset", json={"token": link_token(out["link"]), "password": "handed over"}).status_code == 200
    monkeypatch.setenv("LITLEDGER_DATA", str(lg.settings.data_dir))
    cli.main(["reset-password", "bo@example.com"])
    assert browser(app).post("/auth/reset", json={"token": link_token(capsys.readouterr().out), "password": "from the cli"}).status_code == 200
    cli.main(["admin", "promote", "bo@example.com"])
    assert bo.get("/api/v1/me").status_code == 401  # the reset signed bo out; sign in again as an admin
    bo.post("/auth/password", json={"email": "bo@example.com", "password": "from the cli"}).raise_for_status()
    assert bo.get("/api/v1/me").json()["role"] == "admin"
    assert {p["name"]: p["role"] for p in accounts.people(lg.db)} == {"admin": "admin", "ada": "admin", "bo": "admin"}
    cli.main(["token", "create", "admin", "--admin"])  # re-issues the admin's token and its file
    fresh = capsys.readouterr().out.strip().splitlines()[-1]
    assert (lg.settings.token_dir / "admin").read_text().strip() == fresh != c.token
    assert c.get("/api/v1/me").status_code == 401 and admin_browser(app, fresh).get("/api/v1/me").json()["name"] == "admin"


# ------------------------------------------------------------------------------------------------ invitations
def test_invitations(srv, monkeypatch):
    lg, app, c = srv
    ada = admin_browser(app, c.token)  # the root admin invites
    made = ada.post("/api/v1/invites", json={"email": "Cy@Example.com", "role": "admin", "projects": ["thesis"]}).json()
    assert made["sent"] is False and made["days"] == accounts.INVITE_DAYS  # no SMTP: the admin passes the link on
    t = link_token(made["link"])
    assert browser(app).get("/auth/invite", params={"t": t}).json()["email"] == "cy@example.com"
    assert [i["email"] for i in ada.get("/api/v1/invites").json()] == ["cy@example.com"]
    again = link_token(ada.post("/api/v1/invites", json={"email": "cy@example.com"}).json()["link"])
    assert browser(app).get("/auth/invite", params={"t": t}).status_code == 404  # a new invite replaces the old one
    cy = browser(app)
    r = cy.post("/auth/invite", json={"token": again, "name": "Cy", "password": "cy password"})
    assert r.status_code == 200 and cy.get("/api/v1/me").json()["role"] == "member"
    assert browser(app).post("/auth/invite", json={"token": again, "password": "12345678"}).status_code == 404  # single use
    assert ada.post("/api/v1/invites", json={"email": "cy@example.com"}).status_code == 400  # already has an account
    assert ada.post("/api/v1/invites", json={"email": "dee@example.com", "role": "owner"}).status_code == 400
    # revoked and expired invites stop working; with SMTP the link is emailed
    dead = link_token(ada.post("/api/v1/invites", json={"email": "dee@example.com"}).json()["link"])
    ada.delete(f"/api/v1/invites/{ada.get('/api/v1/invites').json()[0]['id']}").raise_for_status()
    assert browser(app).get("/auth/invite", params={"t": dead}).status_code == 404
    sent = mail_box(monkeypatch, lg)
    late = link_token(ada.post("/api/v1/invites", json={"email": "eve@example.com"}).json()["link"])
    assert sent[0][0] == "eve@example.com" and late in sent[0][2]
    with lg.db.write("test setup") as conn:
        conn.execute("UPDATE credentials SET expires_at='2000-01-01T00:00:00+00:00' WHERE kind='invite'")
    assert browser(app).post("/auth/invite", json={"token": late, "password": "12345678"}).status_code == 404


# ------------------------------------------------------------------------------------------------ the permission matrix
ADMIN_ONLY = [
    ("put", "/api/v1/config", {"values": {"CONTACT_EMAIL": "x@example.com"}}),
    ("post", "/api/v1/config/test/arxiv", None),
    ("post", "/api/v1/sources/arxiv", {"action": "remove"}),
    ("get", "/api/v1/backups", None),
    ("post", "/api/v1/backups", None),
    ("get", "/api/v1/backups/litledger-x.tar.gz", None),
    ("get", "/api/v1/principals", None),
    ("post", "/api/v1/principals", {"name": "ci"}),
    ("patch", "/api/v1/principals/ada", {"role": "member"}),
    ("patch", "/api/v1/principals/ada", {"disabled": True}),
    ("patch", "/api/v1/principals/ada", {"email": "mine@example.com"}),
    ("delete", "/api/v1/principals/ada", None),
    ("post", "/api/v1/principals/ada/reset-link", None),
    ("post", "/api/v1/principals/ada/sign-out", None),
    ("get", "/api/v1/invites", None),
    ("post", "/api/v1/invites", {"email": "x@example.com"}),
    ("delete", "/api/v1/invites/0123456789abcdef", None),
    ("get", "/api/v1/audit", None),
    ("post", "/api/v1/mail/test", None),
]


def test_members_and_agents_get_403_on_every_admin_endpoint(team):
    lg, app, c, ada, bo = team
    agent = {"Authorization": "Bearer " + accounts.create_token(lg.db, "bot").token}
    bo_token = {"Authorization": "Bearer " + bo.post("/api/v1/me/tokens", json={"name": "ci"}).json()["token"]}
    for method, path, body in ADMIN_ONLY:
        kw = {"json": body} if body is not None else {}
        assert getattr(bo, method)(path, **kw).status_code == 403, (method, path)
        assert getattr(c, method)(path, headers=agent, **kw).status_code == 403, (method, path, "agent")
        assert getattr(c, method)(path, headers=bo_token, **kw).status_code == 403, (method, path, "member token")
    for path in ("/api/v1/principals", "/api/v1/invites", "/api/v1/audit", "/api/v1/backups"):
        assert ada.get(path).status_code == 200 and c.get(path).status_code == 200
    me = bo.get("/api/v1/me").json()
    assert me["role"] == "member" and me["admin"] is False and me["warnings"] == []
    view = bo.get("/api/v1/config").json()  # members read settings like agents: secrets and personal values hidden
    assert view["editable"] is False


def test_roles(team):
    lg, app, c, ada, bo = team
    cy = invited_browser(app, ada, "cy@example.com", role="admin")
    assert cy.patch("/api/v1/principals/bo", json={"role": "admin"}).status_code == 200
    assert bo.get("/api/v1/me").json()["admin"] is True  # applies at once
    assert cy.patch("/api/v1/principals/bo", json={"role": "member"}).status_code == 200
    assert cy.patch("/api/v1/principals/bo", json={"role": "owner"}).status_code == 400  # two roles only
    assert cy.patch("/api/v1/principals/ada", json={"role": "member"}).status_code == 200  # admins change admins
    assert ada.get("/api/v1/principals").status_code == 403
    assert {p["name"]: (p["role"], p["root"]) for p in cy.get("/api/v1/principals").json()["people"]} == {
        "admin": ("admin", True), "ada": ("member", False), "bo": ("member", False), "cy": ("admin", False)}
    assert cy.patch("/api/v1/principals/cy", json={"role": "member"}).status_code == 200  # the token admin is still there
    # a database without the token admin: the last admin can't demote themselves away
    with lg.db.write("test setup") as conn:
        conn.execute("UPDATE principals SET origin=NULL, role='member' WHERE name='admin'")
        conn.execute("UPDATE principals SET role='admin' WHERE name='ada'")
    assert ada.patch("/api/v1/principals/ada", json={"role": "member"}).status_code == 400


def test_disabling_signs_out_and_pauses_tokens_and_apps(team):
    lg, app, c, ada, bo = team
    token = bo.post("/api/v1/me/tokens", json={"name": "laptop"}).json()["token"]
    bearer = {"Authorization": f"Bearer {token}"}
    assert c.get("/api/v1/me", headers=bearer).json()["name"] == "laptop-bo"
    assert ada.patch("/api/v1/principals/bo", json={"disabled": True}).status_code == 200
    assert bo.get("/api/v1/me").status_code == 401 and c.get("/api/v1/me", headers=bearer).status_code == 401
    r = browser(app).post("/auth/password", json={"email": "bo@example.com", "password": "member pass 1"})
    assert r.status_code == 403 and "disabled" in r.json()["detail"]
    assert ada.patch("/api/v1/principals/ada", json={"disabled": True}).status_code == 400  # not yourself
    ada.patch("/api/v1/principals/bo", json={"disabled": False}).raise_for_status()
    assert c.get("/api/v1/me", headers=bearer).status_code == 200  # the token was paused, not revoked
    bo.post("/auth/password", json={"email": "bo@example.com", "password": "member pass 1"}).raise_for_status()
    # removing is for good, and takes the person's agents along; the email can be invited again
    assert ada.delete("/api/v1/principals/bo").json() == {"removed": "bo"}
    assert c.get("/api/v1/me", headers=bearer).status_code == 401 and bo.get("/api/v1/me").status_code == 401
    bo2 = invited_browser(app, ada, "bo@example.com", name="bo")
    assert bo2.get("/api/v1/me").json()["name"] == "bo-2"  # names are never reused: history keeps pointing right


# ------------------------------------------------------------------------------------------------ tokens, apps, terminals
def test_personal_tokens(team):
    lg, app, c, ada, bo = team
    made = bo.post("/api/v1/me/tokens", json={"name": "CI runner", "expires": "30", "projects": ["thesis"]}).json()
    assert made["name"] == "ci-runner-bo" and made["token"].startswith("ll_")
    assert bo.post("/api/v1/me/tokens", json={"name": "x", "access": "full"}).status_code == 403
    assert bo.post("/api/v1/me/tokens", json={"name": "x", "expires": "1000"}).status_code == 400
    mine = bo.get("/api/v1/me/agents").json()
    assert [(a["label"], a["by"], a["access"], a["projects"]) for a in mine] == [("ci-runner", "ci-runner via bo", "library", ["thesis"])]
    assert mine[0]["expires_at"] and not mine[0]["expired"]
    h = {"Authorization": f"Bearer {made['token']}", "X-Litledger-Project": "thesis"}
    assert c.get("/api/v1/me", headers=h).json() | {"warnings": []} == {
        "name": "ci-runner-bo", "kind": "agent", "role": "member", "admin": False, "project": "thesis", "warnings": []}
    assert c.get("/api/v1/me", headers={**h, "X-Litledger-Project": "other"}).status_code == 403
    # other members can't touch it, its owner and admins can
    cy = invited_browser(app, ada, "cy@example.com")
    assert cy.delete("/api/v1/principals/ci-runner-bo").status_code == 403
    assert bo.patch("/api/v1/principals/ci-runner-bo", json={"projects": []}).status_code == 200
    assert bo.delete("/api/v1/principals/ci-runner-bo").json() == {"removed": "ci-runner-bo"}
    assert c.get("/api/v1/me", headers=h).status_code == 401
    # an expired token stops working
    old = bo.post("/api/v1/me/tokens", json={"name": "old"}).json()["token"]
    with lg.db.write("test setup") as conn:
        conn.execute("UPDATE principals SET expires_at='2000-01-01T00:00:00+00:00' WHERE name='old-bo'")
    assert c.get("/api/v1/me", headers={"Authorization": f"Bearer {old}"}).status_code == 401


def test_an_agent_never_outranks_its_owner(team):
    lg, app, c, ada, bo = team
    full = {"Authorization": "Bearer " + ada.post("/api/v1/me/tokens", json={"name": "ops", "access": "full"}).json()["token"]}
    assert c.put("/api/v1/config", json={"values": {"MAX_UPLOAD_MB": "50"}}, headers=full).status_code == 200
    cy = invited_browser(app, ada, "cy@example.com", role="admin")
    cy_full = {"Authorization": "Bearer " + cy.post("/api/v1/me/tokens", json={"name": "ops", "access": "full"}).json()["token"]}
    assert c.get("/api/v1/principals", headers=cy_full).status_code == 200
    ada.patch("/api/v1/principals/cy", json={"role": "member"}).raise_for_status()
    assert c.get("/api/v1/principals", headers=cy_full).status_code == 403  # cy is a member now, so is their token
    assert c.get("/api/v1/me", headers=cy_full).json()["role"] == "member"


def test_terminal_sign_in_is_owned_by_whoever_approves(team):
    lg, app, c, ada, bo = team
    anon = {"Authorization": ""}
    start = c.post("/api/v1/login/start", json={"name": "Laptop"}, headers=anon).json()
    assert start["approve_path"] == f"/settings/apps?code={start['code']}"
    assert c.get("/api/v1/logins", headers={"Authorization": "Bearer " + accounts.create_token(lg.db, "bot").token}).status_code == 403
    assert [p["code"] for p in bo.get("/api/v1/logins").json()] == [start["code"]]
    assert bo.post(f"/api/v1/logins/{start['code']}", json={}).json() == {"name": "laptop-bo"}
    token = c.get(f"/api/v1/login/poll/{start['poll']}", headers=anon).json()["token"]
    h = {"Authorization": f"Bearer {token}", "X-Litledger-Project": "p1"}
    assert c.post("/api/v1/tools/tag", json={"action": "define", "tags": ["x"]}, headers=h).status_code == 200
    entry = next(e for e in c.get("/api/v1/journal", headers={"X-Litledger-Project": "p1"}).json() if e["principal"] == "laptop-bo")
    assert entry["by"] == "laptop via bo"  # attribution: the agent and the person it acts for
    assert [a["origin"] for a in bo.get("/api/v1/me/agents").json()] == ["login"]
    # the admin token in the container approves too (`litledger approve`): that terminal is the admin's
    start = c.post("/api/v1/login/start", json={"name": "server"}, headers=anon).json()
    assert c.post(f"/api/v1/logins/{start['code']}", json={}).json() == {"name": "server-admin"}
    # a shared agent with full access approves as no one: its terminal is shared
    ci = {"Authorization": "Bearer " + accounts.create_token(lg.db, "ci", admin=True).token}
    start = c.post("/api/v1/login/start", json={"name": "runner"}, headers=anon).json()
    assert c.post(f"/api/v1/logins/{start['code']}", json={}, headers=ci).json() == {"name": "runner"}


# ------------------------------------------------------------------------------------------------ Google and GitHub
def provider_round_trip(app, client, monkeypatch, who, **start):
    monkeypatch.setattr(signin, "_identity", lambda lg, provider, code, uri: who)
    r = client.get("/auth/github/start", params=start, follow_redirects=False)
    assert r.status_code == 302, r.text
    state = parse_qs(urlsplit(r.headers["location"]).query)["state"][0]
    return client.get("/auth/github/callback", params={"code": "c", "state": state}, follow_redirects=False)


def test_google_and_github_sign_in_sign_up_and_linking(team, monkeypatch):
    lg, app, c, ada, bo = team
    lg.set_config(SYSTEM, {"GITHUB_CLIENT_ID": "id", "GITHUB_CLIENT_SECRET": "secret"})
    assert browser(app).get("/auth/methods").json() == {"signup": "anyone", "password_signup": True, "domains": [],
                                                       "github": True, "google": False, "email": False}
    # an unconfirmed address typed at sign-up never links a GitHub account: mal can't catch victim's GitHub
    signup_browser(app, "victim@uni.edu", "mal's password", "mal")
    r = provider_round_trip(app, browser(app), monkeypatch, ("8", "victim@uni.edu", "victim"))
    assert r.headers["location"] == "/?error=email_taken&provider=github"
    assert accounts.profile(lg.db, accounts.person_by_email(lg.db, "victim@uni.edu")["id"])["identities"] == []
    # invite only: an unknown account can't join
    lg.set_config(SYSTEM, {"SIGNUP": "invited"})
    r = provider_round_trip(app, browser(app), monkeypatch, ("1", "eve@uni.edu", "eve"))
    assert r.headers["location"] == "/?error=no_account&provider=github"
    # allowed domains: a verified address there joins as a member
    lg.set_config(SYSTEM, {"SIGNUP": "domains", "SIGNUP_DOMAINS": "uni.edu"})
    assert browser(app).get("/auth/methods").json()["domains"] == ["uni.edu"]
    eve = browser(app)
    r = provider_round_trip(app, eve, monkeypatch, ("1", "eve@uni.edu", "eve"))
    assert r.headers["location"] == "/" and eve.get("/api/v1/me").json() | {"warnings": []} == {
        "name": "eve", "kind": "human", "role": "member", "admin": False, "project": "default", "warnings": []}
    assert provider_round_trip(app, browser(app), monkeypatch, ("2", "mal@gmail.com", "mal")).headers["location"].endswith("no_account&provider=github")
    # an invite works with any GitHub account; the person gets the invited email and role
    t = link_token(ada.post("/api/v1/invites", json={"email": "fay@lab.org", "role": "admin"}).json()["link"])
    fay = browser(app)
    r = provider_round_trip(app, fay, monkeypatch, ("3", "fay.personal@gmail.com", "fay-gh"), invite=t)
    assert r.headers["location"] == "/" and fay.get("/api/v1/me").json()["role"] == "admin"
    assert fay.get("/api/v1/me/account").json()["email"] == "fay@lab.org"
    # a verified email that matches a person signs them in and links the account; linking from Settings
    assert provider_round_trip(app, browser(app), monkeypatch, ("4", "bo@example.com", "bo-gh")).headers["location"] == "/"
    r = provider_round_trip(app, ada, monkeypatch, ("4", "bo@example.com", "bo-gh"), link=1)
    assert r.headers["location"] == "/settings/profile?error=taken"  # bo's GitHub can't be linked to ada
    r = provider_round_trip(app, ada, monkeypatch, ("5", None, "ada-gh"), link=1)
    assert r.headers["location"] == "/settings/profile?linked=github"
    assert [i["login"] for i in ada.get("/api/v1/me/account").json()["identities"]] == ["ada-gh"]
    assert ada.delete("/api/v1/me/identities/github").json() == {"removed": 1}
    assert eve.delete("/api/v1/me/identities/github").status_code == 400  # eve has no password: her only way in
    # a disabled person can't come in through GitHub either
    ada.patch("/api/v1/principals/eve", json={"disabled": True}).raise_for_status()
    assert provider_round_trip(app, browser(app), monkeypatch, ("1", "eve@uni.edu", "eve")).headers["location"].startswith("/?error=disabled")


def test_a_google_github_round_trip_finishes_only_in_the_browser_that_began_it(team, monkeypatch):
    lg, app, c, ada, bo = team
    lg.set_config(SYSTEM, {"GITHUB_CLIENT_ID": "id", "GITHUB_CLIENT_SECRET": "secret"})
    monkeypatch.setattr(signin, "_identity", lambda lg, provider, code, uri: ("9", "bo@example.com", "bo-gh"))
    r = browser(app).get("/auth/github/start", follow_redirects=False)  # the attacker starts with their own account
    state = parse_qs(urlsplit(r.headers["location"]).query)["state"][0]
    victim = browser(app)
    r = victim.get("/auth/github/callback", params={"code": "c", "state": state}, follow_redirects=False)
    assert r.headers["location"] == "/?error=expired&provider=github" and "litledger_session" not in r.cookies


def test_who_can_join(srv):
    lg, app, c = srv
    assert accounts.may_join(lg.settings, "bo@uni.edu")  # default: anyone
    lg.set_config(SYSTEM, {"SIGNUP": "invited"})
    assert not accounts.may_join(lg.settings, "bo@uni.edu")
    lg.set_config(SYSTEM, {"SIGNUP": "domains", "SIGNUP_DOMAINS": "uni.edu, @lab.org"})
    assert accounts.may_join(lg.settings, "bo@uni.edu") and accounts.may_join(lg.settings, "al@LAB.org")
    assert not accounts.may_join(lg.settings, "eve@gmail.com") and not accounts.may_join(lg.settings, None)
    lg.set_config(SYSTEM, {"SIGNUP": "anyone"})
    assert accounts.may_join(lg.settings, "eve@gmail.com")


# ------------------------------------------------------------------------------------------------ profile, audit
def test_profile_name_and_email(team, monkeypatch):
    lg, app, c, ada, bo = team
    assert bo.patch("/api/v1/me/account", json={"name": "Bo B"}).json() == {"name": "bo-b"}
    assert bo.patch("/api/v1/me/account", json={"name": "ada"}).status_code == 400
    assert bo.get("/api/v1/me/account").json()["email_change"] == "admin"
    assert bo.patch("/api/v1/me/account", json={"email": "bo@new.org"}).status_code == 400  # no email: an admin changes it
    assert ada.patch("/api/v1/me/account", json={"email": "ada@new.org"}).json() == {"email": "ada@new.org"}
    sent = mail_box(monkeypatch, lg)
    assert bo.patch("/api/v1/me/account", json={"email": "bo@new.org"}).json() == {"confirm": "bo@new.org"}
    assert bo.get("/api/v1/me/account").json()["email"] == "bo@example.com"  # not until confirmed
    r = browser(app).get("/auth/email/confirm", params={"t": link_token(sent[0][2])}, follow_redirects=False)
    assert r.headers["location"] == "/settings/profile?email=confirmed"
    assert bo.get("/api/v1/me/account").json()["email"] == "bo@new.org"


def test_audit_log_has_account_events_and_the_journal_does_not(team):
    lg, app, c, ada, bo = team
    ada.patch("/api/v1/principals/bo", json={"role": "admin"}).raise_for_status()
    browser(app).post("/auth/password", json={"email": "bo@example.com", "password": "wrong!"})
    browser(app).post("/auth/password", json={"email": "bo@example.com", "password": "member pass 1"})
    log = ada.get("/api/v1/audit").json()
    ops = [(e["op"], e["actor"], e["target"]) for e in log]
    assert ("role.change", "ada", "bo") in ops and ("invite.create", "ada", "bo@example.com") in ops
    assert ("signin.failed", None, "bo@example.com") in ops and ("account.join", "ada", "ada@example.com") in ops
    assert ("role.change", "admin", "ada") in ops and any(op == "signin" and actor == "admin" for op, actor, _ in ops)
    assert ops[0] == ("signin", "bo", None) and log[0]["detail"] == {"method": "password"} and log[0]["ip"]
    assert ("invite.accept", "bo", "bo@example.com") in ops and ops.count(("signin", "bo", None)) == 1  # the invite was the first sign-in
    journal = ada.get("/api/v1/journal", params={"all_projects": True}).json()
    assert not any(e["op"].startswith(("principal", "role", "invite", "signin")) for e in journal)


def test_hashes_at_rest(team):
    lg, app, c, ada, bo = team
    token = bo.post("/api/v1/me/tokens", json={"name": "t"}).json()["token"]
    with lg.db.read() as conn:
        rows = [tuple(r) for r in conn.execute("SELECT token_hash, password_hash FROM principals")]
        creds = [r[0] for r in conn.execute("SELECT hash FROM credentials")]
    assert (auth.digest(token), None) in rows and all(not h or h.startswith("scrypt$") for _, h in rows)
    assert token not in str(rows) and all(len(h) == 64 for h in creds)
    assert auth.verify(lg.db, token).name == "t-bo"
    assert auth.verify(lg.db, token[:-1] + ("A" if token[-1] != "A" else "B")) is None
    assert auth.lookup(lg.db, "nope", "session") is None and not auth.valid(None)
    # expiries are UTC ISO strings in db.now()'s form, so stored times compare with now() as strings
    from datetime import datetime, timezone
    from litledger.db import now
    hour = datetime.fromisoformat(auth.at(timedelta(hours=1)))
    assert hour.utcoffset() == timedelta(0) and 3590 <= (hour - datetime.now(timezone.utc)).total_seconds() <= 3600
    assert len(auth.at(timedelta(0))) == len(now()) and auth.at(timedelta(seconds=-5)) < now() < auth.at(timedelta(seconds=5))


# ------------------------------------------------------------------------------------------------ pre-claimed accounts
def test_a_reset_takes_a_pre_claimed_account_over(srv, monkeypatch):
    """Someone signs up with another person's address (sign-up is open and the address unconfirmed), makes an API
    token, links their own GitHub and connects an app. The address's owner is told the email is in use, resets the
    password from their inbox, and is the account's only owner from then on: nothing the stranger set up still works."""
    from conftest import oauth_grant
    lg, app, c = srv
    sent = mail_box(monkeypatch, lg)
    lg.set_config(SYSTEM, {"GITHUB_CLIENT_ID": "id", "GITHUB_CLIENT_SECRET": "secret"})
    mallory = signup_browser(app, "victim@uni.edu", "mallory pass", name="victim")
    spy = mallory.post("/api/v1/me/tokens", json={"name": "spy", "expires": "never"}).json()["token"]
    assert provider_round_trip(app, mallory, monkeypatch, ("gh-mallory", None, "mallory"), link=1).headers["location"].endswith("linked=github")
    client_id, grant = oauth_grant(c, mallory)
    assert c.get("/api/v1/me", headers={"Authorization": f"Bearer {spy}"}).status_code == 200
    # the owner of the address arrives
    victim = browser(app)
    r = victim.post("/auth/signup", json={"name": "victim", "email": "victim@uni.edu", "password": "victim pass 1"})
    assert r.status_code == 400 and r.json()["detail"] == accounts.TAKEN
    confirm = browser(app).get("/auth/email/confirm", params={"t": link_token(sent[0][2])})  # not where it was made
    assert confirm.status_code == 403 and not accounts.person_by_email(lg.db, "victim@uni.edu")["verified"]
    victim.post("/auth/forgot", json={"email": "victim@uni.edu"}).raise_for_status()
    r = victim.post("/auth/reset", json={"token": link_token(sent[-1][2]), "password": "victim pass 1"})
    assert r.status_code == 200 and r.json() == {"name": "victim", "notice": accounts.RECLAIMED}
    account = victim.get("/api/v1/me/account").json()
    assert account["email_verified"] and account["identities"] == [] and victim.get("/api/v1/me/agents").json() == []
    # the stranger's way in is gone: session, token, GitHub, the app's access and refresh tokens, the old password
    assert mallory.get("/api/v1/me").status_code == 401
    assert c.get("/api/v1/me", headers={"Authorization": f"Bearer {spy}"}).status_code == 401
    gh = browser(app)
    r = provider_round_trip(app, gh, monkeypatch, ("gh-mallory", None, "mallory"))
    assert r.headers["location"].startswith("/?error=") and gh.get("/api/v1/me").status_code == 401
    mcp = {"Authorization": f"Bearer {grant['access_token']}", "Accept": "application/json, text/event-stream"}
    assert c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, headers=mcp).status_code == 401
    refreshed = c.post("/oauth/token", data={"grant_type": "refresh_token", "refresh_token": grant["refresh_token"],
                                             "client_id": client_id})
    assert refreshed.json()["error"] == "invalid_grant"
    assert browser(app).post("/auth/password", json={"email": "victim@uni.edu", "password": "mallory pass"}).status_code == 401
    # recorded in the same transaction, as a security event
    row = next(r for r in accounts.audit_log(lg.db) if r["op"] == "account.reclaim")
    assert row["level"] == "security" and row["subject"] == "victim"
    assert row["detail"]["identities"] == 1 and row["detail"]["agents"] == 2  # the token and the app
    # a later reset of the now-confirmed account is an ordinary one: it keeps the owner's own tokens
    mine = victim.post("/api/v1/me/tokens", json={"name": "mine"}).json()["token"]
    victim.post("/auth/forgot", json={"email": "victim@uni.edu"}).raise_for_status()
    r = browser(app).post("/auth/reset", json={"token": link_token(sent[-1][2]), "password": "victim pass 2"})
    assert r.json() == {"name": "victim"} and c.get("/api/v1/me", headers={"Authorization": f"Bearer {mine}"}).status_code == 200


def test_an_admin_reset_link_also_takes_an_unconfirmed_account_over(srv):
    """Without email, the owner asks an admin for a reset link: the same change of owner."""
    lg, app, c = srv
    mallory = signup_browser(app, "victim@uni.edu", "mallory pass", name="victim")
    spy = mallory.post("/api/v1/me/tokens", json={"name": "spy"}).json()["token"]
    link = admin_browser(app, c.token).post("/api/v1/principals/victim/reset-link").json()["link"]
    victim = browser(app)
    assert victim.post("/auth/reset", json={"token": link_token(link), "password": "victim pass 1"}).json()["notice"]
    assert c.get("/api/v1/me", headers={"Authorization": f"Bearer {spy}"}).status_code == 401
    assert victim.get("/api/v1/me/account").json()["email_verified"]


def test_an_admin_cannot_clear_the_email_someone_signs_in_with(team, monkeypatch):
    lg, app, c, ada, bo = team
    r = ada.patch("/api/v1/principals/bo", json={"email": ""})
    assert r.status_code == 400 and "no way to sign in" in r.json()["detail"]
    assert accounts.person_by_email(lg.db, "bo@example.com")  # unchanged, and bo still signs in
    browser(app).post("/auth/password", json={"email": "bo@example.com", "password": "member pass 1"}).raise_for_status()
    # someone who signs in only with GitHub can do without an email
    lg.set_config(SYSTEM, {"GITHUB_CLIENT_ID": "id", "GITHUB_CLIENT_SECRET": "secret"})
    cy = invited_browser(app, ada, "cy@example.com", password="cy password 1")
    provider_round_trip(app, cy, monkeypatch, ("cy-gh", None, "cy"), link=1)
    with lg.db.tx(SYSTEM, internal="test setup: a GitHub-only person") as tx:
        tx.execute("UPDATE principals SET password_hash=NULL WHERE name='cy'")
    assert ada.patch("/api/v1/principals/cy", json={"email": ""}).status_code == 200
    assert accounts.people(lg.db)[-1]["email"] is None
    assert ada.patch("/api/v1/principals/bo", json={"projects": ["thesis"]}).status_code == 200  # no email sent: kept
    assert accounts.person_by_email(lg.db, "bo@example.com")


def test_terminal_sign_ins_are_capped_per_address(srv):
    """Anyone can start `litledger login`: a few waiting per address, a short list for approvers, codes still work."""
    from litledger import access
    lg, app, c = srv
    anon = {"Authorization": ""}
    codes = []
    for i in range(access.MAX_PENDING_PER_IP + 2):
        r = c.post("/api/v1/login/start", json={"name": f"t{i}"}, headers=anon)
        codes.append(r)
    assert [r.status_code for r in codes] == [200] * access.MAX_PENDING_PER_IP + [429, 429]
    assert "waiting" in codes[-1].json()["detail"]
    # many addresses: the approval list shows the newest few; the rate per address is limited too
    pairings = access.Pairings()
    for i in range(12):
        pairings.start(f"m{i}", f"10.0.0.{i}", "cli")
    shown = pairings.pending()
    assert len(shown) == access.SHOWN and shown[0]["name"] == "m11"
    for _ in range(access.START_LIMIT[0] - 1):
        pairings.limit.ok("ip:10.9.9.9")
    with pytest.raises(ValueError, match="too many sign-in requests from this address"):
        for i in range(2):
            pairings.start("x", "10.9.9.9", "cli")
    oldest = next(r for _, r in pairings.store.items() if r.name == "m0")
    assert pairings._find(oldest.code) is oldest  # not shown, still approvable by its code
