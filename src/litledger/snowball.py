"""Citation chasing as persistent state: expand seeds into a ranked frontier, list it, record decisions."""
from __future__ import annotations

import json
import math

from .citations import graph
from .db import Actor, dumps, now
from .ledger import Ledger
from .library import update_works
from .resolve import resolve_items
from .works import ensure_project, require_work


def expand(lg: Ledger, actor: Actor, project: str, seeds: list[str], direction: str = "both", sources: list[str] | None = None) -> dict:
    if direction not in ("refs", "cites", "both"):
        raise ValueError("direction must be refs, cites or both")
    kinds = ["references", "citations"] if direction == "both" else ["references" if direction == "refs" else "citations"]
    added = updated = 0
    notes: list[str] = []
    seed_keys = []
    for seed in seeds:
        with lg.db.read() as conn:
            seed_keys.append(require_work(conn, seed, project)["citekey"])
    for seed in seed_keys:
        for kind in kinds:
            g = graph(lg, project, seed, kind=kind, sources=sources, limit=60)
            notes += [n for n in g["notes"] if n not in notes]
            with lg.db.tx(actor) as tx:
                ensure_project(tx, project)
                for item in g["items"]:
                    if item.get("in_project"):
                        continue
                    # Library works by citekey; external ones by ID, or by their title (resolve accepts titles).
                    handle = item.get("citekey") or item.get("handle") or item.get("title")
                    if not handle or len(handle) < 6:
                        continue
                    row = tx.execute("SELECT * FROM snowball WHERE project=? AND handle=?", (project, handle)).fetchone()
                    if row:
                        seeds_now = sorted(set(json.loads(row["seeds"])) | {seed})
                        tx.execute("UPDATE snowball SET seeds=?, score=? WHERE project=? AND handle=?",
                                   (dumps(seeds_now), _score(len(seeds_now), item.get("citation_count")), project, handle))
                        updated += 1
                    else:
                        tx.execute("INSERT INTO snowball(project,handle,title,year,work_id,direction,seeds,score,state,created_at) "
                                   "VALUES(?,?,?,?,?,?,?,?,?,?)",
                                   (project, handle, item.get("title") or "", item.get("year"), item.get("id"),
                                    "refs" if kind == "references" else "cites", dumps([seed]),
                                    _score(1, item.get("citation_count")), "pending", now()))
                        added += 1
                tx.log("snowball.expand", {"seed": seed, "kind": kind, "items": len(g["items"])}, project=project)
    return {"added": added, "updated": updated, "notes": notes, "seeds": seed_keys}


def _score(n_seeds: int, cites: int | None) -> float:
    return n_seeds + (math.log10(cites + 1) / 10 if cites else 0)


def pending(lg: Ledger, project: str, limit: int = 20, offset: int = 0, state: str = "pending") -> dict:
    with lg.db.read() as conn:
        total = conn.execute("SELECT count(*) FROM snowball WHERE project=? AND state=?", (project, state)).fetchone()[0]
        rows = conn.execute("SELECT * FROM snowball WHERE project=? AND state=? ORDER BY score DESC, year DESC LIMIT ? OFFSET ?",
                            (project, state, limit, offset)).fetchall()
        counts = dict(conn.execute("SELECT state, count(*) FROM snowball WHERE project=? GROUP BY state", (project,)).fetchall())
    return {"items": [{"handle": r["handle"], "title": r["title"], "year": r["year"], "seeds": json.loads(r["seeds"]),
                       "direction": r["direction"], "score": round(r["score"], 2), "reason": r["reason"]} for r in rows],
            "total": total, "counts": counts}


def decide(lg: Ledger, actor: Actor, project: str, items: list[dict], tags: list[str] | None = None) -> dict:
    items = [i for i in items if isinstance(i, dict) and i.get("handle")]
    accept = [i for i in items if i.get("state") == "in"]
    reject = [i for i in items if i.get("state") == "out"]
    result = {"in": 0, "out": 0, "problems": []}
    result["problems"] += [f"{i['handle']}: state must be in or out" for i in items if i.get("state") not in ("in", "out")]
    captured = []
    if accept:
        outcomes, _ = resolve_items(lg, actor, [i["handle"] for i in accept], add=True, tags=tags,
                                    why=None, project=project)
        for item, o in zip(accept, outcomes):
            if o.status != "ok":
                # Not captured: the item stays pending so a later decide (or retry after a rate limit) can take it.
                result["problems"].append(f"{item['handle']}: {o.status} {o.message}".strip() + " (still pending)")
                continue
            if item.get("why"):
                update_works(lg, actor, project, [{"work": o.citekey, "why": item["why"]}])
            result["in"] += 1
            captured.append(item)
    with lg.db.tx(actor) as tx:
        accept = captured
        for item in accept + reject:
            tx.execute("UPDATE snowball SET state=?, reason=?, decided_by=?, decided_at=? WHERE project=? AND handle=?",
                       (item["state"], item.get("reason") or item.get("why"), actor.label, now(), project, item["handle"]))
        result["out"] = len(reject)
        tx.log("snowball.decide", {"in": [i["handle"] for i in accept], "out": [i["handle"] for i in reject]}, project=project)
    return result
