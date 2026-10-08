"""Library operations behind the tools on single works: work detail, project updates (read depth, why, citekeys,
removal, unmerging) and notes."""
from __future__ import annotations

import json
import re
import sqlite3

from . import tags as tagmod
from .check import check_quote
from .db import Actor, Tx, hash_id, now, who_sql
from .fulltext import current_document
from .knowledge import entity_row, is_entity_id, links_for, public_data
from .ledger import Ledger
from .search import note_brief
from .works import (READ_LEVELS, active_merges, add_to_project, ensure_project, mark_not_duplicate, project_state, require_work,
                    resolve_ref, unmerge_works, work_brief)

NOTE_KINDS = ["note", "quote", "summary", "inference", "result", "critique", "question", "todo"]


# ------------------------------------------------------------------------------------------------ work detail
def work_detail(lg: Ledger, project: str, ref: str, include: list[str] | None = None) -> dict:
    include = include or []
    with lg.db.read() as conn:
        row = require_work(conn, ref, project)
        wid = row["id"]
        state = project_state(conn, project, [wid])[wid]
        csl = json.loads(row["csl"])
        d = work_brief(conn, row, state)
        d["csl"] = csl
        d["versions"] = [dict(v) | {"ids": json.loads(v["ids"])} for v in
                         conn.execute("SELECT kind, label, ids, date FROM versions WHERE work_id=? ORDER BY kind DESC", (wid,))]
        d["counts"] = {
            "notes": conn.execute("SELECT count(*) FROM notes WHERE subject=? AND project=?", (wid, project)).fetchone()[0],
            "links": conn.execute("SELECT count(*) FROM links WHERE (source=? OR target=?) AND project IN (?, '') AND retracted_at IS NULL",
                                  (wid, wid, project)).fetchone()[0],
            "cited_by_local": conn.execute("SELECT count(DISTINCT d.work_id) FROM refs_extracted r JOIN documents d ON d.id=r.document_id "
                                           "WHERE r.resolved_work_id=?", (wid,)).fetchone()[0],
            "projects": conn.execute("SELECT count(*) FROM project_works WHERE work_id=? AND removed_at IS NULL", (wid,)).fetchone()[0],
        }
        d["merged_from"] = [r["citekey"] for r in conn.execute(
            "SELECT w.citekey FROM merges m JOIN works w ON w.id=m.absorbed WHERE m.survivor=? AND m.undone_at IS NULL", (wid,))]
        doc = current_document(conn, wid)
        d["document"] ={"id": doc["id"], "source": doc["source"], "passages": doc["n_passages"], "chars": doc["chars"]} if doc else None
        if "notes" in include:
            d["notes"] = [note_brief(conn, r) for r in conn.execute(
                "SELECT * FROM notes WHERE subject=? AND project=? ORDER BY created_at", (wid, project))]
        if "links" in include:
            d["links"] = links_for(conn, project, wid)
        if "outline" in include and doc:
            d["outline"] = json.loads(doc["outline"])
        if "projects" in include:
            d["projects"] = [r["project"] for r in conn.execute("SELECT project FROM project_works WHERE work_id=? AND removed_at IS NULL", (wid,))]
        if "sources" in include:
            d["sources"] = {r["provider"]: json.loads(r["data"]) for r in conn.execute("SELECT provider, data FROM work_sources WHERE work_id=?", (wid,))}
        if "history" in include:
            d["history"] = [dict(r) for r in conn.execute(
                f"SELECT j.ts, j.op, j.agent, j.principal_id, j.project, {who_sql()} AS by FROM journal j LEFT JOIN principals p "
                "ON p.id=j.principal_id LEFT JOIN principals o ON o.id=p.owner_id WHERE j.subject=? ORDER BY j.seq DESC LIMIT 30", (wid,))]
        if "knowledge" in include:
            d["knowledge"] = [{"id": e["id"], "kind": e["kind"], "title": e["title"], "data": public_data(e["data"]),
                               "relation": e["relation"]} for e in conn.execute(
                "SELECT e.*, l.relation FROM links l JOIN entities e ON e.id=l.target WHERE l.source=? AND l.retracted_at IS NULL "
                "AND e.project IN (?, '') UNION SELECT e.*, l.relation FROM links l JOIN entities e ON e.id=l.source WHERE l.target=? "
                "AND l.retracted_at IS NULL AND e.project IN (?, '') ORDER BY kind, title", (wid, project, wid, project))]
        if "citations" in include:
            d["cited_by"] = [dict(r) for r in conn.execute(
                "SELECT w.citekey, c.sentence FROM citation_contexts c JOIN works w ON w.id=c.citing_work WHERE c.cited_work=? LIMIT 20", (wid,))]
    return d


# ------------------------------------------------------------------------------------------------ updates
def update_works(lg: Ledger, actor: Actor, project: str, items: list[dict]) -> list[dict]:
    results = []
    with lg.db.tx(actor) as tx:
        ensure_project(tx, project)
        for item in items:
            if str(item.get("unmerge")).strip().lower() in ("false", "0", "no", ""):
                item = {k: v for k, v in item.items() if k != "unmerge"}
            if item.get("unmerge") or item.get("not_duplicate"):
                try:  # merges are library-wide: splitting one changes the work for every project
                    actor.check_library_change(f"the merge of {item.get('work') or item.get('ref') or 'this work'}")
                except PermissionError as exc:
                    results.append({"work": item.get("work"), "status": "error", "message": str(exc)})
                    continue
            if item.get("unmerge"):
                results.append(_unmerge_item(tx, project, item))
                continue
            if item.get("not_duplicate"):
                results.append(_not_duplicate_item(tx, project, str(item.get("work") or ""), str(item["not_duplicate"])))
                continue
            try:
                row = require_work(tx.conn, item.get("work") or item.get("ref") or "", project)
            except LookupError as exc:
                results.append({"work": item.get("work"), "status": "no_match", "message": str(exc)})
                continue
            wid = row["id"]
            if item.get("remove"):
                tx.execute("UPDATE project_works SET removed_at=? WHERE project=? AND work_id=?", (now(), project, wid))
                tx.log("project.remove", {"work": wid}, project=project)
                results.append({"work": row["citekey"], "status": "removed"})
                continue
            live = tx.execute("SELECT 1 FROM project_works WHERE project=? AND work_id=? AND removed_at IS NULL",
                              (project, wid)).fetchone()
            if not live and not item.get("add"):  # never add a work to the project as a side effect of an edit
                results.append({"work": row["citekey"], "status": "error",
                                "message": f"not in project {project}; add it first (resolve add=true), or pass add: true"})
                continue
            add_to_project(tx, project, wid, item.get("why"))
            changes = {}
            if item.get("read"):
                level = item["read"]
                if level not in READ_LEVELS:
                    raise ValueError(f"read must be one of {', '.join(READ_LEVELS)}")
                tx.execute("UPDATE project_works SET read_depth=? WHERE project=? AND work_id=?", (level, project, wid))
                changes["read"] = level
            if item.get("citekey"):
                key = re.sub(r"[^\w:\-.]", "", str(item["citekey"]))
                clash = tx.execute("SELECT citekey FROM works WHERE citekey=? AND id!=?", (key, wid)).fetchone() or \
                    tx.execute("SELECT w.citekey FROM project_works pw JOIN works w ON w.id=pw.work_id WHERE pw.project=? "
                               "AND pw.citekey_override=? AND pw.work_id!=?", (project, key, wid)).fetchone()
                if not key or clash:
                    results.append({"work": row["citekey"], "status": "error",
                                    "message": f"citekey {key} belongs to {clash['citekey']}" if clash else "empty citekey"})
                    continue
                tx.execute("UPDATE project_works SET citekey_override=? WHERE project=? AND work_id=?",
                           (None if key == row["citekey"] else key, project, wid))
                changes["citekey"] = key
            if item.get("why"):
                changes["why"] = item["why"]
            if changes:
                tx.log("project.update", {"work": wid, **changes}, project=project)
            results.append({"work": row["citekey"], "status": "ok", **changes})
    return results


def _raw_work(conn: sqlite3.Connection, ref: str, project: str) -> sqlite3.Row | None:
    """A work row WITHOUT following merges (so an absorbed work's own citekey names it), else resolve_ref."""
    ref = (ref or "").strip()
    row = conn.execute("SELECT * FROM works WHERE citekey=? OR id=?", (ref, ref)).fetchone()
    return row or resolve_ref(conn, ref, project)


def _unmerge_item(tx: Tx, project: str, item: dict) -> dict:
    """{unmerge: true, work: absorbed-or-survivor citekey} or {unmerge: <merge id>}."""
    ref = str(item.get("work") or item.get("ref") or "")
    if re.fullmatch(r"m[0-9A-Z]{26}", str(item["unmerge"])):
        merge_id = str(item["unmerge"])
    else:
        row = _raw_work(tx.conn, ref, project)
        if not row:
            return {"work": ref, "status": "no_match", "message": f"unknown work {ref!r}"}
        merges = active_merges(tx.conn, row["id"])
        own = [m for m in merges if m["absorbed"] == row["id"]]
        if own:
            merge_id = own[0]["id"]
        elif len(merges) == 1:
            merge_id = merges[0]["id"]
        elif merges:
            return {"work": row["citekey"], "status": "error",
                    "message": "merged from " + ", ".join(m["absorbed_key"] for m in merges) + "; pass the one to split off"}
        else:
            return {"work": row["citekey"], "status": "error", "message": "no merge to undo"}
    try:
        survivor, absorbed = unmerge_works(tx, merge_id)
    except (LookupError, ValueError) as exc:
        return {"work": ref, "status": "error", "message": str(exc)}
    keys = {r["id"]: r["citekey"] for r in tx.execute("SELECT id, citekey FROM works WHERE id IN (?,?)", (survivor, absorbed))}
    return {"work": keys[absorbed], "status": "unmerged", "message": f"split from {keys[survivor]}, marked not a duplicate"}


def _not_duplicate_item(tx: Tx, project: str, ref: str, other_ref: str) -> dict:
    """Mark two works as distinct, so resolution never merges them; when one was merged into the other, that merge is
    undone."""
    a, b = _raw_work(tx.conn, ref, project), _raw_work(tx.conn, other_ref, project)
    if not a or not b:
        missing = ref if not a else other_ref
        return {"work": missing, "status": "no_match", "message": f"unknown work {missing!r}"}
    pair = {a["id"], b["id"]}
    if len(pair) == 1:
        return {"work": a["citekey"], "status": "error", "message": "same work; pass the absorbed work's own citekey"}
    for m in active_merges(tx.conn, a["id"]):
        if {m["survivor"], m["absorbed"]} == pair:
            unmerge_works(tx, m["id"])
            loser = a if m["absorbed"] == a["id"] else b
            winner = b if loser is a else a
            return {"work": loser["citekey"], "status": "unmerged", "message": f"split from {winner['citekey']}, marked not a duplicate"}
    mark_not_duplicate(tx, a["id"], b["id"])
    tx.log("work.not_duplicate", {"work": a["id"], "other": b["id"]}, project=project)
    return {"work": a["citekey"], "status": "ok"}


def bump_read(tx: Tx, project: str, work_id: str, level: str) -> None:
    row = tx.execute("SELECT read_depth FROM project_works WHERE project=? AND work_id=? AND removed_at IS NULL",
                     (project, work_id)).fetchone()
    if not row:
        return
    current = READ_LEVELS.index(row["read_depth"]) if row["read_depth"] in READ_LEVELS else -1
    if READ_LEVELS.index(level) > current:
        tx.execute("UPDATE project_works SET read_depth=? WHERE project=? AND work_id=?", (level, project, work_id))
        tx.log("work.read", {"work": work_id, "read": level}, project=project)


# ------------------------------------------------------------------------------------------------ notes
def _subject(conn: sqlite3.Connection, ref: str, project: str) -> tuple[str, str | None]:
    """(subject id, work id or None). Entities are referenced as topic:/claim:/question: ids."""
    if is_entity_id(ref or ""):
        if not entity_row(conn, project, ref):
            raise LookupError(f"unknown entity {ref} — create it with entity()")
        return ref, None
    row = require_work(conn, ref, project)
    return row["id"], row["id"]


def add_notes(lg: Ledger, actor: Actor, project: str, items: list[dict]) -> list[dict]:
    results = []
    with lg.db.tx(actor) as tx:
        ensure_project(tx, project)
        for item in items:
            text = (item.get("text") or "").strip()
            quote = (item.get("quote") or "").strip() or None
            if not text and not quote:
                results.append({"status": "error", "message": "text or quote required"})
                continue
            try:
                subject, work_id = _subject(tx.conn, item.get("work") or item.get("subject") or "", project)
            except LookupError as exc:
                results.append({"status": "no_match", "message": str(exc)})
                continue
            kind = item.get("kind") or ("quote" if quote and not text else "note")
            if kind not in NOTE_KINDS:
                kind = "note"
            verification = item.get("verification") if item.get("verification") in ("snippet", "recall") else "unchecked"
            anchor: dict = {}
            check = None
            if quote and work_id:
                check = check_quote(tx.conn, work_id, quote)
                if check["status"] == "verified":
                    verification = "verified"
                    anchor = {"document_id": check["document_id"], "passage_id": check["passage_id"], "page": check.get("page")}
            note_id = hash_id("n:", [project, subject, text, quote, actor.principal_id, actor.agent])
            if tx.execute("SELECT 1 FROM notes WHERE id=?", (note_id,)).fetchone():
                results.append({"status": "ok", "id": note_id, "duplicate": True, "verification": verification})
                continue
            insert_note(tx, {"id": note_id, "project": project, "subject": subject, "kind": kind, "text": text or quote,
                             "quote": quote, "document_id": anchor.get("document_id"), "passage_id": anchor.get("passage_id"),
                             "page": item.get("page") or anchor.get("page"), "verification": verification,
                             "principal_id": actor.principal_id, "agent": actor.agent, "session": actor.session,
                             "created_at": now()})
            if item.get("tags"):
                tagmod.apply(tx, project, item["tags"], "note", [note_id])
            tx.log("note.add", {"note": note_id, "subject": subject, "kind": kind, "verification": verification}, project=project)
            res = {"status": "ok", "id": note_id, "verification": verification}
            if check and check["status"] != "verified":
                res["quote_check"] = check
            elif check:
                res["anchor"] = f"§{check['section']} p{check['seq']}" + (f" page {check['page']}" if check.get("page") else "")
            results.append(res)
    return results


def insert_note(tx: Tx, note: dict) -> None:
    """Store a note (a dict of notes columns) and index it for search."""
    cols = list(note)
    tx.execute(f"INSERT INTO notes({','.join(cols)}) VALUES({','.join('?' * len(cols))})", tuple(note.values()))
    tx.execute("INSERT INTO notes_fts VALUES(?,?,?,?)", (note["id"], note["project"], note["subject"],
                                                       f"{note['text']} {note.get('quote') or ''}"))
