"""HTTP surface: auth, REST tools endpoint, MCP JSON-RPC with per-client toolsets, uploads, events."""
import json

import pytest
from fastapi.testclient import TestClient

from litledger import accounts, auth
from litledger.app import create_app

from conftest import make_ledger

MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


@pytest.fixture
def client(tmp_path):
    lg = make_ledger(tmp_path)
    app = create_app(ledger=lg)
    with TestClient(app) as c:
        token = (lg.settings.token_dir / "admin").read_text().strip()
        c.headers.update({"Authorization": f"Bearer {token}", "X-Litledger-Project": "web"})
        c.lg = lg
        yield c


def rpc(client, method, params=None, headers=None, id_=1):
    body = {"jsonrpc": "2.0", "id": id_, "method": method, "params": params or {}}
    r = client.post("/mcp", json=body, headers={**MCP_HEADERS, **(headers or {})})
    assert r.status_code == 200, r.text
    return r.json()


def init_params():
    return {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}}


def test_auth_required(client):
    assert client.get("/api/v1/health").status_code == 200
    r = client.get("/api/v1/me", headers={"Authorization": ""})
    assert r.status_code == 401
    r = client.post("/mcp", json={}, headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401
    agent_token = accounts.create_token(client.lg.db, "codex").token
    r = client.get("/api/v1/me", headers={"Authorization": f"Bearer {agent_token}"})
    assert r.json()["name"] == "codex"


def test_mcp_toolsets_and_call(client):
    init = rpc(client, "initialize", init_params())
    assert "litledger" in json.dumps(init["result"]["serverInfo"])
    assert "S2_API_KEY" in init["result"]["instructions"]
    names = [t["name"] for t in rpc(client, "tools/list")["result"]["tools"]]
    assert names == ["find", "discover", "resolve", "work", "read", "note", "tag", "export"]
    names = [t["name"] for t in rpc(client, "tools/list", headers={"X-Litledger-Tools": "core,maps"})["result"]["tools"]]
    assert "map_edit" in names and "graph" not in names
    res = rpc(client, "tools/call", {"name": "resolve", "arguments": {"items": ["1706.03762"], "add": True, "agent": "sub-1"}})
    text = res["result"]["content"][0]["text"]
    assert text.startswith("ok 1/1 · 1 added"), text
    journal = client.get("/api/v1/journal").json()
    assert any(e["op"] == "project.add" and e["agent"] == "sub-1" and e["principal"] == "admin" for e in journal)
    res = rpc(client, "tools/call", {"name": "work", "arguments": {"work": "nope0000"}})
    assert res["result"]["isError"] is True


def test_rest_tools_and_resources(client):
    r = client.post("/api/v1/tools/resolve", json={"items": ["1706.03762"], "add": True, "tags": ["phase:a"]}, headers={"Accept": "text/plain"})
    assert r.text.startswith("ok 1/1")
    works = client.get("/api/v1/works", params={"tags": "phase:a"}).json()
    assert works["total"] == 1 and works["items"][0]["citekey"] == "vaswani2017attention"
    detail = client.get("/api/v1/works/vaswani2017attention").json()
    assert detail["ids"]["arxiv"] == "1706.03762" and detail["in_project"]
    assert client.get("/api/v1/works/1706.03762").json()["citekey"] == "vaswani2017attention"
    assert client.get("/api/v1/tags").json()[0]["name"] == "phase:a"
    net = client.get("/api/v1/network").json()
    assert len(net["nodes"]) == 1
    provs = client.get("/api/v1/providers").json()
    assert any(p["id"] == "arxiv" and p["status"] == "enabled" for p in provs["providers"])


def test_upload_bib_import(client):
    bib = b"@article{a, title={Attention Is All You Need}, author={Vaswani, Ashish}, year={2017}, eprint={1706.03762}, archivePrefix={arXiv}}\n"
    r = client.post("/api/v1/uploads", files={"file": ("refs.bib", bib)}, data={"tags": "imported"})
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["format"] == "bibtex" and res["added"] == 1


def test_settings_from_the_ui(client):
    view = client.get("/api/v1/config").json()
    assert view["editable"] is True
    s2 = next(f for f in view["fields"] if f["key"] == "S2_API_KEY")
    assert not s2["set"] and s2["secret"]
    assert any(w["provider"] == "s2" for w in view["warnings"])
    # Agents can read the field list but cannot change settings.
    agent = accounts.create_token(client.lg.db, "bot").token
    r = client.put("/api/v1/config", json={"values": {"S2_API_KEY": "x"}}, headers={"Authorization": f"Bearer {agent}"})
    assert r.status_code == 403
    assert client.put("/api/v1/config", json={"values": {"NOPE": "x"}}).status_code == 400
    r = client.put("/api/v1/config", json={"values": {"S2_API_KEY": "secret-key-1234", "CONTACT_EMAIL": "me@example.com"}})
    assert r.status_code == 200, r.text
    body = r.json()
    assert "secret-key-1234" not in r.text  # never echoed
    s2 = next(f for f in body["fields"] if f["key"] == "S2_API_KEY")
    assert s2["set"] and s2["value"].endswith("1234")
    assert not any(w["provider"] in ("s2", "contact_email") for w in body["warnings"])
    assert any(p["id"] == "s2" and p["status"] == "enabled" for p in body["providers"])
    # Applied without restart: the live registry and new MCP sessions see it.
    assert client.lg.providers.get("s2") is not None and client.lg.settings.get("S2_API_KEY") == "secret-key-1234"
    init = rpc(client, "initialize", init_params())
    assert "S2_API_KEY" not in init["result"]["instructions"]
    journal = client.get("/api/v1/journal", params={"all_projects": True}).json()
    entry = next(e for e in journal if e["op"] == "config.set")
    assert entry["payload"] == {"keys": ["S2_API_KEY", "CONTACT_EMAIL"]}
    assert client.put("/api/v1/config", json={"values": {"TOOLS": "core,bogus"}}).status_code == 400
    assert client.put("/api/v1/config", json={"values": {"TOOLS": "core,maps"}}).status_code == 200
    names = [t["name"] for t in rpc(client, "tools/list")["result"]["tools"]]
    assert "map_edit" in names  # new default toolsets apply to the next request
    # Clearing removes the key and the warning returns.
    body = client.put("/api/v1/config", json={"values": {"S2_API_KEY": None}}).json()
    assert any(w["provider"] == "s2" for w in body["warnings"])
    assert client.post("/api/v1/config/test/s2").json()["ok"] is False  # disabled again


def test_map_nodes_reference_anything(client):
    t = lambda name, **a: client.post(f"/api/v1/tools/{name}", json=a, headers={"Accept": "text/plain"}).text  # noqa: E731
    t("resolve", items=["1706.03762"], add=True, tags=["phase:background"])
    t("entity", items=[{"kind": "claim", "title": "Attention is enough"}])
    note_id = t("note", items=[{"work": "vaswani2017attention", "text": "self-attention only"}]).split("\n")[1].split()[0]
    t("map_edit", title="Other map", ops=[{"op": "add", "text": "x"}])
    refs = client.get("/api/v1/refs", params={"q": "attention"}).json()
    assert refs["works"][0]["ref"] == "vaswani2017attention" and refs["entities"][0]["ref"] == "claim:attention-is-enough"
    assert client.get("/api/v1/refs", params={"q": "background"}).json()["tags"][0]["ref"] == "tag:phase:background"
    assert client.get("/api/v1/refs", params={"q": "2412.06464"}).json()["capture"]["handle"] == "arxiv:2412.06464"
    out = t("map_edit", title="Everything", ops=[
        {"op": "add", "key": "w", "ref": "vaswani2017attention"}, {"op": "add", "ref": "claim:attention-is-enough", "parent": "w"},
        {"op": "add", "ref": note_id, "parent": "w"}, {"op": "add", "ref": "tag:phase:background"},
        {"op": "add", "ref": "map:other-map"}])
    assert out.startswith("ok map:everything · added n1 n2 n3 n4 n5"), out
    kinds = {n["kind"] for n in client.get("/api/v1/maps/map:everything").json()["nodes"]}
    assert kinds == {"work", "claim", "note", "tag", "map"}
    bad = t("map_edit", map="map:everything", ops=[{"op": "add", "ref": "tag:nope"}])
    assert "unknown tag" in bad


def test_login_by_approval_and_token_management(client):
    anon = {"Authorization": ""}
    start = client.post("/api/v1/login/start", json={"name": "My Laptop"}, headers=anon).json()
    assert start["name"] == "my-laptop" and len(start["code"]) == 9
    assert client.get(f"/api/v1/login/poll/{start['poll']}", headers=anon).json() == {"status": "pending"}
    assert client.get("/api/v1/logins", headers=anon).status_code == 401  # the pending list needs a token
    pending = client.get("/api/v1/logins").json()
    assert [p["code"] for p in pending] == [start["code"]]
    agent = accounts.create_token(client.lg.db, "helper").token
    assert client.post(f"/api/v1/logins/{start['code']}", json={}, headers={"Authorization": f"Bearer {agent}"}).status_code == 403
    assert client.post(f"/api/v1/logins/{start['code'].lower()}", json={}).json() == {"name": "my-laptop-admin"}
    got = client.get(f"/api/v1/login/poll/{start['poll']}", headers=anon).json()
    assert got["status"] == "approved" and got["token"].startswith("ll_")
    assert client.get(f"/api/v1/login/poll/{start['poll']}", headers=anon).json() == {"status": "expired"}  # once only
    assert client.get("/api/v1/me", headers={"Authorization": f"Bearer {got['token']}"}).json()["name"] == "my-laptop-admin"
    # a second machine with the same name gets its own token instead of rotating the first one
    second = client.post("/api/v1/login/start", json={"name": "my-laptop"}, headers=anon).json()
    assert client.post(f"/api/v1/logins/{second['code']}", json={}).json() == {"name": "my-laptop-admin-2"}
    denied = client.post("/api/v1/login/start", json={}, headers=anon).json()
    client.delete(f"/api/v1/logins/{denied['code']}")
    assert client.get(f"/api/v1/login/poll/{denied['poll']}", headers=anon).json()["status"] == "denied"
    # shared tokens made by an admin (e.g. for CI), listing, revoking
    made = client.post("/api/v1/principals", json={"name": "codex ci"}).json()
    assert made["name"] == "codex-ci" and made["token"].startswith("ll_")
    agents = {p["name"]: p for p in client.get("/api/v1/principals").json()["agents"]}
    assert {"my-laptop-admin", "my-laptop-admin-2", "codex-ci", "helper"} <= set(agents)
    assert agents["my-laptop-admin"]["by"] == "my-laptop via admin" and agents["my-laptop-admin"]["origin"] == "login"
    assert agents["codex-ci"]["access"] == "library" and "admin" not in agents  # the admin is a person, not an agent
    assert client.delete("/api/v1/principals/codex-ci").json() == {"removed": "codex-ci"}
    assert client.get("/api/v1/me", headers={"Authorization": f"Bearer {made['token']}"}).status_code == 401


def test_add_and_remove_sources(client):
    srcs = {p["id"]: p for p in client.get("/api/v1/providers").json()["providers"]}
    assert srcs["core"]["keys"] == ["CORE_API_KEY"] and srcs["core"]["key_url"].startswith("https://core.ac.uk")
    assert srcs["pubmed"]["optional_keys"] == ["NCBI_API_KEY"] and srcs["inspire"]["status"] == "optin_off"
    r = client.post("/api/v1/sources/inspire", json={"action": "add"}).json()
    assert r["status"] == "enabled"
    r = client.post("/api/v1/sources/core", json={"action": "add", "values": {"CORE_API_KEY": "k" * 20}}).json()
    assert r["status"] == "enabled"
    assert client.post("/api/v1/sources/core", json={"action": "add", "values": {"S2_API_KEY": "x"}}).status_code == 400
    r = client.post("/api/v1/sources/arxiv", json={"action": "remove"}).json()
    assert r["status"] == "disabled"
    r = client.post("/api/v1/sources/arxiv", json={"action": "add"}).json()
    assert r["status"] == "enabled"
    r = client.post("/api/v1/sources/core", json={"action": "remove", "forget_keys": True}).json()
    assert r["status"] == "disabled"
    fields = {f["key"]: f for f in client.get("/api/v1/config").json()["fields"]}
    assert not fields["CORE_API_KEY"]["set"] and fields["DISABLE"]["value"] == "core"
    assert fields["ENABLE"]["value"] == "inspire"


def test_workspace_folder_picks_an_existing_project(client):
    client.post("/api/v1/tools/tag", json={"action": "list"}, headers={"X-Litledger-Project": "thesis"})
    with client.lg.db.tx(auth.Actor(name="t"), internal="test setup") as tx:
        tx.execute("INSERT OR IGNORE INTO projects(id,title,created_at) VALUES('thesis','thesis','2026-01-01')")
    client.app.state.project_ids = None  # forget the cached project list
    me = client.get("/api/v1/me", headers={"X-Litledger-Project": "", "X-Litledger-Workspace": r"D:\code\thesis"}).json()
    assert me["project"] == "thesis"
    me = client.get("/api/v1/me", headers={"X-Litledger-Project": "", "X-Litledger-Workspace": "/home/me/elsewhere"}).json()
    assert me["project"] == "default"
    me = client.get("/api/v1/me", headers={"X-Litledger-Project": "web", "X-Litledger-Workspace": "/x/thesis"}).json()
    assert me["project"] == "web"  # an explicit project wins


# ------------------------------------------------------------------------------------------------ review fixes
def test_unknown_tools_and_bad_projects_answer_in_text(client):
    """POST /tools/{name} and MCP answer one line with a way out, never a 500."""
    text = client.post("/api/v1/tools/nope", json={}, headers={"Accept": "text/plain"})
    assert text.status_code == 200 and text.text.startswith("error: unknown tool nope (tools: find, discover,")
    for bad in (["a"], {"x": 1}, 5):
        r = client.post("/api/v1/tools/find", json={"project": bad}, headers={"Accept": "text/plain"})
        assert r.status_code == 200 and r.text.startswith("error: project must be a project name"), (bad, r.text)
    out = rpc(client, "tools/call", {"name": "nope", "arguments": {}})["result"]
    assert out["isError"] and out["content"][0]["text"].startswith("error: unknown tool nope (tools: find,")
    out = rpc(client, "tools/call", {"name": "find", "arguments": {"project": ["a"]}})["result"]
    assert out["isError"] and out["content"][0]["text"].startswith("error: project must be")
    with client.lg.db.read() as conn:
        assert not conn.execute("SELECT 1 FROM call_stats WHERE tool='nope'").fetchone()  # only real tools are counted


def test_members_and_agents_get_only_the_library_settings(client):
    agent = {"Authorization": "Bearer " + accounts.create_token(client.lg.db, "bot").token}
    client.lg.set_config(accounts.SYSTEM, {"SMTP_HOST": "smtp.example.com", "PUBLIC_URL": "https://lit.example.com"})
    view = client.get("/api/v1/config", headers=agent).json()
    keys = {f["key"] for f in view["fields"]}
    assert {"TOOLS", "MAX_UPLOAD_MB", "S2_API_KEY"} <= keys
    assert not keys & {"SMTP_HOST", "PUBLIC_URL", "OAUTH_CLIENT_HOSTS", "BACKUP_DIR", "SIGNUP", "GITHUB_CLIENT_ID"}
    assert view["warnings"] == [] and view["editable"] is False and "smtp.example.com" not in json.dumps(view)
    assert {"SMTP_HOST", "BACKUP_DIR"} <= {f["key"] for f in client.get("/api/v1/config").json()["fields"]}  # admins


def test_exports_are_served_to_their_project_and_pruned(client, monkeypatch):
    from litledger import formats
    lg = client.lg
    thesis = {"Authorization": "Bearer " + accounts.create_token(lg.db, "t", projects=["thesis"]).token,
              "X-Litledger-Project": "thesis"}
    name = formats.save_export(lg.settings.export_dir, "web", "bibtex", "bib", "@misc{x}")
    assert name.startswith("web~bibtex-") and client.get(f"/api/v1/exports/{name}").text == "@misc{x}"
    assert client.get(f"/api/v1/exports/{name}", headers=thesis).status_code == 404  # made in another project
    mine = formats.save_export(lg.settings.export_dir, "thesis", "csl", "json", "[]")
    assert client.get(f"/api/v1/exports/{mine}", headers=thesis).status_code == 200
    (lg.settings.export_dir / "old-bibtex-x.bib").write_text("x")  # from before names carried the project
    assert client.get("/api/v1/exports/old-bibtex-x.bib", headers=thesis).status_code == 404
    assert client.get("/api/v1/exports/old-bibtex-x.bib").status_code == 200
    import os
    import time
    stale = time.time() - (formats.EXPORT_KEEP_DAYS + 1) * 86400
    os.utime(lg.settings.export_dir / name, (stale, stale))
    monkeypatch.setattr(formats, "EXPORT_KEEP", 2)
    for i in range(3):
        formats.save_export(lg.settings.export_dir, "web", "ris", "ris", str(i))
    left = [p.name for p in lg.settings.export_dir.iterdir()]
    assert len(left) == 2 and name not in left and all(n.startswith("web~ris-") for n in left)  # stale and surplus gone


def test_the_client_address_comes_from_a_trusted_proxy_only(tmp_path):
    """Behind a reverse proxy listed in LITLEDGER_TRUSTED_PROXIES, rate limits and the audit log see the client's
    address (X-Forwarded-For) and https (X-Forwarded-Proto); from anyone else those headers are ignored."""
    from litledger.config import Settings
    from litledger.ledger import Ledger
    from conftest import ReplayTransport
    from litledger.jobs import HANDLERS

    def failed_ip(settings_env, headers):
        lg = Ledger(Settings.from_env({"LITLEDGER_DATA": str(tmp_path / str(len(settings_env))), **settings_env}),
                    transport=ReplayTransport(), run_jobs_inline=True, handlers=HANDLERS)
        with TestClient(create_app(ledger=lg), base_url="http://127.0.0.1:8765") as c:
            r = c.post("/auth/password", json={"email": "x@example.com", "password": "nope"},
                       headers={"X-Litledger-CSRF": "1", **headers})
            assert r.status_code == 401
            secure = c.post("/auth/token", json={"token": (lg.settings.token_dir / "admin").read_text().strip()},
                            headers={"X-Litledger-CSRF": "1", **headers}).headers["set-cookie"]
        with lg.db.read() as conn:
            return conn.execute("SELECT ip FROM audit WHERE op='signin.failed'").fetchone()["ip"], "secure" in secure.lower()

    fwd = {"X-Forwarded-For": "203.0.113.9", "X-Forwarded-Proto": "https"}
    assert failed_ip({"LITLEDGER_TRUSTED_PROXIES": "testclient"}, fwd) == ("203.0.113.9", True)
    assert failed_ip({}, fwd) == ("testclient", False)  # the default trusts only 127.0.0.1


def test_https_public_address_behind_an_untrusted_proxy_is_logged_once(client, caplog):
    client.lg.set_config(accounts.SYSTEM, {"PUBLIC_URL": "https://lit.example.com"})
    with caplog.at_level("WARNING", logger="litledger"):
        for _ in range(3):
            client.get("/api/v1/me", headers={"X-Forwarded-Proto": "https"})
    warned = [r for r in caplog.records if "LITLEDGER_TRUSTED_PROXIES" in r.getMessage()]
    assert len(warned) == 1


def test_events_replay_what_a_reconnecting_tab_missed(tmp_path):
    """/events replays the project's entries after the last id it was given: the Last-Event-ID header EventSource sends
    on its own reconnects, or ?last_id= when the page reopens the stream itself."""
    import anyio
    from litledger.db import Actor
    from conftest import call
    lg = make_ledger(tmp_path)
    app = create_app(ledger=lg)
    token = accounts.create_token(lg.db, "watcher", admin=True).token
    p = Actor(name="t", kind="agent", project="p")
    call(lg, p, "tag", action="define", tags=["first"])
    with lg.db.read() as conn:
        seen = conn.execute("SELECT max(seq) FROM journal").fetchone()[0]
    call(lg, p, "tag", action="define", tags=["missed"])

    async def stream(query: bytes, headers: list) -> str:
        sent: list = []

        async def receive():
            await anyio.sleep(0.5)  # the tab goes away after the replay
            return {"type": "http.disconnect"}

        async def send(message):
            sent.append(message)

        scope = {"type": "http", "method": "GET", "path": "/events", "raw_path": b"/events", "query_string": query,
                 "root_path": "", "scheme": "http", "server": ("127.0.0.1", 8765), "client": ("127.0.0.1", 5000),
                 "http_version": "1.1", "headers": [(b"authorization", f"Bearer {token}".encode()), (b"x-litledger-project", b"p"),
                                                    *headers]}
        with anyio.fail_after(10):
            await app(scope, receive, send)
        return b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body").decode()

    by_query = anyio.run(stream, f"last_id={seen}".encode(), [])
    by_header = anyio.run(stream, b"", [(b"last-event-id", str(seen).encode())])
    for body in (by_query, by_header):
        assert '"missed"' in body and '"first"' not in body and body.startswith("retry: 3000")
    assert '"missed"' not in anyio.run(stream, b"", [])  # a fresh stream starts now
