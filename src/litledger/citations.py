"""Citations: references extracted from stored full text are linked to works in the library (when a document is
stored, and incrementally as new works arrive), and `graph` answers references/citations of a work from that local
graph first, then from the citation sources."""
from __future__ import annotations

import json
import re
import sqlite3

from rapidfuzz import fuzz

from .db import SYSTEM, Tx, now
from .ids import STRONG_SCHEMES, Ident, parse_ident
from .knowledge import evidence_graph, resolve_node
from .ledger import Ledger
from .providers.base import Record
from .providers.http import ProviderError
from .textutil import STOPWORDS, norm_title
from .works import fuzzy_candidates, project_state, require_work, work_brief, work_by_ident, work_ids


def _ref_idents(raw: str, links: list[str]) -> list:
    out = []
    for text in links + re.findall(r"(?:arXiv[:\s]*|arxiv\.org/abs/)(\d{4}\.\d{4,5})", raw, re.I) + \
            re.findall(r"\b(10\.\d{4,9}/[^\s,;]+)", raw):
        ident = parse_ident(text if not re.match(r"^\d{4}\.\d{4,5}$", text) else "arxiv:" + text)
        if ident and ident.scheme in ("arxiv", "doi", "acl", "openreview"):
            out.append(ident)
    return out


def _ref_titles(raw: str) -> list[str]:
    """Candidate titles from a formatted reference: its sentence-like chunks, most title-like first."""
    body = re.sub(r"^\[?\d+\]?\s*", "", raw)
    chunks = [c.strip(" ,;") for c in re.split(r"(?<=[a-z0-9)\]?!])[.?!]\s+(?=[A-Z0-9“\"'])", body)]
    out = []
    m = re.search(r"\((?:19|20)\d{2}[a-z]?\)\.?\s*(.+?)[.?]\s", body)  # author-year: "Name (2020). Title."
    if m:
        out.append(m.group(1))
    for i, c in enumerate(chunks):
        if len(c) < 12 or len(c) > 300 or re.match(r"^(In|Proceedings|arXiv|URL|https?:|pp\b|volume|vol\.)", c, re.I):
            continue
        if i == 0 and (c.count(",") >= 2 or " and " in c or re.search(r"et al", c)) and len(chunks) > 1:
            continue  # author list
        out.append(re.sub(r",?\s*(?:19|20)\d{2}[a-z]?$", "", c))
    return out[:4]


def _ref_links(parsed: str | None) -> list[str]:
    try:
        return json.loads(parsed or "{}").get("links", [])
    except ValueError:
        return []


def resolve_reference(conn: sqlite3.Connection, raw: str, links: list[str]) -> str | None:
    """The local work a formatted reference points to (IDs first, then a unique fuzzy title match), else None."""
    for ident in _ref_idents(raw, links):
        row = work_by_ident(conn, ident)
        if row:
            return row["id"]
    for title in _ref_titles(raw):
        cands = fuzzy_candidates(conn, title, None, None)
        if len(cands) == 1:
            return cands[0]["id"]
    return None


def link_references(tx: Tx, work_id: str, doc_id: str) -> int:
    """Resolve one new document's extracted references against the library. No network."""
    linked = 0
    for r in tx.execute("SELECT seq, raw, parsed FROM refs_extracted WHERE document_id=? AND resolved_work_id IS NULL",
                        (doc_id,)).fetchall():
        target = resolve_reference(tx.conn, r["raw"], _ref_links(r["parsed"]))
        if target and target != work_id:
            tx.execute("UPDATE refs_extracted SET resolved_work_id=? WHERE document_id=? AND seq=?", (target, doc_id, r["seq"]))
            linked += 1
    return linked


RELINK_MARK = "relink_since"  # meta key: works updated at or after this time have not been relinked yet
RELINK_BATCH = 200


def _similar_norm(a: str, b: str) -> bool:
    """title_similarity(...) >= 0.93 for titles that are already normalised (the pre-filter for relinking)."""
    return bool(a and b) and min(fuzz.token_set_ratio(a, b), fuzz.ratio(a, b) + 15) >= 93


def _key_words(norm: str) -> list[str]:
    return [w for w in norm.split() if len(w) > 2 and w not in STOPWORDS]


def relink_all(lg: Ledger) -> int:
    """Link earlier documents' unresolved references to works that are new (or changed) since the last run.

    Incremental: only works updated since the watermark are considered, candidate references are found through an
    in-memory word index, all reads and matching happen without the write lock, and links are written in small
    batches (each only fills a reference that is still unresolved)."""
    started = now()
    since = lg.db.meta(RELINK_MARK)
    with lg.db.read() as conn:
        sql = "SELECT id, title, norm_title FROM works WHERE merged_into IS NULL"
        works = conn.execute(sql + (" AND updated_at >= ?" if since else ""), (since,) if since else ()).fetchall()
        refs = conn.execute("SELECT r.document_id, r.seq, r.raw, r.parsed, d.work_id AS citing FROM refs_extracted r "
                            "JOIN documents d ON d.id=r.document_id WHERE r.resolved_work_id IS NULL").fetchall() if works else []
        if not refs:
            lg.db.set_meta(RELINK_MARK, started)
            return 0
        new_ids = {w["id"] for w in works}
        idents: set[tuple[str, str]] = set()
        ids_list = list(new_ids)
        for i in range(0, len(ids_list), 500):
            chunk_ids = ids_list[i:i + 500]
            idents.update((r["scheme"], r["value"].lower()) for r in conn.execute(
                f"SELECT scheme, value FROM work_ids WHERE work_id IN ({','.join('?' * len(chunk_ids))})", chunk_ids))
        # Word index over the unresolved references (document frequency picks each work's rarest words).
        postings: dict[str, list[int]] = {}
        for i, r in enumerate(refs):
            for w in set(_key_words(norm_title(r["raw"]))):
                postings.setdefault(w, []).append(i)
        candidates: set[int] = set()
        for i, r in enumerate(refs):  # references naming a new work by ID
            if any((x.scheme, x.value.lower()) in idents for x in _ref_idents(r["raw"], _ref_links(r["parsed"]))):
                candidates.add(i)
        titles_of: dict[int, list[str]] = {}  # normalised candidate titles per reference, computed on demand
        for w in works:
            wt = w["norm_title"] or norm_title(w["title"])
            words = sorted({x for x in _key_words(wt) if x in postings}, key=lambda x: len(postings[x]))
            if len(words) < 2 and len(_key_words(wt)) >= 2:
                continue  # a reference with this title would contain at least two of its words
            for i in {i for x in words[:2] for i in postings[x]} - candidates:
                if i not in titles_of:
                    titles_of[i] = [norm_title(t) for t in _ref_titles(refs[i]["raw"])]
                if any(_similar_norm(t, wt) for t in titles_of[i]):
                    candidates.add(i)
        updates = []
        for i in sorted(candidates):
            r = refs[i]
            target = resolve_reference(conn, r["raw"], _ref_links(r["parsed"]))
            if target and target != r["citing"]:
                updates.append((target, r["document_id"], r["seq"]))
    linked = 0
    for i in range(0, len(updates), RELINK_BATCH):
        with lg.db.tx(SYSTEM, internal="derived: extracted references matched to works") as tx:
            for target, doc_id, seq in updates[i:i + RELINK_BATCH]:
                linked += tx.execute("UPDATE refs_extracted SET resolved_work_id=? WHERE document_id=? AND seq=? "
                                     "AND resolved_work_id IS NULL", (target, doc_id, seq)).rowcount
    lg.db.set_meta(RELINK_MARK, started)
    return linked


# ------------------------------------------------------------------------------------------------ graph
def graph(lg: Ledger, project: str, ref: str, kind: str = "references", depth: int = 1, sources: list[str] | None = None,
          limit: int = 30) -> dict:
    limit = max(1, min(int(limit or 30), 200))
    with lg.db.read() as conn:
        if kind == "evidence":
            node = resolve_node(conn, ref, project)
            return evidence_graph(conn, project, node, min(int(depth or 1), 4), limit)
        row = require_work(conn, ref, project)
        wid = row["id"]
        ids = work_ids(conn, wid)
        local: list[dict] = []
        if kind == "references":
            for r in conn.execute("SELECT r.seq, r.raw, r.parsed, r.resolved_work_id FROM refs_extracted r JOIN documents d ON d.id=r.document_id "
                                  "WHERE d.work_id=? AND d.status='ok' ORDER BY d.created_at DESC, r.seq", (wid,)):
                item = {"in_library": bool(r["resolved_work_id"]), "work_id": r["resolved_work_id"], "raw": r["raw"]}
                if not r["resolved_work_id"]:
                    idents = _ref_idents(r["raw"], json.loads(r["parsed"] or "{}").get("links", []))
                    titles = _ref_titles(r["raw"])
                    item["handle"] = idents[0].handle if idents else None
                    item["title"] = titles[0] if titles else None
                local.append(item)
            # Deduplicate across multiple documents of the same work.
            seen, uniq = set(), []
            for item in local:
                key = item["work_id"] or item["raw"][:80]
                if key not in seen:
                    seen.add(key)
                    uniq.append(item)
            local = uniq
        elif kind == "citations":
            for r in conn.execute("SELECT DISTINCT d.work_id FROM refs_extracted r JOIN documents d ON d.id=r.document_id "
                                  "WHERE r.resolved_work_id=?", (wid,)):
                ctx = conn.execute("SELECT sentence FROM citation_contexts WHERE citing_work=? AND cited_work=? LIMIT 1",
                                   (r["work_id"], wid)).fetchone()
                local.append({"in_library": True, "work_id": r["work_id"], "context": ctx["sentence"] if ctx else None})
        else:
            raise ValueError("kind must be references, citations or evidence")
        for item in local:
            if item.get("work_id"):
                w = conn.execute("SELECT * FROM works WHERE id=?", (item["work_id"],)).fetchone()
                st = project_state(conn, project, [w["id"]])[w["id"]]
                item.update(work_brief(conn, w, st))
    remote: list[Record] = []
    notes: list[str] = []
    want_remote = sources is not None or not local
    if want_remote:
        order = sources or ["s2", "openalex", "opencitations", "europepmc"]
        s2 = lg.providers.get("s2")
        if not sources and not (s2 and s2.keyed):
            order = [o for o in order if o != "s2"]  # keyless S2 only when explicitly requested
        for pid in order:
            p = lg.providers.get(pid)
            if not p or (("refs" if kind == "references" else "cites") not in p.capabilities):
                if sources:
                    notes.append(f"{pid}: unavailable")
                continue
            try:
                got = p.references(ids, limit) if kind == "references" else p.citations(ids, limit)
            except ProviderError as exc:
                notes.append(f"{pid}: {exc.kind}")
                continue
            except Exception as exc:
                notes.append(f"{pid}: error {type(exc).__name__}")
                continue
            if got:
                remote = got
                notes.append(f"via {pid}")
                break
        if not lg.providers.get("s2"):
            notes.append("degraded: Semantic Scholar off (no key)")
    items = [i for i in local]
    with lg.db.read() as conn:
        known_local = {i.get("work_id") for i in items if i.get("work_id")}
        for rec in remote:
            existing = None
            for s, v in rec.ids.items():
                if s in STRONG_SCHEMES:
                    existing = work_by_ident(conn, Ident(s, v))
                    if existing:
                        break
            if existing and existing["id"] in known_local:
                continue
            if existing:
                st = project_state(conn, project, [existing["id"]])[existing["id"]]
                items.append({"in_library": True, **work_brief(conn, existing, st)})
            else:
                items.append({"in_library": False, "handle": rec.handle, "title": rec.title, "year": rec.year,
                              "csl": {k: rec.csl.get(k) for k in ("author", "container-title") if rec.csl.get(k)},
                              "context": (rec.extra.get("contexts") or [None])[0], "intents": rec.extra.get("intents"),
                              "citation_count": rec.extra.get("citation_count")})
    return {"kind": kind, "work": row["citekey"], "items": items[:limit], "total": len(items), "notes": notes,
            "local": len(local)}
