"""Tool-level behaviour, argument checking, errors and the token-economy budgets (SPEC §4.1)."""
import json
import sqlite3

from litledger import tools
from litledger.db import now
from litledger.textutil import est_tokens

from conftest import call, item

PAPERS = ["1706.03762", "10.18653/v1/N19-1423", "2412.06464", "2102.11174", "https://proceedings.mlr.press/v162/baevski22a.html"]


def test_schema_budgets():
    def size(sets):
        names = tools.toolset_names(sets)
        return est_tokens(json.dumps([{"name": n, "description": tools.TOOLS[n].description,
                                       "inputSchema": tools.mcp_schema(tools.TOOLS[n])} for n in names]))
    assert size(["core"]) <= 1200
    assert size(["all"]) <= 3500
    assert set(tools.toolset_names(["core"])) == {"find", "discover", "resolve", "work", "read", "note", "tag", "export"}


def test_capture_find_work_export_budgets(lg, actor):
    out = call(lg, actor, "resolve", items=PAPERS, add=True, tags=["phase:baselines"], why="main baselines")
    assert out.startswith("ok 5/5 · 5 added · 5 new to library"), out
    assert est_tokens(out) <= 8 * len(PAPERS) + 40  # ≈ one citekey per item plus a header
    out = call(lg, actor, "find", tags=["phase:*"])
    assert out.splitlines()[0].startswith("5 works")
    assert est_tokens(out) <= 60 * 5 + 30
    assert "why: main baselines" in out
    out = call(lg, actor, "work", work="vaswani2017attention")
    assert out.startswith("vaswani2017attention · Attention Is All You Need")
    assert est_tokens(out) <= 200
    out = call(lg, actor, "export", tags=["phase:baselines"])
    assert out.startswith("% 5 entries") and "@inproceedings{devlin2019bert," in out
    err = call(lg, actor, "work", work="nonexistent2099thing")
    assert err.startswith("no_match") and est_tokens(err) <= 60


def test_tags_namespaces_and_hints(lg, actor):
    call(lg, actor, "resolve", items=PAPERS[:2], add=True, tags=["phase:baselines"])
    out = call(lg, actor, "tag", action="apply", tags=["phase:baseline"], works=["vaswani2017attention"])
    assert "did you mean phase:baselines" in out
    call(lg, actor, "tag", action="merge", tags=["phase:baseline"], to="phase:baselines")
    listing = call(lg, actor, "tag", action="list")
    assert "phase:baselines · 2" in listing and "phase:baseline ·" not in listing
    call(lg, actor, "tag", action="apply", tags=["survey"], works=["devlin2019bert"], scope="library")
    other = type(actor)(name="tester", kind="agent", project="other")
    assert "survey" in call(lg, other, "tag", action="list")  # library-wide tags are visible everywhere
    assert call(lg, actor, "find", tags=["-survey"]).startswith("1 work")
    assert call(lg, actor, "find", tags=["phase:*|survey"]).startswith("2 works")


def test_library_scope_and_projects(lg, actor):
    call(lg, actor, "resolve", items=PAPERS[:1], add=True)
    other = type(actor)(name="tester", kind="agent", project="other")
    assert call(lg, other, "find").startswith("0 works")
    assert call(lg, other, "find", scope="library").startswith("1 work")
    assert "vaswani2017attention" in call(lg, other, "find", query="attention", scope="library")


def test_discover_collapses_known_and_reuses_search(lg, actor):
    call(lg, actor, "resolve", items=["2412.06464"], add=True)
    first = call(lg, actor, "discover", query="gated delta networks mamba2 delta rule", sources=["arxiv", "hf"], limit=5)
    assert "already in project: yang2024gated" in first.splitlines()[0]
    second = call(lg, actor, "discover", query="Gated delta networks: Mamba2 delta rule", sources=["arxiv", "hf"], limit=5)
    head = second.splitlines()[0]
    assert "same query" in head and "refresh=true to rerun" in head, second  # served from the logged search
    assert "already in project: yang2024gated" in head


def test_status_mentions_setup(lg, actor):
    out = call(lg, actor, "status")
    assert "WARN Semantic Scholar" in out and "sources on:" in out
    assert "S2_API_KEY" in tools.instructions(lg) and "Admin → Sources" in tools.instructions(lg)


def test_keyless_semantic_scholar_is_opt_in_and_off_the_hot_path(tmp_path):
    from conftest import make_ledger
    lg = make_ledger(tmp_path, enable="s2")
    st = lg.providers.states["s2"]
    assert st.enabled and "keyless" in st.note
    assert not lg.providers.get("s2").discover_default
    assert "s2" not in [p.id for p in lg.providers.discover_sources(None)[0]]
    assert any(w["provider"] == "s2" for w in lg.providers.warnings())  # still recommends a key
    assert not make_ledger(tmp_path / "b").providers.states["s2"].enabled


def test_scalar_where_a_list_is_expected_is_one_item(off, actor):
    out = call(off, actor, "resolve", items=json.dumps([item("A paper on tags", "Tagg", 2020)]), add="true", tags="phase:base")
    assert out.startswith("ok 1/1") and "new tags: phase:base" in out, out
    listing = call(off, actor, "tag", action="list")
    assert listing.splitlines()[0] == "1 tags" and "phase:base · 1" in listing
    assert call(off, actor, "find", tags="phase:base", limit="5").startswith("1 work")


def test_wrong_argument_types_get_actionable_errors(off, actor):
    assert call(off, actor, "resolve", items=["x"], tags=[{"a": 1}]) == 'error: tags must be a list of strings, e.g. ["phase:x"]'
    assert call(off, actor, "note", items=[{"work": "x", "text": {"a": 1}}]) == "error: items[0].text must be a string, not an object"
    assert call(off, actor, "find", limit="many") == "error: limit must be a number"
    assert call(off, actor, "find", kind="papers").startswith("error: kind must be one of works, passages")
    assert call(off, actor, "update_work", items=[{"work": "x", "read": "half"}]).startswith("error: items[0].read must be one of")
    assert call(off, actor, "work") == "error: work required"


def test_errors_are_typed_with_a_hint(off, actor, monkeypatch):
    def boom(exc):
        def handler(lg, actor, a):
            raise exc
        return handler
    for exc, expected in ((KeyError("id"), "error: KeyError in status — required field 'id' is missing"),
                          (IndexError("x"), "error: IndexError in status — "),
                          (sqlite3.IntegrityError("UNIQUE"), "error: IntegrityError in status — conflicts with existing data"),
                          (sqlite3.OperationalError("database is locked"), "error: OperationalError in status — server busy"),
                          (LookupError("unknown work 'zz'"), "no_match: unknown work 'zz'")):
        monkeypatch.setattr(tools.TOOLS["status"], "handler", boom(exc))
        assert call(off, actor, "status").startswith(expected), (exc, call(off, actor, "status"))


def test_punctuation_only_queries_return_empty_with_a_hint(off, actor):
    call(off, actor, "resolve", add=True, items=[item("A paper on tags", "Tagg", 2020)])
    for kind, head in (("notes", "0 notes"), ("works", "0 works"), ("passages", "0 passages")):
        out = call(off, actor, "find", kind=kind, query="!!!")
        assert out.startswith(head) and "no searchable words" in out, out


def test_failed_snowball_accept_stays_pending(off, actor):
    with off.db.tx(actor, internal="test setup") as tx:
        tx.execute("INSERT INTO projects(id,title,created_at) VALUES('t','t',?)", (now(),))
        tx.execute("INSERT INTO snowball(project,handle,title,direction,seeds,score,state,created_at) "
                   "VALUES('t','arxiv:2999.99999','Unreachable','refs','[]',1,'pending',?)", (now(),))
    out = call(off, actor, "snowball", action="decide", decisions={"handle": "arxiv:2999.99999", "state": "in"})
    assert out.splitlines()[0] == "ok in=0 out=0" and "arxiv:2999.99999:" in out and "(still pending)" in out, out
    assert "arxiv:2999.99999" in call(off, actor, "snowball", action="list")


def test_quiet_recommendations_quiets_what_agents_see(lg, actor):
    from litledger.db import SYSTEM
    lg.set_config(SYSTEM, {"QUIET_RECOMMENDATIONS": "1"})
    assert "WARN" not in call(lg, actor, "status") and "setup incomplete" not in tools.instructions(lg)
    assert lg.providers.warnings()  # still known (Admin → Sources lists them)


def test_editing_a_work_never_adds_it_to_the_project(off, actor):
    from litledger.db import Actor
    other = Actor(name="tester", kind="agent", project="other")
    call(off, other, "resolve", add=True, items=[item("Somebody else's paper", "Ott", 2021)])
    out = call(off, actor, "update_work", items=[{"work": "ott2021somebody", "read": "full"}])
    assert out.splitlines()[1].startswith("error ott2021somebody: not in project t") and "add: true" in out
    assert "not in project" in call(off, actor, "work", work="ott2021somebody")
    assert call(off, actor, "update_work", items=[{"work": "ott2021somebody", "read": "full", "add": True}]) == "ok 1/1"
    assert "read: full" in call(off, actor, "work", work="ott2021somebody")


def test_saving_settings_keeps_the_openalex_budget(lg):
    """The registry is rebuilt on every settings save; OpenAlex's remaining daily credit carries over (a new key
    starts fresh)."""
    from litledger.db import SYSTEM
    oa = lg.providers.states["openalex"].provider
    oa._remaining_usd, oa._reset_at = 0.0001, 9e12
    lg.set_config(SYSTEM, {"MAX_UPLOAD_MB": "50"})
    fresh = lg.providers.states["openalex"].provider
    assert fresh is not oa and (fresh._remaining_usd, fresh._reset_at) == (0.0001, 9e12)
    lg.set_config(SYSTEM, {"OPENALEX_API_KEY": "new-key"})
    assert lg.providers.states["openalex"].provider._remaining_usd is None


def test_discover_uses_one_bounded_pool(lg, actor):
    from litledger import discover
    assert discover.POOL._max_workers == 16
    before = discover.POOL
    call(lg, actor, "discover", query="gated delta networks")
    assert discover.POOL is before


def test_last_seen_memory_is_bounded(lg, monkeypatch):
    from litledger import auth
    monkeypatch.setattr(auth, "SEEN_MAX", 5)
    monkeypatch.setattr(auth, "_seen", auth.OrderedDict())
    for i in range(12):
        auth._touch(lg.db, f"p{i}", None)
    assert list(auth._seen) == [f"p{i}" for i in range(7, 12)]
