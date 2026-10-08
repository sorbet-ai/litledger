"""Works: canonical records, identifier index, per-provider sources, merged CSL, citekeys, projects."""
from __future__ import annotations

import contextlib
import hashlib
import json
import re
import sqlite3
from contextvars import ContextVar
from typing import Any

from . import tags as tagmod
from .csl import year_of
from .db import Tx, dumps, now, ulid
from .ids import STRONG_SCHEMES, Ident, is_work_id, parse_ident, safe_url
from .providers.base import Record
from .render import venue
from .textutil import ascii_fold, family_name, first_significant_word, norm_surname, norm_title, title_similarity

READ_LEVELS = ["metadata", "abstract", "skimmed", "sections", "full"]
SAME_TITLE = 0.9  # normalised title similarity two records need before they may describe one work

# Field authority (first wins). Venue fields prefer published records; see merge_csl.
CORE_ORDER = ["agent", "arxiv", "crossref", "datacite", "s2", "dblp", "openreview", "web", "europepmc", "pubmed",
              "biorxiv", "inspire", "ads", "zenodo", "openalex", "core", "opencitations", "hf", "import"]
ABSTRACT_ORDER = ["agent", "arxiv", "s2", "crossref", "europepmc", "openreview", "web", "inspire", "ads", "openalex",
                  "biorxiv", "datacite", "zenodo", "hf", "core", "import"]
VENUE_ORDER = ["agent", "dblp", "crossref", "web", "openreview", "acl", "europepmc", "pubmed", "inspire", "ads", "s2",
               "openalex", "datacite", "zenodo", "import", "opencitations", "core", "biorxiv", "arxiv", "hf"]
VENUE_FIELDS = ("container-title", "event-title", "volume", "issue", "page", "publisher", "DOI", "ISSN", "ISBN")
CORE_FIELDS = ("title", "author", "URL", "number", "version", "language")


# ------------------------------------------------------------------------------------------------ lookup
def work_by_id(conn: sqlite3.Connection, work_id: str) -> sqlite3.Row | None:
    for _ in range(10):  # follow merges
        row = conn.execute("SELECT * FROM works WHERE id=?", (work_id,)).fetchone()
        if not row or not row["merged_into"]:
            return row
        work_id = row["merged_into"]
    return None


def work_by_ident(conn: sqlite3.Connection, ident: Ident) -> sqlite3.Row | None:
    row = conn.execute("SELECT work_id FROM work_ids WHERE scheme=? AND value=?", (ident.scheme, ident.value)).fetchone()
    return work_by_id(conn, row["work_id"]) if row else None


def resolve_ref(conn: sqlite3.Connection, ref: str, project: str | None = None) -> sqlite3.Row | None:
    """A work reference as agents write it: citekey (global or project override), work id, or any identifier."""
    ref = (ref or "").strip()
    if not ref:
        return None
    if project:  # a project's own citekey wins over a global key minted later
        pw = conn.execute("SELECT work_id FROM project_works WHERE project=? AND citekey_override=?", (project, ref)).fetchone()
        if pw:
            return work_by_id(conn, pw["work_id"])
    row = conn.execute("SELECT * FROM works WHERE citekey=?", (ref,)).fetchone()
    if row:
        return work_by_id(conn, row["id"])
    if is_work_id(ref):
        return work_by_id(conn, ref)
    ident = parse_ident(ref)
    return work_by_ident(conn, ident) if ident else None


def work_ids(conn: sqlite3.Connection, work_id: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for r in conn.execute("SELECT scheme, value, trust FROM work_ids WHERE work_id=? ORDER BY trust, scheme", (work_id,)):
        if r["trust"] == "high" or r["scheme"] not in out:
            out.setdefault(r["scheme"], r["value"])
    return out


def fuzzy_candidates(conn: sqlite3.Connection, title: str, first_author: str | None, year: int | None,
                     ids: dict[str, str] | None = None) -> list[sqlite3.Row]:
    """Local works that look like the same paper (title ≥0.93, author and year compatible, no conflicting IDs)."""
    nt = norm_title(title)
    if len(nt) < 8:
        return []
    words = nt.split()
    prefix = " ".join(words[:3])
    rows = conn.execute("SELECT * FROM works WHERE merged_into IS NULL AND norm_title LIKE ? LIMIT 50",
                        (prefix.replace("%", "") + "%",)).fetchall()
    if not rows and len(words) > 3:
        rows = conn.execute("SELECT * FROM works WHERE merged_into IS NULL AND norm_title LIKE ? LIMIT 50",
                            ("%" + " ".join(words[1:4]) + "%",)).fetchall()
    out = []
    for row in rows:
        if title_similarity(title, row["title"]) < 0.93:
            continue
        if first_author and row["first_author"] and norm_surname(first_author) != row["first_author"]:
            continue
        if year and row["year"] and abs(int(year) - int(row["year"])) > 2:
            continue
        if ids and _conflicting(work_ids(conn, row["id"]), ids):
            continue
        out.append(row)
    return out


def surnames(csl: dict) -> list[str]:
    return [s for s in (norm_surname(family_name(a)) for a in (csl.get("author") or [])[:12]) if s]


def incompatible(title_a: str, authors_a: list[str], year_a: int | None,
                 title_b: str, authors_b: list[str], year_b: int | None) -> str | None:
    """Why two descriptions cannot be one work ('title differs', 'authors differ', 'year differs'), or None.
    Unknown fields never conflict; author order may change between versions, so any first author among the other's
    authors is enough."""
    if title_a and title_b and title_similarity(title_a, title_b) < SAME_TITLE:
        return "title differs"
    if authors_a and authors_b and authors_a[0] not in authors_b and authors_b[0] not in authors_a:
        return "authors differ"
    if year_a and year_b and abs(int(year_a) - int(year_b)) > 3:
        return "year differs"
    return None


def work_conflict(conn: sqlite3.Connection, row: sqlite3.Row, title: str, authors: list[str], year: int | None) -> str | None:
    """incompatible() between a stored work and a description of a record."""
    wtitle = row["title"] if row["title"] not in ("(pending)", "(untitled)") else ""
    return incompatible(wtitle, surnames(json.loads(row["csl"] or "{}")), row["year"], title, authors, year)


def record_conflict(a: Record, b: Record) -> str | None:
    return incompatible(a.title, surnames(a.csl), a.year, b.title, surnames(b.csl), b.year)


def not_duplicate(conn: sqlite3.Connection, a: str, b: str) -> bool:
    return bool(conn.execute("SELECT 1 FROM not_duplicates WHERE (a=? AND b=?) OR (a=? AND b=?)", (a, b, b, a)).fetchone())


def _conflicting(a: dict[str, str], b: dict[str, str]) -> bool:
    """Two different IDs of one exclusive scheme mean different works (e.g. two arXiv ids). DOIs and DBLP keys are not
    exclusive: a preprint and its venue version may each have one."""
    return any(a.get(s) and b.get(s) and a[s] != b[s] for s in ("arxiv", "pmid", "corpusid"))


# The projects the current request may see works of: None (everything) unless the caller is limited to some projects
# (docs/ACCESS.md §3). Set once per request by the auth middleware and by tools.call, so every lookup of a work an
# agent names (require_work) and every library-wide search honours it without each function taking the actor.
VISIBLE: ContextVar[tuple[str, ...] | None] = ContextVar("litledger_visible_projects", default=None)


@contextlib.contextmanager
def limited_to(projects: tuple[str, ...] | None):
    token = VISIBLE.set(tuple(projects) if projects is not None else None)
    try:
        yield
    finally:
        VISIBLE.reset(token)


def visible_sql(column: str = "w.id") -> tuple[str, list]:
    """SQL restricting works to those the current request may see ('' when unlimited): works that are, or were, in
    one of its projects."""
    allowed = VISIBLE.get()
    if allowed is None:
        return "", []
    return (f"{column} IN (SELECT work_id FROM project_works WHERE project IN ({','.join('?' * len(allowed)) or 'NULL'}))",
            list(allowed))


def visible(conn: sqlite3.Connection, work_id: str) -> bool:
    cond, params = visible_sql("?")
    return not cond or bool(conn.execute(f"SELECT {cond}", (work_id, *params)).fetchone()[0])


def require_work(conn: sqlite3.Connection, ref: str, project: str) -> sqlite3.Row:
    """The work an agent names. A caller limited to some projects only reaches works in them; others look unknown
    (capturing one with resolve adds it to the project)."""
    row = resolve_ref(conn, ref, project)
    if not row or not visible(conn, row["id"]):
        raise LookupError(f"unknown work {ref!r} — capture it first with resolve(add=true)")
    return row


# ------------------------------------------------------------------------------------------------ merge logic
def provider_family(provider: str) -> str:
    """'import:3f2a…' (one source row per imported input) -> 'import'."""
    return provider.split(":", 1)[0]


def _records(conn: sqlite3.Connection, work_id: str) -> list[dict]:
    # Oldest first, so among equal-rank sources (two imports) the first one stays canonical.
    return [dict(provider=r["provider"], **json.loads(r["data"]))
            for r in conn.execute("SELECT provider, data FROM work_sources WHERE work_id=? ORDER BY fetched_at, provider",
                                  (work_id,))]


def _pick(records: list[dict], order: list[str], field: str, published_only: bool = False) -> Any:
    rank = {p: i for i, p in enumerate(order)}
    candidates = [r for r in records if r["csl"].get(field) and not (published_only and r["kind"] not in ("published",))
                  and not (r.get("extra", {}).get("low_trust") and field in VENUE_FIELDS)
                  and not (field == "URL" and not safe_url(r["csl"][field]))]
    candidates.sort(key=lambda r: rank.get(provider_family(r["provider"]), len(order)))
    return candidates[0]["csl"][field] if candidates else None


def merge_csl(records: list[dict]) -> tuple[dict, list[dict]]:
    """Merged CSL plus the version list, from all provider records of one work."""
    csl: dict = {}
    published = [r for r in records if r["kind"] == "published" and not r.get("extra", {}).get("low_trust")]
    software = [r for r in records if r["kind"] in ("software", "webpage")]
    for field in CORE_FIELDS:
        value = _pick(records, CORE_ORDER, field)
        if value:
            csl[field] = value
    abstract = _pick(records, ABSTRACT_ORDER, "abstract")
    if abstract:
        csl["abstract"] = abstract
    if published:
        for field in VENUE_FIELDS:
            value = _pick(records, VENUE_ORDER, field, published_only=True)
            if value:
                csl[field] = value
        csl["type"] = _pick(records, VENUE_ORDER, "type", published_only=True) or "article-journal"
        csl["issued"] = _pick(records, VENUE_ORDER, "issued", published_only=True) or _pick(records, CORE_ORDER, "issued")
        if csl["type"] == "article":
            csl["type"] = "article-journal"
        if csl["type"] == "article-journal" and re.search(
                r"proceedings|conference|workshop|symposium|advances in neural information|\b(neurips|icml|iclr|cvpr|"
                r"iccv|eccv|acl|emnlp|naacl|aaai|ijcai|colm|aistats|uai|corl)\b", str(csl.get("container-title", "")), re.I):
            csl["type"] = "paper-conference"
    else:
        for field in VENUE_FIELDS:
            value = _pick(records, CORE_ORDER, field)
            if value and field != "DOI":
                csl[field] = value
        csl["type"] = (software[0]["csl"].get("type") if software else None) or _pick(records, CORE_ORDER, "type") or "article"
        csl["issued"] = _earliest(records) or _pick(records, CORE_ORDER, "issued")
    csl = {k: v for k, v in csl.items() if v}
    # Versions: one preprint row per preprint source family, one per published venue.
    versions, seen = [], set()
    for r in records:
        ids = r.get("ids", {})
        if r["kind"] == "preprint" and ids.get("arxiv"):
            key = ("arxiv", ids["arxiv"])
            label = f"arXiv {r.get('version') or ''}".strip()
        elif r["kind"] == "published":
            venue = r["csl"].get("container-title") or r["csl"].get("event-title") or r["provider"]
            key = ("pub", norm_title(venue)[:40], year_of(r["csl"]))
            label = f"{venue} {year_of(r['csl']) or ''}".strip()
        else:
            continue
        if key in seen:
            continue
        seen.add(key)
        versions.append({"kind": r["kind"], "label": label, "ids": ids,
                         "date": ".".join(str(p) for p in ((r["csl"].get("issued") or {}).get("date-parts") or [[None]])[0] if p)})
    return csl, versions


def _earliest(records: list[dict]) -> dict | None:
    dated = [(r["csl"]["issued"]["date-parts"][0], r["csl"]["issued"]) for r in records
             if (r["csl"].get("issued") or {}).get("date-parts") and r["csl"]["issued"]["date-parts"][0]]
    if not dated:
        return None
    dated.sort(key=lambda d: [int(x) for x in d[0]] + [99] * (3 - len(d[0])))
    return dated[0][1]


# ------------------------------------------------------------------------------------------------ citekeys
def make_citekey(conn: sqlite3.Connection, csl: dict, records: list[dict]) -> str:
    authors = csl.get("author") or []
    base = norm_surname(family_name(authors[0])) if authors else ""
    if not base:
        if csl.get("type") in ("software", "webpage"):
            base = re.sub(r"[^a-z0-9]", "", ascii_fold(str(csl.get("container-title") or csl.get("publisher") or "web")).lower())[:12]
        base = base or "anon"
    years = [y for y in (year_of(r["csl"]) for r in records) if y] or [year_of(csl)]
    year = min(y for y in years if y) if any(years) else None
    key = f"{base}{year or 'nd'}{first_significant_word(csl.get('title', ''))}"[:60]
    candidate, n = key, 0
    while conn.execute("SELECT 1 FROM works WHERE citekey=?", (candidate,)).fetchone() or \
            conn.execute("SELECT 1 FROM project_works WHERE citekey_override=? LIMIT 1", (candidate,)).fetchone():
        n += 1
        candidate = key + "abcdefghijklmnopqrstuvwxyz"[(n - 1) % 26] * (1 + (n - 1) // 26)
    return candidate


# ------------------------------------------------------------------------------------------------ writes
def record_to_source(rec: Record) -> dict:
    return {"ids": rec.ids, "csl": rec.csl, "kind": rec.kind, "version": rec.version,
            "extra": {k: v for k, v in rec.extra.items() if v not in (None, [], {}, "")}}


def source_key(rec: Record) -> str:
    """Provider records keep one row per provider; user-supplied (import) records one row per distinct input, so a
    later import can never overwrite an earlier one or a provider's metadata."""
    if rec.provider != "import":
        return rec.provider
    return "import:" + hashlib.sha256(dumps([rec.ids, rec.csl]).encode()).hexdigest()[:10]


def store_sources(tx: Tx, work_id: str, records: list[Record]) -> None:
    for rec in records:
        provider = source_key(rec)
        # Keep one source row per provider, but a provider can describe two versions (DBLP CoRR + venue).
        existing = tx.execute("SELECT data FROM work_sources WHERE work_id=? AND provider=?", (work_id, provider)).fetchone()
        if existing and provider == "dblp":
            old = json.loads(existing["data"])
            if old.get("kind") == "published" and rec.kind != "published":
                continue
        tx.execute("INSERT INTO work_sources VALUES(?,?,?,?) ON CONFLICT(work_id, provider) DO UPDATE SET data=excluded.data, "
                   "fetched_at=excluded.fetched_at", (work_id, provider, dumps(record_to_source(rec)), now()))


def add_ids(tx: Tx, work_id: str, ids: dict[str, str], trust: str = "high") -> list[str]:
    """Index identifiers; returns IDs that already belong to a different work (merge candidates)."""
    conflicts = []
    for scheme, value in ids.items():
        if not value:
            continue
        row = tx.execute("SELECT work_id, trust FROM work_ids WHERE scheme=? AND value=?", (scheme, value)).fetchone()
        if row and row["work_id"] != work_id:
            target = work_by_id(tx.conn, row["work_id"])
            if target and target["id"] != work_id:
                conflicts.append(target["id"])
            continue
        if row and trust == "high" and row["trust"] != "high":
            tx.execute("UPDATE work_ids SET trust='high' WHERE scheme=? AND value=?", (scheme, value))
        if not row:
            tx.execute("INSERT INTO work_ids(scheme,value,work_id,trust) VALUES(?,?,?,?)", (scheme, value, work_id, trust))
    return sorted(set(conflicts))


def refresh_work(tx: Tx, work_id: str) -> dict:
    """Recompute merged CSL, versions, IDs and search index from the stored provider records."""
    records = _records(tx.conn, work_id)
    csl, versions = merge_csl(records)
    provider_ids: dict[str, set[str]] = {}
    for r in records:
        if provider_family(r["provider"]) != "import" and not r.get("extra", {}).get("low_trust"):
            for k, v in r.get("ids", {}).items():
                provider_ids.setdefault(k, set()).add(v)
    for r in records:
        trust = "low" if r.get("extra", {}).get("low_trust") else "high"
        ids = {k: v for k, v in r.get("ids", {}).items() if k != "url"}
        if provider_family(r["provider"]) == "import":
            # A user-supplied ID a provider contradicts is not indexed; one no provider confirms is low trust.
            ids = {k: v for k, v in ids.items() if not provider_ids.get(k) or v in provider_ids[k]}
            add_ids(tx, work_id, {k: v for k, v in ids.items() if v in provider_ids.get(k, ())}, "high")
            add_ids(tx, work_id, {k: v for k, v in ids.items() if v not in provider_ids.get(k, ())}, "low")
            continue
        add_ids(tx, work_id, ids, trust)
    url = safe_url(csl.get("URL"))
    if url and not any(r.get("ids", {}).get(s) for r in records for s in STRONG_SCHEMES):
        add_ids(tx, work_id, {"url": url.rstrip("/")})
    authors = csl.get("author") or []
    first = norm_surname(family_name(authors[0])) if authors else ""
    tx.execute("UPDATE works SET type=?, title=?, csl=?, year=?, norm_title=?, first_author=?, updated_at=? WHERE id=?",
               (csl.get("type", "article"), csl.get("title", "(untitled)"), dumps(csl), year_of(csl),
                norm_title(csl.get("title", "")), first, now(), work_id))
    tx.execute("DELETE FROM versions WHERE work_id=?", (work_id,))
    for v in versions:
        tx.execute("INSERT INTO versions VALUES(?,?,?,?,?,?)", ("v" + ulid(), work_id, v["kind"], v["label"],
                                                                dumps(v["ids"]), v["date"] or None))
    index_work(tx.conn, work_id)
    return csl


def index_work(conn: sqlite3.Connection, work_id: str) -> None:
    row = conn.execute("SELECT * FROM works WHERE id=?", (work_id,)).fetchone()
    conn.execute("DELETE FROM works_fts WHERE work_id=?", (work_id,))
    if not row or row["merged_into"]:
        return
    csl = json.loads(row["csl"])
    authors = " ".join(f"{a.get('given', '')} {a.get('family', '')} {a.get('literal', '')}" for a in csl.get("author", []))
    ids = " ".join(f"{s}:{v}" for s, v in work_ids(conn, work_id).items())
    conn.execute("INSERT INTO works_fts VALUES(?,?,?,?,?,?,?)", (work_id, row["citekey"], row["title"], authors,
                                                                  csl.get("abstract", ""),
                                                                  csl.get("container-title", ""), ids))


def create_work(tx: Tx, records: list[Record]) -> str:
    work_id = "w" + ulid()
    stamp = now()
    tx.execute("INSERT INTO works(id,type,title,csl,created_at,updated_at) VALUES(?,?,?,?,?,?)",
               (work_id, "article", "(pending)", "{}", stamp, stamp))
    store_sources(tx, work_id, records)
    csl, _ = merge_csl(_records(tx.conn, work_id))
    citekey = make_citekey(tx.conn, csl, _records(tx.conn, work_id))
    tx.execute("UPDATE works SET citekey=? WHERE id=?", (citekey, work_id))
    refresh_work(tx, work_id)
    tx.log("work.create", {"work": work_id, "citekey": citekey, "ids": _all_ids(records)}, project="")
    return work_id


def _all_ids(records: list[Record]) -> dict[str, str]:
    out: dict[str, str] = {}
    for r in records:
        for k, v in r.ids.items():
            out.setdefault(k, v)
    return out


# Rows that point at a work: (table, key columns, work columns). A merge moves them; an unmerge moves them back.
WORK_REFS = (("notes", ("id",), ("subject",)), ("links", ("id",), ("source", "target")), ("documents", ("id",), ("work_id",)),
             ("citation_contexts", ("citing_work", "cited_work", "passage_id"), ("citing_work", "cited_work")),
             ("refs_extracted", ("document_id", "seq"), ("resolved_work_id",)), ("snowball", ("project", "handle"), ("work_id",)),
             ("map_nodes", ("id",), ("ref",)))
_REF_KEYS = {t: (keys, cols) for t, keys, cols in WORK_REFS}
PW_FIELDS = ("why", "read_depth", "citekey_override", "removed_at")


def _deeper_read(a: str | None, b: str | None) -> str | None:
    rank = lambda x: READ_LEVELS.index(x) if x in READ_LEVELS else -1  # noqa: E731
    return a if rank(a) >= rank(b) else b


def merge_works(tx: Tx, survivor: str, absorbed: str, reason: str) -> bool:
    """Fold `absorbed` into `survivor`: sources, IDs, project membership, tags, notes and links move over. What moved is
    recorded in the journal entry so unmerge_works can put it back. False (nothing done) for a not-duplicate pair."""
    if survivor == absorbed or not_duplicate(tx.conn, survivor, absorbed):
        return False
    merge_id = "m" + ulid()
    undo: dict = {"ids": [], "sources": [], "project_works": [], "taggings": [], "refs": {}}
    have = {r["provider"] for r in tx.execute("SELECT provider FROM work_sources WHERE work_id=?", (survivor,))}
    for r in tx.execute("SELECT provider, data, fetched_at FROM work_sources WHERE work_id=?", (absorbed,)).fetchall():
        if r["provider"] not in have:
            tx.execute("INSERT INTO work_sources VALUES(?,?,?,?)", (survivor, r["provider"], r["data"], r["fetched_at"]))
            undo["sources"].append(r["provider"])
    undo["ids"] = [[r["scheme"], r["value"], r["trust"]]
                   for r in tx.execute("SELECT scheme, value, trust FROM work_ids WHERE work_id=?", (absorbed,))]
    tx.execute("UPDATE work_ids SET work_id=? WHERE work_id=?", (survivor, absorbed))
    for pw in tx.execute("SELECT * FROM project_works WHERE work_id=?", (absorbed,)).fetchall():
        existing = tx.execute("SELECT * FROM project_works WHERE project=? AND work_id=?", (pw["project"], survivor)).fetchone()
        undo["project_works"].append({"row": dict(pw), "survivor": {k: existing[k] for k in PW_FIELDS} if existing else None})
        if not existing:
            tx.execute("UPDATE project_works SET work_id=? WHERE project=? AND work_id=?", (survivor, pw["project"], absorbed))
            continue
        why = existing["why"] or pw["why"]
        if pw["why"] and existing["why"] and pw["why"] not in existing["why"]:
            why = f"{existing['why']}; {pw['why']}"
        # In the project if either was; the survivor's own project citekey wins, else the absorbed one's is kept.
        removed = existing["removed_at"] if existing["removed_at"] and pw["removed_at"] else None
        tx.execute("UPDATE project_works SET why=?, read_depth=?, citekey_override=COALESCE(citekey_override, ?), removed_at=? "
                   "WHERE project=? AND work_id=?", (why, _deeper_read(existing["read_depth"], pw["read_depth"]),
                                                      pw["citekey_override"], removed, pw["project"], survivor))
    tx.execute("DELETE FROM project_works WHERE work_id=?", (absorbed,))
    for t in tx.execute("SELECT * FROM taggings WHERE target_type='work' AND target_id=?", (absorbed,)).fetchall():
        cur = tx.execute("SELECT removed_at FROM taggings WHERE tag_id=? AND target_type='work' AND target_id=? AND project=?",
                         (t["tag_id"], survivor, t["project"])).fetchone()
        undo["taggings"].append({"row": dict(t), "survivor": {"removed_at": cur["removed_at"]} if cur else None})
        if not cur:
            tx.execute("INSERT INTO taggings VALUES(?,?,?,?,?,?,?,?)",
                       (t["tag_id"], "work", survivor, t["project"], t["principal_id"], t["agent"], t["at"], t["removed_at"]))
        elif cur["removed_at"] and not t["removed_at"]:  # a live tagging beats a removed one
            tx.execute("UPDATE taggings SET removed_at=NULL WHERE tag_id=? AND target_type='work' AND target_id=? AND project=?",
                       (t["tag_id"], survivor, t["project"]))
    tx.execute("DELETE FROM taggings WHERE target_type='work' AND target_id=?", (absorbed,))
    for table, keys, cols in WORK_REFS:
        where = " AND ".join(f"{k}=?" for k in keys)
        for col in cols:
            rows = tx.execute(f"SELECT {', '.join(keys)} FROM {table} WHERE {col}=?", (absorbed,)).fetchall()
            if not rows:
                continue
            tx.execute(f"UPDATE OR IGNORE {table} SET {col}=? WHERE {col}=?", (survivor, absorbed))
            moved = [[survivor if k == col else r[k] for k in keys] for r in rows
                     if not tx.execute(f"SELECT 1 FROM {table} WHERE {where} AND {col}=?", (*tuple(r), absorbed)).fetchone()]
            if moved:
                undo["refs"].setdefault(table, {})[col] = moved
    tx.execute("UPDATE maps SET version=version+1 WHERE id IN (SELECT DISTINCT map_id FROM map_nodes WHERE ref=?)", (survivor,))
    tx.execute("UPDATE works SET merged_into=?, updated_at=? WHERE id=?", (survivor, now(), absorbed))
    tx.execute("DELETE FROM works_fts WHERE work_id=?", (absorbed,))
    tx.execute("INSERT INTO merges VALUES(?,?,?,?,?,?,NULL)", (merge_id, survivor, absorbed, reason, tx.actor.principal_id, now()))
    refresh_work(tx, survivor)
    tx.log("work.merge", {"survivor": survivor, "absorbed": absorbed, "reason": reason, "merge": merge_id, "undo": undo}, project="")
    return True


def mark_not_duplicate(tx: Tx, a: str, b: str) -> None:
    a, b = sorted((a, b))
    tx.execute("INSERT OR IGNORE INTO not_duplicates(a,b,principal_id,at) VALUES(?,?,?,?)", (a, b, tx.actor.principal_id, now()))


def active_merges(conn: sqlite3.Connection, work_id: str) -> list[sqlite3.Row]:
    """Merges not undone in which `work_id` absorbed another work or was absorbed, newest first."""
    return conn.execute("SELECT m.*, s.citekey AS survivor_key, a.citekey AS absorbed_key FROM merges m "
                        "JOIN works s ON s.id=m.survivor JOIN works a ON a.id=m.absorbed "
                        "WHERE (m.survivor=? OR m.absorbed=?) AND m.undone_at IS NULL ORDER BY m.at DESC, m.id DESC",
                        (work_id, work_id)).fetchall()


def unmerge_works(tx: Tx, merge_id: str) -> tuple[str, str]:
    """Undo one merge: the absorbed work gets back its IDs, sources, project membership, taggings and the notes/links/
    documents that moved, and the pair is marked not-duplicate so resolution never merges it again."""
    m = tx.execute("SELECT * FROM merges WHERE id=? AND undone_at IS NULL", (merge_id,)).fetchone()
    if not m:
        raise LookupError(f"no active merge {merge_id}")
    survivor, absorbed = m["survivor"], m["absorbed"]
    srow = tx.execute("SELECT citekey, merged_into FROM works WHERE id=?", (survivor,)).fetchone()
    if srow["merged_into"]:
        raise ValueError(f"{srow['citekey']} was itself merged later; unmerge that first")
    entry = tx.execute("SELECT payload FROM journal WHERE op='work.merge' AND subject=? AND json_extract(payload,'$.merge')=?",
                       (survivor, merge_id)).fetchone()
    undo = (json.loads(entry["payload"]).get("undo") if entry else None) or {}
    if undo:
        for provider in undo.get("sources", []):
            tx.execute("DELETE FROM work_sources WHERE work_id=? AND provider=?", (survivor, provider))
    else:  # merged before undo records existed: drop sources identical to the absorbed work's
        for r in tx.execute("SELECT provider, data FROM work_sources WHERE work_id=?", (absorbed,)).fetchall():
            tx.execute("DELETE FROM work_sources WHERE work_id=? AND provider=? AND data=?", (survivor, r["provider"], r["data"]))
    for scheme, value, trust in undo.get("ids", []):
        tx.execute("UPDATE work_ids SET work_id=?, trust=? WHERE scheme=? AND value=? AND work_id=?",
                   (absorbed, trust, scheme, value, survivor))
    kept = {(s, v) for r in _records(tx.conn, survivor) for s, v in r.get("ids", {}).items()}
    for s, v in {(s, v) for r in _records(tx.conn, absorbed) for s, v in r.get("ids", {}).items()} - kept:
        tx.execute("UPDATE work_ids SET work_id=? WHERE scheme=? AND value=? AND work_id=?", (absorbed, s, v, survivor))
    for e in undo.get("project_works", []):
        row, before = e["row"], e["survivor"]
        if before is None:
            tx.execute("DELETE FROM project_works WHERE project=? AND work_id=?", (row["project"], survivor))
        else:
            tx.execute("UPDATE project_works SET why=?, read_depth=?, citekey_override=?, removed_at=? WHERE project=? AND work_id=?",
                       (*(before[k] for k in PW_FIELDS), row["project"], survivor))
        cols = list(row)
        tx.execute(f"INSERT OR REPLACE INTO project_works({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
                   tuple(row[c] for c in cols))
    for e in undo.get("taggings", []):
        t, before = e["row"], e["survivor"]
        key = (t["tag_id"], survivor, t["project"])
        if before is None:
            tx.execute("DELETE FROM taggings WHERE tag_id=? AND target_type='work' AND target_id=? AND project=?", key)
        else:
            tx.execute("UPDATE taggings SET removed_at=? WHERE tag_id=? AND target_type='work' AND target_id=? AND project=?",
                       (before["removed_at"], *key))
        cols = list(t)
        tx.execute(f"INSERT OR REPLACE INTO taggings({','.join(cols)}) VALUES({','.join('?' * len(cols))})", tuple(t[c] for c in cols))
    for table, by_col in (undo.get("refs") or {}).items():
        if table not in _REF_KEYS:
            continue
        keys, cols = _REF_KEYS[table]
        where = " AND ".join(f"{k}=?" for k in keys)
        for col, rows in by_col.items():
            if col in cols:
                for key in rows:
                    tx.execute(f"UPDATE OR IGNORE {table} SET {col}=? WHERE {where}", (absorbed, *key))
    tx.execute("UPDATE works SET merged_into=NULL, updated_at=? WHERE id=?", (now(), absorbed))
    tx.execute("UPDATE merges SET undone_at=? WHERE id=?", (now(), merge_id))
    mark_not_duplicate(tx, survivor, absorbed)
    refresh_work(tx, absorbed)
    refresh_work(tx, survivor)
    tx.log("work.unmerge", {"survivor": survivor, "absorbed": absorbed, "merge": merge_id}, project="")
    return survivor, absorbed


# ------------------------------------------------------------------------------------------------ projects
def ensure_project(tx: Tx, project: str) -> None:
    if not tx.execute("SELECT 1 FROM projects WHERE id=?", (project,)).fetchone():
        tx.execute("INSERT INTO projects(id,title,created_at) VALUES(?,?,?)", (project, project, now()))
        tx.log("project.create", {"project": project}, project=project)


def add_to_project(tx: Tx, project: str, work_id: str, why: str | None = None) -> bool:
    """Returns True when newly added (or re-added after removal)."""
    ensure_project(tx, project)
    row = tx.execute("SELECT * FROM project_works WHERE project=? AND work_id=?", (project, work_id)).fetchone()
    if row and not row["removed_at"]:
        if why and why != row["why"]:
            tx.execute("UPDATE project_works SET why=? WHERE project=? AND work_id=?", (why, project, work_id))
            tx.log("project.why", {"work": work_id, "why": why}, project=project)
        return False
    if row:
        tx.execute("UPDATE project_works SET removed_at=NULL, why=COALESCE(?, why), added_by=?, added_at=? WHERE project=? AND work_id=?",
                   (why, tx.actor.label, now(), project, work_id))
    else:
        tx.execute("INSERT INTO project_works(project,work_id,added_by,added_at,why) VALUES(?,?,?,?,?)",
                   (project, work_id, tx.actor.label, now(), why))
    tx.log("project.add", {"work": work_id, "why": why}, project=project)
    return True


def project_state(conn: sqlite3.Connection, project: str, work_ids_: list[str]) -> dict[str, dict]:
    if not work_ids_:
        return {}
    marks = ",".join("?" * len(work_ids_))
    out = {w: {"in_project": False} for w in work_ids_}
    for r in conn.execute(f"SELECT * FROM project_works WHERE project=? AND work_id IN ({marks}) AND removed_at IS NULL",
                          (project, *work_ids_)):
        out[r["work_id"]] = {"in_project": True, "why": r["why"], "read": r["read_depth"], "added_at": r["added_at"],
                             "citekey_override": r["citekey_override"]}
    tagmap = tagmod.tags_for(conn, project, "work", work_ids_)
    for w in work_ids_:
        out[w]["tags"] = tagmap.get(w, [])
    return out


def work_brief(conn: sqlite3.Connection, row: sqlite3.Row, state: dict | None = None) -> dict:
    csl = json.loads(row["csl"])
    d = {"id": row["id"], "citekey": (state or {}).get("citekey_override") or row["citekey"], "title": row["title"],
         "year": row["year"], "type": row["type"], "csl": {k: csl.get(k) for k in ("author", "container-title", "type", "event-title") if csl.get(k)},
         "ids": work_ids(conn, row["id"]), "venue": venue(csl, row["year"]),
         "flags": json.loads(row["trust_flags"] or "[]")}
    if state:
        d.update({k: v for k, v in state.items() if k in ("in_project", "why", "read", "tags", "added_at")})
    return d
