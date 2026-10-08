"""Typed knowledge: what a subagent extracts from a paper, merging across papers, leaderboards, map expansion."""
from litledger.prompts import extract_paper

from conftest import call, item


EXTRACT = [
    {"kind": "method", "title": "Gated DeltaNet", "aliases": ["GDN"], "from": "yang2024gated", "rel": "introduces",
     "links": [{"relation": "extends", "target": "method:deltanet"}]},
    {"kind": "method", "title": "DeltaNet", "from": "yang2024gated", "rel": "uses"},
    {"kind": "benchmark", "title": "S-NIAH", "aliases": ["single needle in a haystack"], "from": "yang2024gated"},
    {"kind": "metric", "title": "accuracy", "data": {"higher_is_better": True}},
    {"kind": "result", "from": "yang2024gated", "data": {"method": "Gated DeltaNet", "benchmark": "S-NIAH", "metric": "accuracy",
                                                         "value": 91.8, "setting": "8K", "role": "proposed", "source": "Table 2"}},
    {"kind": "result", "from": "yang2024gated", "data": {"method": "Mamba2", "benchmark": "S-NIAH", "metric": "accuracy",
                                                         "value": 30.4, "setting": "8K", "role": "baseline", "source": "Table 2"}},
    {"kind": "limitation", "title": "Gating trades recall for forgetting", "from": "yang2024gated"},
]


def test_extract_merge_and_leaderboard(lg, actor):
    call(lg, actor, "resolve", items=["2412.06464", "2406.06484"], add=True)
    out = call(lg, actor, "entity", items=EXTRACT)
    assert out.startswith("ok 7/7"), out
    assert "method:gated-deltanet" in out and "benchmark:s-niah" in out
    # A second paper mentioning the same things by alias merges instead of duplicating.
    out = call(lg, actor, "entity", items=[{"kind": "method", "title": "GDN", "from": "yang2024parallelizing", "rel": "uses"},
                                           {"kind": "benchmark", "title": "Single Needle in a Haystack", "from": "yang2024parallelizing"}])
    assert "matched existing: method:gated-deltanet benchmark:s-niah" in out, out
    listing = call(lg, actor, "entity", action="list", kind="method")
    assert listing.startswith("2 items") and "papers: yang2024gated yang2024parallelizing" in listing
    board = call(lg, actor, "entity", action="results", benchmark="S-NIAH")
    lines = board.splitlines()
    assert lines[0] == "S-NIAH · accuracy"
    assert lines[1].strip() == "8K: Gated DeltaNet 91.8 · Mamba2 30.4  (yang2024gated)", board
    work = call(lg, actor, "work", work="yang2024gated", include=["knowledge"])
    assert "method: gated-deltanet, deltanet" in work or "method: deltanet, gated-deltanet" in work
    # Library-wide: another project sees the same benchmark.
    other = type(actor)(name="tester", kind="agent", project="other")
    assert "benchmark:s-niah" in call(lg, other, "entity", action="list", query="needle")


def test_project_thinking_stays_in_project_and_maps_expand(lg, actor):
    call(lg, actor, "resolve", items=["2412.06464"], add=True)
    call(lg, actor, "entity", items=EXTRACT[:3])
    out = call(lg, actor, "entity", items=[{"kind": "hypothesis", "title": "Decay hurts long-context recall",
                                            "links": [{"relation": "about", "target": "method:gated-deltanet"}]}])
    assert "hypothesis:decay-hurts-long-context-recall" in out
    other = type(actor)(name="tester", kind="agent", project="other")
    assert "hypothesis" not in call(lg, other, "entity", action="list")
    res = call(lg, actor, "map_edit", title="GDN", ops=[{"op": "expand", "ref": "yang2024gated"}])
    assert res.startswith("ok map:gdn"), res
    outline = call(lg, actor, "map_get", map="map:gdn")
    assert "- n1 yang2024gated" in outline and '"methods"' in outline and "method:gated-deltanet" in outline
    assert "benchmark:s-niah" in outline
    # expand again under the same node adds nothing new
    again = call(lg, actor, "map_edit", map="map:gdn", ops=[{"op": "expand", "id": "n1"}])
    assert "added" not in again
    out = call(lg, actor, "entity", action="delete", ids=["benchmark:s-niah"])
    assert "deleted: benchmark:s-niah" in out
    assert "benchmark:s-niah" not in call(lg, actor, "map_get", map="map:gdn")


def test_extract_brief_mentions_contract():
    text = extract_paper("yang2024gated", focus="state tracking")
    assert '"from": "yang2024gated"' in text and "result_of" in text and "state tracking" in text


def test_links_by_title_in_same_call_and_loud_failures(lg, actor):
    call(lg, actor, "resolve", items=["2412.06464"], add=True)
    out = call(lg, actor, "entity", items=[
        {"kind": "method", "title": "DeltaNet", "from": "yang2024gated", "rel": "uses"},
        {"kind": "method", "title": "Gated DeltaNet", "aliases": ["GDN"], "from": "yang2024gated", "rel": "introduces",
         "links": [{"relation": "extends", "target": "DeltaNet"}, {"relation": "outperforms", "target": "Nonexistent Thing"}]},
    ])
    assert "links 3 added, 1 FAILED" in out, out
    assert "Nonexistent Thing" in out and "use an item id" in out


def test_tables_are_readable(lg, actor):
    call(lg, actor, "resolve", items=["2412.06464"], add=True)
    outline = call(lg, actor, "read", work="yang2024gated")
    assert "7 tables" in outline.splitlines()[0]
    table = call(lg, actor, "read", work="yang2024gated", mode="section", target="Table 2")
    assert "Gated DeltaNet" in table and "91.8" in table and " | " in table


def test_results_are_identified_and_grouped_through_aliases(off, actor):
    call(off, actor, "resolve", add=True, items=[item("Gated memories for recurrent models", "Yang", 2024, arxiv="2412.00001")])
    call(off, actor, "entity", items=[{"kind": "method", "title": "Gated DeltaNet", "aliases": ["GDN"]},
                                      {"kind": "benchmark", "title": "S-NIAH"}, {"kind": "metric", "title": "accuracy"}])
    r1 = {"kind": "result", "from": "yang2024gated", "data": {"method": "GDN", "benchmark": "SNIAH", "metric": "accuracy",
                                                               "value": 91.8, "setting": "8K"}}
    out = call(off, actor, "entity", items=[r1])
    assert "created: result:" in out
    out = call(off, actor, "entity", items=[{**r1, "data": {**r1["data"], "method": "Gated DeltaNet", "benchmark": "S-NIAH", "value": 92.0}}])
    assert "matched existing: result:" in out, out
    call(off, actor, "entity", items=[{"kind": "result", "from": "yang2024gated",
                                       "data": {"method": "Mamba2", "benchmark": "s niah", "metric": "Accuracy", "value": 30.4, "setting": "8K"}}])
    with off.db.read() as conn:
        assert conn.execute("SELECT count(*) FROM entities WHERE kind='result'").fetchone()[0] == 2
    board = call(off, actor, "entity", action="results", benchmark="SNIAH")
    assert board.splitlines() == ["S-NIAH · accuracy", "  8K: Gated DeltaNet 92.0 · Mamba2 30.4  (yang2024gated)"], board


def test_entity_lookup_by_alias_key(off, actor):
    call(off, actor, "entity", items=[{"kind": "benchmark", "title": "S-NIAH", "aliases": ["single needle in a haystack"]}])
    out = call(off, actor, "entity", items=[{"kind": "benchmark", "title": "SNIAH"}, {"kind": "benchmark", "title": "Single-Needle in a Haystack"}])
    assert "matched existing: benchmark:s-niah benchmark:s-niah" in out
    out = call(off, actor, "entity", items=[{"kind": "topic", "title": "Recall", "links": [{"relation": "about", "target": "sniah"}]}])
    assert "links 1 added" in out and "FAILED" not in out
