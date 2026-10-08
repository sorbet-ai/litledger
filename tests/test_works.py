"""Works: one canonical work per paper. IDs shared with other works never merge them blindly, compatible duplicates
merge and unmerge cleanly, and project citekeys stay unique."""
from litledger.db import Actor, now
from litledger.providers.base import Record
from litledger.resolve import Fetched, Spec, _persist
from litledger.works import create_work, merge_works, resolve_ref, work_by_ident
from litledger.ids import Ident

from conftest import call, item


def live_works(lg):
    with lg.db.read() as conn:
        return {r["citekey"]: dict(r) for r in conn.execute("SELECT * FROM works WHERE merged_into IS NULL")}


def test_import_sharing_ids_of_two_other_works_never_merges_them(off, actor):
    call(off, actor, "resolve", add=True, items=[item("Alpha: a study of something specific", "Alpha", 2020, arxiv="2001.00001"),
                                                  item("Completely different topic entirely", "Beta", 2021, PMID="12345678")])
    assert set(live_works(off)) == {"alpha2020alpha", "beta2021completely"}
    out = call(off, actor, "resolve", add=True, items=[item("Some Third Paper", "Gamma", 2022, DOI="10.1234/new",
                                                             arxiv="2001.00001", PMID="12345678")])
    assert "conflict: arxiv:2001.00001 belongs to alpha2020alpha (title differs)" in out, out
    assert "conflict: pmid:12345678 belongs to beta2021completely (title differs)" in out
    works = live_works(off)
    assert len(works) == 3 and works["beta2021completely"]["title"] == "Completely different topic entirely"
    with off.db.read() as conn:
        assert work_by_ident(conn, Ident("pmid", "12345678"))["citekey"] == "beta2021completely"
        assert work_by_ident(conn, Ident("arxiv", "2001.00001"))["citekey"] == "alpha2020alpha"
        assert work_by_ident(conn, Ident("doi", "10.1234/new"))["citekey"] == "gamma2022some"


def test_bib_entry_with_a_wrong_doi_does_not_attach_to_the_doi_owner(off, actor):
    call(off, actor, "resolve", add=True, items=[item("Completely different topic entirely", "Beta", 2021, DOI="10.5555/beta")])
    out = call(off, actor, "resolve", add=True,
               items=["@article{x, title={A Correct Title For Another Paper}, author={Smith, John}, year={2020}, doi={10.5555/beta}}"])
    assert "conflict: doi:10.5555/beta belongs to beta2021completely (title differs)" in out, out
    works = live_works(off)
    assert "smith2020correct" in works and works["beta2021completely"]["title"].startswith("Completely")
    with off.db.read() as conn:
        assert work_by_ident(conn, Ident("doi", "10.5555/beta"))["citekey"] == "beta2021completely"


TITLE = "Delta rules for linear recurrent memory models"


CSL_A = {"title": TITLE, "author": [{"family": "Doe", "given": "J"}], "issued": {"date-parts": [[2021]]}}


CSL_B = {**CSL_A, "type": "article-journal", "container-title": "Journal of Things", "issued": {"date-parts": [[2022]]}}


def _two_versions(lg, actor):
    """The arXiv and journal versions of one paper captured separately, each with its own project state."""
    with lg.db.tx(actor, internal="test setup") as tx:
        a = create_work(tx, [Record("arxiv", {"arxiv": "2101.00001"}, CSL_A, "preprint")])
        b = create_work(tx, [Record("crossref", {"doi": "10.1000/xyz"}, CSL_B, "published")])
        keys = {r["id"]: r["citekey"] for r in tx.execute("SELECT id, citekey FROM works WHERE id IN (?,?)", (a, b))}
    return a, b, keys[a], keys[b]


def _fetched():
    # An arXiv record whose DOI follow found the journal version: the IDs of both works in one resolution.
    return Fetched(Spec(raw="2101.00001"), records=[Record("arxiv", {"arxiv": "2101.00001", "doi": "10.1000/xyz"}, CSL_A, "preprint"),
                                                    Record("crossref", {"doi": "10.1000/xyz"}, CSL_B, "published")])


def test_compatible_works_merge_and_unmerge_restores_them(lg, actor):
    a, b, ka, kb = _two_versions(lg, actor)
    call(lg, actor, "update_work", items=[{"work": ka, "why": "arxiv copy", "add": True},
                                          {"work": kb, "why": "journal copy", "read": "full", "add": True}])
    call(lg, actor, "tag", action="apply", tags=["v:journal"], works=[kb])
    other = Actor(name="tester", kind="agent", project="other")
    call(lg, other, "resolve", items=["10.1000/xyz"], add=True, tags=["elsewhere"])
    call(lg, actor, "note", items=[{"work": kb, "text": "journal note"}])
    out = _persist(lg, actor, _fetched(), "t", True, None)
    assert out.status == "ok" and out.work_id == a and not out.conflicts
    assert kb not in live_works(lg)
    assert f"merged from: {kb}" in call(lg, actor, "work", work=ka)
    # Undo through the admin tool (REST: POST /api/v1/tools/update_work).
    res = call(lg, actor, "update_work", items=[{"work": kb, "unmerge": True}])
    assert res.splitlines() == ["ok 1/1", f"unmerged {kb}: split from {ka}, marked not a duplicate"], res
    with lg.db.read() as conn:
        assert work_by_ident(conn, Ident("doi", "10.1000/xyz"))["id"] == b
        assert work_by_ident(conn, Ident("arxiv", "2101.00001"))["id"] == a
        assert {r["provider"] for r in conn.execute("SELECT provider FROM work_sources WHERE work_id=?", (a,))} == {"arxiv"}
        pw = {(r["project"], r["work_id"]): dict(r) for r in conn.execute("SELECT * FROM project_works")}
        assert pw[("t", b)]["why"] == "journal copy" and pw[("t", b)]["read_depth"] == "full" and ("other", b) in pw
        assert pw[("t", a)]["why"] == "arxiv copy" and pw[("t", a)]["read_depth"] is None and ("other", a) not in pw
        assert conn.execute("SELECT subject FROM notes").fetchone()["subject"] == b
        assert conn.execute("SELECT count(*) FROM merges WHERE undone_at IS NOT NULL").fetchone()[0] == 1
    assert "v:journal" in call(lg, actor, "work", work=kb) and "v:journal" not in call(lg, actor, "work", work=ka)
    assert "elsewhere" in call(lg, other, "find")
    # The pair is now known distinct: the same resolution reports it instead of merging again.
    again = _persist(lg, actor, _fetched(), "t", True, None)
    assert again.conflicts == [f"{kb} not merged (marked not a duplicate)"] and kb in live_works(lg)


def test_not_duplicate_action_prevents_merging(lg, actor):
    a, b, ka, kb = _two_versions(lg, actor)
    assert call(lg, actor, "update_work", items=[{"work": ka, "not_duplicate": kb}]) == "ok 1/1"
    _persist(lg, actor, _fetched(), "t", True, None)
    assert {ka, kb} <= set(live_works(lg))


def test_conflicting_secondary_record_is_reported_not_merged(lg, actor):
    with lg.db.tx(actor, internal="test setup") as tx:
        create_work(tx, [Record("europepmc", {"pmid": "777"}, {"title": "An unrelated clinical trial of aspirin",
                                                                 "author": [{"family": "Roe"}]}, "published")])
    f = Fetched(Spec(raw="2101.00001"), records=[Record("arxiv", {"arxiv": "2101.00001"}, CSL_A, "preprint"),
                                                 Record("s2", {"arxiv": "2101.00001", "pmid": "777"}, CSL_A, "unknown")])
    out = _persist(lg, actor, f, "t", True, None)
    assert out.created and out.conflicts == ["pmid:777 belongs to roendunrelated (title differs)"], out.conflicts
    assert len(live_works(lg)) == 2


def test_merge_keeps_removals_overrides_and_live_taggings(lg, actor):
    a, b, ka, kb = _two_versions(lg, actor)
    with lg.db.tx(actor, internal="test setup") as tx:
        for project, work, removed, override in (("p1", a, now(), None), ("p1", b, now(), None), ("p2", a, None, None),
                                                 ("p2", b, None, "mykey")):
            tx.execute("INSERT INTO projects(id,title,created_at) VALUES(?,?,?) ON CONFLICT DO NOTHING", (project, project, now()))
            tx.execute("INSERT INTO project_works(project,work_id,added_at,removed_at,citekey_override) VALUES(?,?,?,?,?)",
                       (project, work, now(), removed, override))
    call(lg, actor, "tag", action="apply", tags=["x"], works=[ka, kb])
    call(lg, actor, "tag", action="remove", tags=["x"], works=[ka])
    with lg.db.tx(actor, internal="test setup") as tx:
        assert merge_works(tx, a, b, "test")
    with lg.db.read() as conn:
        rows = {r["project"]: r for r in conn.execute("SELECT * FROM project_works WHERE work_id=?", (a,))}
        assert rows["p1"]["removed_at"] is not None  # both removed: stays removed
        assert rows["p2"]["removed_at"] is None and rows["p2"]["citekey_override"] == "mykey"
    assert "x" in call(lg, actor, "work", work=ka)  # the absorbed work's live tagging beats the survivor's removed one


def test_project_citekeys_are_unique_and_win_in_their_project(off, actor):
    call(off, actor, "resolve", add=True, items=[item("First paper here", "Aaa", 2020), item("Second paper here", "Bbb", 2020),
                                                  item("Third paper here", "Ccc", 2020)])
    assert call(off, actor, "update_work", items=[{"work": "aaa2020first", "citekey": "mykey"}]) == "ok 1/1"
    out = call(off, actor, "update_work", items=[{"work": "ccc2020third", "citekey": "mykey"}])
    assert out.splitlines()[1] == "error ccc2020third: citekey mykey belongs to aaa2020first", out
    with off.db.tx(actor, internal="test setup") as tx:  # a global key minted later with the same spelling
        tx.execute("UPDATE works SET citekey='mykey' WHERE citekey='bbb2020second'")
    with off.db.read() as conn:
        assert resolve_ref(conn, "mykey", "t")["title"] == "First paper here"
        assert resolve_ref(conn, "mykey", "other")["title"] == "Second paper here"
    call(off, actor, "note", items=[{"work": "mykey", "text": "lands on the first"}])
    assert "1 notes" in call(off, actor, "work", work="aaa2020first")
    # new global keys avoid spellings already used as project keys
    call(off, actor, "update_work", items=[{"work": "ccc2020third", "citekey": "zed2020fourth"}])
    call(off, actor, "resolve", add=True, items=[item("Fourth paper here", "Zed", 2020)])
    assert "zed2020fourtha" in live_works(off)
