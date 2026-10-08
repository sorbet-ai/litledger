"""Snapshots: a project written to a repo and read back, on a fresh server or into another project, loses nothing."""
import json

from litledger.db import Actor, now
from litledger.portable import import_snapshot

from conftest import call, item, make_ledger, offline_ledger


def test_snapshot_round_trip(lg, actor, tmp_path):
    call(lg, actor, "resolve", items=["1706.03762", "10.18653/v1/N19-1423"], add=True, tags=["phase:x"], why="w")
    call(lg, actor, "note", items=[{"work": "devlin2019bert", "text": "bidirectional"}])
    snap = call(lg, actor, "export", format="snapshot", inline=True).split("\n", 1)[1]
    data = json.loads(snap)
    assert data["litledger_snapshot"] == 1 and len(data["works"]) == 2
    fresh = make_ledger(tmp_path / "other")
    counts = import_snapshot(fresh, Actor(name="restore", project="t"), data)
    assert counts["works_new"] == 2 and counts["notes"] == 1
    again = import_snapshot(fresh, Actor(name="restore", project="t"), data)
    assert again["works_matched"] == 2 and again["notes"] == 0
    out = call(fresh, Actor(name="x", project="t"), "find", tags=["phase:x"])
    assert out.startswith("2 works") and "vaswani2017attention" in out


def _build_project(lg, actor):
    call(lg, actor, "resolve", add=True, tags=["phase:x"], why="core",
         items=[item("Gated memories for recurrent models", "Yang", 2024, arxiv="2412.00001"),
                item("Fast weight programmers revisited", "Schlag", 2021, arxiv="2102.00001"),
                item("A paper we dropped later", "Drop", 2019, arxiv="1901.00001")])
    call(lg, actor, "entity", items=[
        {"kind": "method", "title": "Gated Memory", "aliases": ["GM"], "from": "yang2024gated", "rel": "introduces",
         "links": [{"relation": "extends", "target": "method:fast-weights"}]},
        {"kind": "method", "id": "method:fast-weights", "title": "Fast Weights"},
        {"kind": "benchmark", "title": "NIAH-1", "from": "yang2024gated", "links": [{"relation": "part_of", "target": "benchmark:niah-suite"}]},
        {"kind": "benchmark", "id": "benchmark:niah-suite", "title": "NIAH suite"},
        {"kind": "result", "from": "yang2024gated", "data": {"method": "GM", "benchmark": "NIAH-1", "metric": "acc", "value": 90.1},
         "links": [{"relation": "result_of", "target": "Gated Memory"}, {"relation": "measured_on", "target": "NIAH-1"}]},
        {"kind": "topic", "title": "Memory", "links": [{"relation": "about", "target": "method:gated-memory"}]},
    ])
    call(lg, actor, "note", items=[{"work": "yang2024gated", "text": "gates help", "tags": ["key"]},
                                   {"work": "drop2019paper", "text": "why we dropped it"},
                                   {"work": "topic:memory", "text": "a thought"}])
    res = call(lg, actor, "link", items=[{"source": "schlag2021fast", "target": "yang2024gated", "relation": "cites"}])
    with lg.db.read() as conn:
        lid = conn.execute("SELECT id FROM links WHERE relation='cites'").fetchone()["id"]
    call(lg, actor, "link", items=[{"retract": lid}])
    assert res.startswith("ok 1/1")
    call(lg, actor, "map_edit", title="Mem", ops=[{"op": "add", "key": "r", "ref": "topic:memory"},
                                                  {"op": "add", "key": "a", "ref": "yang2024gated", "parent": "r", "x": 5, "y": 6},
                                                  {"op": "add", "key": "b", "ref": "schlag2021fast", "parent": "r"},
                                                  {"op": "edge", "src": "a", "dst": "b", "label": "extends"}])
    call(lg, actor, "map_edit", map="map:mem", ops=[{"op": "promote", "edge": "e1", "relation": "extends"}])
    call(lg, actor, "update_work", items=[{"work": "drop2019paper", "remove": True}])


def _snapshot(lg, project):
    return json.loads(call(lg, Actor(name="x", project=project), "export", format="snapshot", inline=True).split("\n", 1)[1])


def _norm(data):
    out = {}
    for k, v in data.items():
        if k in ("exported_at",):
            continue
        out[k] = sorted(json.dumps(x, sort_keys=True) for x in v) if isinstance(v, list) else v
    return out


def test_snapshot_round_trip_is_lossless(off, actor, tmp_path):
    _build_project(off, actor)
    first = _snapshot(off, "t")
    assert any(w.get("removed") and w["citekey"] == "drop2019paper" for w in first["works"])
    assert any(n["text"] == "why we dropped it" for n in first["notes"])
    rels = {(l["source"], l["relation"], l["target"]) for l in first["links"]}
    assert ("method:gated-memory", "extends", "method:fast-weights") in rels  # entity-entity links travel too
    assert ("benchmark:niah-1", "part_of", "benchmark:niah-suite") in rels
    assert any(l["relation"] == "result_of" for l in first["links"])
    assert any(l.get("retracted_at") for l in first["links"])
    fresh = offline_ledger(tmp_path, "fresh")
    counts = import_snapshot(fresh, Actor(name="restore", project="t"), first)
    assert counts["notes"] == 3 and counts["maps"] == 1 and counts["works_new"] == 3
    second = _snapshot(fresh, "t")
    assert _norm(second) == _norm(first)
    assert "[link]" in call(fresh, Actor(name="x", project="t"), "map_get", map="map:mem")


def test_snapshot_import_into_another_project_on_the_same_server(off, actor):
    _build_project(off, actor)
    with off.db.tx(actor, internal="test setup") as tx:  # an anchored note: stored text plus a verified quote
        work = tx.execute("SELECT id FROM works WHERE citekey='yang2024gated'").fetchone()["id"]
        tx.execute("INSERT INTO documents(id,work_id,source,parser,parser_version,status,outline,n_passages,chars,created_at) "
                   "VALUES('dTEST',?,'upload','x','1','ok','[]',1,20,?)", (work, now()))
        pid = tx.execute("INSERT INTO passages(document_id,seq,section,page,char_start,char_end,text) VALUES('dTEST',1,'Intro',2,0,20,"
                         "'gates help a lot here')").lastrowid
        tx.execute("UPDATE notes SET document_id='dTEST', passage_id=?, verification='verified' "
                   "WHERE text='gates help'", (pid,))
    data = _snapshot(off, "t")
    q = Actor(name="tester", kind="agent", project="q")
    counts = import_snapshot(off, q, data, project="q")
    assert counts["notes"] == 3 and counts["maps"] == 1 and counts["entities"] >= 1, counts
    assert import_snapshot(off, q, data, project="q") == {"works_new": 0, "works_matched": 3, "notes": 0, "entities": 0,
                                                         "links": 0, "maps": 0}
    copy = _snapshot(off, "q")
    assert len(copy["notes"]) == 3 and len(copy["links"]) == len(data["links"])
    assert sorted(e["title"] for e in copy["entities"]) == sorted(e["title"] for e in data["entities"])
    assert {e["id"] for e in copy["entities"] if e["scope"] == "library"} == {e["id"] for e in data["entities"] if e["scope"] == "library"}
    assert "topic:memory-q" in {e["id"] for e in copy["entities"]}
    anchored = next(n for n in copy["notes"] if n["text"] == "gates help")
    assert anchored["anchor"] == {"document": "dTEST", "seq": 1} and anchored["tags"] == ["key"]
    assert copy["maps"][0]["id"] == "map:mem-q" and all(n["id"].startswith("map:mem-q#") for n in copy["maps"][0]["nodes"])
    assert "[link]" in call(off, q, "map_get", map="map:mem-q")
    assert call(off, actor, "map_get", map="map:mem").count("\n") == call(off, q, "map_get", map="map:mem-q").count("\n")
    # the original project is untouched
    assert len(_snapshot(off, "t")["notes"]) == 3


def test_a_snapshots_citekeys_are_searchable(off, actor):
    """The citekey a snapshot carries replaces the automatic one in the search index; a project citekey is found in
    its project."""
    src = {"ids": {"doi": "10.1234/zz"}, "csl": {"title": "A Very Particular Paper", "author": [{"family": "Zed"}],
                                                 "issued": {"date-parts": [[2020]]}}, "kind": "unknown"}
    snap = {"litledger_snapshot": 1, "project": "p", "tags": [], "notes": [], "entities": [], "links": [], "maps": [],
            "works": [{"citekey": "custom2020key", "citekey_override": "mine2020", "ids": src["ids"], "csl": src["csl"],
                       "sources": {"import": src}}]}
    p = Actor(name="tester", kind="agent", project="p")
    assert import_snapshot(off, p, snap, "p")["works_new"] == 1
    assert call(off, p, "find", query="custom2020key").splitlines()[0] == "1 work · project=p"
    assert call(off, p, "find", query="custom2020key", scope="library").splitlines()[1].startswith("mine2020")
    assert call(off, p, "find", query="zed2020very").startswith("0 works")  # the automatic key is gone
    assert call(off, p, "find", query="mine2020").splitlines()[1].startswith("mine2020 · A Very Particular Paper")
    assert call(off, actor, "find", query="mine2020", scope="library").startswith("0 works")  # another project's key
