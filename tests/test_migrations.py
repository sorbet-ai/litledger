"""Schema migrations: a newer database is refused before anything is written; an older one is backed up and upgraded."""
import sqlite3

import pytest

from litledger.db import SCHEMA_VERSION, Database


def test_newer_database_is_refused_untouched(tmp_path):
    path = tmp_path / "ledger.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE maps(id TEXT PRIMARY KEY)")
        conn.execute(f"PRAGMA user_version={SCHEMA_VERSION + 1}")
    before = path.read_bytes()
    with pytest.raises(RuntimeError, match="newer"):
        Database(path)
    assert path.read_bytes() == before
    with sqlite3.connect(path) as conn:
        assert [r[1] for r in conn.execute("PRAGMA table_info(maps)")] == ["id"]


def test_older_database_is_backed_up_and_upgraded(tmp_path):
    path = tmp_path / "ledger.sqlite3"
    Database(path)  # current schema
    with sqlite3.connect(path) as conn:  # pretend it is v2: drop a v3 column's effect by resetting the version
        conn.execute("PRAGMA user_version=2")
        conn.execute("INSERT INTO journal(op_id,ts,op,payload) VALUES('x','2026-01-01','project.add',?)", ('{"work": "w1"}',))
    db = Database(path)
    assert (tmp_path / "ledger.sqlite3.v2.bak").exists()
    with db.read() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert conn.execute("SELECT subject FROM journal WHERE op_id='x'").fetchone()[0] == "w1"  # backfilled


# The v3 shape of the tables schema v4 changed (columns and tables that were never read or written).
V3_TABLES = """
CREATE TABLE projects(id TEXT PRIMARY KEY, title TEXT NOT NULL, created_at TEXT NOT NULL, settings TEXT NOT NULL DEFAULT '{}');
CREATE TABLE credentials(hash TEXT PRIMARY KEY, principal_id TEXT NOT NULL, kind TEXT NOT NULL, client_id TEXT, family TEXT,
  resource TEXT, created_at TEXT NOT NULL, expires_at TEXT, last_used_at TEXT, revoked_at TEXT, meta TEXT NOT NULL DEFAULT '{}');
CREATE INDEX credentials_principal ON credentials(principal_id, kind);
CREATE TABLE documents(id TEXT PRIMARY KEY, work_id TEXT NOT NULL, version_label TEXT, source TEXT NOT NULL,
  blob_sha256 TEXT, parser TEXT NOT NULL, parser_version TEXT NOT NULL, status TEXT NOT NULL,
  outline TEXT NOT NULL DEFAULT '[]', n_passages INTEGER NOT NULL DEFAULT 0, chars INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL);
CREATE INDEX documents_work ON documents(work_id);
CREATE TABLE notes(id TEXT PRIMARY KEY, project TEXT NOT NULL, subject TEXT NOT NULL, kind TEXT NOT NULL, text TEXT NOT NULL,
  quote TEXT, document_id TEXT, passage_id INTEGER, char_start INTEGER, char_end INTEGER, page INTEGER,
  verification TEXT NOT NULL DEFAULT 'unchecked', principal_id TEXT, agent TEXT, session TEXT, created_at TEXT NOT NULL);
CREATE INDEX notes_subject ON notes(subject);
CREATE TABLE map_layout(map_id TEXT NOT NULL, node_id TEXT NOT NULL, x REAL, y REAL, w REAL, h REAL,
  style TEXT NOT NULL DEFAULT '{}', PRIMARY KEY(map_id, node_id));
CREATE TABLE http_cache(key TEXT PRIMARY KEY, provider TEXT NOT NULL, url TEXT NOT NULL, status INTEGER NOT NULL,
  content_type TEXT, body BLOB NOT NULL, fetched_at TEXT NOT NULL, expires_at REAL NOT NULL);
CREATE TABLE blobs(sha256 TEXT PRIMARY KEY, media_type TEXT NOT NULL, bytes INTEGER NOT NULL, origin_url TEXT,
  fetched_at TEXT NOT NULL);
INSERT INTO projects VALUES('p1', 'P1', '2026-01-01', '{}');
INSERT INTO credentials(hash,principal_id,kind,created_at,last_used_at) VALUES('h1','pX','session','2026-01-01','2026-01-02');
INSERT INTO documents VALUES('d1','w1',NULL,'pdf','abc','pdf','2','ok','[]',3,99,'2026-01-01');
INSERT INTO notes(id,project,subject,kind,text,quote,document_id,passage_id,char_start,char_end,created_at)
  VALUES('n:1','p1','w1','quote','q','q','d1',7,0,1,'2026-01-01');
INSERT INTO map_layout VALUES('map:m','map:m#n1',1.5,2.5,100,50,'{"color":"red"}');
INSERT INTO http_cache VALUES('k','p','u',200,'text/plain',x'00','x',1e12);
INSERT INTO blobs VALUES('abc','application/pdf',3,NULL,'2026-01-01');
PRAGMA user_version=3;
"""


def test_v3_database_upgrades_to_v4_keeping_its_data(tmp_path):
    path = tmp_path / "ledger.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.executescript(V3_TABLES)
    db = Database(path)
    assert (tmp_path / "ledger.sqlite3.v3.bak").exists()
    with sqlite3.connect(tmp_path / "ledger.sqlite3.v3.bak") as old:  # the backup is the untouched v3 file
        assert old.execute("PRAGMA user_version").fetchone()[0] == 3
        assert old.execute("SELECT w FROM map_layout").fetchone()[0] == 100
    with db.read() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        cols = {t: [r[1] for r in conn.execute(f"PRAGMA table_info({t})")] for t in
                ("projects", "credentials", "documents", "notes", "map_layout")}
        assert "settings" not in cols["projects"] and "last_used_at" not in cols["credentials"]
        assert "version_label" not in cols["documents"] and not {"char_start", "char_end"} & set(cols["notes"])
        assert cols["map_layout"] == ["map_id", "node_id", "x", "y", "style"]
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert not {"http_cache", "blobs"} & tables and {"works", "journal", "passages_fts"} <= tables
        assert dict(conn.execute("SELECT * FROM projects").fetchone()) == {"id": "p1", "title": "P1", "created_at": "2026-01-01"}
        assert conn.execute("SELECT source, blob_sha256, n_passages FROM documents").fetchone()[:] == ("pdf", "abc", 3)
        assert conn.execute("SELECT passage_id, quote FROM notes").fetchone()[:] == (7, "q")
        assert conn.execute("SELECT x, y, style FROM map_layout").fetchone()[:] == (1.5, 2.5, '{"color":"red"}')
        assert conn.execute("SELECT principal_id FROM credentials WHERE hash='h1'").fetchone()[0] == "pX"
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    Database(path)  # opening it again changes nothing


def _v4_people(path):
    """A database as v4 left it: every person an administrator, the first-start token a token-only "person"."""
    Database(path)
    with sqlite3.connect(path) as conn:
        rows = [("p1", "admin", "human", "h1", None, None, None, "2026-01-01"),
                ("p2", "ada", "human", None, "ada@example.com", None, None, "2026-01-02"),
                ("p3", "bo", "human", None, "bo@example.com", None, None, "2026-01-03"),
                ("p4", "laptop", "agent", "h4", None, None, None, "2026-01-04"),
                ("p5", "claude-code-ada", "agent", None, None, "p2", "https://claude.ai/x", "2026-01-05")]
        conn.executemany("INSERT INTO principals(id,name,kind,token_hash,email,owner_id,client_id,created_at) "
                         "VALUES(?,?,?,?,?,?,?,?)", rows)
        conn.execute("INSERT INTO oauth_clients(client_id,name,redirect_uris,created_at) VALUES('https://claude.ai/x','Claude Code','[]','x')")
        conn.execute("PRAGMA user_version=4")


def test_v4_people_become_admins_and_the_admin_token_the_root_admin(tmp_path):
    path = tmp_path / "ledger.sqlite3"
    _v4_people(path)
    db = Database(path)
    with db.read() as conn:
        got = {r["name"]: (r["kind"], r["role"], r["origin"], r["label"], bool(r["email_verified_at"]))
               for r in conn.execute("SELECT * FROM principals")}
    assert got == {"admin": ("human", "admin", "bootstrap", None, False), "ada": ("human", "admin", None, None, True),
                   "bo": ("human", "admin", None, None, True), "laptop": ("agent", "member", "token", None, False),
                   "claude-code-ada": ("agent", "member", "oauth", "claude-code", False)}


def test_a_v5_database_gains_the_v6_audit_columns(tmp_path):
    """v6 (the audit log's folding and filters) on a deployed v5 database: rows are kept, columns added, backed up."""
    path = tmp_path / "ledger.sqlite3"
    _v4_people(path)
    Database(path)
    with sqlite3.connect(path) as conn:  # back to the v5 shape
        conn.execute("DROP INDEX audit_actor")
        conn.execute("DROP INDEX audit_subject")
        for column in ("subject_id", "ua", "count", "last_at"):
            conn.execute(f"ALTER TABLE audit DROP COLUMN {column}")
        conn.execute("INSERT INTO audit(ts,actor_id,op,detail) VALUES('2026-01-01','p2','signin','{}')")
        conn.execute("PRAGMA user_version=5")
    db = Database(path)
    assert (tmp_path / "ledger.sqlite3.v5.bak").exists()
    with db.read() as conn:
        assert dict(conn.execute("SELECT op, count, subject_id FROM audit").fetchone()) == {"op": "signin", "count": 1, "subject_id": None}
        assert conn.execute("SELECT role FROM principals WHERE name='ada'").fetchone()[0] == "admin"


# Real databases written by earlier litledger releases (tests/fixtures/db):
# v1 by the first commit (458f8ca), v3 by the pre-refactor tree, v4 by 8c8e80d. Each holds three captured papers in
# project "thesis" with tags, a note, a topic, a link, a mind map, a read depth and an API token "laptop" (the script
# that made them: tests/fixtures/db/make_old_db.py). There was
# never a committed v2 release.
OLD_DBS = {1: "ll_kYx2_4Ahq4y-xz6aYnX_FNQwQcdi2Vgxr2DdNVgHjaM", 3: "ll_Xt8qlScIwysOkkvi-d_vaIVHqFAcM_HeArVrMSTyDb0",
           4: "ll_EPw8SVis7HApXcg7gESjysdLexbw_Gp3qQOscWst_HA"}


@pytest.mark.parametrize("version", sorted(OLD_DBS))
def test_real_old_databases_upgrade_with_their_data(tmp_path, version):
    import shutil
    from pathlib import Path

    from litledger import auth, tools
    from litledger.config import Settings
    from litledger.db import Actor
    from litledger.ledger import Ledger
    data = tmp_path / "data"
    data.mkdir()
    shutil.copy(Path(__file__).parent / "fixtures" / "db" / f"ledger-v{version}.sqlite3", data / "ledger.sqlite3")
    lg = Ledger(Settings.from_env({"LITLEDGER_DATA": str(data), "LITLEDGER_OFFLINE": "1"}))
    assert (data / f"ledger.sqlite3.v{version}.bak").exists()
    with lg.db.read() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert not conn.execute("PRAGMA foreign_key_check").fetchall()
    a = Actor(name="t", kind="agent", project="thesis")
    found = tools.call(lg, a, "find", {"tags": ["phase:baselines"]})
    assert found.splitlines()[0].startswith("3 works · project=thesis")
    assert {"vaswani2017attention", "yang2024gated", "schlag2021linear"} <= set(found.replace("\n", " ").split(" "))
    assert "1 notes" in tools.call(lg, a, "work", {"work": "schlag2021linear"})
    assert "read: skimmed" in tools.call(lg, a, "work", {"work": "yang2024gated"})
    assert "todo:read" in tools.call(lg, a, "tag", {"action": "list"})
    assert "topic:linear-attention" in tools.call(lg, a, "entity", {"action": "list"})
    assert "-about-> topic:linear-attention" in tools.call(lg, a, "graph", {"work": "vaswani2017attention", "kind": "evidence"})
    assert "vaswani2017attention" in tools.call(lg, a, "map_get", {"map": "map:memory"})
    assert tools.call(lg, a, "find", {"query": "fast weight"}).splitlines()[1].startswith("schlag2021linear")  # index intact
    agent = auth.verify(lg.db, OLD_DBS[version])  # the old token still signs in, as an agent with library access
    assert agent and agent.name == "laptop" and agent.kind == "agent" and not agent.admin
    # and it keeps working: new writes go through today's code
    assert tools.call(lg, a, "note", {"items": [{"work": "yang2024gated", "text": "after the upgrade"}]}).startswith("ok 1/1")
