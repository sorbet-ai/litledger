"""Checks that return only problems: quotes against stored text, .bib entries, \\cite coverage."""
from litledger.check import check_quote
from litledger.fulltext import store_document
from litledger.parse import Parsed

from conftest import ACTOR, add_work, call, make_ledger


def test_hyphenated_quotes_verify(tmp_path):
    lg = make_ledger(tmp_path)
    with lg.db.tx(ACTOR, internal="test setup") as tx:
        add_work(tx.conn, "wq", "Quote paper")
    text = ("Our model reaches state-of-the-art accuracy while the earlier stateof-the-art baseline (joined by an old parser) "
            "uses a transfor-mer encoder and a convolution-free decoder.")
    store_document(lg, "wq", Parsed(passages=[{"section": "Body", "page": 1, "text": text}]), "pdf", None)
    with lg.db.read() as conn:
        for quote in ("reaches state-of-the-art accuracy", "the earlier state-of-the-art baseline",
                      "uses a transformer encoder", "a convolution-free decoder", "a convolution-\nfree decoder"):
            assert check_quote(conn, "wq", quote)["status"] == "verified", quote
        assert check_quote(conn, "wq", "reaches state of the art accuracy")["status"] != "verified"


def test_check_bib_and_tex(lg, actor):
    call(lg, actor, "resolve", items=["1706.03762"], add=True)
    bib = """@article{vaswani2017attention, title={Attention Is All You Need}, author={Vaswani, Ashish and others},
journal={arXiv preprint arXiv:1706.03762}, year={2017}}
@inproceedings{wrong, title={BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding},
author={Smith, John}, booktitle={NAACL}, year={2015}, doi={10.18653/v1/N19-1423}}"""
    out = call(lg, actor, "check", kind="bib", content=bib)
    assert out.startswith("bib: 0/2 ok"), out
    assert "vaswani2017attention: published version exists: NeurIPS'17" in out
    assert "wrong:" in out and "first author" in out
    tex = r"See \citep{vaswani2017attention, devlin2019bert} and \cite[p.~3]{unknownkey2020}."
    out = call(lg, actor, "check", kind="tex", content=tex)
    unknown = next(line for line in out.splitlines() if line.startswith("unknown:"))
    assert "unknownkey2020" in unknown and "devlin2019bert" in unknown and "vaswani2017attention" not in unknown
