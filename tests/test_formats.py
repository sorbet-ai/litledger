"""Bibliographic formats: BibTeX parsing and writing (math kept verbatim), RIS export."""
from litledger.formats import csl_to_bibtex, entry_to_csl, parse_bibtex


BIB = r"""
@string{iclr = "International Conference on Learning Representations"}
@inproceedings{yang2025gated,
  title = {Gated Delta Networks: Improving {Mamba2} with Delta Rule},
  author = {Yang, Songlin and Kautz, Jan and Hatamizadeh, Ali},
  booktitle = iclr,
  year = {2025},
}
@article{vaswani2017,
  title = "Attention is All you Need",
  author = {Ashish Vaswani and Noam Shazeer and others},
  journal = {arXiv preprint arXiv:1706.03762},
  year = 2017
}
@misc{kaiser, title={{\"U}ber Sch{\"o}nheit}, author={M{\"u}ller, J{\"o}rg}, eprint={2101.00001}, archivePrefix={arXiv}}
"""


def test_bibtex_parse_and_write():
    entries = parse_bibtex(BIB)
    assert [e["ID"] for e in entries] == ["yang2025gated", "vaswani2017", "kaiser"]
    csl, ids = entry_to_csl(entries[0])
    assert csl["type"] == "paper-conference" and csl["container-title"].startswith("International Conference")
    assert csl["title"] == "Gated Delta Networks: Improving Mamba2 with Delta Rule"
    assert csl["author"][0] == {"family": "Yang", "given": "Songlin"}
    csl2, ids2 = entry_to_csl(entries[1])
    assert ids2 == {"arxiv": "1706.03762"}
    assert len(csl2["author"]) == 2  # "others" dropped
    csl3, ids3 = entry_to_csl(entries[2])
    assert ids3 == {"arxiv": "2101.00001"} and csl3["author"][0]["family"] == "Müller"
    out = csl_to_bibtex("yang2024gated", csl, {"arxiv": "2412.06464"})
    assert out.startswith("@inproceedings{yang2024gated,") and "{Mamba2}" in out and "booktitle" in out
    pre = csl_to_bibtex("k", {"type": "article", "title": "A BERT study", "container-title": "arXiv",
                              "issued": {"date-parts": [[2020]]}}, {"arxiv": "2001.1"}, arxiv_class="cs.CL")
    assert "@misc{k," in pre and "eprint = {2001.1}" in pre and "primaryClass = {cs.CL}" in pre and "{BERT}" in pre
    ascii_out = csl_to_bibtex("k", csl3, ids3, ascii_only=True)
    assert "M{\\\"u}ller" in ascii_out or "M\\\"{u}ller" in ascii_out or "{\\\"u}" in ascii_out


def test_bibtex_keeps_math_verbatim_and_escapes_the_rest():
    csl = {"type": "article", "title": r"Linear $\mathcal{O}(n)$ attention for $x_i$ & 100% of {GPUs}_v2", "author": [{"family": "Lee"}],
           "issued": {"date-parts": [[2024]]}}
    bib = csl_to_bibtex("lee2024linear", csl, {"arxiv": "2401.00001"})
    title = next(line for line in bib.splitlines() if line.strip().startswith("title"))
    assert r"{$\mathcal{O}(n)$}" in title and r"{$x_i$}" in title
    assert r"\& 100\% of" in title and r"\{GPUs\}\_v2" in title and r"\$" not in title


def test_ris_export_keeps_one_value_per_line():
    from litledger.formats import csl_to_ris
    ris = csl_to_ris("k", {"type": "article", "title": "Two\nlines\nER  - fake", "abstract": 'He said "hi"\r\nTY  - JOUR',
                           "URL": "javascript:alert(1)"}, {})
    assert ris.count("ER  - ") == 1 and ris.count("TY  - ") == 1 and "UR  -" not in ris
    assert 'AB  - He said "hi" TY - JOUR' in ris
