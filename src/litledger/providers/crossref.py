"""Crossref (DOI metadata of record for journals and ACL) and DataCite (arXiv/Zenodo/dataset DOIs)."""
from __future__ import annotations

import re
from urllib.parse import quote

from ..csl import csl_date, person
from ..ids import Ident, is_low_trust_doi, norm_doi
from .base import LOOKUP, REFS, SEARCH, VERIFY, Provider, Record

TYPE_MAP = {
    "journal-article": "article-journal", "proceedings-article": "paper-conference", "book-chapter": "chapter",
    "book": "book", "monograph": "book", "edited-book": "book", "posted-content": "article", "dissertation": "thesis",
    "report": "report", "dataset": "dataset", "reference-entry": "entry", "standard": "standard",
    "peer-review": "review", "component": "article",
}


NOT_WORKS = {"peer-review", "component", "grant", "journal-issue", "journal-volume", "journal", "proceedings",
             "proceedings-series", "book-series", "book-set", "report-series"}


def strip_jats(text: str) -> str:
    text = re.sub(r"<jats:title>.*?</jats:title>", " ", text or "", flags=re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def crossref_record(msg: dict) -> Record:
    doi = norm_doi(msg.get("DOI", ""))
    date = None
    for key in ("published-print", "published-online", "issued", "published", "created"):
        parts = (msg.get(key) or {}).get("date-parts") or []
        if parts and parts[0] and parts[0][0]:
            date = csl_date(*(parts[0] + [None, None])[:3])
            break
    authors = []
    for a in msg.get("author", []) or []:
        authors.append(person(a.get("given"), a.get("family"), a.get("name")))
    titles = msg.get("title") or []
    containers = msg.get("container-title") or []
    csl = {
        "type": TYPE_MAP.get(msg.get("type", ""), "article-journal"),
        "title": re.sub(r"\s+", " ", titles[0]).strip() if titles else "",
        "author": [a for a in authors if a],
        "issued": date,
        "container-title": containers[0] if containers else msg.get("event", {}).get("name", ""),
        "volume": msg.get("volume"), "issue": msg.get("issue"), "page": msg.get("page"),
        "publisher": msg.get("publisher"), "DOI": doi, "URL": f"https://doi.org/{doi}" if doi else None,
        "abstract": strip_jats(msg.get("abstract", "")) or None,
        "ISSN": (msg.get("ISSN") or [None])[0], "ISBN": (msg.get("ISBN") or [None])[0],
    }
    if msg.get("type") == "proceedings-article" and msg.get("event", {}).get("name"):
        csl["event-title"] = msg["event"]["name"]
    kind = "preprint" if msg.get("type") == "posted-content" else "published"
    extra = {"citation_count": msg.get("is-referenced-by-count"), "crossref_type": msg.get("type"),
             "references": [norm_doi(r["DOI"]) for r in msg.get("reference", []) or [] if r.get("DOI")],
             "update_to": msg.get("update-to"), "relation": msg.get("relation")}
    if is_low_trust_doi(doi):
        extra["low_trust"] = True
    return Record("crossref", {"doi": doi} if doi else {}, {k: v for k, v in csl.items() if v}, kind=kind, extra=extra)


class Crossref(Provider):
    id = "crossref"
    name = "Crossref"
    capabilities = frozenset({LOOKUP, SEARCH, REFS, VERIFY})
    schemes = frozenset({"doi"})
    default = "on"
    discover_default = True
    recommendation = "recommended"
    interval = 0.4
    wants_mailto = True

    def _params(self, extra: dict | None = None) -> dict:
        params = dict(extra or {})
        if self.settings.contact_email:
            params["mailto"] = self.settings.contact_email
        return params

    def degraded_note(self) -> str | None:
        return None if self.settings.contact_email else "slow public pool (no contact email set)"

    def lookup(self, ident: Ident) -> Record | None:
        resp = self.get("https://api.crossref.org/works/" + quote(ident.value, safe=""), params=self._params(),
                        ttl=14 * 86400)
        return crossref_record(resp.json()["message"])

    def search(self, query: str, limit: int = 10, year_from: int | None = None, year_to: int | None = None) -> list[Record]:
        filters = []
        if year_from:
            filters.append(f"from-pub-date:{year_from}")
        if year_to:
            filters.append(f"until-pub-date:{year_to}")
        params = self._params({"query.bibliographic": query, "rows": limit,
                               "select": "DOI,title,author,issued,published,container-title,type,publisher,is-referenced-by-count,volume,issue,page,event"})
        if filters:
            params["filter"] = ",".join(filters)
        resp = self.get("https://api.crossref.org/works", params=params, ttl=86400, interval=1.0)
        return [crossref_record(m) for m in resp.json()["message"]["items"] if m.get("type") not in NOT_WORKS]

    def match_title(self, title: str, author: str | None = None, year: int | None = None) -> list[Record]:
        params = self._params({"query.bibliographic": title, "rows": 5,
                               "select": "DOI,title,author,issued,published,container-title,type,publisher,volume,issue,page,event"})
        if author:
            params["query.author"] = author
        resp = self.get("https://api.crossref.org/works", params=params, ttl=7 * 86400, interval=1.0)
        return [crossref_record(m) for m in resp.json()["message"]["items"]]

    def references(self, record_ids: dict[str, str], limit: int = 100) -> list[Record]:
        doi = record_ids.get("doi")
        if not doi:
            return []
        rec = self.lookup(Ident("doi", doi))
        return [Record("crossref", {"doi": d}) for d in (rec.extra.get("references") or [])[:limit]] if rec else []

    def updates(self, doi: str) -> list[dict]:
        resp = self.get("https://api.crossref.org/works", params=self._params({"filter": f"updates:{doi}", "rows": 20}),
                        ttl=86400, interval=1.0)
        out = []
        for item in resp.json()["message"]["items"]:
            for upd in item.get("update-to", []) or []:
                if norm_doi(upd.get("DOI", "")) == doi:
                    out.append({"type": upd.get("type"), "notice_doi": norm_doi(item.get("DOI", "")),
                                "date": ((upd.get("updated") or {}).get("date-parts") or [[None]])[0][0]})
        return out


DATACITE_TYPES = {"Software": "software", "Dataset": "dataset", "Preprint": "article", "Text": "article",
                  "JournalArticle": "article-journal", "ConferencePaper": "paper-conference", "Report": "report",
                  "Book": "book", "BookChapter": "chapter", "Dissertation": "thesis"}


class DataCite(Provider):
    id = "datacite"
    name = "DataCite"
    capabilities = frozenset({LOOKUP})
    schemes = frozenset({"doi"})
    default = "on"
    interval = 0.6

    def lookup(self, ident: Ident) -> Record | None:
        resp = self.get("https://api.datacite.org/dois/" + quote(ident.value, safe=""), ttl=14 * 86400)
        attrs = resp.json()["data"]["attributes"]
        authors = []
        for c in attrs.get("creators", []) or []:
            if c.get("familyName"):
                authors.append(person(c.get("givenName"), c.get("familyName")))
            elif c.get("name"):
                authors.append(person(literal=c["name"]) if c.get("nameType") == "Personal" else {"literal": c["name"]})
        general = (attrs.get("types") or {}).get("resourceTypeGeneral", "")
        titles = attrs.get("titles") or [{}]
        doi = norm_doi(attrs.get("doi", ident.value))
        descriptions = [d.get("description") for d in attrs.get("descriptions", []) or [] if d.get("descriptionType") == "Abstract"]
        csl = {"type": DATACITE_TYPES.get(general, "article"), "title": titles[0].get("title", ""),
               "author": authors, "issued": csl_date(attrs.get("publicationYear")), "publisher": attrs.get("publisher"),
               "DOI": doi, "URL": attrs.get("url") or f"https://doi.org/{doi}",
               "abstract": descriptions[0] if descriptions else None, "version": attrs.get("version")}
        ids = {"doi": doi}
        m = re.match(r"^10\.48550/arxiv\.(.+)$", doi)
        if m:
            ids = {"arxiv": m.group(1)}
            csl["container-title"] = "arXiv"
        kind = "software" if general == "Software" else "preprint" if general == "Preprint" or m else "published"
        return Record("datacite", ids, {k: v for k, v in csl.items() if v}, kind=kind)
