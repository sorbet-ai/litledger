"""Full-text parsing: arXiv/LaTeXML HTML, PDF, JATS XML, web pages and plain text into anchored passages, reference
lists and citation contexts. Pure functions: no database, no network."""
from __future__ import annotations

import gzip
import io
import re
import tarfile
from dataclasses import dataclass, field

from bs4 import BeautifulSoup, NavigableString

PARSER_VERSION = "2"  # 2: tables kept as passages
MIN_PASSAGE, MAX_PASSAGE = 250, 2400
TABLE_MARK = "\u0000table\u0000"


@dataclass
class Parsed:
    passages: list[dict] = field(default_factory=list)  # {section, page, text}
    references: list[dict] = field(default_factory=list)  # {label, raw, anchor}
    cites: list[dict] = field(default_factory=list)  # {passage_index, anchor, sentence}
    title: str | None = None
    status: str = "ok"


# ------------------------------------------------------------------------------------------------ chunking
def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9(\[])", text) if s.strip()]


def chunk(paragraphs: list[tuple[str, int | None, str]]) -> list[dict]:
    """Merge tiny paragraphs and split huge ones, keeping section/page anchors."""
    out: list[dict] = []
    buf: dict | None = None
    for section, page, text in paragraphs:
        if text.startswith(TABLE_MARK):
            if buf:
                out.append(buf)
                buf = None
            body = text[len(TABLE_MARK):].strip()
            piece = []
            for line in body.split("\n"):  # split long tables on row boundaries, repeating nothing
                if piece and sum(len(x) + 1 for x in piece) + len(line) > MAX_PASSAGE * 2:
                    out.append({"section": section, "page": page, "text": "\n".join(piece)})
                    piece = []
                piece.append(line)
            if piece:
                out.append({"section": section, "page": page, "text": "\n".join(piece)})
            continue
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            continue
        if buf and buf["section"] == section and len(buf["text"]) < MIN_PASSAGE and len(buf["text"]) + len(text) < MAX_PASSAGE:
            buf["text"] += " " + text
            continue
        if buf:
            out.append(buf)
        if len(text) > MAX_PASSAGE:
            piece = ""
            for sent in _sentences(text):
                if piece and len(piece) + len(sent) > MAX_PASSAGE * 0.6:
                    out.append({"section": section, "page": page, "text": piece.strip()})
                    piece = ""
                piece += " " + sent
            buf = {"section": section, "page": page, "text": piece.strip()} if piece.strip() else None
        else:
            buf = {"section": section, "page": page, "text": text}
    if buf:
        out.append(buf)
    return out


# ------------------------------------------------------------------------------------------------ parsers
def _tidy_tex(tex: str) -> str:
    tex = re.sub(r"\\color\[[^\]]*\]\{[^}]*\}|\\color\{[^}]*\}|\\displaystyle|\\textstyle", "", tex)
    tex = re.sub(r"\{\\(mathbf|bm|boldsymbol|mathrm|mathit)\{([^{}]*)\}\}", r"\\\1{\2}", tex)
    return re.sub(r"\s+", " ", tex).strip()


def _clean_html_text(node) -> str:
    for m in node.select("math"):
        alt = m.get("alttext")
        m.replace_with(NavigableString(f" ${_tidy_tex(alt)}$ " if alt else " "))
    for junk in node.select("span.ltx_note_outer, span.ltx_note, .ltx_tag_note"):
        junk.decompose()
    return re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip()


def _table_text(fig) -> str:
    """A LaTeXML table as text: its caption, then one line per row with cells joined by ' | '."""
    caption = fig.select_one("figcaption")
    cap = _clean_html_text(caption) if caption else ""
    rows = []
    for tr in fig.select("tr"):
        cells = [_clean_html_text(td) for td in tr.find_all(["td", "th"])]
        if any(c for c in cells):
            rows.append(" | ".join(cells))
    if not rows:
        return ""
    return (cap + "\n" if cap else "") + "\n".join(rows)


def parse_arxiv_html(html: str) -> Parsed:
    soup = BeautifulSoup(html, "lxml")
    out = Parsed()
    t = soup.select_one("h1.ltx_title_document")
    out.title = re.sub(r"\s+", " ", t.get_text(" ", strip=True)) if t else None
    paragraphs: list[tuple[str, int | None, str]] = []
    para_cites: list[tuple[int, list[str], str]] = []  # (paragraph index, bib anchors, text)
    abstract = soup.select_one("div.ltx_abstract")
    if abstract:
        for h in abstract.select("h6, .ltx_title"):
            h.decompose()
        paragraphs.append(("Abstract", None, _clean_html_text(abstract)))
    for para in soup.select("div.ltx_para, figure figcaption, figure.ltx_table"):
        if para.find_parent("div", class_="ltx_abstract") or para.find_parent(class_="ltx_bibliography"):
            continue
        if para.name == "figcaption" and para.find_parent("figure", class_="ltx_table"):
            continue  # the table handler includes its caption
        if para.name == "figure" and para.find_parent("figure", class_="ltx_table"):
            continue
        section_el = para.find_parent("section")
        heads = []
        node = section_el
        while node is not None:
            h = node.find(re.compile(r"^h[1-6]$"), class_="ltx_title", recursive=False)
            if h and "ltx_paragraph" not in (node.get("class") or []):
                heads.append(re.sub(r"\s+", " ", h.get_text(" ", strip=True)))
            node = node.find_parent("section")
        section = heads[-1] if heads else "Body"
        if len(heads) > 1:
            section = heads[-1] + " › " + heads[0]
        if para.name == "figure":
            table_text = _table_text(para)
            if table_text:
                label = para.select_one(".ltx_tag_table")
                paragraphs.append((section + " › " + (label.get_text(" ", strip=True).rstrip(":") if label else "Table"), None,
                                   TABLE_MARK + table_text))
            continue
        for a in para.select('a.ltx_ref[href^="#bib."]'):
            a.insert_after(NavigableString(f" ⟦{a.get('href', '')[1:]}⟧ "))
        marked = _clean_html_text(para)
        text = re.sub(r"\s*⟦[^⟧]*⟧", "", marked).strip()
        if para.name == "figcaption":
            section = section + " › caption"
        if len(text) < 2:
            continue
        paragraphs.append((section, None, text))
        if "⟦" in marked:
            para_cites.append((len(paragraphs) - 1, [], marked))
    out.passages = chunk(paragraphs)
    # Chunking merges paragraphs: find each citing paragraph's passage, then attribute sentences to bib anchors.
    for _, _, marked in para_cites:
        plain = re.sub(r"\s*⟦[^⟧]*⟧", "", marked)
        probe = plain[:120]
        idx = next((i for i, p in enumerate(out.passages) if probe in p["text"]), None)
        if idx is None:
            continue
        for sent in _sentences(marked):
            anchors = set(re.findall(r"⟦([^⟧]+)⟧", sent))
            clean = re.sub(r"\s*⟦[^⟧]*⟧", "", sent).strip()
            for a in anchors:
                out.cites.append({"passage_index": idx, "anchor": a, "sentence": clean})
    for item in soup.select("li.ltx_bibitem"):
        tag = item.select_one(".ltx_tag_bibitem")
        label = tag.get_text(" ", strip=True) if tag else None
        if tag:
            tag.decompose()
        raw = re.sub(r"\s+", " ", item.get_text(" ", strip=True))
        links = [a.get("href") for a in item.select("a[href]")]
        out.references.append({"label": label, "anchor": item.get("id"), "raw": raw, "links": links})
    if not out.passages:
        out.status = "empty"
    return out


HEADING = re.compile(r"^(?:(?:\d+(?:\.\d+)*\.?|[IVX]+\.|[A-H]\.?)\s+)?(Abstract|Introduction|Background|Related Work|Preliminar(?:y|ies)|"
                     r"Method(?:s|ology)?|Approach|Model|Experiments?|Results|Evaluation|Discussion|Analysis|Limitations|"
                     r"Conclusions?|Future Work|Acknowledg(?:e)?ments?|References|Bibliography|Appendix|Supplementary.*)\s*$", re.I)
NUMBERED = re.compile(r"^(\d+(?:\.\d+){0,2})\.?\s+([A-Z][^.!?]{2,70})$")


def _numbered_heading(line: str):
    m = NUMBERED.match(line)
    if not m:
        return None
    first = int(m.group(1).split(".")[0])
    words = m.group(2).split()
    if first > 15 or "," in m.group(2) or len(words) > 12 or sum(w[:1].isdigit() for w in words) > 1:
        return None
    return m


def parse_pdf(data: bytes) -> Parsed:
    import pypdfium2 as pdfium

    out = Parsed()
    pdf = pdfium.PdfDocument(data)
    paragraphs: list[tuple[str, int | None, str]] = []
    ref_lines: list[str] = []
    section, in_refs = "Body", False
    try:
        meta_title = (pdf.get_metadata_dict().get("Title") or "").strip()
        out.title = meta_title if len(meta_title) > 8 else None
    except Exception:
        pass
    pages: list[str] = []
    for pno in range(len(pdf)):
        text = pdf[pno].get_textpage().get_text_range() or ""
        pages.append(text.replace("\r\n", "\n").replace("\r", "\n").replace("\ufffe", "").replace("\u00ad", "").replace("\u0002", ""))
    for pno, text in enumerate(dehyphenate(pages)):
        buf: list[str] = []
        for line in text.split("\n"):
            s = line.strip()
            if not s:
                continue
            m_head = HEADING.match(s) or (_numbered_heading(s) if len(s) < 80 else None)
            if m_head:
                if buf:
                    paragraphs.append((section, pno + 1, " ".join(buf)))
                    buf = []
                section = s
                in_refs = bool(re.search(r"references|bibliography", s, re.I))
                continue
            if in_refs:
                ref_lines.append(s)
                continue
            buf.append(s)
            if s.endswith((".", ":", "?", "!")) and len(" ".join(buf)) > 600:
                paragraphs.append((section, pno + 1, " ".join(buf)))
                buf = []
        if buf:
            paragraphs.append((section, pno + 1, " ".join(buf)))
    out.passages = chunk(paragraphs)
    out.references = split_references(ref_lines)
    if not out.passages or sum(len(p["text"]) for p in out.passages) < 200:
        out.status = "scanned"
    return out


_LINE_HYPHEN = re.compile(r"(\w+)-[ \t]*\n[ \t]*(\w+)")


def dehyphenate(pages: list[str]) -> list[str]:
    """Undo hyphens at line breaks. "transfor-\nmer" becomes "transformer" when the joined word occurs elsewhere in
    the document and the hyphenated form does not; otherwise the hyphen is kept, so "state-\nof-the-art" stays
    "state-of-the-art". Quote checks ignore hyphens between letters, so either reading still verifies."""
    body = _LINE_HYPHEN.sub(" ", "\n".join(pages))  # words as they appear away from line ends
    words = {w.lower() for w in re.findall(r"\w+", body)}
    hyphenated = {h.lower() for h in re.findall(r"\w+-\w+", body)}

    def fix(m: re.Match) -> str:
        a, b = m.group(1), m.group(2)
        if f"{a}-{b}".lower() not in hyphenated and (
                (a + b).lower() in words or (a.lower() not in words and b.lower() not in words)):
            return a + b  # a word split for layout ("transfor-mer", "con-volution")
        return f"{a}-{b}"

    return [_LINE_HYPHEN.sub(fix, p) for p in pages]


def split_references(lines: list[str]) -> list[dict]:
    refs, cur, label = [], [], None
    for s in lines:
        m = re.match(r"^\[(\d+)\]\s*(.*)$", s)
        if m:
            if cur:
                refs.append({"label": label, "raw": " ".join(cur)})
            label, cur = m.group(1), [m.group(2)]
            continue
        # Author-year styles: a new entry usually starts with "Surname, I." or "Surname I," after a line ending in a period.
        if cur and cur[-1].endswith(".") and re.match(r"^[A-Z][A-Za-z'\-]+,?\s+(?:[A-Z]\.|[A-Z][a-z]+)", s) and len(" ".join(cur)) > 60:
            refs.append({"label": label, "raw": " ".join(cur)})
            label, cur = None, [s]
            continue
        cur.append(s)
    if cur:
        refs.append({"label": label, "raw": " ".join(cur)})
    return [r for r in refs if len(r["raw"]) > 25][:600]


def parse_jats(xml: bytes) -> Parsed:
    soup = BeautifulSoup(xml, "lxml-xml")
    out = Parsed()
    paragraphs = []
    abstract = soup.find("abstract")
    if abstract:
        paragraphs.append(("Abstract", None, abstract.get_text(" ", strip=True)))
    body = soup.find("body")
    if body:
        for p in body.find_all("p"):
            sec = p.find_parent("sec")
            title = sec.find("title").get_text(" ", strip=True) if sec and sec.find("title") else "Body"
            paragraphs.append((title, None, p.get_text(" ", strip=True)))
    out.passages = chunk(paragraphs)
    for ref in soup.find_all("ref"):
        label = ref.find("label")
        out.references.append({"label": label.get_text(strip=True) if label else None, "anchor": ref.get("id"),
                               "raw": re.sub(r"\s+", " ", ref.get_text(" ", strip=True))})
    if not out.passages:
        out.status = "empty"
    return out


def parse_webpage(html: str) -> Parsed:
    soup = BeautifulSoup(html, "lxml")
    for junk in soup.select("script, style, nav, footer, header, aside, noscript, form"):
        junk.decompose()
    root = soup.find("article") or soup.find("main") or soup.body or soup
    out = Parsed(title=soup.title.get_text(strip=True) if soup.title else None)
    paragraphs, section = [], "Body"
    for el in root.find_all(["h1", "h2", "h3", "p", "li", "pre"]):
        text = re.sub(r"\s+", " ", el.get_text(" ", strip=True))
        if el.name in ("h1", "h2", "h3"):
            section = text[:80] or section
        elif len(text) > 30:
            paragraphs.append((section, None, text))
    out.passages = chunk(paragraphs)
    if not out.passages:
        out.status = "empty"
    return out


def parse_latex_bbl(data: bytes) -> list[dict]:
    """References from an arXiv source tarball's .bbl file (exact, as compiled)."""
    try:
        try:
            raw = gzip.decompress(data)
        except OSError:
            raw = data
        refs = []
        with tarfile.open(fileobj=io.BytesIO(raw)) as tar:
            for member in tar.getmembers():
                if member.name.endswith(".bbl"):
                    bbl = tar.extractfile(member).read().decode("utf-8", "replace")
                    for item in re.split(r"\\bibitem", bbl)[1:]:
                        m = re.match(r"\s*(?:\[[^\]]*\])?\s*\{([^}]*)\}(.*)", item, re.S)
                        if m:
                            body = re.sub(r"\\newblock|\\em\b|\\emph|[{}~]|\\&", " ", m.group(2))
                            refs.append({"label": m.group(1), "raw": re.sub(r"\s+", " ", body).strip()[:600]})
        return refs
    except Exception:
        return []


# ------------------------------------------------------------------------------------------------ dispatch
def parse(data: bytes, kind: str) -> Parsed:
    """Parse full text of one kind: arxiv_html (LaTeXML), jats, pdf, webpage or text (paragraphs split by blank lines)."""
    if kind == "arxiv_html":
        return parse_arxiv_html(data.decode("utf-8", "replace"))
    if kind == "jats":
        return parse_jats(data)
    if kind == "pdf":
        return parse_pdf(data)
    if kind == "webpage":
        return parse_webpage(data.decode("utf-8", "replace"))
    if kind == "text":
        paras = [("Body", None, p) for p in re.split(r"\n\s*\n", data.decode("utf-8-sig", "replace")) if p.strip()]
        return Parsed(passages=chunk(paras), status="ok" if paras else "empty")
    raise ValueError(f"no parser for {kind}")


def file_kind(filename: str, data: bytes) -> str:
    """The parser for an uploaded file: PDF by its bytes, HTML and XML by the file name, anything else as text."""
    name = filename.lower()
    if data[:5] == b"%PDF-":
        return "pdf"
    if name.endswith((".html", ".htm")):
        return "webpage"
    return "jats" if name.endswith(".xml") else "text"


def pdf_front(data: bytes) -> tuple[str | None, str]:
    """(title, front matter) of an uploaded PDF: its metadata title, else the start of its first passage, and the
    text of its first few passages (where the paper's own arXiv stamp or DOI is printed)."""
    if data[:5] != b"%PDF-":
        return None, ""
    try:
        parsed = parse_pdf(data)
    except Exception:
        return None, ""
    first = parsed.passages[0]["text"] if parsed.passages else ""
    front = " ".join(p["text"] for p in parsed.passages[:4])[:4000]
    return parsed.title or first[:160] or None, front


def pdf_title(data: bytes) -> str | None:
    """A title for an uploaded PDF: its metadata title, else the start of its first passage."""
    return pdf_front(data)[0]
