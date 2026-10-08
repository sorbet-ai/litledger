"""Semantic Scholar: ID cross-walk and the ML citation graph with contexts/intents."""
from __future__ import annotations

from ..csl import csl_date, csl_date_from_iso, person
from ..ids import Ident, norm_doi
from .base import CITES, LOOKUP, REFS, SEARCH, Provider, Record

API = "https://api.semanticscholar.org"
FIELDS = ("paperId,corpusId,externalIds,title,abstract,venue,publicationVenue,year,publicationDate,authors,"
          "citationCount,influentialCitationCount,openAccessPdf,tldr,journal,publicationTypes")
LIGHT = "paperId,corpusId,externalIds,title,venue,year,authors,citationCount"


def s2_record(p: dict) -> Record | None:
    if not p or not p.get("title"):
        return None
    ext = p.get("externalIds") or {}
    ids = {}
    if p.get("paperId"):
        ids["s2"] = p["paperId"]
    if p.get("corpusId") or ext.get("CorpusId"):
        ids["corpusid"] = str(p.get("corpusId") or ext.get("CorpusId"))
    if ext.get("ArXiv"):
        ids["arxiv"] = ext["ArXiv"]
    if ext.get("DOI"):
        doi = norm_doi(ext["DOI"])
        if doi.startswith("10.48550/arxiv."):
            ids.setdefault("arxiv", doi.split("arxiv.", 1)[1])
        else:
            ids["doi"] = doi
    for src, dst in (("DBLP", "dblp"), ("ACL", "acl"), ("PubMed", "pmid"), ("PubMedCentral", "pmcid")):
        if ext.get(src):
            ids[dst] = str(ext[src]) if dst != "pmcid" else ("PMC" + str(ext[src]).removeprefix("PMC"))
    types = p.get("publicationTypes") or []
    venue = (p.get("publicationVenue") or {}).get("name") or p.get("venue") or ""
    journal = p.get("journal") or {}
    is_preprint = (not venue or venue.lower() in ("arxiv", "arxiv.org")) and "arxiv" in ids
    csl = {"type": "article" if is_preprint else "paper-conference" if "Conference" in types else "article-journal",
           "title": p["title"], "author": [person(literal=a.get("name", "")) for a in p.get("authors", []) or []],
           "issued": csl_date_from_iso(p.get("publicationDate")) or csl_date(p.get("year")),
           "container-title": "arXiv" if is_preprint else venue or journal.get("name"),
           "volume": journal.get("volume"), "page": (journal.get("pages") or "").strip() or None,
           "abstract": p.get("abstract"), "DOI": ids.get("doi")}
    return Record("s2", ids, {k: v for k, v in csl.items() if v}, kind="preprint" if is_preprint else "published", extra={
        "citation_count": p.get("citationCount"), "influential": p.get("influentialCitationCount"),
        "oa_pdf": (p.get("openAccessPdf") or {}).get("url") or None, "tldr": (p.get("tldr") or {}).get("text")})


def s2_key(ident: Ident) -> str | None:
    return {"arxiv": f"ARXIV:{ident.value}", "doi": f"DOI:{ident.value}", "corpusid": f"CorpusId:{ident.value}",
            "s2": ident.value, "acl": f"ACL:{ident.value}", "pmid": f"PMID:{ident.value}",
            "pmcid": f"PMCID:{ident.value.removeprefix('PMC')}", "url": f"URL:{ident.value}"}.get(ident.scheme)


class SemanticScholar(Provider):
    id = "s2"
    name = "Semantic Scholar"
    capabilities = frozenset({LOOKUP, SEARCH, REFS, CITES})
    schemes = frozenset({"arxiv", "doi", "corpusid", "s2", "acl", "pmid", "pmcid", "url"})
    default = "key"
    discover_default = True
    recommendation = "strongly-recommended"
    impact = "no ML citation graph, no recommendations, no arXiv<->DOI<->venue ID cross-walk"
    setup = ["Request a free key at https://www.semanticscholar.org/product/api#api-key-form",
             "Paste it under Admin → Sources on the web UI (or: litledger config set S2_API_KEY=<your key>); it applies at once"]
    credential_vars = ["S2_API_KEY"]
    interval = 1.05

    def __init__(self, settings, http):
        super().__init__(settings, http)
        # Without a key, S2 can still be switched on explicitly ('Extra sources to enable': s2) as a best-effort source on the
        # shared unauthenticated pool: slower pacing, used only when asked for, never in default discovery/enrichment.
        self.keyed = bool(settings.get("S2_API_KEY"))
        self.discover_default = self.keyed
        self.interval = 1.05 if self.keyed else 3.5

    def credentials_present(self) -> bool:
        return self.keyed or "s2" in self.settings.enable

    def degraded_note(self) -> str | None:
        return None if self.keyed else "keyless best-effort (shared pool, often rate-limited)"

    def _headers(self) -> dict:
        return {"x-api-key": self.settings.get("S2_API_KEY")} if self.keyed else {}

    def _get(self, path: str, **kw):
        return self.get(API + path, headers=self._headers(), **kw)

    def _ids_key(self, record_ids: dict[str, str]) -> str | None:
        for scheme in ("s2", "corpusid", "arxiv", "doi", "acl", "pmid"):
            if record_ids.get(scheme):
                return s2_key(Ident(scheme, record_ids[scheme]))
        return None

    def lookup(self, ident: Ident) -> Record | None:
        key = s2_key(ident)
        if not key:
            return None
        resp = self._get(f"/graph/v1/paper/{key}", params={"fields": FIELDS}, ttl=7 * 86400)
        return s2_record(resp.json())

    def search(self, query: str, limit: int = 10, year_from: int | None = None, year_to: int | None = None) -> list[Record]:
        params = {"query": query, "limit": limit, "fields": FIELDS}
        if year_from or year_to:
            params["year"] = f"{year_from or ''}-{year_to or ''}"
        resp = self._get("/graph/v1/paper/search", params=params, ttl=86400)
        return [r for r in (s2_record(p) for p in resp.json().get("data", []) or []) if r]

    def match_title(self, title: str, author: str | None = None, year: int | None = None) -> list[Record]:
        resp = self._get("/graph/v1/paper/search/match", params={"query": title, "fields": FIELDS}, ttl=7 * 86400, ok_404=True)
        if resp.status == 404:
            return []
        return [r for r in (s2_record(p) for p in resp.json().get("data", []) or []) if r]

    def _edges(self, kind: str, record_ids: dict[str, str], limit: int) -> list[Record]:
        key = self._ids_key(record_ids)
        if not key:
            return []
        side = "citedPaper" if kind == "references" else "citingPaper"
        resp = self._get(f"/graph/v1/paper/{key}/{kind}",
                         params={"fields": "contexts,intents,isInfluential," + LIGHT, "limit": min(limit, 1000)}, ttl=3 * 86400)
        out = []
        for edge in resp.json().get("data", []) or []:
            rec = s2_record(edge.get(side) or {})
            if rec:
                rec.extra.update(contexts=(edge.get("contexts") or [])[:3], intents=edge.get("intents") or [],
                                 influential=edge.get("isInfluential"))
                out.append(rec)
        return out

    def references(self, record_ids: dict[str, str], limit: int = 100) -> list[Record]:
        return self._edges("references", record_ids, limit)

    def citations(self, record_ids: dict[str, str], limit: int = 100) -> list[Record]:
        return self._edges("citations", record_ids, limit)
