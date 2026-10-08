"""Looking things up in the ledger: find (works, passages, notes), the reference picker, a project's overview,
network and activity journal."""
from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from . import tags as tagmod
from .db import who_sql
from .ids import is_work_id, parse_ident
from .knowledge import label_of
from .ledger import Ledger
from .textutil import clip
from .works import project_state, require_work, visible_sql, work_brief, work_by_ident


def fts_query(text: str, prefix_last: bool = True) -> str:
    tokens = [t for t in re.findall(r"\w+", text, re.UNICODE) if t]
    if not tokens:
        return ""
    quoted = [f'"{t}"' for t in tokens]
    if prefix_last and len(tokens[-1]) >= 3:
        quoted[-1] += "*"
    return " ".join(quoted)


def work_filter(project: str, scope: str = "project", all_tags=None, any_tags=None,
                not_tags=None) -> tuple[str, list, list[str], list]:
    """(join, join params, where clauses, where params) selecting live works `w` in the project (or the library)
    by tag expressions. scope='project' joins project_works as `pw`."""
    join, join_params = "", []
    if scope == "project":
        join, join_params = "JOIN project_works pw ON pw.work_id=w.id AND pw.project=? AND pw.removed_at IS NULL", [project]
    where, params = ["w.merged_into IS NULL"], []
    seen_sql, seen_params = visible_sql("w.id") if scope != "project" else ("", [])
    if seen_sql:  # a caller limited to some projects searches the library of its projects
        where.append(seen_sql)
        params += seen_params
    tag_sql, tag_params = tagmod.filter_sql(project, all_tags, any_tags, not_tags)
    if tag_sql:
        where.append(tag_sql)
        params += tag_params
    return join, join_params, where, params


# ------------------------------------------------------------------------------------------------ find
def find(lg: Ledger, project: str, query: str = "", scope: str = "project", kind: str = "works", all_tags=None,
         any_tags=None, not_tags=None, limit: int = 10, offset: int = 0, since: str | None = None, year_from=None,
         year_to=None, read: str | None = None, work: str | None = None) -> dict:
    limit = max(1, min(int(limit or 10), 100))
    offset = max(0, int(offset or 0))
    out: dict[str, Any] = {"kind": kind, "items": [], "total": 0}
    if query and query.strip() and not fts_query(query):
        # Nothing searchable (punctuation only): an empty result with a hint, never an FTS syntax error.
        out["hint"] = "query has no searchable words; use letters or digits"
        if kind in ("passages", "all"):
            out["passages"] = []
        if kind in ("notes", "all"):
            out["notes"] = []
        return out
    with lg.db.read() as conn:
        if kind in ("works", "all"):
            join, join_params, where, params = work_filter(project, scope, all_tags, any_tags, not_tags)
            if year_from:
                where.append("w.year >= ?")
                params.append(int(year_from))
            if year_to:
                where.append("w.year <= ?")
                params.append(int(year_to))
            if read and scope == "project":
                if read == "unread":
                    where.append("pw.read_depth IS NULL")
                else:
                    where.append("pw.read_depth = ?")
                    params.append(read)
            if since:
                where.append("(w.updated_at >= ?" + (" OR pw.added_at >= ?)" if scope == "project" else ")"))
                params += [since, since] if scope == "project" else [since]
            q = fts_query(query) if query else ""
            cols = "w.id, w.citekey, w.title, w.year, w.type, w.csl, w.trust_flags"
            # A project's own citekey (update_work citekey, or a snapshot's) isn't in the library-wide index.
            own = conn.execute("SELECT work_id FROM project_works WHERE project=? AND citekey_override=?",
                               (project, query.strip())).fetchone() if q else None
            if own:
                body = f"FROM works w {join} WHERE w.id=? AND {' AND '.join(where)}"
                params = join_params + [own["work_id"]] + params
                order = "w.id"
            elif q:
                body = f"FROM works_fts f JOIN works w ON w.id=f.work_id {join} WHERE works_fts MATCH ? AND {' AND '.join(where)}"
                params = join_params + [q] + params
                order = "bm25(works_fts, 0, 3.0, 4.0, 2.0, 1.0, 1.0, 3.0)"
            else:
                body = f"FROM works w {join} WHERE {' AND '.join(where)}"
                params = join_params + params
                order = "pw.added_at DESC" if scope == "project" else "w.updated_at DESC"
            out["total"] = conn.execute(f"SELECT count(*) {body}", params).fetchone()[0]
            page = conn.execute(f"SELECT {cols} {body} ORDER BY {order} LIMIT ? OFFSET ?", (*params, limit, offset)).fetchall()
            states = project_state(conn, project, [r["id"] for r in page])
            out["items"] = [work_brief(conn, r, states.get(r["id"])) for r in page]
        if kind in ("passages", "all") and query:
            q = fts_query(query, prefix_last=False)
            params = [q]
            scope_sql = ""
            if scope == "project":
                scope_sql = "AND d.work_id IN (SELECT work_id FROM project_works WHERE project=? AND removed_at IS NULL)"
                params.append(project)
            else:
                seen_sql, seen_params = visible_sql("d.work_id")
                if seen_sql:
                    scope_sql = "AND " + seen_sql
                    params += seen_params
            if work:
                wrow = require_work(conn, work, project)
                scope_sql += " AND d.work_id=?"
                params.append(wrow["id"])
            rows = conn.execute(f"SELECT p.id, p.seq, p.section, p.page, d.work_id, snippet(passages_fts, 0, '[', ']', '…', 24) AS snip, "
                                f"bm25(passages_fts) AS rank FROM passages_fts JOIN passages p ON p.id=passages_fts.rowid "
                                f"JOIN documents d ON d.id=p.document_id WHERE passages_fts MATCH ? {scope_sql} "
                                f"AND d.id=(SELECT d2.id FROM documents d2 WHERE d2.work_id=d.work_id AND d2.status='ok' ORDER BY "
                                f"CASE d2.source WHEN 'arxiv_html' THEN 0 WHEN 'jats' THEN 1 WHEN 'upload' THEN 2 WHEN 'pdf' THEN 3 ELSE 4 END, "
                                f"d2.created_at DESC LIMIT 1) ORDER BY rank LIMIT ?",
                                (*params, limit + offset)).fetchall()[offset:]
            keys = {r["work_id"]: conn.execute("SELECT citekey FROM works WHERE id=?", (r["work_id"],)).fetchone()["citekey"] for r in rows}
            out["passages"] = [{"work": keys[r["work_id"]], "seq": r["seq"], "section": r["section"], "page": r["page"],
                                "snippet": r["snip"], "passage_id": r["id"]} for r in rows]
        if kind in ("notes", "all") and query:
            q = fts_query(query)
            rows = conn.execute("SELECT n.* FROM notes_fts f JOIN notes n ON n.id=f.note_id WHERE notes_fts MATCH ? AND n.project=? "
                                "ORDER BY bm25(notes_fts) LIMIT ? OFFSET ?", (q, project, limit, offset)).fetchall()
            out["notes"] = [note_brief(conn, r) for r in rows]
    return out


def note_brief(conn: sqlite3.Connection, r: sqlite3.Row) -> dict:
    subject = r["subject"]
    if is_work_id(subject):
        w = conn.execute("SELECT citekey FROM works WHERE id=?", (subject,)).fetchone()
        subject = w["citekey"] if w else subject
    p = conn.execute(f"SELECT {who_sql()} AS by FROM principals p LEFT JOIN principals o ON o.id=p.owner_id WHERE p.id=?",
                     (r["principal_id"],)).fetchone() if r["principal_id"] else None
    by = p["by"] if p else "system"
    return {"id": r["id"], "subject": subject, "kind": r["kind"], "text": r["text"], "quote": r["quote"],
            "verification": r["verification"], "section": None, "page": r["page"],
            "by": f"{by}/{r['agent']}" if r["agent"] else by, "created_at": r["created_at"]}


# ------------------------------------------------------------------------------------------------ web UI views
def search_refs(lg: Ledger, project: str, q: str, limit: int = 6) -> dict:
    """Everything a mind-map node (or a link) can point at, for the UI's reference picker."""
    q = q.strip()
    out: dict[str, list] = {"works": [], "entities": [], "notes": [], "tags": [], "maps": [], "capture": None}
    if not q:
        return out
    res = find(lg, project, q, "library", "works", limit=limit)
    out["works"] = [{"ref": w["citekey"], "title": w["title"], "year": w["year"], "in_project": w.get("in_project", False)}
                    for w in res["items"]]
    like = f"%{q}%"
    with lg.db.read() as conn:
        out["entities"] = [{"ref": r["id"], "kind": r["kind"], "title": r["title"]} for r in conn.execute(
            "SELECT id, kind, title FROM entities WHERE project IN (?, '') AND (title LIKE ? OR id LIKE ? OR aliases LIKE ?) "
            "ORDER BY updated_at DESC LIMIT ?",
            (project, like, like, like, limit))]
        fq = fts_query(q)
        if fq:
            out["notes"] = [{"ref": r["id"], "text": clip(r["quote"] or r["text"], 120), "kind": r["kind"],
                             "subject": label_of(conn, r["subject"])} for r in conn.execute(
                "SELECT n.* FROM notes_fts f JOIN notes n ON n.id=f.note_id WHERE notes_fts MATCH ? AND n.project=? LIMIT ?",
                (fq, project, limit))]
        out["tags"] = [{"ref": "tag:" + t["name"], "title": t["name"], "count": t["count"]}
                       for t in tagmod.listing(conn, project) if q.lower() in t["name"]][:limit]
        out["maps"] = [{"ref": r["id"], "title": r["title"]} for r in conn.execute(
            "SELECT id, title FROM maps WHERE project=? AND deleted_at IS NULL AND (title LIKE ? OR id LIKE ?) LIMIT ?",
            (project, like, like, limit))]
        ident = parse_ident(q)
        if ident and (ident.scheme != "url" or q.startswith("http")) and not work_by_ident(conn, ident):
            out["capture"] = {"handle": ident.handle, "input": q}
    return out


def project_overview(lg: Ledger, project: str) -> str:
    """The litledger://project resource: counts, tag vocabulary, recent captures and maps."""
    with lg.db.read() as conn:
        tags = tagmod.listing(conn, project)
        n, unread = conn.execute("SELECT count(*), count(*) FILTER (WHERE read_depth IS NULL) FROM project_works "
                                 "WHERE project=? AND removed_at IS NULL", (project,)).fetchone()
        recent = conn.execute("SELECT w.citekey FROM project_works pw JOIN works w ON w.id=pw.work_id WHERE pw.project=? AND "
                              "pw.removed_at IS NULL ORDER BY pw.added_at DESC LIMIT 8", (project,)).fetchall()
        maps = conn.execute("SELECT id FROM maps WHERE project=? AND deleted_at IS NULL", (project,)).fetchall()
    out = [f"project {project}: {n} works ({unread} unread)"]
    if tags:
        out.append("tags: " + ", ".join(f"{t['name']}({t['count']})" for t in tags))
    if recent:
        out.append("recent: " + " ".join(r["citekey"] for r in recent))
    if maps:
        out.append("maps: " + " ".join(m["id"] for m in maps))
    return "\n".join(out)


def project_network(lg: Ledger, project: str, all_tags=None, any_tags=None, not_tags=None) -> dict:
    """Local citation + link network among the project's works and the entities they link to (the UI's graph)."""
    with lg.db.read() as conn:
        join, join_params, where, params = work_filter(project, "project", all_tags, any_tags, not_tags)
        rows = conn.execute(f"SELECT w.*, pw.read_depth FROM works w {join} WHERE {' AND '.join(where)}",
                            (*join_params, *params)).fetchall()
        ids = {r["id"] for r in rows}
        tagmap = tagmod.tags_for(conn, project, "work", list(ids))
        nodes = [{"id": r["id"], "citekey": r["citekey"], "title": r["title"], "year": r["year"], "type": r["type"],
                  "tags": tagmap.get(r["id"], []), "read": r["read_depth"]} for r in rows]
        edges = []
        if ids:
            marks = ",".join("?" * len(ids))
            for r in conn.execute(f"SELECT DISTINCT d.work_id AS src, r.resolved_work_id AS dst FROM refs_extracted r JOIN documents d "
                                  f"ON d.id=r.document_id WHERE d.work_id IN ({marks}) AND r.resolved_work_id IN ({marks})", (*ids, *ids)):
                edges.append({"source": r["src"], "target": r["dst"], "kind": "cites"})
        entities = [dict(e) for e in conn.execute("SELECT id, kind, title FROM entities WHERE project IN (?, '')", (project,))]
        known = ids | {e["id"] for e in entities}
        links = conn.execute("SELECT * FROM links WHERE project IN (?, '') AND retracted_at IS NULL", (project,)).fetchall()
    # Work-to-work links first, then links that touch an entity (as the UI has always received them).
    edges += [{"source": r["source"], "target": r["target"], "kind": r["relation"]} for r in links
              if r["source"] in ids and r["target"] in ids]
    edges += [{"source": r["source"], "target": r["target"], "kind": r["relation"]} for r in links
              if r["source"] in known and r["target"] in known and not (r["source"] in ids and r["target"] in ids)]
    return {"nodes": nodes, "entities": entities, "edges": edges}


def projects(lg: Ledger) -> list[dict]:
    with lg.db.read() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT p.id, p.title, p.created_at, (SELECT count(*) FROM project_works pw WHERE pw.project=p.id "
            "AND pw.removed_at IS NULL) AS works FROM projects p ORDER BY p.id")]


def journal(lg: Ledger, project: str | None, since: int = 0, limit: int = 100, oldest_first: bool = False,
            own_only: bool = False) -> list[dict]:
    """Journal entries after `since` for one project (plus library-wide ones unless own_only), or for all projects
    (project=None)."""
    sql = (f"SELECT j.*, p.name AS principal, {who_sql()} AS by FROM journal j LEFT JOIN principals p ON p.id=j.principal_id "
           "LEFT JOIN principals o ON o.id=p.owner_id WHERE j.seq>?")
    params: list[Any] = [since]
    if project is not None:
        sql += " AND j.project=?" if own_only else " AND (j.project=? OR j.project='' OR j.project IS NULL)"
        params.append(project)
    with lg.db.read() as conn:
        rows = conn.execute(sql + f" ORDER BY j.seq {'ASC' if oldest_first else 'DESC'} LIMIT ?", (*params, limit)).fetchall()
    return [dict(r) | {"payload": json.loads(r["payload"])} for r in rows]
