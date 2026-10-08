"""Provider registry: which sources are enabled, why, and what setup is recommended."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from importlib.metadata import entry_points

from ..config import FIELDS, Settings
from .arxiv import Arxiv
from .base import Provider
from .crossref import Crossref, DataCite
from .dblp import Dblp
from .hf import HuggingFacePapers
from .http import Http
from .more import Ads, BioRxiv, Core, EuropePMC, Inspire, OpenCitations, OpenReview, PubMed, Unpaywall, Zenodo
from .openalex import OpenAlex
from .s2 import SemanticScholar
from .web import Web

log = logging.getLogger("litledger.providers")

BUILTIN: list[type[Provider]] = [Arxiv, SemanticScholar, OpenAlex, Crossref, DataCite, Dblp, HuggingFacePapers, Web,
                                 OpenCitations, Unpaywall, EuropePMC, PubMed, BioRxiv, Core, OpenReview, Inspire,
                                 Zenodo, Ads]

# One line per built-in source for the "Add source" dialog.
ABOUT = {
    "arxiv": "Preprints in CS, ML, physics and maths: metadata, versions, search, and the HTML/PDF full text.",
    "s2": "The ML citation graph, citing sentences, recommendations and ID cross-walks.",
    "openalex": "OpenAlex: 250M+ works across all fields, with search, citations, venues and open-access links.",
    "crossref": "Crossref: DOI metadata from publishers (journals, proceedings, books).",
    "datacite": "DataCite: DOIs for datasets, software and other research outputs (Zenodo, figshare…).",
    "dblp": "Computer-science venues; matches preprints to where they were published.",
    "hf": "Trending ML papers with linked models, datasets and code.",
    "web": "Any web page that carries citation meta tags, plus GitHub repositories.",
    "opencitations": "Open DOI-to-DOI citation links.",
    "unpaywall": "Legal open-access PDF locations for papers with a DOI.",
    "europepmc": "Life-sciences literature with open full text (JATS).",
    "pubmed": "Biomedical literature from NCBI.",
    "biorxiv": "Biology and medicine preprints.",
    "core": "Full text from open repositories worldwide.",
    "openreview": "OpenReview: ICLR/NeurIPS submissions, reviews and decisions.",
    "inspire": "High-energy physics literature.",
    "zenodo": "Research outputs, datasets and software archives.",
    "ads": "Astronomy and physics literature.",
}
_URL = re.compile(r"https?://\S+")

CONTACT_EMAIL_SETUP = ["Enter it under Admin → Server on the web UI (or: litledger config set CONTACT_EMAIL=<you@example.com>)"]


@dataclass
class ProviderState:
    provider: Provider
    enabled: bool
    status: str  # enabled | disabled | missing_credential | optin_off
    note: str = ""


class Registry:
    def __init__(self, settings: Settings, http: Http, previous: "Registry | None" = None):
        self.settings = settings
        self.http = http
        classes = list(BUILTIN)
        try:
            for ep in entry_points(group="litledger.providers"):
                try:
                    classes.append(ep.load())
                except Exception as exc:  # a broken plugin must not take the server down
                    log.warning("provider plugin %s failed to load: %s", ep.name, exc)
        except Exception:
            pass
        self.states: dict[str, ProviderState] = {}
        for cls in classes:
            p = cls(settings, http)
            old = previous.states.get(p.id) if previous else None
            if old is not None:
                p.carry_over(old.provider)
            self.states[p.id] = self._state(p)

    def _state(self, p: Provider) -> ProviderState:
        s = self.settings
        if p.id in s.disable:
            return ProviderState(p, False, "disabled", "listed in 'Sources to disable'")
        has_creds = p.credentials_present()
        if p.default == "optin" and p.id not in s.enable:
            return ProviderState(p, False, "optin_off", "opt-in: add to 'Extra sources to enable'")
        if not has_creds:
            missing = ", ".join(p.credential_vars) or ("CONTACT_EMAIL" if p.auth == "email" else "credentials")
            return ProviderState(p, False, "missing_credential", f"needs {missing}")
        return ProviderState(p, True, "enabled", p.degraded_note() or "")

    # -- access --------------------------------------------------------------------------------------
    def get(self, pid: str) -> Provider | None:
        st = self.states.get(pid)
        return st.provider if st and st.enabled else None

    def enabled(self, capability: str | None = None) -> list[Provider]:
        return [st.provider for st in self.states.values()
                if st.enabled and (capability is None or capability in st.provider.capabilities)]

    def discover_sources(self, requested: list[str] | None) -> tuple[list[Provider], list[str]]:
        """Providers to query plus notes about requested-but-unavailable ones."""
        notes = []
        if requested:
            out = []
            for pid in requested:
                st = self.states.get(pid)
                if not st:
                    notes.append(f"{pid}: unknown source")
                elif not st.enabled:
                    notes.append(f"{pid}: {st.status} ({st.note})")
                elif "search" not in st.provider.capabilities:
                    notes.append(f"{pid}: cannot search")
                else:
                    out.append(st.provider)
            return out, notes
        return [p for p in self.enabled("search") if p.discover_default], notes

    # -- setup recommendations ---------------------------------------------------------------------
    def warnings(self) -> list[dict]:
        """Missing strongly-recommended setup, each with impact and numbered steps."""
        out = []
        for st in self.states.values():
            p = st.provider
            if p.recommendation != "strongly-recommended" or p.id in self.settings.disable:
                continue
            missing_key = bool(p.credential_vars) and not all(self.settings.get(v) for v in p.credential_vars)
            if missing_key:
                var = p.credential_vars[0]
                what = "API key" if var.endswith("API_KEY") else var.split("_", 1)[1].lower().replace("_", " ")
                headline = (f"{p.name} is disabled: no {what} set." if not st.enabled
                            else f"{p.name} is limited: no {what} set.")
                out.append({"provider": p.id, "headline": headline, "impact": p.impact, "steps": p.setup, "key": var,
                            "short": f"{p.name} {what} ({p.impact})"})
        if not self.settings.contact_email:
            out.append({"provider": "contact_email", "key": "CONTACT_EMAIL", "headline": "No contact email set.",
                        "impact": "Crossref uses the slow public pool; Unpaywall (OA full-text links) is disabled",
                        "steps": CONTACT_EMAIL_SETUP,
                        "short": "contact email (Crossref polite pool, Unpaywall)"})
        return out

    def warning_text(self) -> list[str]:
        lines = []
        for w in self.warnings():
            lines.append(w["headline"])
            lines.append(f"  Impact: {w['impact']}.")
            lines.append("  To enable:")
            lines.extend(f"    {i}. {step}" for i, step in enumerate(w["steps"], 1))
        return lines

    def config_fields(self) -> list[dict]:
        """The sources' own settings (their keys), as config.FIELDS declares the others."""
        fields = []
        for st in self.states.values():
            p = st.provider
            for var in p.credential_vars:
                suffix = var.split("_", 1)[1]
                nice = {"API_KEY": "API key", "TOKEN": "token", "USERNAME": "username", "PASSWORD": "password"}.get(suffix, suffix.lower())
                username = var.endswith("USERNAME")
                fields.append({"key": var, "label": f"{p.name} {nice}", "secret": not username, "provider": p.id,
                               "recommendation": p.recommendation, "help": " · ".join(p.setup[:1]) or p.impact,
                               **({"personal": True} if username else {})})
        return fields

    def summary(self) -> list[dict]:
        extra = {}
        for f in FIELDS:
            if f.get("provider"):
                extra.setdefault(f["provider"], []).append(f["key"])
        out = []
        for st in self.states.values():
            p = st.provider
            link = next((m.group(0).rstrip(").,") for step in p.setup for m in [_URL.search(step)] if m), None)
            out.append({"id": p.id, "name": p.name, "status": st.status, "note": st.note,
                        "capabilities": sorted(p.capabilities), "discover_default": p.discover_default,
                        "about": getattr(p, "about", "") or ABOUT.get(p.id, ""), "default": p.default, "auth": p.auth,
                        "recommendation": p.recommendation, "impact": p.impact, "keys": list(p.credential_vars),
                        "optional_keys": extra.get(p.id, []), "key_url": link,
                        # keyless use is possible (degraded) for sources whose key is optional
                        "keyless_ok": p.id in ("openalex", "s2")})
        return out
