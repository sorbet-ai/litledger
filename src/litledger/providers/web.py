"""Web pages: Highwire `citation_*` meta tags (PMLR, NeurIPS, CVF, ACL, journals), GitHub repos, plain pages."""
from __future__ import annotations

import re
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from ..csl import csl_date, csl_date_from_iso, person
from ..ids import Ident, norm_doi, parse_ident
from .base import LOOKUP, Provider, Record

BROWSER_UA = "Mozilla/5.0 (compatible; litledger; +https://github.com/) AppleWebKit/537.36 (KHTML, like Gecko)"


def _metas(soup: BeautifulSoup) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for tag in soup.find_all("meta"):
        name = (tag.get("name") or tag.get("property") or "").strip().lower()
        content = (tag.get("content") or "").strip()
        if name and content:
            out.setdefault(name, []).append(content)
    return out


def page_record(url: str, html: str) -> Record:
    soup = BeautifulSoup(html, "lxml")
    m = _metas(soup)
    first = lambda k: (m.get(k) or [""])[0]  # noqa: E731
    ids: dict[str, str] = {}
    if first("citation_title"):
        authors = [person(literal=a) for a in m.get("citation_author", [])]
        date = first("citation_publication_date") or first("citation_date") or first("citation_online_date")
        year_match = re.search(r"(\d{4})", date)
        issued = csl_date_from_iso(date.replace("/", "-")) if re.match(r"\d{4}[-/]\d", date) else \
            csl_date(year_match.group(1)) if year_match else None
        conference = first("citation_conference_title")
        journal = first("citation_journal_title")
        pages = "-".join(p for p in (first("citation_firstpage"), first("citation_lastpage")) if p)
        csl = {"type": "paper-conference" if conference else "article-journal" if journal else "article",
               "title": " ".join(first("citation_title").split()), "author": [a for a in authors if a],
               "issued": issued, "container-title": conference or journal or first("citation_inbook_title"),
               "volume": first("citation_volume"), "issue": first("citation_issue"), "page": pages or None,
               "publisher": first("citation_publisher"), "URL": url,
               "abstract": first("citation_abstract") or first("description") or None}
        if first("citation_doi"):
            doi = norm_doi(first("citation_doi"))
            ident = parse_ident(doi)
            if ident and ident.scheme == "arxiv":
                ids["arxiv"] = ident.value
            else:
                ids["doi"] = doi
                csl["DOI"] = doi
        if first("citation_arxiv_id"):
            ids["arxiv"] = first("citation_arxiv_id").split("v")[0] if re.match(r"\d{4}\.\d+v\d+$", first("citation_arxiv_id")) else first("citation_arxiv_id")
        pdf = first("citation_pdf_url")
        kind = "published" if (conference or journal) else "unknown"
        return Record("web", ids, {k: v for k, v in csl.items() if v}, kind=kind, extra={"pdf_url": pdf or None})
    title = first("og:title") or (soup.title.string.strip() if soup.title and soup.title.string else url)
    csl = {"type": "webpage", "title": " ".join(title.split()), "URL": url,
           "container-title": first("og:site_name") or urlsplit(url).hostname,
           "abstract": first("og:description") or first("description") or None,
           "issued": csl_date_from_iso(first("article:published_time")) if first("article:published_time") else None}
    author = first("author")
    if author:
        csl["author"] = [person(literal=author)]
    return Record("web", ids, {k: v for k, v in csl.items() if v}, kind="webpage")


class Web(Provider):
    id = "web"
    name = "Web pages (citation meta tags)"
    capabilities = frozenset({LOOKUP})
    schemes = frozenset({"url", "acl"})
    default = "on"
    interval = 0.5

    def lookup(self, ident: Ident) -> Record | None:
        if ident.scheme == "acl":
            rec = self._page(f"https://aclanthology.org/{ident.value}/")
            rec.ids["acl"] = ident.value
            return rec
        parts = urlsplit(ident.value)
        host = (parts.hostname or "").removeprefix("www.")
        if host == "github.com":
            bits = [b for b in parts.path.split("/") if b]
            if len(bits) >= 2:
                return self._github(bits[0], bits[1].removesuffix(".git"), ident.value)
        return self._page(ident.value)

    def _page(self, url: str) -> Record:
        resp = self.get(url, headers={"User-Agent": BROWSER_UA, "Accept": "text/html,application/xhtml+xml"},
                        ttl=7 * 86400, max_bytes=8 * 1024 * 1024)
        ctype = resp.headers.get("content-type", "")
        if "html" not in ctype and not resp.text.lstrip().startswith("<"):
            name = urlsplit(url).path.rsplit("/", 1)[-1] or url
            return Record("web", {}, {"type": "document", "title": name, "URL": url}, kind="unknown",
                          extra={"content_type": ctype, "pdf_url": url if "pdf" in ctype else None})
        return page_record(resp.url or url, resp.text)

    def _github(self, owner: str, repo: str, url: str) -> Record:
        try:
            resp = self.get(f"https://api.github.com/repos/{owner}/{repo}",
                            headers={"Accept": "application/vnd.github+json"}, ttl=7 * 86400)
            data = resp.json()
        except Exception:
            data = {}
        name = data.get("full_name") or f"{owner}/{repo}"
        title = name + (f": {data['description']}" if data.get("description") else "")
        csl = {"type": "software", "title": title, "author": [{"literal": (data.get("owner") or {}).get("login") or owner}],
               "issued": csl_date_from_iso(data.get("created_at")), "URL": f"https://github.com/{name}",
               "publisher": "GitHub", "container-title": "GitHub repository"}
        return Record("web", {}, {k: v for k, v in csl.items() if v}, kind="software",
                      extra={"stars": data.get("stargazers_count"), "license": (data.get("license") or {}).get("spdx_id")})
