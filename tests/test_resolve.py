import json

import pytest

from litledger.providers.base import Record
from litledger.resolve import enrich, parse_spec, resolve_items
from litledger.works import merge_csl

from conftest import call, item, wid


def keys(outcomes):
    return [o.citekey for o in outcomes]


def test_capture_and_dedupe_across_id_forms(lg, actor):
    outs, info = resolve_items(lg, actor, ["1706.03762", "10.18653/v1/N19-1423"], add=True, tags=["phase:background"], why="seed")
    assert [o.status for o in outs] == ["ok", "ok"]
    assert keys(outs) == ["vaswani2017attention", "devlin2019bert"]
    assert all(o.created and o.added for o in outs)
    assert info["created"] == ["phase:background"]
    # Every other spelling of the same paper resolves locally to the same work, without creating anything.
    again, _ = resolve_items(lg, actor, ["https://arxiv.org/html/1706.03762v5", "arXiv:1706.03762v7",
                                         "10.48550/arXiv.1706.03762", "vaswani2017attention"], add=True)
    assert set(keys(again)) == {"vaswani2017attention"}
    assert not any(o.created for o in again) and not any(o.added for o in again)
    with lg.db.read() as conn:
        assert conn.execute("SELECT count(*) FROM works WHERE merged_into IS NULL").fetchone()[0] == 2


def test_title_and_bibtex_and_url_inputs(lg, actor):
    outs, _ = resolve_items(lg, actor, [
        "Gated Delta Networks: Improving Mamba2 with Delta Rule",
        {"title": "Linear Transformers Are Secretly Fast Weight Programmers", "author": "Schlag", "year": 2021},
        "https://proceedings.mlr.press/v162/baevski22a.html",
        "@article{x, title={BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding}, "
        "author={Devlin, Jacob and Chang, Ming-Wei}, year={2019}, doi={10.18653/v1/N19-1423}}",
    ], add=True)
    assert [o.status for o in outs] == ["ok"] * 4, [(o.status, o.message) for o in outs]
    assert keys(outs)[:3] == ["yang2024gated", "schlag2021linear", "baevski2022data2vec"]
    assert keys(outs)[3] == "devlin2019bert"
    with lg.db.read() as conn:
        csl = json.loads(conn.execute("SELECT csl FROM works WHERE citekey='yang2024gated'").fetchone()["csl"])
        # The background enrichment found the ICLR version on DBLP: venue metadata comes from the published record.
        assert "Learning Representations" in csl["container-title"] and csl["type"] == "paper-conference"
        versions = [r["kind"] for r in conn.execute("SELECT kind FROM versions w JOIN works x ON x.id=w.work_id WHERE x.citekey='yang2024gated'")]
        assert sorted(versions) == ["preprint", "published"]


def test_low_trust_openalex_record_does_not_duplicate(lg, actor):
    resolve_items(lg, actor, ["1706.03762"], add=True)
    outs, _ = resolve_items(lg, actor, ["W2626778328"], add=True)  # OpenAlex canonical DOI is a junk 2025 repost
    assert outs[0].status == "ok" and outs[0].citekey == "vaswani2017attention" and not outs[0].created


def test_no_match_reports_hint(lg, actor):
    outs, _ = resolve_items(lg, actor, ["A title that surely does not exist zzqx wobble frobnicate 1999"])
    assert outs[0].status in ("no_match", "unavailable")
    assert outs[0].work_id is None


def test_parse_spec_free_text():
    s = parse_spec("Attention is all you need (Vaswani, 2017)")
    assert s.title == "Attention is all you need" and s.author == "Vaswani" and s.year == 2017
    s = parse_spec("Gated Delta Networks — Yang 2024")
    assert s.title == "Gated Delta Networks" and s.author == "Yang" and s.year == 2024


def test_javascript_urls_are_dropped(off, actor):
    out = call(off, actor, "resolve", add=True,
               items=["@misc{x, title={A misc thing about stuff}, author={Doe, Jane}, year={2020}, url={javascript:alert(document.domain)}}"])
    assert out.startswith("ok 1/1") and "note: dropped non-http URL 'javascript:alert(document.domain)'" in out, out
    with off.db.read() as conn:
        row = conn.execute("SELECT id, csl FROM works").fetchone()
        assert "URL" not in json.loads(row["csl"])
        assert not conn.execute("SELECT 1 FROM work_ids WHERE scheme='url'").fetchone()
    csl, _ = merge_csl([{"provider": "web", "kind": "webpage", "ids": {}, "csl": {"title": "x", "URL": "javascript:alert(1)"}},
                        {"provider": "import", "kind": "unknown", "ids": {}, "csl": {"title": "x", "URL": "https://ok.example/a"}}])
    assert csl["URL"] == "https://ok.example/a"


class FakeDBLP:
    capabilities = frozenset({"search"})

    def __init__(self, family):
        self.family = family

    def match_title(self, title, author=None, year=None):
        return [Record("dblp", {"dblp": "conf/icml/X20"}, {"title": title, "author": [{"family": self.family, "given": "A"}],
                                                           "issued": {"date-parts": [[2020]]}, "container-title": "ICML",
                                                           "type": "paper-conference"}, "published")]


@pytest.mark.parametrize("family,venue", [("Other", None), ("Smith", "ICML")])
def test_dblp_venue_needs_the_same_first_author(off, actor, monkeypatch, family, venue):
    call(off, actor, "resolve", add=True, items=[item("Learning to learn things", "Smith", 2020, arxiv="2001.00009")])
    original = off.providers.get
    monkeypatch.setattr(off.providers, "get", lambda pid: FakeDBLP(family) if pid == "dblp" else original(pid))
    assert enrich(off, {"work": wid(off, "smith2020learning")}).get("venue") == venue
