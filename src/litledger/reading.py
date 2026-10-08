"""`read`: narrow, budgeted access to a work's text (outline first, then sections/passages/search), and the web reader's
passage pages."""
from __future__ import annotations

import json
import re

from .db import Actor, dumps, now
from .fulltext import current_document, fetch_document, upgrade_document
from .ledger import Ledger
from .library import bump_read
from .search import fts_query
from .textutil import clip, norm_title
from .works import require_work


def _range(target: str) -> list[int]:
    out: list[int] = []
    for part in re.split(r"[,\s]+", target or ""):
        m = re.match(r"^p?(\d+)(?:-p?(\d+))?$", part)
        if m:
            a, b = int(m.group(1)), int(m.group(2) or m.group(1))
            out.extend(range(a, min(b, a + 60) + 1))
    return out


def section_matches(section: str, target: str) -> bool:
    s, t = norm_title(section), norm_title(target)
    if not t:
        return False
    if re.match(r"^table\s*\d+$", t):
        return s.endswith(t)
    if s.startswith(t) or t in s:
        return True
    num = re.match(r"^(\d+(?:\.\d+)*)", target.strip())
    return bool(num and re.match(rf"^{re.escape(num.group(1))}(?:\D|$)", section.strip()))


def read(lg: Ledger, actor: Actor, project: str, work: str, mode: str = "outline", target: str | None = None,
         query: str | None = None, max_chars: int = 4000, cursor: int | None = None) -> dict:
    max_chars = max(200, min(int(max_chars or 4000), 60000))
    with lg.db.read() as conn:
        row = require_work(conn, work, project)
        doc = current_document(conn, row["id"])
    if doc and upgrade_document(lg, doc):
        with lg.db.read() as conn:
            doc = current_document(conn, row["id"])
    message = None
    if not doc:
        doc_id, message = fetch_document(lg, row["id"])
        with lg.db.read() as conn:
            doc = current_document(conn, row["id"])
    if not doc:
        csl = json.loads(row["csl"])
        out = {"work": row["citekey"], "status": "no_text", "message": message or "no full text stored"}
        if csl.get("abstract"):
            out["abstract"] = csl["abstract"]
        return out
    out = {"work": row["citekey"], "status": "ok", "source": doc["source"], "passages_total": doc["n_passages"], "chars": doc["chars"],
           "tables": sum(1 for s in json.loads(doc["outline"]) if re.search(r"›\s*Table\b", s["section"]))}
    served: list[int] = []
    with lg.db.read() as conn:
        outline = json.loads(doc["outline"])
        if mode == "outline":
            out["outline"] = [{"section": s["section"], "from": s["first"], "to": s["first"] + s["n"] - 1, "chars": s["chars"]} for s in outline]
            return out
        if mode == "search":
            if not query:
                raise ValueError("search mode needs query")
            q = fts_query(query, prefix_last=False)
            rows = conn.execute("SELECT p.* FROM passages_fts JOIN passages p ON p.id=passages_fts.rowid WHERE passages_fts MATCH ? "
                                "AND p.document_id=? ORDER BY bm25(passages_fts) LIMIT 6", (q, doc["id"])).fetchall() if q else []
            if not rows and q:  # fall back to any-word match
                q_any = " OR ".join(f'"{t}"' for t in re.findall(r"\w{3,}", query))
                rows = conn.execute("SELECT p.* FROM passages_fts JOIN passages p ON p.id=passages_fts.rowid WHERE passages_fts MATCH ? "
                                    "AND p.document_id=? ORDER BY bm25(passages_fts) LIMIT 6", (q_any, doc["id"])).fetchall() if q_any else []
            budget, items = max_chars, []
            per = max(300, max_chars // max(1, len(rows))) if rows else 0
            for r in rows:
                text = clip(r["text"], min(per, budget))
                budget -= len(text)
                items.append({"seq": r["seq"], "section": r["section"], "page": r["page"], "text": text})
                served.append(r["id"])
                if budget <= 0:
                    break
            out["matches"] = items
        else:
            if mode == "section":
                if not target:
                    raise ValueError("section mode needs target (section name or number)")
                secs = [s for s in outline if section_matches(s["section"], target)]
                if not secs:
                    out["status"] = "no_match"
                    out["message"] = f"no section matches {target!r}; sections: " + "; ".join(s["section"][:40] for s in outline[:20])
                    return out
                seqs = [i for s in secs for i in range(s["first"], s["first"] + s["n"])]
            elif mode == "passages":
                seqs = _range(target or "")
                if not seqs:
                    raise ValueError("passages mode needs target like '12-15'")
            elif mode == "full":
                seqs = list(range(1, doc["n_passages"] + 1))
            else:
                raise ValueError("mode must be outline, section, passages, search or full")
            if cursor:
                seqs = [s for s in seqs if s >= int(cursor)]
            items, used = [], 0
            next_cursor = None
            for seq in seqs:
                r = conn.execute("SELECT * FROM passages WHERE document_id=? AND seq=?", (doc["id"], seq)).fetchone()
                if not r:
                    continue
                if used + len(r["text"]) > max_chars and items:
                    next_cursor = seq
                    break
                text = r["text"] if used + len(r["text"]) <= max_chars else r["text"][: max_chars - used] + "…"
                items.append({"seq": r["seq"], "section": r["section"], "page": r["page"], "text": text})
                used += len(text)
                served.append(r["id"])
            out["passages"] = items
            out["next_cursor"] = next_cursor
    if served:  # the reading log is itself the record of what was read; a deeper read depth is journaled (bump_read)
        with lg.db.tx(actor, internal="the reading log is its own record") as tx:
            tx.execute("INSERT INTO reading_log(project,work_id,document_id,passage_ids,principal_id,at) VALUES(?,?,?,?,?,?)",
                       (project, row["id"], doc["id"], dumps(served), actor.principal_id, now()))
            in_project = tx.execute("SELECT 1 FROM project_works WHERE project=? AND work_id=? AND removed_at IS NULL",
                                    (project, row["id"])).fetchone()
            if in_project:
                level = "skimmed"
                if mode in ("section", "full"):
                    level = "sections"
                    seen = set()
                    for r in tx.execute("SELECT passage_ids FROM reading_log WHERE project=? AND document_id=?", (project, doc["id"])):
                        seen.update(json.loads(r["passage_ids"]))
                    if len(seen) >= doc["n_passages"] * 0.9:
                        level = "full"
                bump_read(tx, project, row["id"], level)
    return out


def passage_page(lg: Ledger, project: str, ref: str, start: int = 1, end: int = 40) -> dict:
    """The web reader's view: a work's current document (outline included) and passages start..end, each with the
    note anchored there, if any."""
    with lg.db.read() as conn:
        row = require_work(conn, ref, project)
        doc = current_document(conn, row["id"])
        if not doc:
            return {"document": None, "passages": []}
        rows = conn.execute("SELECT id, seq, section, page, text FROM passages WHERE document_id=? AND seq BETWEEN ? AND ? ORDER BY seq",
                            (doc["id"], start, end)).fetchall()
        notes = {r["passage_id"]: r["id"] for r in conn.execute("SELECT id, passage_id FROM notes WHERE subject=? AND project=? "
                                                               "AND passage_id IS NOT NULL", (row["id"], project))}
    return {"document": {"id": doc["id"], "source": doc["source"], "passages": doc["n_passages"], "outline": json.loads(doc["outline"])},
            "passages": [dict(r) | {"note": notes.get(r["id"])} for r in rows]}
