"""Uploads and bulk import: BibTeX, CSL-JSON, RIS, ID/title lists or a litledger snapshot (every entry goes through
resolve), and full-text files attached to a work."""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import tags as tagmod
from .db import Actor
from .formats import parse_bibtex, parse_ris
from .fulltext import ingest_upload
from .ids import parse_ident
from .ledger import Ledger
from .parse import pdf_front
from .portable import import_snapshot
from .providers.base import Record
from .resolve import resolve_items
from .works import add_to_project, create_work, require_work

def detect(filename: str, text: str) -> str:
    name = filename.lower()
    stripped = text.lstrip()
    if name.endswith(".bib") or re.match(r"@\w+\s*[{(]", stripped):
        return "bibtex"
    if name.endswith(".ris") or stripped.startswith("TY  -"):
        return "ris"
    if stripped.startswith("{") and '"litledger_snapshot"' in stripped[:400]:
        return "snapshot"
    if stripped.startswith(("[", "{")):
        return "csl"
    return "list"


def import_text(lg: Ledger, actor: Actor, project: str, filename: str, text: str, tags: list[str] | None = None,
                why: str | None = None) -> dict:
    kind = detect(filename, text)
    if kind == "snapshot":
        return {"format": "snapshot", **import_snapshot(lg, actor, json.loads(text), project)}
    if kind == "bibtex":
        items = parse_bibtex(text)
    elif kind == "ris":
        items = [{"csl": c} for c in parse_ris(text)]
    elif kind == "csl":
        data = json.loads(text)
        items = [{"csl": c} for c in (data if isinstance(data, list) else data.get("items", [data]))]
    else:
        items = [line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith("#")]
    outcomes, tag_info = resolve_items(lg, actor, items, add=True, tags=tags, why=why, project=project)
    summary = {"format": kind, "entries": len(items), "added": 0, "already": 0, "new_works": 0, "problems": [],
               "conflicts": [c for o in outcomes for c in o.conflicts], "notes": [n for o in outcomes for n in o.notes]}
    for o in outcomes:
        if o.status == "ok":
            summary["added" if o.added else "already"] += 1
            summary["new_works"] += int(o.created)
        else:
            summary["problems"].append({"input": o.input[:80], "status": o.status, "message": o.message, "candidates": o.candidates})
    summary["tag_hints"] = tag_info.get("hints", {})
    return summary


_ARXIV_STAMP = re.compile(r"arXiv:\s*(\d{4}\.\d{4,5})(?:v\d+)?\s*\[")  # the margin stamp arXiv prints on its PDFs
_DOI_LABEL = re.compile(r"(?:\bdoi:\s*|https?://(?:dx\.)?doi\.org/)(10\.\d{4,9}/[^\s\"<>]+)", re.I)


def upload_ident(filename: str, front: str) -> str | None:
    """The paper's own identifier: a file named by its arXiv id or DOI, else the arXiv stamp or a labelled DOI on its
    first page."""
    stem = Path(filename).stem
    ident = parse_ident(stem.replace("_", "/", 1) if stem.startswith("10.") else stem)  # 10.1145_3292500 -> 10.1145/3292500
    if ident and ident.scheme in ("arxiv", "doi"):
        return ident.handle
    m = _ARXIV_STAMP.search(front)
    if m:
        return "arxiv:" + m.group(1)
    m = _DOI_LABEL.search(front)
    return "doi:" + m.group(1).rstrip(".,;)") if m else None


def upload(lg: Ledger, actor: Actor, project: str, filename: str, data: bytes, work: str = "", tags: list[str] | None = None,
           why: str | None = None) -> dict:
    """An uploaded file. A PDF (or an HTML/text/XML file for a named work) is that work's full text. Without a named
    work, a PDF attaches only to a sure match: its arXiv id or DOI (in the file name or printed on its first page), else
    a paper with exactly its title; otherwise a new work is made from the title, and the answer says so. Anything else
    is a bibliography."""
    is_doc = data[:5] == b"%PDF-" or filename.lower().endswith((".html", ".htm", ".txt", ".md", ".xml")) and work
    if not is_doc:
        return import_text(lg, actor, project, filename, data.decode("utf-8-sig", "replace"), tags, why)
    note = None
    if not work:
        title, front = pdf_front(data)
        title = title or Path(filename).stem.replace("_", " ")
        ident = upload_ident(filename, front)
        o = resolve_items(lg, actor, [ident or {"title": title}], add=True, tags=tags, why=why, exact_title=True)[0][0]
        work = o.work_id
        if o.status != "ok":
            with lg.db.tx(actor) as tx:
                work = create_work(tx, [Record("import", {}, {"type": "document", "title": title}, "unknown")])
                add_to_project(tx, project, work, why)
                if tags:
                    tagmod.apply(tx, project, tags, "work", [work])
            note = f"no paper matched {ident or 'this exact title'}; made a new work titled {title[:80]!r}"
    with lg.db.read() as conn:
        row = require_work(conn, work, project)
    doc_id, status = ingest_upload(lg, actor, row["id"], filename, data)
    return {"work": row["citekey"], "document": doc_id, "status": status, **({"note": note} if note else {})}
