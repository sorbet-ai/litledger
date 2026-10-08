"""arXiv: preprint metadata of record, search, and full-text locations (HTML, LaTeX source, PDF)."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from ..csl import csl_date_from_iso, person
from ..ids import Ident, norm_doi
from .base import FULLTEXT, LOOKUP, SEARCH, Location, Provider, Record

NS = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom",
      "os": "http://a9.com/-/spec/opensearch/1.1/"}
API = "https://export.arxiv.org/api/query"


def _text(el, path: str) -> str:
    found = el.find(path, NS)
    return re.sub(r"\s+", " ", found.text).strip() if found is not None and found.text else ""


def parse_feed(xml: bytes) -> list[Record]:
    root = ET.fromstring(xml)
    out = []
    for entry in root.findall("a:entry", NS):
        raw_id = _text(entry, "a:id")
        m = re.search(r"arxiv\.org/abs/(.+?)(v\d+)?$", raw_id)
        if not m:
            continue
        arxiv_id, version = m.group(1), m.group(2)
        title = _text(entry, "a:title")
        if not title or title.lower() == "error":
            continue
        authors = [person(literal=_text(a, "a:name")) for a in entry.findall("a:author", NS)]
        primary = entry.find("arxiv:primary_category", NS)
        ids = {"arxiv": arxiv_id}
        published_doi = _text(entry, "arxiv:doi")
        if published_doi:
            ids["doi"] = norm_doi(published_doi.split()[0])
        csl = {
            "type": "article", "title": title, "author": authors, "abstract": _text(entry, "a:summary"),
            "issued": csl_date_from_iso(_text(entry, "a:published")), "container-title": "arXiv",
            "number": arxiv_id, "URL": f"https://arxiv.org/abs/{arxiv_id}", "publisher": "arXiv",
        }
        out.append(Record("arxiv", ids, {k: v for k, v in csl.items() if v}, kind="preprint", version=version, extra={
            "primary_class": primary.get("term") if primary is not None else None,
            "journal_ref": _text(entry, "arxiv:journal_ref") or None,
            "comment": _text(entry, "arxiv:comment") or None,
            "updated": _text(entry, "a:updated") or None,
        }))
    return out


class Arxiv(Provider):
    id = "arxiv"
    name = "arXiv"
    capabilities = frozenset({LOOKUP, SEARCH, FULLTEXT})
    schemes = frozenset({"arxiv"})
    default = "on"
    discover_default = True
    interval = 3.1  # arXiv asks for one request per 3 seconds

    def lookup(self, ident: Ident) -> Record | None:
        resp = self.get(API, params={"id_list": ident.value, "max_results": 1}, ttl=3 * 86400)
        records = parse_feed(resp.content)
        return records[0] if records else None

    def search(self, query: str, limit: int = 10, year_from: int | None = None, year_to: int | None = None) -> list[Record]:
        words = [w for w in re.findall(r"[\w\-]+", query) if len(w) > 1][:12]
        if not words:
            return []
        q = " AND ".join(f"all:{w}" for w in words)
        if year_from or year_to:
            q += f" AND submittedDate:[{year_from or 1991}01010000 TO {year_to or 2100}12312359]"
        resp = self.get(API, params={"search_query": q, "max_results": limit, "sortBy": "relevance"}, ttl=86400)
        return parse_feed(resp.content)

    def match_title(self, title: str, author: str | None = None, year: int | None = None) -> list[Record]:
        clean = re.sub(r"[^\w\s\-]", " ", title)
        clean = re.sub(r"\s+", " ", clean).strip()
        if not clean:
            return []
        q = f'ti:"{clean}"'
        if author:
            q += f" AND au:{re.sub(r'[^A-Za-z]', '', author)}"
        resp = self.get(API, params={"search_query": q, "max_results": 5}, ttl=7 * 86400)
        return parse_feed(resp.content)

    def locations(self, record_ids: dict[str, str]) -> list[Location]:
        arxiv_id = record_ids.get("arxiv")
        if not arxiv_id:
            return []
        return [Location(f"https://arxiv.org/html/{arxiv_id}", "arxiv_html", self.id),
                # ar5iv renders older papers (before arXiv's native HTML) with the same LaTeXML markup.
                Location(f"https://ar5iv.labs.arxiv.org/html/{arxiv_id}", "arxiv_html", "ar5iv"),
                Location(f"https://arxiv.org/pdf/{arxiv_id}", "pdf", self.id)]
