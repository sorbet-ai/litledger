"""OpenAlex: free ID/DOI lookups, OA locations, budgeted search, references and citations."""
from __future__ import annotations

import threading
import time

from ..csl import csl_date, person
from ..ids import Ident, is_low_trust_doi, norm_doi
from .base import CITES, FULLTEXT, LOOKUP, REFS, SEARCH, Location, Provider, Record

API = "https://api.openalex.org"
SELECT = ("id,doi,title,display_name,publication_year,publication_date,authorships,primary_location,"
          "best_oa_location,type,ids,cited_by_count,abstract_inverted_index,biblio,referenced_works")
TYPE_MAP = {"article": "article-journal", "preprint": "article", "book-chapter": "chapter", "book": "book",
            "dissertation": "thesis", "dataset": "dataset", "report": "report", "review": "article-journal"}


def _abstract(inverted: dict | None) -> str | None:
    if not inverted:
        return None
    slots: dict[int, str] = {}
    for word, positions in inverted.items():
        for p in positions:
            slots[p] = word
    return " ".join(slots[i] for i in sorted(slots)) or None


def openalex_record(w: dict) -> Record:
    ids = {}
    wid = (w.get("id") or "").rsplit("/", 1)[-1]
    if wid:
        ids["openalex"] = wid
    doi = norm_doi(w.get("doi") or "")
    low_trust = False
    if doi.startswith("10.48550/arxiv."):
        ids["arxiv"] = doi.split("arxiv.", 1)[1]
    elif doi:
        low_trust = is_low_trust_doi(doi)
        ids["doi"] = doi
    raw_ids = w.get("ids") or {}
    if raw_ids.get("pmid"):
        ids["pmid"] = raw_ids["pmid"].rsplit("/", 1)[-1]
    if raw_ids.get("pmcid"):
        ids["pmcid"] = raw_ids["pmcid"].rsplit("/", 1)[-1].upper()
    authors = [person(literal=(a.get("author") or {}).get("display_name", "")) for a in w.get("authorships", []) or []]
    loc = w.get("primary_location") or {}
    source = loc.get("source") or {}
    biblio = w.get("biblio") or {}
    pages = "-".join(p for p in (biblio.get("first_page"), biblio.get("last_page")) if p)
    csl = {"type": TYPE_MAP.get(w.get("type", ""), "article-journal"), "title": w.get("title") or w.get("display_name") or "",
           "author": [a for a in authors if a], "issued": csl_date(*((w.get("publication_date") or "").split("-") + [None] * 3)[:3])
           or csl_date(w.get("publication_year")),
           "container-title": source.get("display_name"), "volume": biblio.get("volume"), "issue": biblio.get("issue"),
           "page": pages or None, "DOI": ids.get("doi"), "abstract": _abstract(w.get("abstract_inverted_index"))}
    oa = w.get("best_oa_location") or {}
    kind = "preprint" if w.get("type") == "preprint" else "published"
    return Record("openalex", ids, {k: v for k, v in csl.items() if v}, kind=kind, extra={
        "citation_count": w.get("cited_by_count"), "oa_pdf": oa.get("pdf_url"), "oa_license": oa.get("license"),
        "referenced_works": [r.rsplit("/", 1)[-1] for r in w.get("referenced_works", []) or []],
        "low_trust": low_trust or None})


class OpenAlex(Provider):
    id = "openalex"
    name = "OpenAlex"
    capabilities = frozenset({LOOKUP, SEARCH, REFS, CITES, FULLTEXT})
    schemes = frozenset({"doi", "openalex", "pmid", "pmcid", "arxiv"})
    default = "on"
    discover_default = True
    recommendation = "strongly-recommended"
    impact = "search capped at ~$0.10/day of free credit (ID/DOI lookups unaffected)"
    setup = ["Create a free account at https://openalex.org and copy the API key from your account settings",
             "Paste it under Admin → Sources on the web UI (or: litledger config set OPENALEX_API_KEY=<your key>); it applies at once"]
    credential_vars = ["OPENALEX_API_KEY"]
    interval = 0.12
    wants_mailto = True

    def __init__(self, settings, http):
        super().__init__(settings, http)
        self._remaining_usd: float | None = None
        self._lock = threading.Lock()
        self._reset_at = 0.0

    def carry_over(self, old: Provider) -> None:
        """The day's remaining credit survives a settings save, unless the key changed (a new key has its own)."""
        if isinstance(old, OpenAlex) and old.settings.get("OPENALEX_API_KEY") == self.settings.get("OPENALEX_API_KEY"):
            with old._lock:
                self._remaining_usd, self._reset_at = old._remaining_usd, old._reset_at

    def credentials_present(self) -> bool:  # keyless is allowed, the key only raises the budget
        return True

    def has_key(self) -> bool:
        return bool(self.settings.get("OPENALEX_API_KEY"))

    def degraded_note(self) -> str | None:
        return None if self.has_key() else "keyless: search limited to ~$0.10/day"

    def _params(self, extra: dict) -> dict:
        params = dict(extra)
        if self.has_key():
            params["api_key"] = self.settings.get("OPENALEX_API_KEY")
        return params

    def _call(self, path: str, params: dict, cost: float, ttl: float):
        with self._lock:
            if self._remaining_usd is not None and self._remaining_usd < cost and time.time() < self._reset_at:
                raise self.fail("budget", f"daily OpenAlex credit exhausted (remaining ${self._remaining_usd:.4f})")
        resp = self.get(API + path, params=self._params(params), ttl=ttl)
        remaining = resp.headers.get("x-ratelimit-remaining-usd")
        if remaining and not resp.cached:
            with self._lock:
                try:
                    self._remaining_usd = float(remaining)
                    self._reset_at = time.time() + float(resp.headers.get("x-ratelimit-reset", 3600))
                except ValueError:
                    pass
        return resp

    def lookup(self, ident: Ident) -> Record | None:
        key = {"doi": f"doi:{ident.value}", "openalex": ident.value, "pmid": f"pmid:{ident.value}",
               "pmcid": f"pmcid:{ident.value}", "arxiv": f"doi:10.48550/arxiv.{ident.value}"}.get(ident.scheme)
        if not key:
            return None
        resp = self._call(f"/works/{key}", {"select": SELECT}, cost=0.0, ttl=14 * 86400)
        return openalex_record(resp.json())

    def search(self, query: str, limit: int = 10, year_from: int | None = None, year_to: int | None = None) -> list[Record]:
        params = {"search": query, "per-page": limit, "select": SELECT}
        filters = []
        if year_from:
            filters.append(f"from_publication_date:{year_from}-01-01")
        if year_to:
            filters.append(f"to_publication_date:{year_to}-12-31")
        if filters:
            params["filter"] = ",".join(filters)
        resp = self._call("/works", params, cost=0.001, ttl=86400)
        return [openalex_record(w) for w in resp.json().get("results", [])]

    def match_title(self, title: str, author: str | None = None, year: int | None = None) -> list[Record]:
        clean = title.replace(",", " ").replace(":", " ").replace("|", " ")
        resp = self._call("/works", {"filter": f"title.search:{clean}", "per-page": 5, "select": SELECT}, cost=0.0001,
                          ttl=7 * 86400)
        return [openalex_record(w) for w in resp.json().get("results", [])]

    def _work(self, record_ids: dict[str, str]) -> Record | None:
        for scheme in ("openalex", "doi", "arxiv", "pmid"):
            if record_ids.get(scheme):
                try:
                    return self.lookup(Ident(scheme, record_ids[scheme]))
                except Exception:
                    continue
        return None

    def references(self, record_ids: dict[str, str], limit: int = 100) -> list[Record]:
        work = self._work(record_ids)
        refs = (work.extra.get("referenced_works") if work else None) or []
        out = []
        for i in range(0, min(len(refs), limit), 50):
            chunk = refs[i:i + 50]
            resp = self._call("/works", {"filter": "openalex:" + "|".join(chunk), "per-page": 50, "select": SELECT},
                              cost=0.0001, ttl=14 * 86400)
            out.extend(openalex_record(w) for w in resp.json().get("results", []))
        return out

    def citations(self, record_ids: dict[str, str], limit: int = 100) -> list[Record]:
        work = self._work(record_ids)
        if not work or not work.ids.get("openalex"):
            return []
        resp = self._call("/works", {"filter": f"cites:{work.ids['openalex']}", "per-page": min(limit, 100),
                                     "sort": "cited_by_count:desc", "select": SELECT}, cost=0.0001, ttl=3 * 86400)
        return [openalex_record(w) for w in resp.json().get("results", [])]

    def locations(self, record_ids: dict[str, str]):
        work = self._work(record_ids)
        if work and work.extra.get("oa_pdf"):
            return [Location(work.extra["oa_pdf"], "pdf", self.id, work.extra.get("oa_license"))]
        return []
