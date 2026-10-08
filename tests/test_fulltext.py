"""Full text: fetching, parsing, reading narrowly, local citations, uploads, concurrency and re-parsing."""
import threading
import time

import httpx

from litledger import fulltext, tools
from litledger.db import now
from litledger.fulltext import fetch_document, store_document
from litledger.parse import Parsed, dehyphenate
from litledger.textutil import est_tokens

from conftest import ACTOR, add_work, call, make_ledger


def test_read_outline_section_search_and_quote_verification(lg, actor):
    call(lg, actor, "resolve", items=["2412.06464", "2006.16236"], add=True)  # GDN + Katharopoulos (cited by GDN)
    outline = call(lg, actor, "read", work="yang2024gated")
    assert outline.startswith("yang2024gated · arxiv_html")
    assert "1 Introduction" in outline and est_tokens(outline) <= 400
    section = call(lg, actor, "read", work="yang2024gated", mode="section", target="3.1")
    assert "Formulation" in section and "<<<paper text" in section
    assert est_tokens(section) <= 1200
    found = call(lg, actor, "read", work="yang2024gated", mode="search", query="single needle in a haystack")
    assert "S-NIAH" in found or "needle" in found.lower()
    # Quotes are verified against the stored text and anchored; misses are kept but reported.
    good = call(lg, actor, "note", items=[{"work": "yang2024gated", "text": "core idea",
                                            "quote": "Linear Transformers have gained attention as efficient alternatives to standard Transformers"}])
    assert "verified" in good
    bad = call(lg, actor, "note", items=[{"work": "yang2024gated", "text": "made up", "quote": "Gated delta networks were invented in 1965 by aliens"}])
    assert "quote not found" in bad and bad.startswith("ok 1/1")
    work = call(lg, actor, "work", work="yang2024gated", include=["notes"])
    assert "read: sections" in work or "read: skimmed" in work
    assert "✓" in work


def test_local_citation_graph_from_full_text(lg, actor):
    call(lg, actor, "resolve", items=["2412.06464", "2006.16236"], add=True)
    call(lg, actor, "read", work="yang2024gated")
    refs = call(lg, actor, "graph", work="yang2024gated", kind="references", limit=200)
    assert "katharopoulos2020transformers" in refs
    cited = call(lg, actor, "work", work="katharopoulos2020transformers", include=["citations"])
    assert "cited by yang2024gated" in cited


def test_older_paper_uses_ar5iv_and_pdf_upload_parses(lg, actor):
    call(lg, actor, "resolve", items=["2006.16236"], add=True)
    out = call(lg, actor, "read", work="katharopoulos2020transformers")
    assert out.startswith("katharopoulos2020transformers · arxiv_html"), out[:200]
    # PDF path: parse the arXiv PDF as an upload attached to another work.
    from litledger.fulltext import ingest_upload
    pdf = lg.http.get("test", "https://arxiv.org/pdf/2102.11174").content
    assert pdf[:5] == b"%PDF-"
    call(lg, actor, "resolve", items=["2102.11174"], add=True)
    with lg.db.read() as conn:
        wid = conn.execute("SELECT id FROM works WHERE citekey='schlag2021linear'").fetchone()["id"]
    doc_id, status = ingest_upload(lg, actor, wid, "schlag.pdf", pdf)
    assert status == "ok"
    outline = call(lg, actor, "read", work="schlag2021linear")
    assert "upload" in outline.splitlines()[0] and "Introduction" in outline
    hit = call(lg, actor, "read", work="schlag2021linear", mode="search", query="fast weight programmers")
    assert "page" in hit


def test_snowball_from_local_references(lg, actor):
    call(lg, actor, "resolve", items=["2412.06464", "2006.16236"], add=True)
    call(lg, actor, "read", work="yang2024gated")
    out = call(lg, actor, "snowball", action="expand", seeds=["yang2024gated"], direction="refs", limit=5)
    assert out.startswith("ok frontier +") and "+0 new" not in out, out
    listing = call(lg, actor, "snowball", action="list", limit=50)
    handle = next(line.split(" · ")[0] for line in listing.splitlines()[1:] if line.startswith("arxiv:"))
    res = call(lg, actor, "snowball", action="decide", decisions=[{"handle": handle, "state": "out", "reason": "off-topic"}])
    assert res.startswith("ok in=0 out=1")
    assert handle not in call(lg, actor, "snowball", action="list", limit=200)


def test_line_break_hyphens():
    pages = dehyphenate(["We report state-\nof-the-art results with a transfor-\nmer. The transformer is a con-\nvolution-free",
                         "model trained end-to-\nend; self-\nattention helps. Attention is all you need. The state of",
                         "the art moves fast; at the end of the day the model is what we report."])
    assert "state-of-the-art" in pages[0] and "transformer." in pages[0] and "convolution-free" in pages[0]
    assert "end-to-end" in pages[1] and "self-attention" in pages[1]


class SlowPage(httpx.BaseTransport):
    def __init__(self):
        self.hits = 0

    def handle_request(self, request):
        self.hits += 1
        time.sleep(0.2)
        body = b"<html><body><article><p>" + b"A readable paragraph of the blog post about concurrency. " * 20 + b"</p></article></body></html>"
        return httpx.Response(200, headers={"content-type": "text/html"}, content=body, request=request)


def test_concurrent_fetches_store_one_document(tmp_path):
    lg = make_ledger(tmp_path)
    transport = SlowPage()
    lg.http.client._transport = transport
    with lg.db.tx(ACTOR, internal="test setup") as tx:
        add_work(tx.conn, "wblog", "Concurrency post", "webpage", {"title": "Concurrency post", "URL": "https://blog.example/p"})
    results = []
    threads = [threading.Thread(target=lambda: results.append(fetch_document(lg, "wblog"))) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    with lg.db.read() as conn:
        assert conn.execute("SELECT count(*) FROM documents WHERE work_id='wblog'").fetchone()[0] == 1
    assert transport.hits == 1 and len({r[0] for r in results}) == 1


def test_passage_search_only_sees_the_current_document(tmp_path):
    lg = make_ledger(tmp_path)
    with lg.db.tx(ACTOR, internal="test setup") as tx:
        add_work(tx.conn, "wdup", "Duplicate passages")
    para = {"section": "Body", "page": 1, "text": "The zymurgical quokka experiment is described here in detail."}
    old = store_document(lg, "wdup", Parsed(passages=[para]), "pdf", None)
    new = store_document(lg, "wdup", Parsed(passages=[dict(para, page=None)]), "arxiv_html", None)
    low = store_document(lg, "wdup", Parsed(passages=[para]), "upload", None)  # ranks below arxiv_html: not indexed
    with lg.db.read() as conn:
        hits = conn.execute("SELECT p.document_id FROM passages_fts JOIN passages p ON p.id=passages_fts.rowid "
                            "WHERE passages_fts MATCH 'quokka'").fetchall()
        assert [h["document_id"] for h in hits] == [new]
        assert conn.execute("SELECT count(*) FROM passages WHERE document_id IN (?,?)", (old, low)).fetchone()[0] == 2
        conn.execute("INSERT INTO passages_fts(passages_fts) VALUES('integrity-check')")  # the index is consistent


def test_failed_reparse_falls_back_to_the_stored_parse(tmp_path):
    lg = make_ledger(tmp_path)
    with lg.db.tx(ACTOR, internal="test setup") as tx:
        add_work(tx.conn, "wold", "Old parse", citekey="old2020parse")
        tx.execute("INSERT OR IGNORE INTO projects(id,title,created_at) VALUES('t','t',?)", (now(),))
        tx.execute("INSERT INTO project_works(project,work_id,added_at) VALUES('t','wold',?)", (now(),))
    digest = fulltext.store_blob(lg, b"%PDF-1.4 this is not really a pdf")
    doc = store_document(lg, "wold", Parsed(passages=[{"section": "Body", "page": 1, "text": "Stored text from parser one."}]),
                         "pdf", digest)
    with lg.db.connection() as conn:
        conn.execute("UPDATE documents SET parser_version='1' WHERE id=?", (doc,))
    out = tools.call(lg, ACTOR, "read", {"work": "old2020parse", "mode": "full"})
    assert "Stored text from parser one." in out
    assert "Stored text from parser one." in tools.call(lg, ACTOR, "read", {"work": "old2020parse", "mode": "full"})


def test_a_pdf_upload_attaches_only_to_a_sure_match(lg, actor, monkeypatch):
    """Without --work, a PDF attaches to the paper its arXiv id or DOI names, or to the one with exactly its title;
    a near title makes a new work, and the answer says so."""
    from litledger import importer
    call(lg, actor, "resolve", items=["2102.11174"], add=True)
    pdf = lg.http.get("test", "https://arxiv.org/pdf/2102.11174").content
    out = importer.upload(lg, actor, "t", "fwp.pdf", pdf)  # its metadata title is exactly the paper's
    assert out["work"] == "schlag2021linear" and "note" not in out
    monkeypatch.setattr(importer, "pdf_front", lambda data: ("Linear Transformers Are Secretly Fast Weight Programmer", ""))
    out = importer.upload(lg, actor, "t", "near.pdf", pdf)
    assert out["work"] != "schlag2021linear" and out["note"].startswith("no paper matched this exact title; made a new work")
    assert importer.upload_ident("2102.11174v2.pdf", "") == "arxiv:2102.11174"
    assert importer.upload_ident("x.pdf", "margin arXiv:2102.11174v3 [cs.LG] 9 Jun 2021") == "arxiv:2102.11174"
    assert importer.upload_ident("x.pdf", "Published at https://doi.org/10.1000/xyz123. More") == "doi:10.1000/xyz123"
    assert importer.upload_ident("x.pdf", "[3] Smith. 10.1000/abc. 2019") is None  # an unlabelled DOI may be a reference
