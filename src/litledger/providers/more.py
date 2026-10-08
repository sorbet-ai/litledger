"""Smaller adapters: Unpaywall, OpenCitations, Europe PMC, PubMed, bioRxiv/medRxiv, INSPIRE, Zenodo, CORE,
OpenReview, NASA ADS."""
from __future__ import annotations

import re
import threading
import time
from urllib.parse import quote

from ..csl import csl_date, csl_date_from_iso, person
from ..ids import Ident, norm_doi
from .base import CITES, FULLTEXT, LOOKUP, REFS, REVIEWS, SEARCH, Location, Provider, Record


# ---------------------------------------------------------------------------------------------- Unpaywall
class Unpaywall(Provider):
    id = "unpaywall"
    name = "Unpaywall"
    capabilities = frozenset({FULLTEXT})
    auth = "email"
    default = "on"
    recommendation = "recommended"
    impact = "no open-access PDF locations for DOI'd papers"
    interval = 0.2
    wants_mailto = True

    def credentials_present(self) -> bool:
        return bool(self.settings.contact_email)

    def locations(self, record_ids: dict[str, str]) -> list[Location]:
        doi = record_ids.get("doi")
        if not doi:
            return []
        resp = self.get(f"https://api.unpaywall.org/v2/{quote(doi, safe='/')}",
                        params={"email": self.settings.contact_email}, ttl=7 * 86400)
        data = resp.json()
        out = []
        for loc in [data.get("best_oa_location")] + (data.get("oa_locations") or []):
            if loc and loc.get("url_for_pdf") and all(o.url != loc["url_for_pdf"] for o in out):
                out.append(Location(loc["url_for_pdf"], "pdf", self.id, loc.get("license")))
        return out[:3]


# ---------------------------------------------------------------------------------------------- OpenCitations
def _oc_ids(field: str) -> dict[str, str]:
    ids = {}
    for token in (field or "").split():
        scheme, _, value = token.partition(":")
        if scheme == "doi":
            ids["doi"] = norm_doi(value)
        elif scheme == "pmid":
            ids["pmid"] = value
        elif scheme == "openalex":
            ids["openalex"] = value
    return ids


class OpenCitations(Provider):
    id = "opencitations"
    name = "OpenCitations"
    capabilities = frozenset({REFS, CITES})
    default = "on"
    interval = 0.35  # 180 req/min

    def _headers(self) -> dict:
        token = self.settings.get("OPENCITATIONS_TOKEN")
        return {"authorization": token} if token else {}

    def _meta(self, dois: list[str]) -> list[Record]:
        out = []
        for i in range(0, len(dois), 20):
            chunk = "__".join(f"doi:{d}" for d in dois[i:i + 20])
            resp = self.get(f"https://api.opencitations.net/meta/v1/metadata/{chunk}", headers=self._headers(), ttl=14 * 86400)
            for item in resp.json():
                ids = _oc_ids(item.get("id", ""))
                authors = [person(literal=re.sub(r"\s*\[.*?\]", "", a).strip()) for a in (item.get("author") or "").split(";") if a.strip()]
                csl = {"title": item.get("title"), "author": authors, "issued": csl_date_from_iso(item.get("pub_date")),
                       "container-title": re.sub(r"\s*\[.*?\]", "", item.get("venue") or "").strip() or None,
                       "volume": item.get("volume"), "issue": item.get("issue"), "page": item.get("page"), "DOI": ids.get("doi")}
                out.append(Record("opencitations", ids, {k: v for k, v in csl.items() if v}, kind="published"))
        return out

    def _edges(self, kind: str, record_ids: dict[str, str], limit: int) -> list[Record]:
        doi = record_ids.get("doi")
        if not doi:
            return []
        resp = self.get(f"https://api.opencitations.net/index/v2/{kind}/doi:{doi}", headers=self._headers(), ttl=3 * 86400)
        side = "cited" if kind == "references" else "citing"
        dois = [d for d in (_oc_ids(e.get(side, "")).get("doi") for e in resp.json()) if d][:limit]
        return self._meta(dois) if dois else []

    def references(self, record_ids: dict[str, str], limit: int = 100) -> list[Record]:
        return self._edges("references", record_ids, limit)

    def citations(self, record_ids: dict[str, str], limit: int = 100) -> list[Record]:
        return self._edges("citations", record_ids, limit)


# ---------------------------------------------------------------------------------------------- Europe PMC
EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest"


def epmc_record(r: dict) -> Record:
    ids = {}
    if r.get("pmid"):
        ids["pmid"] = r["pmid"]
    if r.get("pmcid"):
        ids["pmcid"] = r["pmcid"].upper()
    if r.get("doi"):
        ids["doi"] = norm_doi(r["doi"])
    authors = [person(a.get("firstName"), a.get("lastName"), a.get("fullName"))
               for a in ((r.get("authorList") or {}).get("author") or [])]
    journal = (r.get("journalInfo") or {})
    csl = {"type": "article" if r.get("source") == "PPR" else "article-journal", "title": (r.get("title") or "").rstrip("."),
           "author": [a for a in authors if a], "issued": csl_date_from_iso(r.get("firstPublicationDate")) or csl_date(r.get("pubYear")),
           "container-title": (journal.get("journal") or {}).get("title") or r.get("bookOrReportDetails", {}).get("publisher"),
           "volume": journal.get("volume"), "issue": journal.get("issue"), "page": r.get("pageInfo"),
           "abstract": re.sub(r"<[^>]+>", " ", r.get("abstractText") or "").strip() or None, "DOI": ids.get("doi")}
    return Record("europepmc", ids, {k: v for k, v in csl.items() if v},
                  kind="preprint" if r.get("source") == "PPR" else "published",
                  extra={"open_access": r.get("isOpenAccess") == "Y", "in_pmc": r.get("inPMC") == "Y",
                         "citation_count": r.get("citedByCount")})


class EuropePMC(Provider):
    id = "europepmc"
    name = "Europe PMC"
    capabilities = frozenset({LOOKUP, SEARCH, REFS, CITES, FULLTEXT})
    schemes = frozenset({"pmid", "pmcid", "doi"})
    default = "on"
    interval = 0.3

    def _search(self, query: str, limit: int, ttl: float = 86400) -> list[Record]:
        resp = self.get(f"{EPMC}/search", params={"query": query, "format": "json", "resultType": "core", "pageSize": limit}, ttl=ttl)
        return [epmc_record(r) for r in (resp.json().get("resultList") or {}).get("result", [])]

    def lookup(self, ident: Ident) -> Record | None:
        q = {"pmid": f"EXT_ID:{ident.value} AND SRC:MED", "pmcid": f"PMCID:{ident.value}", "doi": f'DOI:"{ident.value}"'}[ident.scheme]
        found = self._search(q, 1, ttl=14 * 86400)
        return found[0] if found else None

    def search(self, query: str, limit: int = 10, year_from: int | None = None, year_to: int | None = None) -> list[Record]:
        if year_from or year_to:
            query += f" AND PUB_YEAR:[{year_from or 1800} TO {year_to or 2100}]"
        return self._search(query, limit)

    def match_title(self, title: str, author: str | None = None, year: int | None = None) -> list[Record]:
        return self._search(f'TITLE:"{title}"', 5, ttl=7 * 86400)

    def _edges(self, kind: str, record_ids: dict[str, str], limit: int) -> list[Record]:
        src, value = ("MED", record_ids["pmid"]) if record_ids.get("pmid") else ("PMC", record_ids.get("pmcid", ""))
        if not value:
            return []
        resp = self.get(f"{EPMC}/{src}/{value}/{kind}", params={"format": "json", "pageSize": min(limit, 1000)}, ttl=3 * 86400)
        key = "referenceList" if kind == "references" else "citationList"
        items = (resp.json().get(key) or {}).get("reference" if kind == "references" else "citation", []) or []
        out = []
        for it in items[:limit]:
            ids = {k: v for k, v in (("pmid", it.get("id") if it.get("source") == "MED" else None), ("doi", norm_doi(it["doi"]) if it.get("doi") else None)) if v}
            csl = {"title": (it.get("title") or "").rstrip("."), "issued": csl_date(it.get("pubYear")),
                   "container-title": it.get("journalAbbreviation"),
                   "author": [person(literal=a.strip()) for a in (it.get("authorString") or "").split(",") if a.strip()][:20]}
            out.append(Record("europepmc", ids, {k: v for k, v in csl.items() if v}, kind="published"))
        return out

    def references(self, record_ids, limit=100):
        return self._edges("references", record_ids, limit)

    def citations(self, record_ids, limit=100):
        return self._edges("citations", record_ids, limit)

    def locations(self, record_ids: dict[str, str]) -> list[Location]:
        pmcid = record_ids.get("pmcid")
        return [Location(f"{EPMC}/{pmcid}/fullTextXML", "jats", self.id)] if pmcid else []


# ---------------------------------------------------------------------------------------------- PubMed
class PubMed(Provider):
    id = "pubmed"
    name = "PubMed E-utilities"
    capabilities = frozenset({LOOKUP, SEARCH})
    schemes = frozenset({"pmid"})
    default = "on"
    interval = 0.35  # 3 rps keyless
    wants_mailto = True

    def _params(self, extra: dict) -> dict:
        params = dict(extra, tool="litledger")
        if self.settings.contact_email:
            params["email"] = self.settings.contact_email
        if self.settings.get("NCBI_API_KEY"):
            params["api_key"] = self.settings.get("NCBI_API_KEY")
        return params

    def _summaries(self, pmids: list[str]) -> list[Record]:
        if not pmids:
            return []
        resp = self.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi",
                        params=self._params({"db": "pubmed", "id": ",".join(pmids), "retmode": "json"}), ttl=14 * 86400)
        result = resp.json().get("result", {})
        out = []
        for pmid in result.get("uids", []):
            r = result[pmid]
            ids = {"pmid": pmid}
            for aid in r.get("articleids", []):
                if aid.get("idtype") == "doi":
                    ids["doi"] = norm_doi(aid["value"])
                elif aid.get("idtype") == "pmc":
                    ids["pmcid"] = aid["value"].upper()
            year = re.match(r"(\d{4})", r.get("pubdate", ""))
            csl = {"type": "article-journal", "title": (r.get("title") or "").rstrip("."),
                   "author": [person(literal=re.sub(r"\s+([A-Z]{1,3})$", r", \1", a["name"])) for a in r.get("authors", []) if a.get("authtype") == "Author"],
                   "issued": csl_date(year.group(1)) if year else None, "container-title": r.get("fulljournalname"),
                   "volume": r.get("volume"), "issue": r.get("issue"), "page": r.get("pages"), "DOI": ids.get("doi")}
            out.append(Record("pubmed", ids, {k: v for k, v in csl.items() if v}, kind="published"))
        return out

    def lookup(self, ident: Ident) -> Record | None:
        found = self._summaries([ident.value])
        return found[0] if found else None

    def search(self, query: str, limit: int = 10, year_from: int | None = None, year_to: int | None = None) -> list[Record]:
        params = {"db": "pubmed", "term": query, "retmax": limit, "retmode": "json", "sort": "relevance"}
        if year_from or year_to:
            params.update(datetype="pdat", mindate=str(year_from or 1800), maxdate=str(year_to or 2100))
        resp = self.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi", params=self._params(params), ttl=86400)
        return self._summaries(resp.json().get("esearchresult", {}).get("idlist", []))


# ---------------------------------------------------------------------------------------------- bioRxiv / medRxiv
class BioRxiv(Provider):
    id = "biorxiv"
    name = "bioRxiv / medRxiv"
    capabilities = frozenset({LOOKUP})
    schemes = frozenset({"doi"})
    default = "on"
    interval = 0.5

    def lookup(self, ident: Ident) -> Record | None:
        if not ident.value.startswith("10.1101/"):
            return None
        for server in ("biorxiv", "medrxiv"):
            resp = self.get(f"https://api.biorxiv.org/details/{server}/{ident.value}/na/json", ttl=7 * 86400)
            coll = resp.json().get("collection") or []
            if coll:
                r = coll[-1]
                authors = [person(literal=a.strip()) for a in (r.get("authors") or "").split(";") if a.strip()]
                csl = {"type": "article", "title": r.get("title"), "author": authors, "issued": csl_date_from_iso(coll[0].get("date")),
                       "container-title": server, "DOI": ident.value, "abstract": r.get("abstract"),
                       "URL": f"https://www.{server}.org/content/{ident.value}v{r.get('version', 1)}"}
                rec = Record(self.id, {"doi": ident.value}, {k: v for k, v in csl.items() if v}, kind="preprint",
                             version=f"v{r.get('version')}" if r.get("version") else None)
                published = r.get("published")
                if published and published != "NA":
                    rec.extra["published_doi"] = norm_doi(published)
                return rec
        return None


# ---------------------------------------------------------------------------------------------- INSPIRE-HEP
def inspire_record(md: dict) -> Record:
    ids = {}
    if md.get("arxiv_eprints"):
        ids["arxiv"] = md["arxiv_eprints"][0]["value"]
    if md.get("dois"):
        ids["doi"] = norm_doi(md["dois"][0]["value"])
    pub = (md.get("publication_info") or [{}])[0]
    csl = {"type": "article-journal" if pub.get("journal_title") else "article",
           "title": (md.get("titles") or [{}])[0].get("title", ""),
           "author": [person(literal=a.get("full_name", "")) for a in (md.get("authors") or [])[:50]],
           "issued": csl_date_from_iso(md.get("earliest_date")), "container-title": pub.get("journal_title") or ("arXiv" if ids.get("arxiv") else None),
           "volume": pub.get("journal_volume"), "page": pub.get("artid") or pub.get("page_start"),
           "abstract": (md.get("abstracts") or [{}])[0].get("value"), "DOI": ids.get("doi")}
    return Record("inspire", ids, {k: v for k, v in csl.items() if v}, kind="published" if pub.get("journal_title") else "preprint",
                  extra={"citation_count": md.get("citation_count")})


class Inspire(Provider):
    id = "inspire"
    name = "INSPIRE-HEP"
    capabilities = frozenset({LOOKUP, SEARCH})
    schemes = frozenset({"arxiv", "doi"})
    default = "optin"
    interval = 0.4
    FIELDS = "titles,authors.full_name,arxiv_eprints,dois,publication_info,abstracts,earliest_date,citation_count"

    def lookup(self, ident: Ident) -> Record | None:
        resp = self.get(f"https://inspirehep.net/api/{ident.scheme}/{ident.value}", params={"fields": self.FIELDS}, ttl=7 * 86400)
        return inspire_record(resp.json().get("metadata", {}))

    def search(self, query: str, limit: int = 10, year_from: int | None = None, year_to: int | None = None) -> list[Record]:
        resp = self.get("https://inspirehep.net/api/literature", params={"q": query, "size": limit, "fields": self.FIELDS}, ttl=86400)
        return [inspire_record(h.get("metadata", {})) for h in resp.json().get("hits", {}).get("hits", [])]


# ---------------------------------------------------------------------------------------------- Zenodo
class Zenodo(Provider):
    id = "zenodo"
    name = "Zenodo"
    capabilities = frozenset({LOOKUP})
    schemes = frozenset({"doi"})
    default = "optin"
    interval = 1.0

    def lookup(self, ident: Ident) -> Record | None:
        m = re.match(r"^10\.5281/zenodo\.(\d+)$", ident.value)
        if not m:
            return None
        md = self.get(f"https://zenodo.org/api/records/{m.group(1)}", ttl=14 * 86400).json().get("metadata", {})
        rtype = (md.get("resource_type") or {}).get("type", "")
        csl = {"type": {"software": "software", "dataset": "dataset", "publication": "article"}.get(rtype, "article"),
               "title": md.get("title"), "author": [person(literal=c.get("name", "")) for c in md.get("creators", [])],
               "issued": csl_date_from_iso(md.get("publication_date")), "publisher": "Zenodo", "DOI": ident.value,
               "version": md.get("version"), "abstract": re.sub(r"<[^>]+>", " ", md.get("description") or "").strip() or None}
        return Record(self.id, {"doi": ident.value}, {k: v for k, v in csl.items() if v},
                      kind="software" if rtype == "software" else "published")


# ---------------------------------------------------------------------------------------------- CORE
class Core(Provider):
    id = "core"
    name = "CORE"
    capabilities = frozenset({SEARCH, FULLTEXT})
    auth = "key"
    default = "key"
    recommendation = "optional"
    impact = "no repository full-text for the long tail"
    setup = ["Register for a free key at https://core.ac.uk/services/api", "Paste it under Admin → Sources on the web UI (or: litledger config set CORE_API_KEY=<key>); it applies at once"]
    credential_vars = ["CORE_API_KEY"]
    interval = 1.0

    def _get(self, path: str, **kw):
        return self.get("https://api.core.ac.uk/v3" + path,
                        headers={"Authorization": f"Bearer {self.settings.get('CORE_API_KEY')}"}, **kw)

    @staticmethod
    def _record(r: dict) -> Record:
        ids = {}
        if r.get("doi"):
            ids["doi"] = norm_doi(r["doi"])
        if r.get("arxivId"):
            ids["arxiv"] = r["arxivId"]
        csl = {"title": r.get("title"), "author": [person(literal=a.get("name", "")) for a in r.get("authors", []) or []],
               "issued": csl_date(r.get("yearPublished")), "abstract": r.get("abstract"), "DOI": ids.get("doi"),
               "publisher": r.get("publisher")}
        return Record("core", ids, {k: v for k, v in csl.items() if v}, extra={"pdf_url": r.get("downloadUrl")})

    def search(self, query: str, limit: int = 10, year_from: int | None = None, year_to: int | None = None) -> list[Record]:
        resp = self._get("/search/works", params={"q": query, "limit": limit}, ttl=86400)
        return [self._record(r) for r in resp.json().get("results", [])]

    def locations(self, record_ids: dict[str, str]) -> list[Location]:
        doi = record_ids.get("doi")
        if not doi:
            return []
        resp = self._get("/search/works", params={"q": f'doi:"{doi}"', "limit": 1}, ttl=7 * 86400)
        results = resp.json().get("results", [])
        url = results[0].get("downloadUrl") if results else None
        return [Location(url, "pdf", self.id)] if url else []


# ---------------------------------------------------------------------------------------------- OpenReview
class OpenReview(Provider):
    id = "openreview"
    name = "OpenReview"
    capabilities = frozenset({LOOKUP, REVIEWS, FULLTEXT})
    schemes = frozenset({"openreview"})
    auth = "account"
    default = "key"
    recommendation = "optional"
    impact = "no ICLR/NeurIPS reviews, decisions or OpenReview-only papers"
    setup = ["Create an account at https://openreview.net",
             "Paste it under Admin → Sources on the web UI (or: litledger config set OPENREVIEW_USERNAME=<email> OPENREVIEW_PASSWORD=<password>); it applies at once"]
    credential_vars = ["OPENREVIEW_USERNAME", "OPENREVIEW_PASSWORD"]
    interval = 1.0

    def locations(self, record_ids: dict[str, str]) -> list[Location]:
        forum = record_ids.get("openreview")
        return [Location(f"https://openreview.net/pdf?id={forum}", "pdf", self.id)] if forum else []
    API = "https://api2.openreview.net"

    def __init__(self, settings, http):
        super().__init__(settings, http)
        self._token: str | None = None
        self._token_at = 0.0
        self._lock = threading.Lock()

    def _auth(self) -> dict:
        with self._lock:
            if not self._token or time.time() - self._token_at > 6 * 86400:
                resp = self.http.request(self.id, self.API + "/login", method="POST", ttl=0, interval=self.interval,
                                         json_body={"id": self.settings.get("OPENREVIEW_USERNAME"),
                                                    "password": self.settings.get("OPENREVIEW_PASSWORD")})
                self._token = resp.json().get("token")
                self._token_at = time.time()
            return {"Authorization": f"Bearer {self._token}"}

    @staticmethod
    def _v(content: dict, key: str):
        value = content.get(key)
        return value.get("value") if isinstance(value, dict) else value

    def lookup(self, ident: Ident) -> Record | None:
        resp = self.get(self.API + "/notes", params={"id": ident.value}, headers=self._auth(), ttl=7 * 86400)
        notes = resp.json().get("notes") or []
        if not notes:
            return None
        n = notes[0]
        c = n.get("content", {})
        venue = self._v(c, "venue") or ""
        year = re.search(r"(20\d{2})", venue)
        csl = {"type": "paper-conference", "title": self._v(c, "title"),
               "author": [person(literal=a) for a in self._v(c, "authors") or []],
               "issued": csl_date(year.group(1)) if year else csl_date(time.gmtime((n.get("cdate") or 0) / 1000).tm_year),
               "container-title": venue, "abstract": self._v(c, "abstract"),
               "URL": f"https://openreview.net/forum?id={n.get('forum') or ident.value}"}
        accepted = bool(venue) and not re.search(r"submitted|withdrawn|rejected", venue, re.I)
        return Record(self.id, {"openreview": n.get("forum") or ident.value}, {k: v for k, v in csl.items() if v},
                      kind="published" if accepted else "preprint",
                      extra={"venueid": self._v(c, "venueid"), "pdf": self._v(c, "pdf")})

    def reviews(self, forum: str) -> list[dict]:
        resp = self.get(self.API + "/notes", params={"forum": forum}, headers=self._auth(), ttl=86400)
        out = []
        for n in resp.json().get("notes", []):
            inv = " ".join(n.get("invitations", []) or [n.get("invitation", "")])
            c = n.get("content", {})
            if re.search(r"Official_Review|Decision|Meta_Review", inv):
                out.append({"type": inv.rsplit("/", 1)[-1], "rating": self._v(c, "rating"),
                            "decision": self._v(c, "decision"),
                            "text": " ".join(str(self._v(c, k) or "") for k in ("summary", "strengths", "weaknesses", "metareview", "comment"))[:4000]})
        return out


# ---------------------------------------------------------------------------------------------- NASA ADS
class Ads(Provider):
    id = "ads"
    name = "NASA ADS"
    capabilities = frozenset({LOOKUP, SEARCH, REFS, CITES})
    schemes = frozenset({"doi", "arxiv"})
    auth = "key"
    default = "key"
    recommendation = "optional"
    impact = "no astrophysics/physics index"
    setup = ["Create a token at https://ui.adsabs.harvard.edu/user/settings/token", "Paste it under Admin → Sources on the web UI (or: litledger config set ADS_TOKEN=<token>); it applies at once"]
    credential_vars = ["ADS_TOKEN"]
    interval = 0.5
    FL = "bibcode,title,author,year,doi,identifier,pub,abstract,volume,page"

    def _q(self, q: str, rows: int, ttl: float) -> list[Record]:
        resp = self.get("https://api.adsabs.harvard.edu/v1/search/query", params={"q": q, "fl": self.FL, "rows": rows},
                        headers={"Authorization": f"Bearer {self.settings.get('ADS_TOKEN')}"}, ttl=ttl)
        out = []
        for d in resp.json().get("response", {}).get("docs", []):
            ids = {}
            if d.get("doi"):
                ids["doi"] = norm_doi(d["doi"][0])
            for ident in d.get("identifier", []) or []:
                if ident.lower().startswith("arxiv:"):
                    ids["arxiv"] = ident.split(":", 1)[1]
            csl = {"title": (d.get("title") or [""])[0], "author": [person(literal=a) for a in (d.get("author") or [])[:50]],
                   "issued": csl_date(d.get("year")), "container-title": d.get("pub"), "volume": d.get("volume"),
                   "page": (d.get("page") or [None])[0], "abstract": d.get("abstract"), "DOI": ids.get("doi")}
            out.append(Record(self.id, ids, {k: v for k, v in csl.items() if v}, extra={"bibcode": d.get("bibcode")}))
        return out

    def lookup(self, ident: Ident) -> Record | None:
        q = f'doi:"{ident.value}"' if ident.scheme == "doi" else f'identifier:"arXiv:{ident.value}"'
        found = self._q(q, 1, 7 * 86400)
        return found[0] if found else None

    def search(self, query: str, limit: int = 10, year_from: int | None = None, year_to: int | None = None) -> list[Record]:
        if year_from or year_to:
            query += f" year:{year_from or 1800}-{year_to or 2100}"
        return self._q(query, limit, 86400)
