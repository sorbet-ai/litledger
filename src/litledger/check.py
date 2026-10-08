"""Checks that return only problems: quotes against stored text, reference lists, .bib files, \\cite coverage."""
from __future__ import annotations

import json
import re
import sqlite3

from rapidfuzz import fuzz

from .csl import year_of
from .formats import entry_to_csl, parse_bibtex
from .ledger import Ledger
from .providers.base import RETRACTIONS
from .providers.http import ProviderError
from .resolve import fetch, parse_spec
from .textutil import family_name, norm_quote, norm_surname, title_similarity, venue_abbrev
from .works import merge_csl, record_to_source, require_work, resolve_ref, work_ids


_INNER_HYPHEN = re.compile(r"(?<=\w)-\s?(?=\w)")


def _quote_key(text: str) -> str:
    """norm_quote, then hyphens between word characters dropped on both sides of the comparison, so
    "state-of-the-art" matches whether the PDF line break kept, dropped or split the hyphen."""
    return _INNER_HYPHEN.sub("", norm_quote(text))


def check_quote(conn: sqlite3.Connection, work_id: str, quote: str) -> dict:
    """{'status': verified|near|not_found|no_text, 'passage_id', 'seq', 'section', 'page', 'closest': [...]}"""
    docs = conn.execute("SELECT id FROM documents WHERE work_id=? AND status='ok'", (work_id,)).fetchall()
    if not docs:
        return {"status": "no_text"}
    q = _quote_key(quote)
    if len(q) < 8:
        return {"status": "not_found", "closest": [], "message": "quote too short"}
    best: list[tuple[float, sqlite3.Row]] = []
    for d in docs:
        rows = conn.execute("SELECT id, seq, section, page, text FROM passages WHERE document_id=? ORDER BY seq", (d["id"],)).fetchall()
        normed = [_quote_key(r["text"]) for r in rows]
        joined, starts, pos = "", [], 0
        for n in normed:
            starts.append(pos)
            joined += n + " "
            pos += len(n) + 1
        idx = joined.find(q)
        if idx >= 0:
            i = max(k for k, s in enumerate(starts) if s <= idx)
            r = rows[i]
            return {"status": "verified", "passage_id": r["id"], "seq": r["seq"], "section": r["section"], "page": r["page"],
                    "document_id": d["id"], "offset": idx - starts[i]}
        for r, n in zip(rows, normed):
            score = fuzz.partial_ratio(q, n) if len(n) >= len(q) * 0.5 else 0
            if score >= 80:
                best.append((score, r))
    best.sort(key=lambda x: -x[0])
    closest = [{"seq": r["seq"], "section": r["section"], "score": round(s), "text": _window(r["text"], quote)} for s, r in best[:3]]
    return {"status": "near" if closest else "not_found", "closest": closest}


def _window(text: str, quote: str, width: int = 220) -> str:
    words = [w for w in re.findall(r"\w{5,}", quote)][:3]
    pos = 0
    for w in words:
        i = text.lower().find(w.lower())
        if i >= 0:
            pos = i
            break
    start = max(0, pos - 40)
    return ("…" if start else "") + text[start:start + width] + ("…" if start + width < len(text) else "")


def check_quotes(lg: Ledger, project: str, items: list[dict]) -> list[dict]:
    out = []
    with lg.db.read() as conn:
        for item in items:
            try:
                row = require_work(conn, item.get("work", ""), project)
            except LookupError as exc:
                out.append({"work": item.get("work"), "status": "no_match", "message": str(exc)})
                continue
            res = check_quote(conn, row["id"], item.get("quote", ""))
            out.append({"work": row["citekey"], **res})
    return out


def check_refs(lg: Ledger, project: str, refs: list[str]) -> list[dict]:
    out = []
    for raw in refs:
        spec = parse_spec(raw)
        f = fetch(lg, spec, project, refresh=False)
        entry = {"input": raw[:100], "status": f.status}
        if f.existing:
            with lg.db.read() as conn:
                row = conn.execute("SELECT citekey, title, year FROM works WHERE id=?", (f.existing,)).fetchone()
            entry.update(status="in_library", citekey=row["citekey"], title=row["title"], year=row["year"])
        elif f.status == "ok":
            best = f.records[0]
            entry.update(status="exists", handle=best.handle, title=best.title, year=best.year)
        else:
            entry.update(message=f.message, candidates=f.candidates)
        out.append(entry)
    return out


def _cmp_authors(a: list[dict], b: list[dict], truncated: bool = False) -> str | None:
    if not a or not b:
        return None
    fa, fb = norm_surname(family_name(a[0])), norm_surname(family_name(b[0]))
    if fa and fb and fa != fb:
        return f"first author {family_name(a[0])} vs {family_name(b[0])}"
    if abs(len(a) - len(b)) > 1 and not truncated:
        return f"{len(a)} authors vs {len(b)}"
    return None


def check_bib(lg: Ledger, project: str, content: str) -> dict:
    entries = parse_bibtex(content)
    problems, ok = [], 0
    crossref = lg.providers.get("crossref")
    for entry in entries:
        key = entry.get("ID", "?")
        csl, ids = entry_to_csl(entry)
        spec = parse_spec(entry)
        spec.import_record = None  # verify against providers, never against the entry itself
        f = fetch(lg, spec, project, refresh=False)
        issues = list(f.conflicts)  # e.g. "doi:10.x belongs to smith2020foo (title differs)": the entry's ID is wrong
        ref = None
        if f.existing:
            with lg.db.read() as conn:
                row = conn.execute("SELECT * FROM works WHERE id=?", (f.existing,)).fetchone()
                ref_csl, ref_ids, ref_title, ref_year = json.loads(row["csl"]), work_ids(conn, row["id"]), row["title"], row["year"]
                published = conn.execute("SELECT label FROM versions WHERE work_id=? AND kind='published'", (row["id"],)).fetchone()
            ref = row["citekey"]
        elif f.status == "ok" and f.records:
            recs = [dict(provider=r.provider, **record_to_source(r)) for r in f.records]
            ref_csl, _ = merge_csl(recs)
            ref_ids = {}
            for r in f.records:
                for s, v in r.ids.items():
                    ref_ids.setdefault(s, v)
            ref_title, ref_year = ref_csl.get("title", ""), year_of(ref_csl)
            published = any(r.kind == "published" for r in f.records)
        else:
            problems.append({"key": key, "issue": "; ".join(issues + [f"not found ({f.status}{': ' + f.message if f.message else ''})"]),
                             "candidates": f.candidates})
            continue
        if csl.get("title") and title_similarity(csl["title"], ref_title) < 0.9:
            issues.append(f"title differs: {ref_title!r}")
        year = year_of(csl)
        if year and ref_year and abs(year - int(ref_year)) > 1 and not ids.get("arxiv"):
            issues.append(f"year {year} vs {ref_year}")
        author_issue = _cmp_authors(csl.get("author") or [], ref_csl.get("author") or [],
                                    truncated=bool(re.search(r"\band\s+others\b", entry.get("author", ""), re.I)))
        if author_issue:
            issues.append(author_issue)
        is_arxiv_entry = bool(ids.get("arxiv")) and (csl.get("container-title") in (None, "", "arXiv") or "arxiv" in (csl.get("container-title") or "").lower())
        if is_arxiv_entry and published:
            label = venue_abbrev(ref_csl.get("container-title") or "", ref_year) or "a venue"
            issues.append(f"published version exists: {label}")
        doi = ids.get("doi") or ref_ids.get("doi")
        if crossref and doi:
            try:
                for upd in crossref.updates(doi):
                    if (upd.get("type") or "").lower() in RETRACTIONS:
                        issues.append(f"{upd['type']} notice {upd['notice_doi']}")
            except ProviderError:
                pass
        if issues:
            problems.append({"key": key, "library": ref, "issue": "; ".join(issues)})
        else:
            ok += 1
    return {"entries": len(entries), "ok": ok, "problems": problems}


CITE_RE = re.compile(r"\\(?:[a-zA-Z]*cite[a-zA-Z]*|nocite)\*?(?:\[[^\]]*\]){0,2}\{([^}]*)\}")


def check_tex(lg: Ledger, project: str, tex: str, bib: str | None = None) -> dict:
    keys: list[str] = []
    for m in CITE_RE.finditer(tex):
        for k in m.group(1).split(","):
            k = k.strip()
            if k and k != "*" and k not in keys:
                keys.append(k)
    bib_keys = {e["ID"] for e in parse_bibtex(bib)} if bib else set()
    unknown, in_bib_only, ok = [], [], 0
    with lg.db.read() as conn:
        known = {}
        for k in keys:
            row = resolve_ref(conn, k, project)
            if row:
                known[k] = row["id"]
                ok += 1
            elif k in bib_keys:
                in_bib_only.append(k)
            else:
                unknown.append(k)
        in_project = {r["work_id"] for r in conn.execute("SELECT work_id FROM project_works WHERE project=? AND removed_at IS NULL", (project,))}
        not_in_project = [k for k, w in known.items() if w not in in_project]
    return {"cited": len(keys), "in_library": ok, "unknown": unknown, "bib_only": in_bib_only,
            "not_in_project": not_in_project, "unused_bib": sorted(bib_keys - set(keys)) if bib else []}
