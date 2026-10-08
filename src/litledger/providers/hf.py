"""Hugging Face Papers: ML-focused search over arXiv papers, trending, and code/model links."""
from __future__ import annotations

from ..csl import csl_date_from_iso, person
from ..ids import Ident
from .base import LOOKUP, SEARCH, Provider, Record


def hf_record(p: dict) -> Record | None:
    paper = p.get("paper", p)
    arxiv_id = paper.get("id")
    if not arxiv_id or not paper.get("title"):
        return None
    authors = [person(literal=a.get("name", "")) for a in paper.get("authors", []) or [] if not a.get("hidden")]
    csl = {"type": "article", "title": " ".join(paper["title"].split()), "author": [a for a in authors if a],
           "issued": csl_date_from_iso(paper.get("publishedAt")), "container-title": "arXiv", "number": arxiv_id,
           "abstract": " ".join((paper.get("summary") or "").split()) or None,
           "URL": f"https://arxiv.org/abs/{arxiv_id}"}
    return Record("hf", {"arxiv": arxiv_id}, {k: v for k, v in csl.items() if v}, kind="preprint", extra={
        "upvotes": paper.get("upvotes"), "github": paper.get("githubRepo"), "stars": paper.get("githubStars"),
        "ai_summary": paper.get("ai_summary")})


class HuggingFacePapers(Provider):
    id = "hf"
    name = "Hugging Face Papers"
    capabilities = frozenset({LOOKUP, SEARCH})
    schemes = frozenset({"arxiv"})
    default = "on"
    discover_default = True
    interval = 0.5

    def lookup(self, ident: Ident) -> Record | None:
        resp = self.get(f"https://huggingface.co/api/papers/{ident.value}", ttl=3 * 86400)
        return hf_record(resp.json())

    def search(self, query: str, limit: int = 10, year_from: int | None = None, year_to: int | None = None) -> list[Record]:
        resp = self.get("https://huggingface.co/api/papers/search", params={"q": query}, ttl=86400)
        out = []
        for item in resp.json():
            rec = hf_record(item)
            if rec and (not year_from or (rec.year or 0) >= year_from) and (not year_to or (rec.year or 9999) <= year_to):
                out.append(rec)
        return out[:limit]

    def code_links(self, arxiv_id: str) -> dict:
        resp = self.get(f"https://huggingface.co/api/arxiv/{arxiv_id}/repos", ttl=86400)
        data = resp.json()
        return {k: [m.get("id") for m in data.get(k, [])][:5] for k in ("models", "datasets", "spaces") if data.get(k)}
