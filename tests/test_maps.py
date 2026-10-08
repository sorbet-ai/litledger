"""Mind maps: outlines, edges and promotion to links, per-op isolation, structure checks, versions, safe exports and
id reuse."""
import json
import re
import xml.etree.ElementTree as ET

from litledger import maps

from conftest import call


def edit(lg, actor, ref, ops, **kw):
    return maps.edit(lg, actor, actor.project, ref, ops, **kw)


def node_ids(lg, actor, ref):
    return {n["id"]: n for n in maps.get_map(lg, actor.project, ref)["nodes"]}


def test_maps_outline_edges_and_promotion(lg, actor):
    call(lg, actor, "resolve", items=["2412.06464", "2006.16236"], add=True)
    call(lg, actor, "entity", items=[{"kind": "topic", "title": "Delta-rule memory"}])
    out = call(lg, actor, "map_edit", title="Memory lit", ops=[
        {"op": "add", "key": "root", "ref": "topic:delta-rule-memory"},
        {"op": "add", "key": "a", "ref": "yang2024gated", "parent": "root"},
        {"op": "add", "key": "b", "ref": "katharopoulos2020transformers", "parent": "root", "text": "linear attention origin"},
        {"op": "add", "text": "open question: decay vs erase?", "parent": "root"},
        {"op": "edge", "src": "a", "dst": "b", "label": "extends"},
    ])
    assert out.startswith("ok map:memory-lit"), out
    outline = call(lg, actor, "map_get", map="map:memory-lit")
    assert "- n1 topic:delta-rule-memory" in outline and "  - n2 yang2024gated" in outline
    assert "e1 n2 -extends-> n3" in outline
    call(lg, actor, "map_edit", map="map:memory-lit", ops=[{"op": "promote", "edge": "e1", "relation": "extends"}])
    assert "[link]" in call(lg, actor, "map_get", map="map:memory-lit")
    evidence = call(lg, actor, "graph", work="yang2024gated", kind="evidence")
    assert "yang2024gated -extends-> katharopoulos2020transformers" in evidence
    canvas = json.loads(call(lg, actor, "map_get", map="map:memory-lit", format="canvas"))
    assert len(canvas["nodes"]) == 4 and any(e.get("label") == "extends" for e in canvas["edges"])
    assert call(lg, actor, "map_get", map="map:memory-lit", format="mermaid").startswith("mindmap")


def test_deleted_map_frees_its_id(lg, actor):
    from litledger import maps
    call(lg, actor, "map_edit", title="Scratch", ops=[{"op": "add", "text": "old"}])
    maps.delete_map(lg, actor, actor.project, "map:scratch")
    out = call(lg, actor, "map_edit", title="Scratch", ops=[{"op": "add", "text": "new"}])
    assert out.startswith("ok map:scratch ")
    outline = call(lg, actor, "map_get", map="map:scratch")
    assert "new" in outline and "old" not in outline


def test_readding_deleted_ids_for_undo(lg, actor):
    call(lg, actor, "map_edit", title="Undo", ops=[{"op": "add", "id": "a", "text": "A"}, {"op": "add", "id": "b", "text": "B", "x": 10, "y": 20},
                                                   {"op": "edge", "id": "e", "src": "a", "dst": "b"}])
    call(lg, actor, "map_edit", map="map:undo", ops=[{"op": "delete", "id": "b"}])
    out = call(lg, actor, "map_edit", map="map:undo", ops=[{"op": "add", "id": "b", "text": "B again", "x": 10, "y": 20},
                                                          {"op": "edge", "id": "e", "src": "a", "dst": "b"}])
    # problems are reported as extra lines ("op N: …") after the "ok …" line
    assert out.startswith("ok map:undo · added b e") and len(out.splitlines()) == 1, out
    outline = call(lg, actor, "map_get", map="map:undo")
    assert '- b "B again"' in outline and "e a --> b" in outline, outline


def test_failing_op_mid_batch_keeps_the_others(lg, actor):
    res = edit(lg, actor, None, [
        {"op": "add", "id": "a", "text": "A"},
        {"op": "add", "id": "bad", "text": {"not": "a string"}},
        {"op": "add", "id": "b", "text": "B", "parent": "a"},
        {"op": "edge", "src": "a", "dst": "nope"},
        {"op": "edge", "id": "ab", "src": "a", "dst": "b", "label": ["x"]},
        {"op": "edge", "id": "ab2", "src": "a", "dst": "b", "label": "fine"},
        "not an op",
    ], title="Batch")
    assert res["added"] == ["a", "b", "ab2"]
    assert [r["ok"] for r in res["results"]] == [True, False, True, False, False, True, False]
    assert [r["i"] for r in res["results"]] == list(range(7))
    assert "text must be a string" in res["results"][1]["error"]
    assert "unknown node nope" in res["results"][3]["error"]
    assert "label must be a string" in res["results"][4]["error"]
    assert res["problems"][0].startswith("op 2: ") and len(res["problems"]) == 4
    nodes = node_ids(lg, actor, res["map"])
    assert set(nodes) == {"a", "b"} and nodes["b"]["parent"] == "a"
    # the agent-facing text keeps its shape: "ok …" then one line per problem
    out = call(lg, actor, "map_edit", map=res["map"], ops=[{"op": "add", "text": "C"}, {"op": "add", "text": 5}])
    assert out.splitlines()[0] == f"ok {res['map']} · added n3" and out.splitlines()[1].startswith("op 2: ")


def test_unexpected_database_error_rolls_back_only_that_op(lg, actor, monkeypatch):
    res = edit(lg, actor, None, [{"op": "add", "id": "a", "text": "A"}], title="Boom")
    real = maps._set_style

    def boom(tx, map_id, node, style, x=None, y=None):
        if node.endswith("#b"):
            tx.execute("INSERT INTO map_nodes(id,map_id,created_at) VALUES('dup','x','y')")
            tx.execute("INSERT INTO map_nodes(id,map_id,created_at) VALUES('dup','x','y')")  # IntegrityError
        return real(tx, map_id, node, style, x, y)

    monkeypatch.setattr(maps, "_set_style", boom)
    out = edit(lg, actor, res["map"], [{"op": "add", "id": "b", "text": "B"}, {"op": "add", "id": "c", "text": "C"}])
    assert [r["ok"] for r in out["results"]] == [False, True]
    assert "rejected by the database" in out["results"][0]["error"]
    assert set(node_ids(lg, actor, res["map"])) == {"a", "c"}
    with lg.db.read() as conn:  # the half-done op left nothing behind
        assert not conn.execute("SELECT 1 FROM map_nodes WHERE id='dup'").fetchone()


def test_auto_ids_skip_client_chosen_ones(lg, actor):
    res = edit(lg, actor, None, [{"op": "add", "id": "n3", "text": "client"}, {"op": "add", "id": "x", "text": "X"},
                                 {"op": "edge", "id": "e2", "src": "n3", "dst": "x"}], title="Ids")
    out = edit(lg, actor, res["map"], [{"op": "add", "key": "k", "text": "auto"}, {"op": "edge", "src": "k", "dst": "x"},
                                       {"op": "edge", "src": "x", "dst": "n3"}])
    assert out["problems"] == [] and out["added"] == ["n4", "e3", "e4"], out
    assert set(node_ids(lg, actor, res["map"])) == {"n3", "x", "n4"}


def test_parent_must_exist_in_the_map_and_not_loop(lg, actor):
    other = edit(lg, actor, None, [{"op": "add", "id": "o", "text": "other map"}], title="Other")
    res = edit(lg, actor, None, [{"op": "add", "id": "a", "text": "A"}, {"op": "add", "id": "b", "text": "B", "parent": "a"},
                                 {"op": "add", "id": "c", "text": "C", "parent": "b"}], title="Tree")
    m = res["map"]
    out = edit(lg, actor, m, [
        {"op": "update", "id": "a", "parent": "c"},           # loop through the branch
        {"op": "update", "id": "b", "parent": "b"},           # own parent
        {"op": "add", "id": "d", "text": "D", "parent": "missing"},
        {"op": "add", "id": "e", "text": "E", "parent": f"{other['map']}#o"},  # another map's node
        {"op": "update", "id": "c", "parent": "a"},           # fine
    ])
    assert [r["ok"] for r in out["results"]] == [False, False, False, False, True], out
    assert "loop" in out["results"][0]["error"] and "unknown parent missing" in out["results"][2]["error"]
    edit(lg, actor, m, [{"op": "delete", "id": "c"}])
    gone = edit(lg, actor, m, [{"op": "add", "id": "f", "text": "F", "parent": "c"}, {"op": "update", "id": "b", "parent": "c"}])
    assert [r["ok"] for r in gone["results"]] == [False, False]  # a deleted node can't be a parent
    nodes = node_ids(lg, actor, m)
    assert set(nodes) == {"a", "b"} and nodes["a"]["parent"] is None


def test_edges_and_promotion_stay_inside_the_map(lg, actor):
    call(lg, actor, "entity", items=[{"kind": "topic", "title": "Alpha"}, {"kind": "topic", "title": "Beta"}])
    a = edit(lg, actor, None, [{"op": "add", "id": "x", "ref": "topic:alpha"}, {"op": "add", "id": "y", "ref": "topic:beta"},
                               {"op": "edge", "id": "xy", "src": "x", "dst": "y", "label": "about"}], title="A")
    b = edit(lg, actor, None, [{"op": "add", "id": "p", "ref": "topic:alpha"}], title="B")
    out = edit(lg, actor, b["map"], [{"op": "edge", "src": "p", "dst": f"{a['map']}#y"},
                                     {"op": "edge", "src": f"{a['map']}#x", "dst": f"{a['map']}#y"},
                                     {"op": "promote", "edge": f"{a['map']}#xy"},
                                     {"op": "edge_update", "id": f"{a['map']}#xy", "label": "hijack"},
                                     {"op": "unedge", "id": f"{a['map']}#xy"}])
    assert [r["ok"] for r in out["results"]] == [False] * 5, out
    assert any(p.startswith("promote: unknown edge") for p in out["problems"])
    assert maps.get_map(lg, actor.project, b["map"])["edges"] == []
    edge = maps.get_map(lg, actor.project, a["map"])["edges"][0]
    assert edge["label"] == "about" and not edge["promoted"]
    # promotion inside its own map still works, and a deleted edge can't be promoted
    ok = edit(lg, actor, a["map"], [{"op": "promote", "edge": "xy"}])
    assert ok["results"] == [{"i": 0, "ok": True}] and ok["changed"] == 1
    edit(lg, actor, a["map"], [{"op": "edge", "id": "yx", "src": "y", "dst": "x"}, {"op": "unedge", "id": "yx"}])
    dead = edit(lg, actor, a["map"], [{"op": "promote", "edge": "yx"}])
    assert dead["results"][0]["ok"] is False


def test_version_bumps_once_per_changing_call_and_flags_conflicts(lg, actor):
    res = edit(lg, actor, None, [{"op": "add", "id": "a", "text": "A"}, {"op": "add", "id": "b", "text": "B"}], title="V")
    m = res["map"]
    assert res["version"] == 1 and res["conflict"] is False
    assert maps.get_map(lg, actor.project, m)["version"] == 1
    assert next(x for x in maps.list_maps(lg, actor.project) if x["id"] == m)["version"] == 1
    nothing = edit(lg, actor, m, [{"op": "update", "id": "zz", "text": "?"}], base_version=1)
    assert nothing["version"] == 1 and nothing["conflict"] is False and nothing["results"][0]["ok"] is False
    two = edit(lg, actor, m, [{"op": "move", "id": "a", "x": 1, "y": 2}, {"op": "update", "id": "b", "text": "B2"}], base_version=1)
    assert two["version"] == 2 and two["conflict"] is False
    stale = edit(lg, actor, m, [{"op": "update", "id": "a", "text": "A2"}], base_version=1)
    assert stale["conflict"] is True and stale["version"] == 3 and stale["results"][0]["ok"]
    assert node_ids(lg, actor, m)["a"]["text"] == "A2"  # applied anyway; the editor rebases
    renamed = edit(lg, actor, m, [], title="V renamed", base_version=3)
    assert renamed["version"] == 4 and maps.get_map(lg, actor.project, m)["title"] == "V renamed"
    with lg.db.read() as conn:
        payloads = [r["payload"] for r in conn.execute("SELECT payload FROM journal WHERE op='map.edit' ORDER BY seq")]
    assert len(payloads) == 4 and json.loads(payloads[-1])["version"] == 4


def test_outline_shows_nodes_in_loops_and_with_missing_parents(lg, actor):
    res = edit(lg, actor, None, [{"op": "add", "id": "a", "text": "A"}, {"op": "add", "id": "b", "text": "B"},
                                 {"op": "add", "id": "c", "text": "C"}], title="Loops")
    m = res["map"]
    with lg.db.tx(actor, internal="test setup") as tx:  # old data written before parents were checked
        tx.execute("UPDATE map_nodes SET parent=? WHERE id=?", (f"{m}#b", f"{m}#a"))
        tx.execute("UPDATE map_nodes SET parent=? WHERE id=?", (f"{m}#a", f"{m}#b"))
        tx.execute("UPDATE map_nodes SET parent=? WHERE id=?", (f"{m}#gone", f"{m}#c"))
    data = maps.get_map(lg, actor.project, m)
    text = maps.outline(data)
    assert "3 nodes" in text and '- a "A"' in text and '  - b "B"' in text and '- c "C"' in text, text
    assert maps.to_mermaid(data).count('["') == 3 and maps.to_opml(data).count("<outline") == 3


def test_opml_and_mermaid_escape_text(lg, actor):
    nasty = 'line one\nline "two" [x] (y) {z} <b>&amp;'
    res = edit(lg, actor, None, [{"op": "add", "id": "a", "text": nasty}, {"op": "add", "id": "b", "text": "kid `tick`", "parent": "a"},
                                 {"op": "add", "id": "c", "text": "ctrl\x01char"}], title='R&D <maps> "quoted"')
    data = maps.get_map(lg, actor.project, res["map"])
    root = ET.fromstring(maps.to_opml(data).encode("utf-8"))
    assert root.find("head/title").text == 'R&D <maps> "quoted"'
    outlines = root.findall("body/outline")
    assert outlines[0].get("text") == nasty and outlines[0].find("outline").get("text") == "kid `tick`"
    assert outlines[1].get("text") == "ctrlchar"
    mm = maps.to_mermaid(data).splitlines()
    assert mm[0] == "mindmap" and mm[1] == "  root((\"R&D <maps> 'quoted'\"))"
    assert len(mm) == 5
    for line in mm[2:]:
        assert re.fullmatch(r'\s+n\d+\["[^"`\n]*"\]', line), line
    assert mm[2] == "    n1[\"line one line 'two' [x] (y) {z} <b>&amp;\"]" and mm[3] == "      n2[\"kid 'tick'\"]"


def test_readding_deleted_ids_replaces_them(lg, actor):
    res = edit(lg, actor, None, [{"op": "add", "id": "a", "text": "A"}, {"op": "add", "id": "b", "text": "B", "parent": "a", "x": 5, "y": 6},
                                 {"op": "edge", "id": "e", "src": "a", "dst": "b"}], title="Readd")
    m = res["map"]
    gone = edit(lg, actor, m, [{"op": "delete", "id": "a"}])
    assert gone["results"] == [{"i": 0, "ok": True}] and node_ids(lg, actor, m) == {}
    again = edit(lg, actor, m, [{"op": "delete", "id": "a"}])  # deleting twice is harmless
    assert again["results"][0]["ok"] and again["version"] == gone["version"] + 1
    back = edit(lg, actor, m, [{"op": "add", "id": "a", "text": "A again"}, {"op": "add", "id": "b", "text": "B again", "parent": "a"},
                               {"op": "edge", "id": "e", "src": "a", "dst": "b"}])
    assert back["problems"] == [] and back["added"] == ["a", "b", "e"]
    nodes = node_ids(lg, actor, m)
    assert nodes["b"]["parent"] == "a" and nodes["b"]["text"] == "B again" and nodes["b"]["x"] is None
    unknown = edit(lg, actor, m, [{"op": "delete", "id": "never"}])
    assert unknown["results"][0]["ok"] is False and "unknown node never" in unknown["problems"][0]
