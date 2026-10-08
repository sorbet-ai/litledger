"""Identifier parsing and normalisation: anything a user or agent pastes -> (scheme, value)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit, urlunsplit, urlencode, unquote

ARXIV_NEW = re.compile(r"^(\d{4}\.\d{4,5})(v\d+)?$")
ARXIV_OLD = re.compile(r"^([a-z\-]+(?:\.[A-Za-z]{2})?/\d{7})(v\d+)?$", re.I)
DOI = re.compile(r"^10\.\d{4,9}/\S+$")
S2_SHA = re.compile(r"^[0-9a-f]{40}$")
OPENALEX = re.compile(r"^[Ww]\d{4,}$")
PMCID = re.compile(r"^PMC\d+$", re.I)
ACL_NEW = re.compile(r"^\d{4}\.[a-z0-9\-]+\.\d+$", re.I)
ACL_OLD = re.compile(r"^[A-Z]\d{2}-\d{4}$")
DBLP = re.compile(r"^(conf|journals|books|phd|series|reference|tr|persons)/[^/\s]+/\S+$")

# Schemes that identify one work (a URL may not). HANDLE_ORDER: which one a work is shown by, most familiar first.
STRONG_SCHEMES = ("arxiv", "doi", "dblp", "s2", "corpusid", "pmid", "pmcid", "acl", "openreview", "openalex", "isbn")
HANDLE_ORDER = ("arxiv", "doi", "acl", "dblp", "openreview", "pmid", "pmcid", "s2", "corpusid", "openalex", "url")
WORK_ID = re.compile(r"w[0-9A-Z]{26}")
TRACKING_PARAMS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "ref", "fbclid", "gclid"}


@dataclass(frozen=True)
class Ident:
    scheme: str
    value: str
    version: str | None = None  # arXiv version suffix, kept apart from the identity

    @property
    def handle(self) -> str:
        return f"{self.scheme}:{self.value}"


def handle(ids: dict[str, str]) -> str:
    """'scheme:value' of the identifier a work is shown by, e.g. arxiv:1706.03762 ('' when it has none)."""
    return next((f"{s}:{ids[s]}" for s in HANDLE_ORDER if ids.get(s)), "")


def is_work_id(value: str | None) -> bool:
    """A work's own id ('w' + ULID), as opposed to an entity, note, map or tag id."""
    return bool(value) and WORK_ID.fullmatch(value) is not None


def norm_doi(value: str) -> str:
    value = unquote(value.strip())
    value = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", value, flags=re.I)
    return value.rstrip(".,;)").lower()


def norm_url(url: str) -> str:
    parts = urlsplit(url.strip())
    query = [(k, v) for k, vs in parse_qs(parts.query, keep_blank_values=True).items()
             for v in vs if k.lower() not in TRACKING_PARAMS]
    path = parts.path or "/"
    if len(path) > 1:
        path = path.rstrip("/")
    return urlunsplit((parts.scheme.lower(), (parts.hostname or "").lower() + (f":{parts.port}" if parts.port else ""),
                       path, urlencode(sorted(query)), ""))


def _arxiv(raw: str) -> Ident | None:
    raw = raw.strip().removesuffix(".pdf")
    m = ARXIV_NEW.match(raw)
    if m:
        return Ident("arxiv", m.group(1), m.group(2))
    m = ARXIV_OLD.match(raw)
    if m:
        return Ident("arxiv", m.group(1), m.group(2))
    return None


def _from_url(url: str) -> Ident | None:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower().removeprefix("www.")
    path = unquote(parts.path)
    qs = parse_qs(parts.query)
    if host in ("arxiv.org", "export.arxiv.org", "ar5iv.org", "ar5iv.labs.arxiv.org", "alphaxiv.org",
                "huggingface.co", "hf.co", "arxiv-vanity.com", "scirate.com"):
        m = re.search(r"/(?:abs|pdf|html|format|papers|arxiv_abs)/(.+?)/?$", path)
        if m:
            ident = _arxiv(m.group(1))
            if ident:
                return ident
    if host in ("doi.org", "dx.doi.org"):
        value = norm_doi(path.lstrip("/"))
        return parse_ident("doi:" + value) if value else None
    if host == "openreview.net" and "id" in qs:
        return Ident("openreview", qs["id"][0])
    if host == "aclanthology.org":
        m = re.match(r"^/([^/]+?)(?:\.pdf)?/?$", path)
        if m and (ACL_NEW.match(m.group(1)) or ACL_OLD.match(m.group(1))):
            return Ident("acl", m.group(1))
    if host in ("semanticscholar.org", "api.semanticscholar.org"):
        m = re.search(r"([0-9a-f]{40})", path)
        if m:
            return Ident("s2", m.group(1))
        m = re.search(r"CorpusI[Dd]:(\d+)", path)
        if m:
            return Ident("corpusid", m.group(1))
    if host in ("openalex.org", "api.openalex.org"):
        m = re.search(r"/(W\d+)", path, re.I)
        if m:
            return Ident("openalex", m.group(1).upper())
    if host == "pubmed.ncbi.nlm.nih.gov":
        m = re.match(r"^/(\d+)", path)
        if m:
            return Ident("pmid", m.group(1))
    if host in ("ncbi.nlm.nih.gov", "pmc.ncbi.nlm.nih.gov", "europepmc.org"):
        m = re.search(r"(PMC\d+)", path, re.I)
        if m:
            return Ident("pmcid", m.group(1).upper())
    if host == "dblp.org" and path.startswith("/rec/"):
        key = re.sub(r"\.(html|bib|xml)$", "", path[len("/rec/"):])
        if DBLP.match(key):
            return Ident("dblp", key)
    return Ident("url", norm_url(url))


def parse_ident(text: str) -> Ident | None:
    """Return the identifier in `text`, or None if it looks like free text (a title)."""
    raw = text.strip().strip("<>").strip()
    if not raw or "\n" in raw:
        return None
    if re.match(r"^https?://", raw, re.I):
        return _from_url(raw)
    prefix, _, rest = raw.partition(":")
    p = prefix.lower().strip()
    rest = rest.strip()
    if rest and p in ("arxiv", "arxiv.org"):
        return _arxiv(rest)
    if rest and p == "doi":
        return parse_ident(norm_doi(rest))
    if rest and p in ("openreview", "or"):
        return Ident("openreview", rest)
    if rest and p in ("acl", "aclanthology"):
        return Ident("acl", rest)
    if rest and p == "s2" and S2_SHA.match(rest.lower()):
        return Ident("s2", rest.lower())
    if rest and p == "corpusid" and rest.isdigit():
        return Ident("corpusid", rest)
    if rest and p == "openalex" and OPENALEX.match(rest):
        return Ident("openalex", rest.upper())
    if rest and p == "pmid" and rest.isdigit():
        return Ident("pmid", rest)
    if rest and p == "pmcid" and PMCID.match(rest):
        return Ident("pmcid", rest.upper())
    if rest and p == "dblp" and DBLP.match(rest):
        return Ident("dblp", rest)
    if rest and p == "isbn":
        digits = re.sub(r"[^0-9Xx]", "", rest)
        return Ident("isbn", digits.upper()) if len(digits) in (10, 13) else None
    if rest and p == "url":
        return parse_ident(rest)
    if DOI.match(raw):
        value = norm_doi(raw)
        m = re.match(r"^10\.48550/arxiv\.(.+)$", value)
        if m:
            return _arxiv(m.group(1)) or Ident("doi", value)
        return Ident("doi", value)
    ident = _arxiv(raw)
    if ident:
        return ident
    if PMCID.match(raw):
        return Ident("pmcid", raw.upper())
    if OPENALEX.match(raw) and len(raw) >= 8:
        return Ident("openalex", raw.upper())
    if DBLP.match(raw):
        return Ident("dblp", raw)
    return None


def safe_url(value) -> str | None:
    """The URL if it is an absolute http(s) URL, else None (javascript:, data:, file:, relative paths are dropped)."""
    if not isinstance(value, str):
        return None
    url = value.strip()
    if not re.match(r"^https?://[^\s/?#]+", url, re.I) or any(c in url for c in "\r\n\t<>\""):
        return None
    return url


def is_low_trust_doi(doi: str) -> bool:
    """Repost/mirror DOI prefixes that must never become canonical (e.g. the 10.65215 junk reposts)."""
    return doi.startswith(("10.65215/", "10.5281/zenodo.")) or doi.startswith("10.31219/")
