"""Identifier parsing and text normalisation."""
from litledger.ids import parse_ident
from litledger.textutil import norm_quote, title_similarity, venue_abbrev


def h(text):
    i = parse_ident(text)
    return i.handle if i else None


def test_arxiv_forms_collapse():
    for raw in ["1706.03762", "arXiv:1706.03762v5", "https://arxiv.org/abs/1706.03762", "https://arxiv.org/html/1706.03762v7",
                "https://arxiv.org/pdf/1706.03762.pdf", "http://export.arxiv.org/abs/1706.03762", "10.48550/arXiv.1706.03762",
                "https://doi.org/10.48550/arXiv.1706.03762", "https://huggingface.co/papers/1706.03762",
                "https://www.alphaxiv.org/abs/1706.03762"]:
        assert h(raw) == "arxiv:1706.03762", raw
    assert parse_ident("arXiv:1706.03762v5").version == "v5"
    assert h("hep-th/9901001") == "arxiv:hep-th/9901001"


def test_other_ids():
    assert h("10.18653/v1/N19-1423") == "doi:10.18653/v1/n19-1423"
    assert h("https://doi.org/10.1038/nature14539") == "doi:10.1038/nature14539"
    assert h("https://openreview.net/forum?id=r8H7xhYPwz") == "openreview:r8H7xhYPwz"
    assert h("https://aclanthology.org/N19-1423/") == "acl:N19-1423"
    assert h("https://aclanthology.org/2023.acl-long.1.pdf") == "acl:2023.acl-long.1"
    assert h("W2626778328") == "openalex:W2626778328"
    assert h("PMC1234567") == "pmcid:PMC1234567"
    assert h("pmid:26017442") == "pmid:26017442"
    assert h("conf/nips/VaswaniSPUJGKP17") == "dblp:conf/nips/VaswaniSPUJGKP17"
    assert h("https://dblp.org/rec/conf/iclr/YangKH25.html") == "dblp:conf/iclr/YangKH25"
    assert h("https://github.com/fla-org/flash-linear-attention/?utm_source=x") == "url:https://github.com/fla-org/flash-linear-attention"
    assert parse_ident("Attention Is All You Need") is None


def test_title_similarity_and_quotes():
    assert title_similarity("Attention is All you Need.", "Attention Is All You Need") > 0.97
    assert title_similarity("Attention Is All You Need", "Attention Is All You Need In Speech Separation") < 0.93
    assert norm_quote("efﬁcient  “models”—fast\nlearn-\ning") == norm_quote('efficient "models"-fast learning')


def test_venue_abbrev():
    assert venue_abbrev("Proceedings of the 2019 Conference of the North American Chapter of the Association for Computational Linguistics", 2019) == "NAACL'19"
    assert venue_abbrev("Advances in Neural Information Processing Systems", 2017) == "NeurIPS'17"
    assert venue_abbrev("arXiv", 2024) == "arXiv'24"
