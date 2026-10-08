"""Provider plugin interface and the normalised record providers return."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar

from ..csl import year_of
from ..ids import Ident, handle
from .http import Http, ProviderError

if TYPE_CHECKING:
    from ..config import Settings

# Crossref update notices that withdraw a work (flagged on it and reported by check).
RETRACTIONS = ("retraction", "withdrawal", "removal", "expression_of_concern")

# Capabilities a provider can declare. reviews (OpenReview) and verify (Crossref update notices) only label a source.
SEARCH, LOOKUP, REFS, CITES, FULLTEXT, REVIEWS, VERIFY = "search", "lookup", "refs", "cites", "fulltext", "reviews", "verify"


@dataclass
class Record:
    """Metadata about one work from one provider. `csl` holds CSL-JSON fields only."""
    provider: str
    ids: dict[str, str] = field(default_factory=dict)  # scheme -> value
    csl: dict = field(default_factory=dict)
    kind: str = "unknown"  # preprint | published | software | webpage | unknown
    version: str | None = None  # e.g. arXiv "v2"
    extra: dict = field(default_factory=dict)  # citation_count, oa_url, journal_ref, tldr, venue_short, ...

    @property
    def title(self) -> str:
        return self.csl.get("title", "")

    @property
    def year(self) -> int | None:
        return year_of(self.csl)

    @property
    def handle(self) -> str:
        return handle(self.ids)


@dataclass
class Location:
    """A place full text can be fetched from."""
    url: str
    kind: str  # arxiv_html | arxiv_latex | pdf | jats | html
    provider: str
    license: str | None = None


class Provider:
    id: ClassVar[str] = ""
    name: ClassVar[str] = ""
    capabilities: ClassVar[frozenset[str]] = frozenset()
    schemes: ClassVar[frozenset[str]] = frozenset()  # identifier schemes `lookup` understands
    auth: ClassVar[str] = "none"  # none | email | key | account
    default: ClassVar[str] = "on"  # on | key | optin
    discover_default: ClassVar[bool] = False  # queried by discover() when sources aren't given
    recommendation: ClassVar[str] = "optional"  # strongly-recommended | recommended | optional
    impact: ClassVar[str] = ""  # what is lost when it's off
    setup: ClassVar[list[str]] = []  # numbered fix steps
    credential_vars: ClassVar[list[str]] = []  # setting names (Admin → Sources) that must be set
    interval: ClassVar[float] = 1.0  # min seconds between requests
    wants_mailto: ClassVar[bool] = False  # the API asks for a contact address in the User-Agent
    keyed: bool = False  # set when an optional API key raises the provider's limits (Semantic Scholar)

    def __init__(self, settings: "Settings", http: Http):
        self.settings = settings
        self.http = http

    # -- configuration -------------------------------------------------------------------------
    def credentials_present(self) -> bool:
        return all(self.settings.get(v) for v in self.credential_vars)

    def degraded_note(self) -> str | None:
        """Short note when enabled but limited (e.g. keyless budget)."""
        return None

    def carry_over(self, old: "Provider") -> None:
        """Keep runtime state (a budget being tracked, say) from the instance this one replaces when settings are
        saved and the registry is rebuilt. Most providers have none."""

    # -- helpers -------------------------------------------------------------------------------
    def get(self, url: str, **kw):
        kw.setdefault("interval", self.interval)
        kw.setdefault("mailto", self.wants_mailto)
        return self.http.get(self.id, url, **kw)

    def fail(self, kind: str, message: str = "") -> ProviderError:
        return ProviderError(self.id, kind, message)

    # -- capabilities (override what you declare) -----------------------------------------------
    def lookup(self, ident: Ident) -> Record | None:
        raise NotImplementedError

    def search(self, query: str, limit: int = 10, year_from: int | None = None, year_to: int | None = None) -> list[Record]:
        raise NotImplementedError

    def match_title(self, title: str, author: str | None = None, year: int | None = None) -> list[Record]:
        return self.search(title, limit=5)

    def references(self, record_ids: dict[str, str], limit: int = 100) -> list[Record]:
        raise NotImplementedError

    def citations(self, record_ids: dict[str, str], limit: int = 100) -> list[Record]:
        raise NotImplementedError

    def locations(self, record_ids: dict[str, str]) -> list[Location]:
        raise NotImplementedError

    def selftest(self) -> str:
        """One small live request exercising this provider (and its credentials). Returns a short result."""
        if LOOKUP in self.capabilities and "doi" in self.schemes:
            rec = self.lookup(Ident("doi", "10.18653/v1/n19-1423"))
            return f"ok: {rec.title}" if rec else "no result"
        if LOOKUP in self.capabilities and "arxiv" in self.schemes:
            rec = self.lookup(Ident("arxiv", "1706.03762"))
            return f"ok: {rec.title}" if rec else "no result"
        if LOOKUP in self.capabilities and "openreview" in self.schemes:
            rec = self.lookup(Ident("openreview", "r8H7xhYPwz"))
            return f"ok: {rec.title}" if rec else "no result"
        if SEARCH in self.capabilities:
            found = self.search("transformer attention", limit=1)
            return f"ok: {found[0].title}" if found else "no result"
        if FULLTEXT in self.capabilities:
            locs = self.locations({"doi": "10.1038/nature14539"})
            return f"ok: {len(locs)} locations"
        if REFS in self.capabilities:
            refs = self.references({"doi": "10.18653/v1/n19-1423"}, 3)
            return f"ok: {len(refs)} references"
        return "nothing to test"

    def updates(self, doi: str) -> list[dict]:
        """Retractions/corrections that update `doi`."""
        raise NotImplementedError
