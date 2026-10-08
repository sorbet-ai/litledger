"""Typed knowledge: the entity kinds and relations agents extract from papers and people arrange in mind maps, the
entities themselves (results included, grouped into leaderboards), typed links between works and entities, and the
evidence graph they form.

Kinds split in two: facts about the literature (library-wide, shared by every project: a benchmark is the same
benchmark everywhere) and the user's own thinking (project-scoped: ideas, hypotheses, experiments)."""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass

from rapidfuzz.distance import Levenshtein

from . import tags as tagmod
from .db import Actor, dumps, free_id, hash_id, now
from .ids import is_work_id
from .ledger import Ledger
from .textutil import alias_key, title_slug
from .works import ensure_project, require_work, resolve_ref


@dataclass(frozen=True)
class Kind:
    name: str
    scope: str  # library | project
    meaning: str
    fields: tuple[str, ...] = ()
    group: str = "literature"  # palette grouping in the UI


KINDS: dict[str, Kind] = {k.name: k for k in [
    # -- the literature (library-wide) ------------------------------------------------------------------------
    Kind("concept", "library", "an idea, notion or term (e.g. 'delta rule', 'state tracking')", ("aliases",)),
    Kind("method", "library", "a technique, architecture, algorithm or model family", ("aliases", "family")),
    Kind("model", "library", "a specific trained model or checkpoint", ("params", "base", "url")),
    Kind("task", "library", "a problem being solved (e.g. language modeling, in-context retrieval)", ("aliases",)),
    Kind("dataset", "library", "data used to train or evaluate", ("size", "modality", "license", "url")),
    Kind("benchmark", "library", "an evaluation suite or protocol", ("metrics", "tasks", "url")),
    Kind("metric", "library", "a measure (perplexity, accuracy, …)", ("higher_is_better", "unit")),
    Kind("result", "library", "one reported number: method × benchmark/dataset × metric (main tables or abstract only)",
         ("value", "metric", "benchmark", "dataset", "method", "split", "setting", "model_size", "compute", "role",
          "higher_is_better", "extra_data", "source")),
    Kind("claim", "library", "an assertion a paper makes", ("qualifier", "quote", "source")),
    Kind("limitation", "library", "a stated weakness, caveat, failure mode or future work", ("quote", "source")),
    # -- your thinking (project-scoped) ----------------------------------------------------------------------
    Kind("topic", "project", "a theme that groups other items", group="thinking"),
    Kind("question", "project", "an open question", group="thinking"),
    Kind("gap", "project", "something nobody has done or measured yet", group="thinking"),
    Kind("idea", "project", "a research idea", group="thinking"),
    Kind("hypothesis", "project", "a testable conjecture", ("prediction", "status"), group="thinking"),
    Kind("experiment", "project", "a planned or completed experiment", ("status", "setup", "outcome", "url"), group="thinking"),
    Kind("finding", "project", "something you observed (an experiment outcome or insight)", group="thinking"),
]}

@dataclass(frozen=True)
class Relation:
    name: str
    meaning: str


RELATIONS: dict[str, Relation] = {r.name: r for r in [
    # paper -> entity
    Relation("introduces", "the paper proposes this method/dataset/benchmark/concept"),
    Relation("uses", "relies on this method/dataset/model"),
    Relation("evaluates_on", "reports results on this benchmark/dataset"),
    Relation("reports", "the paper reports this result"),
    Relation("addresses", "works on this task/question"),
    Relation("raises", "poses this question or limitation"),
    Relation("cites", "cites"),
    # result edges
    Relation("result_of", "result -> the method/model it measures"),
    Relation("measured_on", "result -> the benchmark/dataset it was measured on"),
    # entity <-> entity
    Relation("outperforms", "beats on the stated benchmark/metric"),
    Relation("baseline_for", "is used as the comparison point for"),
    Relation("extends", "builds directly on"),
    Relation("instance_of", "is a kind of"),
    Relation("part_of", "is a component of"),
    Relation("measured_by", "benchmark/task is scored with this metric"),
    Relation("compares_to", "is compared with"),
    # argument / discourse
    Relation("supports", "is evidence for"),
    Relation("contradicts", "is evidence against"),
    Relation("qualifies", "limits the scope of"),
    Relation("answers", "answers this question"),
    Relation("motivates", "inspires this idea/hypothesis/experiment"),
    Relation("tests", "experiment tests this hypothesis"),
    Relation("limits", "limitation applies to this method/claim"),
    Relation("about", "is about"),
]}


def kind_of(entity_id: str) -> str | None:
    head = entity_id.split(":", 1)[0]
    return head if head in KINDS and ":" in entity_id else None


def is_entity_id(value: str) -> bool:
    return kind_of(value or "") is not None


def default_scope(kind: str) -> str:
    return KINDS[kind].scope if kind in KINDS else "project"


def result_title(data: dict) -> str:
    """Readable title for a result entity from its fields: 'GDN · RULER · acc 82.1'."""
    parts = [str(data[k]) for k in ("method", "benchmark", "dataset") if data.get(k)]
    value = data.get("value")
    metric = data.get("metric")
    if value is not None:
        parts.append(f"{metric + ' ' if metric else ''}{value}")
    if data.get("setting"):
        parts.append(f"({data['setting']})")
    return " · ".join(parts) or "result"


# ------------------------------------------------------------------------------------------------ entities
KEYS = "_k"  # data._k: alias_key() of the title and every alias, matched in SQL with json_each (no Python scan)
_KEYED: set[str] = set()  # databases whose entities all carry data._k


def entity_row(conn: sqlite3.Connection, project: str, eid: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM entities WHERE id=? AND project IN (?, '')", (eid, project)).fetchone()


def entity_keys(title: str, aliases) -> list[str]:
    return sorted({k for k in (alias_key(str(x)) for x in [title, *(aliases or [])]) if k})


def public_data(data) -> dict:
    """Entity data as shown to agents and exported: internal keys (data._k) removed."""
    d = json.loads(data or "{}") if isinstance(data, (str, bytes)) or data is None else dict(data)
    return {k: v for k, v in d.items() if not str(k).startswith("_")}


def with_keys(data: dict, kind: str, title: str, aliases) -> str:
    d = public_data(data)
    if kind != "result":
        d[KEYS] = entity_keys(title, aliases)
    return dumps(d)


def ensure_entity_keys(db) -> None:
    """Backfill data._k for entities written before alias keys existed (once per database per process). A data
    migration of derived keys, so it isn't journaled."""
    if str(db.path) in _KEYED:
        return
    with db.write("backfill of derived entity alias keys") as conn:
        for r in conn.execute("SELECT id, kind, title, aliases, data FROM entities WHERE kind!='result' AND "
                              "json_extract(data, '$._k') IS NULL").fetchall():
            conn.execute("UPDATE entities SET data=? WHERE id=?",
                         (with_keys(public_data(r["data"]), r["kind"], r["title"], json.loads(r["aliases"] or "[]")), r["id"]))
    _KEYED.add(str(db.path))


def _alias_hits(conn: sqlite3.Connection, project: str, kinds, name: str) -> list[sqlite3.Row]:
    """Entities of these kinds (None: any) whose title or an alias has the same alias_key as `name`; project items
    first."""
    key = alias_key(name or "")
    if not key:
        return []
    kinds = [kinds] if isinstance(kinds, str) else list(kinds or KINDS)
    marks = ",".join("?" * len(kinds))
    return conn.execute(
        f"SELECT e.* FROM entities e WHERE e.kind IN ({marks}) AND e.project IN (?, '') AND ("
        f"EXISTS(SELECT 1 FROM json_each(e.data, '$._k') WHERE value=?) OR "
        f"(json_type(e.data, '$._k') IS NULL AND lower(e.title)=lower(?))) ORDER BY e.project DESC, e.created_at, e.id",
        (*kinds, project, key, name.strip())).fetchall()


def find_by_alias(conn: sqlite3.Connection, project: str, kind, name: str) -> sqlite3.Row | None:
    """An existing entity of this kind (or kinds) whose title or alias matches `name` (case/punctuation-insensitive)."""
    hits = _alias_hits(conn, project, kind, name)
    return hits[0] if hits else None


def link_scope(conn: sqlite3.Connection, project: str, source: str, target: str, evidence: str | None = None) -> str:
    """Where a link lives: '' (library-wide) between library-level facts (works, library entities) without a project
    note as evidence, else in the project."""
    def library_level(node: str) -> bool:
        if is_work_id(node):
            return True
        row = conn.execute("SELECT project FROM entities WHERE id=?", (node,)).fetchone()
        return bool(row) and row["project"] == ""
    return "" if library_level(source) and library_level(target) and not evidence else project


def link_id(scope: str, source: str, target: str, relation: str, qualifier: str | None) -> str:
    """Links are keyed by what they say, so adding the same link twice (or importing it again) is a no-op."""
    return hash_id("l:", [scope, source, target, relation, qualifier])


# Results are identified by (paper, method, benchmark, metric, setting, split): extracting a table twice updates.
RESULT_DIMS = ("method", "benchmark", "metric", "setting", "split")
_DIM_KINDS = {"method": ("method", "model"), "benchmark": ("benchmark", "dataset"), "metric": ("metric",)}
_DIM_LINKS = {"result_of": "method", "measured_on": "benchmark", "measured_by": "metric"}


def _canon(conn: sqlite3.Connection, project: str, dim: str, name, cache: dict) -> str:
    """Group key of a method/benchmark/metric name: the entity it names (via title/aliases), else its alias_key."""
    if name in (None, ""):
        return ""
    name = str(name)
    if is_entity_id(name):
        return name
    if dim not in _DIM_KINDS:
        return alias_key(name)
    k = (dim, alias_key(name))
    if k not in cache:
        hit = find_by_alias(conn, project, _DIM_KINDS[dim], name)
        cache[k] = hit["id"] if hit else k[1]
    return cache[k]


def _result_values(data: dict) -> dict:
    return {"method": data.get("method"), "benchmark": data.get("benchmark") or data.get("dataset"),
            "metric": data.get("metric"), "setting": data.get("setting"), "split": data.get("split")}


def _result_identity(conn: sqlite3.Connection, project: str, paper: str | None, data: dict, linked: dict, cache: dict) -> tuple:
    vals = _result_values(data)
    out = [paper or ""]
    for dim in RESULT_DIMS:
        out.append(_canon(conn, project, dim, vals[dim], cache) if vals[dim] not in (None, "") else
                   _canon(conn, project, dim, linked.get(dim), cache))
    return tuple(out)


def _stored_result(conn: sqlite3.Connection, rid: str) -> tuple[str | None, dict]:
    """(reporting paper, {dim: linked entity}) of a stored result."""
    paper = conn.execute("SELECT source FROM links WHERE target=? AND relation='reports' AND retracted_at IS NULL "
                         "ORDER BY created_at LIMIT 1", (rid,)).fetchone()
    linked = {_DIM_LINKS[r["relation"]]: r["target"] for r in conn.execute(
        "SELECT relation, target FROM links WHERE source=? AND relation IN ('result_of','measured_on','measured_by') "
        "AND retracted_at IS NULL ORDER BY created_at DESC", (rid,))}
    return (paper["source"] if paper else None), linked


def _match_result(conn: sqlite3.Connection, project: str, item: dict, data: dict, made: dict, cache: dict) -> tuple[sqlite3.Row | None, tuple]:
    paper = None
    ref = item.get("from") or next((l.get("source") for l in item.get("links") or [] if l.get("relation") == "reports"), None)
    if ref:
        w = resolve_ref(conn, str(ref), project)
        paper = w["id"] if w else None
    linked = {}
    for l in item.get("links") or []:
        dim = _DIM_LINKS.get(l.get("relation") or "")
        if dim and l.get("target"):
            linked.setdefault(dim, l["target"])
    ident = _result_identity(conn, project, paper, data, linked, cache)
    if ident in made:
        return entity_row(conn, project, made[ident]), ident
    if paper:
        rows = conn.execute("SELECT e.* FROM links l JOIN entities e ON e.id=l.target WHERE l.source=? AND l.relation='reports' "
                            "AND l.retracted_at IS NULL AND e.kind='result' AND e.project IN (?, '')", (paper, project)).fetchall()
    else:
        rows = conn.execute("SELECT e.* FROM entities e WHERE e.kind='result' AND e.project IN (?, '') AND NOT EXISTS("
                            "SELECT 1 FROM links WHERE target=e.id AND relation='reports' AND retracted_at IS NULL)", (project,)).fetchall()
    for r in rows:
        stored_paper, stored_linked = _stored_result(conn, r["id"])
        if _result_identity(conn, project, stored_paper, json.loads(r["data"] or "{}"), stored_linked, cache) == ident:
            return r, ident
    return None, ident


def upsert_entities(lg: Ledger, actor: Actor, project: str, items: list[dict]) -> list[dict]:
    """Create/update typed entities. Each item: {kind, title, id?, body?, data?, aliases?, tags?, scope?,
    from?: work, rel?: relation from that work, links?: [{relation, target}|{relation, source}], delete?}.
    Existing entities are matched by id, then by title/alias, so extracting the same benchmark twice merges; results
    are matched by (paper, method, benchmark, metric, setting, split), so re-extracting a table updates its numbers."""
    results = []
    pending_links: list[dict] = []
    made_results: dict[tuple, str] = {}
    cache: dict = {}
    ensure_entity_keys(lg.db)
    with lg.db.tx(actor) as tx:
        ensure_project(tx, project)
        for item in items:
            if not isinstance(item, dict):
                results.append({"status": "error", "message": "each item must be an object {kind, title, …}"})
                continue
            if item.get("delete"):
                eid = item.get("id") or ""
                row = entity_row(tx.conn, project, eid)
                if not row:
                    results.append({"status": "no_match", "id": eid, "message": f"unknown entity {eid}"})
                    continue
                try:
                    if row["project"] == "":
                        actor.check_library_change(f"library {row['kind']} {eid}")
                except PermissionError as exc:
                    results.append({"status": "error", "id": eid, "message": str(exc)})
                    continue
                tx.execute("DELETE FROM entities WHERE id=?", (eid,))
                tx.execute("UPDATE links SET retracted_at=? WHERE (source=? OR target=?) AND retracted_at IS NULL", (now(), eid, eid))
                tx.execute("UPDATE maps SET version=version+1 WHERE id IN (SELECT DISTINCT map_id FROM map_nodes WHERE ref=? "
                           "AND deleted_at IS NULL)", (eid,))
                tx.execute("UPDATE map_nodes SET deleted_at=? WHERE ref=? AND deleted_at IS NULL", (now(), eid))
                tx.log("entity.delete", {"entity": eid}, project=project)
                results.append({"status": "ok", "id": eid, "change": "deleted"})
                continue
            kind = item.get("kind") or (kind_of(item.get("id") or "") or "topic")
            if kind not in KINDS:
                results.append({"status": "error", "message": f"kind must be one of {', '.join(KINDS)}"})
                continue
            data = public_data(item.get("data")) if isinstance(item.get("data"), dict) else {}
            aliases_in = [str(a) for a in item.get("aliases") or [] if str(a).strip()]
            title = (item.get("title") or "").strip() or (result_title(data) if kind == "result" else "")
            scope = item.get("scope") or default_scope(kind)
            owner = "" if scope == "library" else project
            eid = item.get("id")
            row = None
            ident = None
            if eid:
                eid = eid if kind_of(eid) else f"{kind}:{title_slug(eid)}"
                row = entity_row(tx.conn, project, eid)
            if not row and kind == "result":
                row, ident = _match_result(tx.conn, project, item, data, made_results, cache)
            elif not row and title:
                row = find_by_alias(tx.conn, project, kind, title)
                if not row:
                    for alias in aliases_in:
                        row = find_by_alias(tx.conn, project, kind, alias)
                        if row:
                            break
            if row and item.get("rename") and row["project"] == "":
                try:
                    actor.check_library_change(f"library {row['kind']} {row['id']}")
                except PermissionError as exc:
                    results.append({"status": "error", "id": row["id"], "message": str(exc)})
                    continue
            if row:
                eid = row["id"]
                merged = public_data(row["data"])
                merged.update({k: v for k, v in data.items() if v not in (None, "")})
                aliases = sorted(set(json.loads(row["aliases"] or "[]")) | set(aliases_in) |
                                 ({title} if title and kind != "result" and alias_key(title) != alias_key(row["title"]) else set()))
                new_title = item.get("rename") or row["title"]
                if kind == "result":
                    new_title = (item.get("title") or "").strip() or result_title(merged)
                tx.execute("UPDATE entities SET title=?, body=COALESCE(NULLIF(?,''), body), data=?, aliases=?, updated_at=? WHERE id=?",
                           (new_title, item.get("body") or "", with_keys(merged, kind, new_title, aliases), dumps(aliases), now(), eid))
                change = "matched"
            else:
                if not title:
                    results.append({"status": "error", "message": "title (or result data) required for a new entity"})
                    continue
                eid = free_id(tx.conn, "entities", eid or f"{kind}:{title_slug(title) or 'item'}")
                aliases = sorted(set(aliases_in))
                tx.execute("INSERT INTO entities(id,kind,project,title,body,data,aliases,created_by,created_at,updated_at) "
                           "VALUES(?,?,?,?,?,?,?,?,?,?)", (eid, kind, owner, title, item.get("body") or "",
                                                           with_keys(data, kind, title, aliases), dumps(aliases), actor.label, now(), now()))
                change = "created"
            if ident is not None:
                made_results[ident] = eid
            if item.get("tags"):
                tagmod.apply(tx, project, item["tags"], "entity", [eid])
            tx.log("entity.upsert", {"entity": eid, "kind": kind, "title": title, "change": change}, project=project)
            results.append({"status": "ok", "id": eid, "change": change, "kind": kind})
            if item.get("from"):
                pending_links.append({"source": item["from"], "target": eid, "relation": item.get("rel") or DEFAULT_FROM_REL.get(kind, "about")})
            for link in item.get("links") or []:
                if not isinstance(link, dict):
                    continue
                if link.get("target"):
                    pending_links.append({"source": eid, "target": link["target"], "relation": link.get("relation") or "about",
                                          "qualifier": link.get("qualifier")})
                elif link.get("source"):
                    pending_links.append({"source": link["source"], "target": eid, "relation": link.get("relation") or "about",
                                          "qualifier": link.get("qualifier")})
    if pending_links:
        added = failed = 0
        for link, res in zip(pending_links, add_links(lg, actor, project, pending_links)):
            if res["status"] == "ok":
                added += 1
            else:
                failed += 1
                results.append({"status": "link_failed", "message": f"{link['source']} -{link['relation']}-> {link['target']}: {res.get('message') or ''}"})
        results.append({"status": "links", "added": added, "failed": failed})
    return results


DEFAULT_FROM_REL = {"result": "reports", "limitation": "raises", "question": "raises", "gap": "raises", "claim": "about",
                    "dataset": "uses", "benchmark": "evaluates_on", "metric": "uses", "task": "addresses", "method": "uses",
                    "model": "uses", "concept": "about"}


def list_entities(lg: Ledger, project: str, kind: str | None = None, query: str = "", work: str | None = None,
                  limit: int = 50, offset: int = 0) -> dict:
    with lg.db.read() as conn:
        where, params = ["e.project IN (?, '')"], [project]
        if kind:
            where.append("e.kind=?")
            params.append(kind)
        if query:
            where.append("(e.title LIKE ? OR e.aliases LIKE ? OR e.id LIKE ? OR e.body LIKE ?)")
            params += [f"%{query}%"] * 4
        if work:
            wid = require_work(conn, work, project)["id"]
            where.append("e.id IN (SELECT target FROM links WHERE source=? AND retracted_at IS NULL UNION "
                         "SELECT source FROM links WHERE target=? AND retracted_at IS NULL)")
            params += [wid, wid]
        body = f"FROM entities e WHERE {' AND '.join(where)}"
        total = conn.execute(f"SELECT count(*) {body}", params).fetchone()[0]
        rows = conn.execute(f"SELECT e.* {body} ORDER BY e.kind, e.title LIMIT ? OFFSET ?",
                            (*params, max(0, int(limit or 50)), max(0, int(offset or 0)))).fetchall()
        items = []
        for r in rows:
            papers = [label_of(conn, x["n"]) for x in conn.execute(
                "SELECT source AS n FROM links WHERE target=? AND source LIKE 'w%' AND retracted_at IS NULL UNION "
                "SELECT target AS n FROM links WHERE source=? AND target LIKE 'w%' AND retracted_at IS NULL", (r["id"], r["id"]))]
            links = conn.execute("SELECT count(*) FROM links WHERE (source=? OR target=?) AND retracted_at IS NULL",
                                 (r["id"], r["id"])).fetchone()[0]
            items.append({"id": r["id"], "kind": r["kind"], "title": r["title"], "body": r["body"], "scope": "library" if r["project"] == "" else "project",
                          "data": public_data(r["data"]), "aliases": json.loads(r["aliases"] or "[]"), "papers": papers,
                          "links": links, "by": r["created_by"], "updated_at": r["updated_at"]})
        counts = dict(conn.execute("SELECT kind, count(*) FROM entities WHERE project IN (?, '') GROUP BY kind", (project,)).fetchall())
    return {"items": items, "total": total, "counts": counts}


def results_table(lg: Ledger, project: str, benchmark: str | None = None, metric: str | None = None, method: str | None = None) -> dict:
    """Result entities as a leaderboard: rows of method × benchmark/dataset × metric with value and source paper.
    Benchmarks, methods and metrics are grouped by the entity they name (result_of/measured_on links, titles and
    aliases), so 'SNIAH' and 'S-NIAH' share a board; the same result stored twice is shown once."""
    with lg.db.read() as conn:
        cache: dict = {}
        ents: dict[str, tuple[str, list[str]]] = {}
        names: dict[str, str] = {}

        def label(key: str, raw) -> str:
            if is_entity_id(key):
                if key not in ents:
                    e = conn.execute("SELECT title, data FROM entities WHERE id=?", (key,)).fetchone()
                    ents[key] = (e["title"], json.loads(e["data"] or "{}").get(KEYS) or [alias_key(e["title"])]) if e else (key, [])
                return ents[key][0]
            return names.setdefault(key, str(raw or ""))

        def matches(want: str | None, key: str, shown: str) -> bool:
            if not want:
                return True
            w = alias_key(want)
            keys = [alias_key(shown), *(ents.get(key, ("", []))[1])]
            return bool(w) and any(w in k for k in keys)

        best: dict[tuple, dict] = {}
        for r in conn.execute("SELECT id, data, updated_at FROM entities WHERE kind='result' AND project IN (?, '') "
                              "ORDER BY updated_at", (project,)):
            d = json.loads(r["data"] or "{}")
            paper, linked = _stored_result(conn, r["id"])
            ident = _result_identity(conn, project, paper, d, linked, cache)
            vals = _result_values(d)
            shown = {dim: label(ident[i + 1], vals[dim] or linked.get(dim)) for i, dim in enumerate(RESULT_DIMS[:3])}
            if not (matches(benchmark, ident[2], shown["benchmark"]) and matches(metric, ident[3], shown["metric"])
                    and matches(method, ident[1], shown["method"])):
                continue
            best[ident] = {"id": r["id"], "benchmark": shown["benchmark"], "metric": shown["metric"] or None,
                           "method": shown["method"] or None, "value": d.get("value"), "split": d.get("split"),
                           "setting": d.get("setting"), "role": d.get("role"), "model_size": d.get("model_size"),
                           "higher_is_better": d.get("higher_is_better"), "paper": label_of(conn, paper) if paper else None}
        rows = list(best.values())

    def sort_key(x):
        try:
            v = float(str(x["value"]).rstrip("%"))
        except (TypeError, ValueError):
            v = float("nan")
        hib = x["higher_is_better"] is not False and str(x["higher_is_better"]).lower() != "false"
        return (x["benchmark"] or "", x["metric"] or "", str(x.get("setting") or ""), str(x.get("split") or ""),
                -(v if hib else -v) if v == v else float("inf"))

    rows.sort(key=sort_key)
    return {"rows": rows}


# ------------------------------------------------------------------------------------------------ links
def links_for(conn: sqlite3.Connection, project: str, node: str) -> list[dict]:
    out = []
    for r in conn.execute("SELECT * FROM links WHERE (source=? OR target=?) AND project IN (?, '') AND retracted_at IS NULL ORDER BY created_at",
                          (node, node, project)):
        out.append({"id": r["id"], "source": label_of(conn, r["source"]), "target": label_of(conn, r["target"]),
                    "relation": r["relation"], "qualifier": r["qualifier"], "evidence": r["evidence_note_id"]})
    return out


def label_of(conn: sqlite3.Connection, node: str) -> str:
    if is_work_id(node):
        w = conn.execute("SELECT citekey FROM works WHERE id=?", (node,)).fetchone()
        return w["citekey"] if w else node
    return node


def resolve_node(conn: sqlite3.Connection, ref: str, project: str) -> str:
    """A link endpoint: an entity id (method:deltanet), a paper (citekey or any ID), or an entity's exact title/alias."""
    ref = (ref or "").strip()
    if is_entity_id(ref):
        if not entity_row(conn, project, ref):
            raise LookupError(f"unknown {kind_of(ref)} {ref} — create it with entity() first")
        return ref
    work = resolve_ref(conn, ref, project)
    if work:
        return work["id"]
    head, _, rest = ref.partition(":")
    kinds = [head] if head in KINDS and rest else list(KINDS)
    name = rest if head in KINDS and rest else ref
    hits, seen = [], set()
    for r in _alias_hits(conn, project, kinds, name):  # one SQL lookup for all kinds; the first hit per kind
        if r["kind"] not in seen:
            seen.add(r["kind"])
            hits.append(r)
    if len(hits) == 1:
        return hits[0]["id"]
    if len(hits) > 1:
        raise LookupError(f"'{ref}' is ambiguous ({', '.join(h['id'] for h in hits)}) — use the id")
    raise LookupError(f"no paper or item '{ref}' — use an item id like method:deltanet, a citekey, or create it first")


def closest_relation(rel: str) -> str | None:
    best = min(RELATIONS, key=lambda r: Levenshtein.distance(r, rel))
    return best if Levenshtein.distance(best, rel) <= 3 and best != rel else None


def add_links(lg: Ledger, actor: Actor, project: str, items: list[dict]) -> list[dict]:
    """Typed links. Links between library-level facts (papers, library entities) are library-wide; links touching
    project thinking (ideas, hypotheses, …) stay in the project."""
    results = []
    ensure_entity_keys(lg.db)
    with lg.db.tx(actor) as tx:
        ensure_project(tx, project)
        for item in items:
            if not isinstance(item, dict):
                results.append({"status": "error", "message": "each item must be an object {source, target, relation}"})
                continue
            if item.get("retract"):
                link = tx.execute("SELECT project FROM links WHERE id=? AND project IN (?, '') AND retracted_at IS NULL",
                                  (item["retract"], project)).fetchone()
                try:
                    if link and link["project"] == "":
                        actor.check_library_change(f"library link {item['retract']}")
                except PermissionError as exc:
                    results.append({"status": "error", "id": item["retract"], "message": str(exc)})
                    continue
                n = tx.execute("UPDATE links SET retracted_at=? WHERE id=? AND project IN (?, '') AND retracted_at IS NULL",
                               (now(), item["retract"], project)).rowcount
                tx.log("link.retract", {"link": item["retract"]}, project=project)
                results.append({"status": "ok" if n else "no_match", "id": item["retract"], "retracted": bool(n)})
                continue
            rel = re.sub(r"[^a-z0-9_:\-]", "", (item.get("relation") or "about").lower().replace(" ", "_"))
            try:
                src = resolve_node(tx.conn, item.get("source", ""), project)
                dst = resolve_node(tx.conn, item.get("target", ""), project)
            except LookupError as exc:
                results.append({"status": "no_match", "message": str(exc)})
                continue
            evidence = item.get("evidence")
            if evidence and not tx.execute("SELECT 1 FROM notes WHERE id=? AND project=?", (evidence, project)).fetchone():
                results.append({"status": "error", "message": f"evidence note {evidence} not found"})
                continue
            scope = link_scope(tx.conn, project, src, dst, evidence)
            lid = link_id(scope, src, dst, rel, item.get("qualifier"))
            existing = tx.execute("SELECT * FROM links WHERE id=?", (lid,)).fetchone()
            if existing:
                change = {}
                if evidence and not existing["evidence_note_id"]:
                    tx.execute("UPDATE links SET evidence_note_id=? WHERE id=?", (evidence, lid))
                    change["evidence"] = evidence
                if existing["retracted_at"]:
                    tx.execute("UPDATE links SET retracted_at=NULL WHERE id=?", (lid,))
                    change["restored"] = True
                if change:
                    tx.log("link.update", {"link": lid, **change}, project=project)
                results.append({"status": "ok", "id": lid, "duplicate": True})
                continue
            tx.execute("INSERT INTO links(id,project,source,target,relation,qualifier,evidence_note_id,principal_id,agent,created_at) "
                       "VALUES(?,?,?,?,?,?,?,?,?,?)", (lid, scope, src, dst, rel, item.get("qualifier"), evidence,
                                                       actor.principal_id, actor.agent, now()))
            tx.log("link.add", {"link": lid, "source": src, "target": dst, "relation": rel}, project=project)
            res = {"status": "ok", "id": lid}
            if rel not in RELATIONS and not rel.startswith("x:"):
                hint = closest_relation(rel)
                if hint:
                    res["hint"] = f"standard relation: {hint}"
            if item.get("map_edge"):
                tx.execute("UPDATE map_edges SET promoted_link_id=? WHERE id=?", (lid, item["map_edge"]))
            results.append(res)
    return results


def evidence_graph(conn: sqlite3.Connection, project: str, node: str, depth: int, limit: int) -> dict:
    seen, frontier, edges = {node}, {node}, {}
    for _ in range(depth):
        nxt = set()
        for n in frontier:
            for r in conn.execute("SELECT * FROM links WHERE (source=? OR target=?) AND project IN (?, '') AND retracted_at IS NULL", (n, n, project)):
                if r["id"] in edges or len(edges) >= limit:
                    continue
                edges[r["id"]] = r
                nxt.update((r["source"], r["target"]))
        frontier = nxt - seen
        seen |= nxt
        if not frontier:
            break
    titles = {}
    for n in seen:
        if is_work_id(n):
            w = conn.execute("SELECT citekey, title FROM works WHERE id=?", (n,)).fetchone()
            titles[n] = (w["citekey"], w["title"]) if w else (n, "")
        else:
            e = conn.execute("SELECT title FROM entities WHERE id=?", (n,)).fetchone()
            titles[n] = (n, e["title"] if e else "")
    return {"kind": "evidence", "root": titles[node][0],
            "nodes": [{"id": titles[n][0], "title": titles[n][1]} for n in seen],
            "edges": [{"id": e["id"], "source": titles[e["source"]][0], "target": titles[e["target"]][0], "relation": e["relation"],
                       "qualifier": e["qualifier"], "evidenced": bool(e["evidence_note_id"])} for e in edges.values()],
            "truncated": len(edges) >= limit}
