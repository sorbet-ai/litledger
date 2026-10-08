"""Project limits hold on every path, and an app's MCP token stays an MCP token."""
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from litledger import accounts, auth
from litledger.app import create_app
from litledger.db import SYSTEM

from conftest import make_ledger

BASE = "http://127.0.0.1:8765"


@pytest.fixture
def env(tmp_path):
    lg = make_ledger(tmp_path)
    app = create_app(ledger=lg)
    with TestClient(app, base_url=BASE) as c:
        with lg.db.tx(SYSTEM, internal="test setup") as tx:
            for p in ("thesis", "secret"):
                tx.execute("INSERT OR IGNORE INTO projects(id,title,created_at) VALUES(?,?,'2026-01-01')", (p, p))
        limited = accounts.create_token(lg.db, "limited", projects=["thesis"]).token
        yield lg, c, {"Authorization": f"Bearer {limited}"}


def test_project_argument_cannot_leave_the_limit(env):
    lg, c, h = env
    out = c.post("/api/v1/tools/find", json={"project": "secret"}, headers={**h, "Accept": "text/plain"}).text
    assert out.startswith("error: this sign-in may only use: thesis")
    assert "project=thesis" in c.post("/api/v1/tools/find", json={}, headers={**h, "Accept": "text/plain"}).text


def test_workspace_header_cannot_leave_the_limit(env):
    lg, c, h = env
    c.app.state.project_ids = None  # forget the cached project list
    me = c.get("/api/v1/me", headers={**h, "X-Litledger-Workspace": "/code/secret"}).json()
    assert me["project"] == "thesis"


def test_limited_principals_see_only_their_journal_and_no_jobs(env):
    lg, c, h = env
    c.post("/api/v1/tools/tag", json={"action": "list"}, headers=h)
    assert c.get("/api/v1/jobs", headers=h).json() == []
    entries = c.get("/api/v1/journal", headers=h).json()
    assert all(e["project"] == "thesis" for e in entries)  # no library-wide entries (config, principals)


def test_mcp_access_token_is_not_a_browser_session(env):
    lg, c, h = env
    person = accounts.create_person(lg.db, "ada", "ada@example.com")
    with lg.db.tx(SYSTEM, internal="test setup") as tx:
        access = auth.issue(tx, person, "access", timedelta(hours=1), "client", "fX", f"{BASE}/mcp")
    assert c.get("/api/v1/me", headers={"Authorization": "", "Cookie": f"litledger_session={access}"}).status_code == 401
    assert c.get("/api/v1/me", headers={"Authorization": f"Bearer {access}"}).status_code == 401


def test_a_refresh_token_rotates_once(env):
    lg, c, h = env
    reg = c.post("/oauth/register", json={"redirect_uris": ["http://localhost/cb"], "client_name": "x"}).json()
    pid = accounts.create_person(lg.db, "bo", "bo@example.com")
    with lg.db.tx(SYSTEM, internal="test setup") as tx:
        raw = auth.issue(tx, pid, "refresh", timedelta(days=1), reg["client_id"], "fam1", f"{BASE}/mcp")
        auth.revoke_credential(tx, raw)  # what a concurrent request does between our lookup and our revoke
    r = c.post("/oauth/token", data={"grant_type": "refresh_token", "refresh_token": raw, "client_id": reg["client_id"]})
    assert r.json()["error"] == "invalid_grant"


# ------------------------------------------------------------------------------------------------ the shared library
@pytest.fixture
def shared(tmp_path):
    """An unlimited agent fills the library from project "other"; `lim` may use only "thesis"."""
    from conftest import add_work, offline_ledger
    from litledger.db import Actor
    lg = offline_ledger(tmp_path)
    other = Actor(name="other", kind="agent", project="other")
    with lg.db.tx(other) as tx:
        tx.log("test.setup", {})
        add_work(tx.conn, "w1", "A Paper Only Other Has", citekey="zed2020paper")
        add_work(tx.conn, "w2", "A Paper Both Have", citekey="kim2021both")
        for pid, wid in (("other", "w1"), ("other", "w2"), ("thesis", "w2")):
            tx.execute("INSERT OR IGNORE INTO projects(id,title,created_at) VALUES(?,?,'2026-01-01')", (pid, pid))
            tx.execute("INSERT INTO project_works(project,work_id,added_at) VALUES(?,?,'2026-01-01')", (pid, wid))
    from litledger.works import index_work
    with lg.db.write("test: search index") as conn:
        index_work(conn, "w1")
        index_work(conn, "w2")
    from conftest import call
    call(lg, other, "entity", items=[{"kind": "benchmark", "title": "MMLU"}, {"kind": "method", "title": "DeltaNet"}])
    call(lg, other, "link", items=[{"source": "method:deltanet", "target": "benchmark:mmlu", "relation": "evaluates_on"}])
    call(lg, other, "tag", action="define", tags=["shared-tag", "other-tag"], scope="library")
    lim = Actor(name="lim", kind="agent", project="thesis", projects=("thesis",))
    return lg, other, lim


def test_limited_principals_add_to_the_library_but_never_destroy_it(shared):
    from conftest import call
    lg, other, lim = shared
    out = call(lg, lim, "entity", action="delete", ids=["benchmark:mmlu"])
    assert "shared by all projects" in out and "deleted" not in out
    out = call(lg, lim, "entity", items=[{"id": "method:deltanet", "kind": "method", "title": "DeltaNet", "rename": "X"}])
    assert "shared by all projects" in out
    with lg.db.read() as conn:
        link = conn.execute("SELECT id FROM links WHERE project=''").fetchone()["id"]
    assert "shared by all projects" in call(lg, lim, "link", items=[{"retract": link}])
    for args in ({"action": "merge", "tags": ["shared-tag"], "to": "other-tag"}, {"action": "archive", "tags": ["shared-tag"]},
                 {"action": "rename", "tags": ["shared-tag"], "to": "renamed"}):
        assert call(lg, lim, "tag", **args).startswith("error: library tag shared-tag is shared"), args
    assert "shared by all projects" in call(lg, lim, "update_work", items=[{"work": "kim2021both", "unmerge": True}])
    with lg.db.read() as conn:  # nothing changed
        assert conn.execute("SELECT count(*) FROM entities").fetchone()[0] == 2
        assert conn.execute("SELECT count(*) FROM links WHERE retracted_at IS NULL").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM tags WHERE archived_at IS NULL AND project=''").fetchone()[0] == 2
    # adding is fine: a new library entity, a link, a project tag of its own
    assert call(lg, lim, "entity", items=[{"kind": "dataset", "title": "C4"}]).startswith("ok 1/1")
    assert call(lg, lim, "link", items=[{"source": "kim2021both", "target": "dataset:c4", "relation": "uses"}]).startswith("ok 1/1")
    assert call(lg, lim, "tag", action="archive", tags=["shared-tag"], scope="project").startswith("error")  # still shared
    # an unlimited principal may
    assert "deleted: benchmark:mmlu" in call(lg, other, "entity", action="delete", ids=["benchmark:mmlu"])
    assert call(lg, other, "tag", action="archive", tags=["other-tag"]).startswith("ok")


def test_limited_principals_reach_only_works_in_their_projects(shared):
    from conftest import call
    lg, other, lim = shared
    assert call(lg, lim, "work", work="kim2021both").startswith("kim2021both")
    for tool, args in (("work", {"work": "zed2020paper"}), ("read", {"work": "zed2020paper"}),
                       ("note", {"items": [{"work": "zed2020paper", "text": "x"}]}),
                       ("update_work", {"items": [{"work": "zed2020paper", "read": "full", "add": True}]})):
        out = call(lg, lim, tool, **args)
        assert "unknown work 'zed2020paper'" in out, (tool, out)
    assert call(lg, lim, "find", scope="library").splitlines()[0].startswith("1 work")  # only its own projects' works
    assert call(lg, lim, "find", query="paper", scope="library").splitlines()[1].startswith("kim2021both")
    assert call(lg, other, "find", scope="library").splitlines()[0].startswith("2 works")
    out = call(lg, lim, "resolve", items=["zed2020paper"], add=True)  # a citekey of another project's work names nothing
    assert not out.startswith("ok 1/1")


def test_limited_principals_reach_only_their_works_over_rest(env):
    lg, c, h = env
    from conftest import add_work
    with lg.db.tx(SYSTEM, internal="test setup") as tx:
        add_work(tx.conn, "w9", "Secret Work", citekey="secret2024work")
        tx.execute("INSERT INTO project_works(project,work_id,added_at) VALUES('secret','w9','2026-01-01')")
    assert c.get("/api/v1/works/secret2024work", headers=h).status_code == 404
    assert c.get("/api/v1/works/secret2024work/passages", headers=h).status_code == 404
    assert c.get("/api/v1/works", params={"scope": "library"}, headers=h).json()["total"] == 0
    admin = {"Authorization": "Bearer " + (lg.settings.token_dir / "admin").read_text().strip()}
    assert c.get("/api/v1/works/secret2024work", headers=admin).status_code == 200
