"""Federated external search: query enabled providers in parallel, merge duplicates, flag what's already known,
and log the search so repeats are free."""
from __future__ import annotations

import concurrent.futures
import json
import time
from datetime import datetime, timedelta, timezone

from .db import Actor, dumps, now, ulid
from .ids import STRONG_SCHEMES, Ident, handle
from .ledger import Ledger
from .providers.base import Record
from .providers.http import ProviderError
from .textutil import family_name, norm_title, short_authors
from .works import ensure_project, work_by_ident

PROVIDER_BUDGET_S = 25
REUSE_DAYS = 30
# One bounded pool for every search: a provider that hangs past the budget keeps its thread until its own HTTP timeout,
# but never more than this many threads exist (later searches queue, and time out the same way if all are stuck).
POOL = concurrent.futures.ThreadPoolExecutor(max_workers=16, thread_name_prefix="litledger-discover")


def _degraded(lg: Ledger, queried: list[str]) -> list[str]:
    notes = []
    for pid in queried:
        p = lg.providers.get(pid)
        note = p.degraded_note() if p else None
        if note:
            notes.append(f"{pid}: {note}")
    st = lg.providers.states.get("s2")
    if st and not st.enabled and "s2" not in lg.settings.disable:
        notes.append("s2 off (no API key): weaker ML coverage")
    return notes


def discover(lg: Ledger, actor: Actor, project: str, query: str, sources: list[str] | None = None, year_from: int | None = None,
             year_to: int | None = None, limit: int = 10, show_known: bool = False, refresh: bool = False) -> dict:
    query = (query or "").strip()
    if not query:
        raise ValueError("query required")
    limit = max(1, min(int(limit or 10), 50))
    providers, notes = lg.providers.discover_sources(sources)
    source_ids = sorted(p.id for p in providers)
    filters = {"year_from": year_from, "year_to": year_to}
    nq = norm_title(query)
    if not refresh:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=REUSE_DAYS)).isoformat(timespec="seconds")
        with lg.db.read() as conn:
            prev = conn.execute("SELECT * FROM searches WHERE project=? AND norm_query=? AND sources=? AND filters=? AND at>=? "
                                "ORDER BY at DESC LIMIT 1", (project, nq, dumps(source_ids), dumps(filters), cutoff)).fetchone()
        if prev:
            hits = json.loads(prev["hits"])
            _mark_known(lg, project, hits)
            return _shape(hits, limit, show_known, notes + json.loads(prev["degraded"]), cached_at=prev["at"])
    if not providers:
        return {"hits": [], "known": [], "notes": notes + ["no searchable sources enabled"], "total": 0}
    results: dict[str, list[Record]] = {}
    errors: list[str] = []

    def run(p):
        return p.id, p.search(query, limit=min(limit + 5, 25), year_from=year_from, year_to=year_to)

    futures = {POOL.submit(run, p): p.id for p in providers}
    deadline = time.monotonic() + PROVIDER_BUDGET_S
    for fut, pid in futures.items():
        try:
            pid, recs = fut.result(timeout=max(0.1, deadline - time.monotonic()))
            results[pid] = recs
        except concurrent.futures.TimeoutError:
            fut.cancel()  # still queued: never runs; already running: finishes on its own and is ignored
            errors.append(f"{pid}: timeout")
        except ProviderError as exc:
            errors.append(f"{pid}: {exc.kind}")
        except Exception as exc:
            errors.append(f"{pid}: error {type(exc).__name__}")
    merged: dict[str, dict] = {}
    by_id: dict[tuple[str, str], str] = {}
    pref = {"arxiv": 0, "s2": 1, "crossref": 2, "dblp": 3, "openalex": 4, "hf": 5}
    for pid, recs in results.items():
        for rank, rec in enumerate(recs):
            if not rec.title or rec.extra.get("low_trust"):
                continue
            key = None
            for s, v in rec.ids.items():
                if s in STRONG_SCHEMES and (s, v) in by_id:
                    key = by_id[(s, v)]
                    break
            key = key or next((k for k, m in merged.items() if norm_title(m["title"]) == norm_title(rec.title)
                               and abs((m.get("year") or 0) - (rec.year or 0)) <= 2), None) or rec.handle or "t:" + norm_title(rec.title)[:80]
            m = merged.setdefault(key, {"ids": {}, "score": 0.0, "sources": [], "best": None})
            m["score"] += 1.0 / (60 + rank)
            m["sources"].append(pid)
            for s, v in rec.ids.items():
                m["ids"].setdefault(s, v)
                if s in STRONG_SCHEMES:
                    by_id[(s, v)] = key
            if m["best"] is None or pref.get(pid, 9) < pref.get(m["best"].provider, 9):
                m["best"] = rec
            m["title"] = m["best"].title
            m["year"] = m["best"].year or rec.year
            if rec.extra.get("citation_count") and not m.get("cites"):
                m["cites"] = rec.extra["citation_count"]
    hits = []
    for m in sorted(merged.values(), key=lambda x: -x["score"]):
        best: Record = m["best"]
        csl = best.csl
        hits.append({"handle": handle(m["ids"]), "ids": m["ids"], "title": m["title"], "year": m["year"],
                     "authors": short_authors(csl.get("author") or []),
                     "first_author": family_name((csl.get("author") or [{}])[0]) if csl.get("author") else "",
                     "venue": csl.get("container-title") if best.kind == "published" else ("arXiv" if m["ids"].get("arxiv") else csl.get("container-title")),
                     "sources": sorted(set(m["sources"])), "cites": m.get("cites"),
                     "abstract": (csl.get("abstract") or "")[:600] or None})
    degraded = _degraded(lg, list(results)) + errors
    _mark_known(lg, project, hits)
    new_to_project = sum(1 for h in hits if not h.get("in_project"))
    with lg.db.tx(actor) as tx:
        ensure_project(tx, project)
        tx.execute("INSERT INTO searches VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                   ("s" + ulid(), project, query, nq, dumps(source_ids), dumps(filters), actor.principal_id, now(),
                    json.dumps(hits, ensure_ascii=False), new_to_project, dumps(degraded)))
        tx.log("search", {"query": query, "sources": source_ids, "hits": len(hits), "new": new_to_project}, project=project)
    return _shape(hits, limit, show_known, notes + degraded)


def _mark_known(lg: Ledger, project: str, hits: list[dict]) -> None:
    with lg.db.read() as conn:
        for h in hits:
            h.pop("citekey", None)
            h["in_library"] = h["in_project"] = False
            for s, v in (h.get("ids") or {}).items():
                if s not in STRONG_SCHEMES:
                    continue
                row = work_by_ident(conn, Ident(s, v))
                if row:
                    h["citekey"] = row["citekey"]
                    h["in_library"] = True
                    h["in_project"] = bool(conn.execute(
                        "SELECT 1 FROM project_works WHERE project=? AND work_id=? AND removed_at IS NULL", (project, row["id"])).fetchone())
                    break


def _shape(hits: list[dict], limit: int, show_known: bool, notes: list[str], cached_at: str | None = None) -> dict:
    known = [h for h in hits if h.get("in_project")] if not show_known else []
    fresh = [h for h in hits if show_known or not h.get("in_project")]
    return {"hits": fresh[:limit], "known": [h.get("citekey") for h in known], "total": len(fresh), "notes": notes,
            "cached_at": cached_at}


def search_log(lg: Ledger, project: str, limit: int = 20) -> list[dict]:
    with lg.db.read() as conn:
        rows = conn.execute("SELECT at, query, sources, new_to_project, hits FROM searches WHERE project=? ORDER BY at DESC LIMIT ?",
                            (project, limit)).fetchall()
    return [{"at": r["at"], "query": r["query"], "sources": json.loads(r["sources"]), "new": r["new_to_project"],
             "hits": len(json.loads(r["hits"]))} for r in rows]
