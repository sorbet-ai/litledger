"""DBLP via SPARQL (the search API and .bib export sit behind a bot wall): clean CS venue metadata."""
from __future__ import annotations

import re

from ..csl import csl_date, person
from ..ids import Ident, norm_doi
from ..textutil import norm_title
from .base import LOOKUP, SEARCH, Provider, Record

ENDPOINT = "https://sparql.dblp.org/sparql"

_FIELDS = """
  ?p dblp:title ?title .
  OPTIONAL { ?p dblp:yearOfPublication ?year }
  OPTIONAL { ?p dblp:publishedIn ?venue }
  OPTIONAL { ?p dblp:bibtexType ?btype }
  OPTIONAL { ?p dblp:publishedAsPartOf ?part . ?part dblp:title ?booktitle }
  OPTIONAL { ?p dblp:doi ?doi }
  OPTIONAL { ?p dblp:primaryDocumentPage ?page }
  OPTIONAL { ?p dblp:pagination ?pages }
  OPTIONAL { ?p dblp:publishedInJournalVolume ?volume }
  OPTIONAL { ?p dblp:publishedInJournalVolumeIssue ?issue }
"""


def _esc(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


class Dblp(Provider):
    id = "dblp"
    name = "DBLP"
    capabilities = frozenset({LOOKUP, SEARCH})
    schemes = frozenset({"dblp"})
    default = "on"
    discover_default = False  # SPARQL substring search is slow; used for venue matching
    interval = 1.0

    def _query(self, sparql: str, ttl: float = 7 * 86400) -> list[dict]:
        resp = self.get(ENDPOINT, params={"query": "PREFIX dblp: <https://dblp.org/rdf/schema#>\n" + sparql},
                        headers={"Accept": "application/sparql-results+json"}, ttl=ttl)
        return [{k: v["value"] for k, v in b.items()} for b in resp.json()["results"]["bindings"]]

    def _authors(self, key_uri: str) -> list[dict]:
        rows = self._query(f"""SELECT ?ord ?name WHERE {{ <{key_uri}> dblp:hasSignature ?s .
            ?s dblp:signatureOrdinal ?ord . ?s dblp:signatureDblpName ?name }}""")
        rows.sort(key=lambda r: int(r.get("ord", 0)))
        # DBLP disambiguates homonyms with a trailing 4-digit number ("Wei Li 0002").
        return [person(literal=re.sub(r"\s+\d{4}$", "", r["name"])) for r in rows]

    def _record(self, row: dict, with_authors: bool = True) -> Record:
        key = row["p"].removeprefix("https://dblp.org/rec/")
        btype = row.get("btype", "").rsplit("#", 1)[-1].lower()
        is_corr = key.startswith("journals/corr/")
        csl = {"title": row.get("title", "").rstrip("."), "issued": csl_date(row.get("year"))}
        if btype == "inproceedings":
            csl["type"] = "paper-conference"
            csl["container-title"] = row.get("booktitle") or row.get("venue", "")
            csl["event-title"] = row.get("venue", "")
        elif btype == "article" and not is_corr:
            csl["type"] = "article-journal"
            csl["container-title"] = row.get("venue", "")
        elif is_corr:
            csl["type"] = "article"
            csl["container-title"] = "arXiv"
        else:
            csl["type"] = {"book": "book", "incollection": "chapter", "phdthesis": "thesis"}.get(btype, "article")
            csl["container-title"] = row.get("booktitle") or row.get("venue", "")
        for src, dst in (("pages", "page"), ("volume", "volume"), ("issue", "issue")):
            if row.get(src):
                csl[dst] = row[src]
        ids = {"dblp": key}
        if row.get("doi"):
            doi = norm_doi(row["doi"])
            if doi.startswith("10.48550/arxiv."):
                ids["arxiv"] = doi.split("arxiv.", 1)[1]
            else:
                ids["doi"] = doi
                csl["DOI"] = doi
        m = re.match(r"journals/corr/abs-(\d{4})-(\d{4,5})$", key)
        if m:
            ids["arxiv"] = f"{m.group(1)}.{m.group(2)}"
        if row.get("page"):
            csl["URL"] = row["page"]
            orm = re.search(r"openreview\.net/(?:forum|pdf)\?id=([\w\-]+)", row["page"])
            if orm:
                ids["openreview"] = orm.group(1)
        if with_authors:
            csl["author"] = self._authors(row["p"])
        kind = "preprint" if is_corr else "published"
        return Record("dblp", ids, {k: v for k, v in csl.items() if v}, kind=kind,
                      extra={"venue_short": row.get("venue")})

    def lookup(self, ident: Ident) -> Record | None:
        uri = f"https://dblp.org/rec/{ident.value}"
        rows = self._query(f"SELECT * WHERE {{ BIND(<{uri}> AS ?p) {_FIELDS} }} LIMIT 1")
        return self._record(rows[0]) if rows else None

    def _rows_to_records(self, rows: list[dict], limit: int = 10, with_authors: bool = True) -> list[Record]:
        seen, out = set(), []
        for row in rows:
            if row["p"] in seen or len(out) >= limit:
                continue
            seen.add(row["p"])
            out.append(self._record(row, with_authors))
        return out

    def match_title(self, title: str, author: str | None = None, year: int | None = None) -> list[Record]:
        title = " ".join(title.split()).rstrip(".")
        if len(title) < 6:
            return []
        variants = list(dict.fromkeys([title + ".", title, title.title() + ".", title[:1].upper() + title[1:].lower() + "."]))
        values = " ".join(f'"{_esc(v)}"' for v in variants)
        rows = self._query(f"SELECT * WHERE {{ VALUES ?title {{ {values} }} ?p dblp:title ?title . {_FIELDS} }} LIMIT 10")
        if rows and not any("/corr/" not in r["p"] for r in rows):
            # Only the CoRR (arXiv) record matched exactly: look for the venue version among the same authors' papers.
            prefix = _esc(norm_title(title)[:24])
            corr = rows[0]["p"]
            rows += self._query(f"""SELECT * WHERE {{ <{corr}> dblp:authoredBy ?a . ?p dblp:authoredBy ?a .
                ?p dblp:title ?title . FILTER(STRSTARTS(REPLACE(LCASE(STR(?title)), "[^a-z0-9 ]", ""), "{prefix}")) {_FIELDS} }} LIMIT 20""")
        if not rows:
            prefix = _esc(title.lower()[:40])
            rows = self._query(f"""SELECT * WHERE {{ ?p dblp:title ?title .
                FILTER(STRSTARTS(LCASE(STR(?title)), "{prefix}")) {_FIELDS} }} LIMIT 30""")
        return self._rows_to_records(rows)

    def search(self, query: str, limit: int = 10, year_from: int | None = None, year_to: int | None = None) -> list[Record]:
        words = [w for w in norm_title(query).split() if len(w) > 2][:5]
        if not words:
            return []
        filters = " ".join(f'FILTER(CONTAINS(LCASE(?title), "{_esc(w)}"))' for w in words)
        if year_from:
            filters += f" FILTER(STR(?year) >= \"{year_from}\")"
        if year_to:
            filters += f" FILTER(STR(?year) <= \"{year_to}\")"
        rows = self._query(f"SELECT * WHERE {{ ?p dblp:title ?title . {filters} {_FIELDS} }} LIMIT {limit * 2}", ttl=86400)
        seen, out = set(), []
        for row in rows:
            if row["p"] in seen or len(out) >= limit:
                continue
            seen.add(row["p"])
            out.append(self._record(row, with_authors=False))
        return out
