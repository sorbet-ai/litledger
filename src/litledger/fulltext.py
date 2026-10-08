"""Full text: fetch a work's text from where enabled sources say it is (arXiv HTML first), keep the original in the
blob store, store it as anchored passages with its reference list, re-parse documents an older parser stored, and
attach uploads."""
from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import sqlite3
import threading
from typing import Iterator

from .citations import link_references
from .db import SYSTEM, Actor, Tx, now, ulid
from .ledger import Ledger
from .parse import PARSER_VERSION, Parsed, file_kind, parse, parse_latex_bbl
from .providers.base import FULLTEXT, Location
from .providers.http import ProviderError
from .works import work_ids

BROWSER_UA = "Mozilla/5.0 (compatible; litledger; self-hosted literature ledger)"
MAX_BYTES = 60 * 1024 * 1024
log = logging.getLogger("litledger.fulltext")


# ------------------------------------------------------------------------------------------------ per-work locks
_work_locks: dict[str, list] = {}  # work id -> [lock, holders]
_work_locks_guard = threading.Lock()


@contextlib.contextmanager
def work_lock(work_id: str):
    """Serialise fetching/upgrading one work's full text within this process (no duplicate documents)."""
    with _work_locks_guard:
        entry = _work_locks.setdefault(work_id, [threading.Lock(), 0])
        entry[1] += 1
    try:
        with entry[0]:
            yield
    finally:
        with _work_locks_guard:
            entry[1] -= 1
            if entry[1] == 0:
                _work_locks.pop(work_id, None)


# ------------------------------------------------------------------------------------------------ blobs
def store_blob(lg: Ledger, data: bytes) -> str:
    """Keep an original (PDF, HTML, …) under its SHA-256, so documents can be re-parsed later."""
    digest = hashlib.sha256(data).hexdigest()
    path = lg.settings.blob_dir / digest[:2] / digest[2:4] / digest
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return digest


def read_blob(lg: Ledger, digest: str) -> bytes:
    return (lg.settings.blob_dir / digest[:2] / digest[2:4] / digest).read_bytes()


# ------------------------------------------------------------------------------------------------ fetching
def _locations(lg: Ledger, work: sqlite3.Row, ids: dict[str, str]) -> Iterator[Location]:
    """Where the text may be, best first: what enabled full-text sources offer (in registry order, so arXiv's HTML
    leads), PDF links the work's metadata records carry, then its own web page. Lazy: a source is only asked when
    every earlier location failed."""
    seen: set[str] = set()

    def fresh(locs):
        for loc in locs:
            if loc.url.startswith("http") and loc.url not in seen:
                seen.add(loc.url)
                yield loc

    for p in lg.providers.enabled(FULLTEXT):
        try:
            locs = p.locations(ids)
        except Exception:  # a source that is down (or offers no locations) is just skipped
            continue
        yield from fresh(locs)
    with lg.db.read() as conn:
        sources = conn.execute("SELECT provider, data FROM work_sources WHERE work_id=?", (work["id"],)).fetchall()
    for r in sources:
        extra = json.loads(r["data"]).get("extra", {})
        yield from fresh(Location(extra[key], "pdf", r["provider"]) for key in ("oa_pdf", "pdf_url", "pdf") if extra.get(key))
    url = (json.loads(work["csl"]).get("URL") if work["type"] in ("webpage", "software") else None) or ids.get("url")
    if url:
        yield from fresh([Location(url, "html", "web")])


def fetch_document(lg: Ledger, work_id: str) -> tuple[str | None, str]:
    """Fetch and parse full text for a work. Returns (document_id, message).

    Concurrent calls for one work wait for each other; whoever comes second finds the stored document."""
    with work_lock(work_id):
        with lg.db.read() as conn:
            doc = current_document(conn, work_id)
            scanned = conn.execute("SELECT 1 FROM documents WHERE work_id=? AND status='scanned' LIMIT 1", (work_id,)).fetchone()
        if doc:
            return doc["id"], f"{doc['source']} (already stored)"
        if scanned:
            return None, "only a scanned PDF (no text layer) was found; upload a PDF with a text layer with `litledger upload`"
        return _fetch_document(lg, work_id)


def _fetch_document(lg: Ledger, work_id: str) -> tuple[str | None, str]:
    with lg.db.read() as conn:
        work = conn.execute("SELECT * FROM works WHERE id=?", (work_id,)).fetchone()
        ids = work_ids(conn, work_id)
    if not work:
        return None, "unknown work"
    tried = []
    for loc in _locations(lg, work, ids):
        try:
            # ttl=0: full text is not kept in the response cache; the blob store keeps what was parsed.
            resp = lg.http.get("fulltext:" + loc.provider, loc.url, headers={"User-Agent": BROWSER_UA}, ttl=0,
                               interval=3.1 if loc.provider == "arxiv" else 1.0, max_bytes=MAX_BYTES)
        except ProviderError as exc:
            tried.append(f"{loc.kind}:{exc.kind}" + (f" ({exc.message})" if exc.kind == "blocked" else ""))
            continue
        ctype = resp.headers.get("content-type", "")
        data = resp.content
        if loc.kind == "arxiv_html":
            if "html" not in ctype or b"ltx_page_main" not in data and b"ltx_document" not in data:
                tried.append("arxiv_html:none")
                continue
            kind = "arxiv_html"
        elif loc.kind == "jats":
            kind = "jats"
        elif data[:5] == b"%PDF-":
            kind = "pdf"
        elif "html" in ctype and loc.kind != "pdf":
            kind = "webpage"
        else:
            tried.append("pdf:html_landing" if "html" in ctype else f"{loc.kind}:unsupported {ctype[:30]}")
            continue
        try:
            parsed = parse(data, kind)
        except Exception as exc:
            tried.append(f"{kind}:parse_error {type(exc).__name__}")
            continue
        if parsed.status != "ok" and kind == "pdf":
            tried.append("pdf:" + parsed.status)
            if parsed.status == "scanned":
                return store_document(lg, work_id, parsed, kind, store_blob(lg, data)), "scanned PDF: no text layer"
            continue
        if kind == "arxiv_html" and ids.get("arxiv") and not parsed.references:
            parsed.references = _bbl_refs(lg, ids["arxiv"])
        return store_document(lg, work_id, parsed, kind, store_blob(lg, data)), f"{kind} via {loc.provider}"
    return None, "no full text found (" + ", ".join(tried or ["no locations"]) + "); upload a PDF with `litledger upload`"


def _bbl_refs(lg: Ledger, arxiv_id: str) -> list[dict]:
    """The exact reference list from the paper's LaTeX source, when its HTML had none."""
    try:
        resp = lg.http.get("fulltext:arxiv", f"https://arxiv.org/e-print/{arxiv_id}", ttl=0, interval=3.1, max_bytes=MAX_BYTES)
        return parse_latex_bbl(resp.content)
    except ProviderError:
        return []


def store_document(lg: Ledger, work_id: str, parsed: Parsed, source: str, digest: str | None,
                   actor: Actor = SYSTEM) -> str:
    doc_id = "d" + ulid()
    outline: list[dict] = []
    for i, p in enumerate(parsed.passages):
        if not outline or outline[-1]["section"] != p["section"]:
            outline.append({"section": p["section"], "first": i + 1, "n": 0, "chars": 0})
        outline[-1]["n"] += 1
        outline[-1]["chars"] += len(p["text"])
    with lg.db.tx(actor) as tx:
        previous = current_document(tx.conn, work_id)
        tx.execute("INSERT INTO documents(id,work_id,source,blob_sha256,parser,parser_version,status,outline,n_passages,chars,"
                   "created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                   (doc_id, work_id, source, digest, source, PARSER_VERSION, parsed.status, json.dumps(outline),
                    len(parsed.passages), sum(len(p["text"]) for p in parsed.passages), now()))
        # Passage search covers each work's current document only: index this one if it becomes current and
        # drop the one it replaces from the index (its passages stay, so notes keep their anchors).
        now_current = current_document(tx.conn, work_id)
        index = bool(now_current) and now_current["id"] == doc_id
        if index and previous:
            unindex_document(tx, previous["id"])
        offset = 0
        passage_ids = []
        for i, p in enumerate(parsed.passages):
            cur = tx.execute("INSERT INTO passages(document_id,seq,section,page,char_start,char_end,text) VALUES(?,?,?,?,?,?,?)",
                             (doc_id, i + 1, p["section"], p.get("page"), offset, offset + len(p["text"]), p["text"]))
            passage_ids.append(cur.lastrowid)
            if index:
                tx.execute("INSERT INTO passages_fts(rowid, text, section) VALUES(?,?,?)", (cur.lastrowid, p["text"], p["section"]))
            offset += len(p["text"]) + 1
        anchor_to_seq = {}
        for i, r in enumerate(parsed.references):
            tx.execute("INSERT INTO refs_extracted(document_id,seq,label,raw,parsed) VALUES(?,?,?,?,?)",
                       (doc_id, i + 1, r.get("label"), r["raw"][:2000], json.dumps({"links": r.get("links", [])[:5]})))
            if r.get("anchor"):
                anchor_to_seq[r["anchor"]] = i + 1
        link_references(tx, work_id, doc_id)
        for c in parsed.cites:
            seq = anchor_to_seq.get(c["anchor"])
            if not seq:
                continue
            ref = tx.execute("SELECT resolved_work_id FROM refs_extracted WHERE document_id=? AND seq=?", (doc_id, seq)).fetchone()
            if ref and ref["resolved_work_id"] and ref["resolved_work_id"] != work_id:
                tx.execute("INSERT OR IGNORE INTO citation_contexts VALUES(?,?,?,?,?)",
                           (work_id, ref["resolved_work_id"], doc_id, passage_ids[c["passage_index"]], c["sentence"][:600]))
        tx.log("document.create", {"work": work_id, "document": doc_id, "source": source, "passages": len(parsed.passages),
                                   "references": len(parsed.references), "status": parsed.status}, project="")
    return doc_id


def unindex_document(tx: Tx, doc_id: str) -> None:
    """Remove a document's passages from passage search (external-content FTS needs the indexed values)."""
    tx.execute("INSERT INTO passages_fts(passages_fts, rowid, text, section) "
               "SELECT 'delete', id, text, section FROM passages WHERE document_id=?", (doc_id,))


def current_document(conn: sqlite3.Connection, work_id: str) -> sqlite3.Row | None:
    rank = "CASE source WHEN 'arxiv_html' THEN 0 WHEN 'jats' THEN 1 WHEN 'upload' THEN 2 WHEN 'pdf' THEN 3 ELSE 4 END"
    return conn.execute(f"SELECT * FROM documents WHERE work_id=? AND status='ok' ORDER BY {rank}, created_at DESC LIMIT 1",
                        (work_id,)).fetchone()


# ------------------------------------------------------------------------------------------------ re-parsing
_upgrade_failed: set[str] = set()  # documents whose re-parse failed in this process (not retried on every read)


def upgrade_document(lg: Ledger, doc: sqlite3.Row) -> str | None:
    """Re-parse a document stored by an older parser from its saved original. Old passages stay (notes keep anchors).

    Never raises: a failed re-parse is logged and the stored parse keeps being served."""
    if not doc or doc["parser_version"] == PARSER_VERSION or not doc["blob_sha256"] or doc["id"] in _upgrade_failed:
        return None
    with work_lock(doc["work_id"]):
        with lg.db.read() as conn:  # another reader may have upgraded it while we waited
            done = conn.execute("SELECT id FROM documents WHERE work_id=? AND blob_sha256=? AND parser_version=? AND status='ok' "
                                "ORDER BY created_at DESC LIMIT 1", (doc["work_id"], doc["blob_sha256"], PARSER_VERSION)).fetchone()
        if done:
            return done["id"]
        try:
            return _upgrade_document(lg, doc)
        except Exception as exc:
            _upgrade_failed.add(doc["id"])
            log.warning("re-parsing document %s (%s) failed; serving the stored parse: %s", doc["id"], doc["source"], exc)
            return None


def _upgrade_document(lg: Ledger, doc: sqlite3.Row) -> str | None:
    try:
        data = read_blob(lg, doc["blob_sha256"])
    except OSError:
        return None
    source = doc["source"]
    kind = "pdf" if data[:5] == b"%PDF-" else source if source in ("arxiv_html", "jats", "webpage") else None
    if not kind:
        return None
    parsed = parse(data, kind)
    if parsed.status != "ok":
        return None
    if source == "arxiv_html" and not parsed.references:
        with lg.db.read() as conn:
            parsed.references = [{"label": r["label"], "raw": r["raw"], "links": json.loads(r["parsed"] or "{}").get("links", [])}
                                 for r in conn.execute("SELECT * FROM refs_extracted WHERE document_id=? ORDER BY seq", (doc["id"],))]
    return store_document(lg, doc["work_id"], parsed, source, doc["blob_sha256"])


def ingest_upload(lg: Ledger, actor: Actor, work_id: str, filename: str, data: bytes) -> tuple[str, str]:
    parsed = parse(data, file_kind(filename, data))
    doc_id = store_document(lg, work_id, parsed, "upload", store_blob(lg, data), actor)
    return doc_id, parsed.status
