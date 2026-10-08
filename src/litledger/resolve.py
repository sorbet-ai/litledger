"""Resolution: anything pasted (IDs, URLs, BibTeX, titles) -> one canonical work, deduplicated, optionally captured."""
from __future__ import annotations

import concurrent.futures
import contextvars
import dataclasses
import json
import re
from dataclasses import dataclass, field

from . import tags as tagmod
from .csl import year_of
from .db import SYSTEM, Actor
from .formats import entry_to_csl, parse_bibtex
from .ids import STRONG_SCHEMES, Ident, parse_ident, safe_url
from .ledger import Ledger
from .providers.base import RETRACTIONS, Record
from .providers.http import ProviderError
from .textutil import clip, family_name, norm_surname, norm_title, title_similarity
from .works import (SAME_TITLE, add_to_project, create_work, fuzzy_candidates, incompatible, merge_works, provider_family,
                    record_conflict, refresh_work, resolve_ref, source_key, store_sources, surnames, visible, work_by_ident, work_conflict,
                    work_ids)

ERROR_RANK = {"blocked": 5, "auth_error": 4, "rate_limited": 3, "budget": 3, "unavailable": 2, "error": 1, "no_match": 0}


def _err_text(e) -> str:
    """'web blocked' alone doesn't tell an agent what to do; a blocked fetch carries its reason."""
    return f"{e.provider} {e.kind}" + (f": {e.message}" if e.kind == "blocked" and getattr(e, "message", "") else "")


@dataclass
class Spec:
    raw: str
    ident: Ident | None = None
    title: str | None = None
    author: str | None = None
    year: int | None = None
    import_record: Record | None = None  # from BibTeX/CSL input, used when no provider knows the work
    extra_idents: list[Ident] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # input problems worth telling the agent (e.g. a dropped URL)


@dataclass
class Fetched:
    spec: Spec
    records: list[Record] = field(default_factory=list)
    existing: str | None = None  # work id found locally without network
    status: str = "ok"
    message: str = ""
    candidates: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)  # "doi:10.x belongs to smith2020foo (title differs)"


@dataclass
class Outcome:
    input: str
    status: str
    work_id: str | None = None
    citekey: str | None = None
    title: str | None = None
    created: bool = False
    added: bool = False
    matched_existing: bool = False
    candidates: list[str] = field(default_factory=list)
    message: str = ""
    conflicts: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


# ------------------------------------------------------------------------------------------------ input parsing
def _spec_from_csl(raw: str, csl: dict, ids: dict[str, str]) -> Spec:
    notes = []
    if csl.get("URL") and not safe_url(csl["URL"]):
        notes.append(f"dropped non-http URL {str(csl['URL'])[:40]!r}")
        csl = {k: v for k, v in csl.items() if k != "URL"}
    kind = "preprint" if (csl.get("container-title") in (None, "", "arXiv") and ids.get("arxiv")) else \
        "published" if csl.get("container-title") else "unknown"
    rec = Record("import", ids, {k: v for k, v in csl.items() if v}, kind=kind)
    spec = Spec(raw=raw, title=csl.get("title"), import_record=rec, notes=notes)
    authors = csl.get("author") or []
    spec.author = family_name(authors[0]) if authors else None
    spec.year = year_of(csl)
    idents = [Ident(s, v) for s, v in ids.items() if s in STRONG_SCHEMES]
    if idents:
        spec.ident = idents[0]
        spec.extra_idents = idents[1:]
    elif csl.get("URL"):
        url_ident = parse_ident(csl["URL"])
        if url_ident and url_ident.scheme != "url":
            spec.ident = url_ident
    return spec


def csl_ids(csl: dict) -> dict[str, str]:
    ids: dict[str, str] = {}
    for value in (csl.get("DOI"), csl.get("URL"), csl.get("number") if str(csl.get("number", "")).count(".") == 1 else None,
                  csl.get("PMID") and f"pmid:{csl['PMID']}", csl.get("arxiv") and f"arxiv:{csl['arxiv']}"):
        if value:
            ident = parse_ident(str(value))
            if ident and ident.scheme != "url":
                ids.setdefault(ident.scheme, ident.value)
    return ids


def parse_spec(item) -> Spec:
    if isinstance(item, dict) and "ENTRYTYPE" in item:
        csl, ids = entry_to_csl(item)
        return _spec_from_csl(item.get("ID") or csl.get("title", "?"), csl, ids)
    if isinstance(item, dict) and isinstance(item.get("csl"), dict):
        csl = item["csl"]
        return _spec_from_csl(csl.get("id") or csl.get("title", "?"), csl, csl_ids(csl))
    if isinstance(item, dict):
        raw = item.get("id") or item.get("bibtex") or item.get("title") or str(item)
        if item.get("bibtex"):
            return parse_spec(item["bibtex"])
        if item.get("id"):
            spec = parse_spec(item["id"])
            spec.title = spec.title or item.get("title")
            return spec
        year = item.get("year")
        return Spec(raw=raw, title=item.get("title"), author=item.get("author"),
                    year=int(year) if str(year or "").isdigit() else None)
    text = str(item).strip()
    if text.startswith("@"):
        entries = parse_bibtex(text)
        if entries:
            return parse_spec(entries[0])
    ident = parse_ident(text)
    if ident:
        return Spec(raw=text, ident=ident)
    # Free text: "Title — Author 2017", "Title (Author, 2017)", or just a title.
    title, author, year = text, None, None
    m = re.search(r"\b(19[5-9]\d|20\d\d)\b", text)
    parts = re.split(r"\s+[—|–]\s+|\s+-\s+(?=[A-Z][a-z]+\s*,?\s*(?:et al\.?)?\s*\(?\d{4})", text, maxsplit=1)
    if len(parts) == 2:
        title, hint = parts
        am = re.match(r"\s*([A-Z][\w'\-]+)", hint)
        author = am.group(1) if am else None
    else:
        pm = re.match(r"^(.*?)\s*\(([A-Z][\w'\-]+)[^()]*?(\d{4})\)\s*$", text)
        if pm:
            title, author = pm.group(1), pm.group(2)
    if m:
        year = int(m.group(1))
        if title.strip().endswith(m.group(1)):
            title = title.strip()[: -len(m.group(1))].rstrip(" ,(")
    return Spec(raw=text, title=title.strip().strip('"'), author=author, year=year)


# ------------------------------------------------------------------------------------------------ fetching
def _try(provider, fn, *args) -> tuple[list[Record], ProviderError | None]:
    try:
        out = fn(*args)
        if out is None:
            return [], ProviderError(provider.id, "no_match")
        return (out if isinstance(out, list) else [out]), None
    except ProviderError as exc:
        return [], exc
    except Exception as exc:  # parsing surprises from upstream must not abort a batch
        return [], ProviderError(provider.id, "error", f"{type(exc).__name__}: {exc}"[:200])


ORDER = {"arxiv": ["arxiv", "hf", "openalex", "s2", "inspire", "ads"],
         "doi": ["biorxiv", "crossref", "datacite", "zenodo", "europepmc", "openalex", "s2", "ads", "inspire"],
         "openalex": ["openalex"], "pmid": ["europepmc", "pubmed", "s2", "openalex"],
         "pmcid": ["europepmc", "openalex", "s2"], "acl": ["web", "s2"], "dblp": ["dblp"],
         "openreview": ["openreview"], "s2": ["s2"], "corpusid": ["s2"], "url": ["web"], "isbn": ["openalex"]}
PRIMARY = {"arxiv": ("arxiv",), "doi": ("biorxiv", "crossref", "datacite", "zenodo", "europepmc")}
HINTS = {"openreview": "OpenReview needs an account (Admin → Sources); pass the arXiv ID or title instead",
         "s2": "Semantic Scholar IDs need an S2 API key (Admin → Sources); pass an arXiv ID, DOI or title instead",
         "corpusid": "Semantic Scholar IDs need an S2 API key (Admin → Sources); pass an arXiv ID, DOI or title instead",
         "isbn": "ISBN lookup is not supported yet; pass a DOI or title"}


def _same_paper(a: Record, b: Record) -> bool:
    if b.extra.get("low_trust") or a.extra.get("low_trust"):  # junk reposts carry wrong years: title and authors only
        return not incompatible(a.title, surnames(a.csl), None, b.title, surnames(b.csl), None)
    return not record_conflict(a, b)


def fetch_ident(lg: Ledger, ident: Ident, depth: int = 0) -> tuple[list[Record], list[ProviderError]]:
    records: list[Record] = []
    errors: list[ProviderError] = []
    for pid in ORDER.get(ident.scheme, []):
        p = lg.providers.get(pid)
        if not p or ident.scheme not in p.schemes:
            continue
        got, err = _try(p, p.lookup, ident)
        got = [r for r in got if r.title]
        if err:
            errors.append(err)
        if got:
            records.extend(got)
            break  # primary found; enrichment below
    if not records:
        return records, errors
    # Enrichment: Semantic Scholar cross-walk (when keyed), and the published version an arXiv record points to.
    # Secondary records only join when they describe the same paper as the primary one (title, authors, year).
    primary = records[0]
    s2 = lg.providers.get("s2")
    if s2 and s2.keyed and not any(r.provider == "s2" for r in records) and ident.scheme in s2.schemes:
        got, _ = _try(s2, s2.lookup, ident)
        records.extend(r for r in got if r.title and _same_paper(primary, r))
    if depth == 0:
        follow: list[Ident] = []
        for r in records:
            if r.provider in ("arxiv", "s2") and r.ids.get("doi") and ident.scheme != "doi":
                follow.append(Ident("doi", r.ids["doi"]))
            if r.provider in ("web", "datacite", "crossref", "europepmc") and r.ids.get("arxiv") and ident.scheme != "arxiv":
                follow.append(Ident("arxiv", r.ids["arxiv"]))
            if r.provider == "web" and r.ids.get("doi") and ident.scheme == "url":
                follow.append(Ident("doi", r.ids["doi"]))
            if r.provider == "biorxiv" and r.extra.get("published_doi"):
                follow.append(Ident("doi", r.extra["published_doi"]))
        for f in follow:
            if any(r.ids.get(f.scheme) == f.value and r.provider in PRIMARY.get(f.scheme, ()) for r in records):
                continue
            more, _ = fetch_ident(lg, f, depth=1)
            records.extend(m for m in more if not any(m.provider == r.provider and m.ids == r.ids for r in records)
                           and _same_paper(primary, m))
    return records, errors


TITLE_ORDER = ["s2", "arxiv", "dblp", "crossref", "openalex", "europepmc"]


def _acceptable(spec: Spec, rec: Record, threshold: float = 0.93) -> bool:
    if rec.extra.get("low_trust") or not rec.title:
        return False
    if title_similarity(spec.title or "", rec.title) < threshold:
        return False
    if spec.author:
        authors = rec.csl.get("author") or []
        surnames = {norm_surname(family_name(a)) for a in authors[:12]}
        if authors and norm_surname(spec.author) not in surnames:
            return False
    if spec.year and rec.year and abs(spec.year - rec.year) > 2:
        return False
    return True


def match_title(lg: Ledger, spec: Spec) -> Fetched:
    out = Fetched(spec)
    errors: list[ProviderError] = []
    near: list[Record] = []
    for pid in TITLE_ORDER:
        p = lg.providers.get(pid)
        if not p or "lookup" not in p.capabilities and "search" not in p.capabilities:
            continue
        if pid == "s2" and not p.keyed:
            continue  # keyless S2 is too rate-limited to sit on the resolution path
        got, err = _try(p, p.match_title, spec.title, spec.author, spec.year)
        if err and err.kind != "no_match":
            errors.append(err)
        accepted = [r for r in got if _acceptable(spec, r)]
        near.extend(r for r in got if r not in accepted and title_similarity(spec.title or "", r.title) >= 0.75)
        if not accepted:
            continue
        exact = [r for r in accepted if norm_title(r.title) == norm_title(spec.title or "")]
        pool = exact or accepted
        distinct = {r.ids.get("arxiv") or r.ids.get("doi") or r.ids.get("dblp") or r.title for r in pool}
        if len(distinct) > 1 and len({r.ids.get("arxiv") for r in pool if r.ids.get("arxiv")}) > 1:
            out.status = "ambiguous"
            out.candidates = [f"{r.handle} — {r.title} ({r.year or '?'})" for r in pool[:4]]
            return out
        best = sorted(pool, key=lambda r: (not r.ids.get("arxiv"), not r.ids.get("dblp"), -title_similarity(spec.title, r.title)))[0]
        ident = next((Ident(s, best.ids[s]) for s in ("arxiv", "doi", "acl", "pmid", "openalex") if best.ids.get(s)), None)
        if ident and best.provider not in PRIMARY.get(ident.scheme, ()):
            more, _ = fetch_ident(lg, ident)
            out.records = more or [best]
            if best.provider not in {r.provider for r in out.records}:
                out.records.append(best)
        else:
            out.records = [best]
        return out
    if spec.import_record:
        out.records = [spec.import_record]
        out.message = "not found by any provider; stored from the supplied BibTeX"
        return out
    if errors and all(e.kind != "no_match" for e in errors) and not near:
        worst = max(errors, key=lambda e: ERROR_RANK.get(e.kind, 0))
        out.status = worst.kind if worst.kind in ERROR_RANK else "error"
        out.message = "; ".join(sorted({_err_text(e) for e in errors}))
        return out
    out.status = "no_match"
    if near:
        seen, cands = set(), []
        for r in near:
            h = r.handle
            if h and h not in seen:
                seen.add(h)
                cands.append(f"{h} — {r.title} ({r.year or '?'})")
        out.candidates = cands[:3]
        out.message = "closest matches below; pass one of the IDs"
    else:
        out.message = "add author/year or an ID"
    return out


def _title_conflict(spec: Spec, title: str) -> str | None:
    """A title the user supplied (BibTeX/CSL/{id,title}) that is not the paper an identifier points at. A bare string
    parsed as a title guess (a citekey, say) is not a supplied title."""
    supplied = spec.title if spec.title and (spec.import_record or spec.title.strip() != spec.raw.strip()) else None
    if supplied and title and title not in ("(pending)", "(untitled)") and title_similarity(supplied, title) < SAME_TITLE:
        return "title differs"
    return None


def _drop_ident(spec: Spec, ident: Ident) -> None:
    """Stop using an identifier the user supplied (it names another paper): not looked up, not indexed, not stored."""
    remaining = [i for i in (spec.ident, *spec.extra_idents) if i and i != ident]
    spec.ident = remaining[0] if remaining else None
    spec.extra_idents = remaining[1:]
    rec = spec.import_record
    if rec and rec.ids.get(ident.scheme) == ident.value:
        csl = {k: v for k, v in rec.csl.items() if not (ident.scheme == "doi" and k == "DOI")}
        spec.import_record = dataclasses.replace(rec, ids={k: v for k, v in rec.ids.items() if k != ident.scheme}, csl=csl)


def fetch(lg: Ledger, spec: Spec, project: str, refresh: bool) -> Fetched:
    conflicts: list[str] = []
    with lg.db.read() as conn:
        if not spec.ident and not spec.title:
            return Fetched(spec, status="no_match", message="empty input")
        local = resolve_ref(conn, spec.raw, project) if not spec.import_record else None
        if local and not spec.ident and not visible(conn, local["id"]):
            local = None  # a citekey of a work outside a limited caller's projects names nothing (an ID still dedups)
        if local and not refresh and not _title_conflict(spec, local["title"]):
            return Fetched(spec, existing=local["id"])
        for ident in [i for i in (spec.ident, *spec.extra_idents) if i]:
            row = work_by_ident(conn, ident)
            if not row:
                continue
            reason = _title_conflict(spec, row["title"])
            if reason:
                conflicts.append(f"{ident.handle} belongs to {row['citekey']} ({reason})")
                _drop_ident(spec, ident)
            elif not refresh:
                return Fetched(spec, existing=row["id"], conflicts=conflicts)
        if not spec.ident and spec.title and not refresh:
            cands = fuzzy_candidates(conn, spec.title, spec.author, spec.year)
            if len(cands) == 1:
                return Fetched(spec, existing=cands[0]["id"], conflicts=conflicts)
    if lg.settings.offline and spec.import_record:
        return Fetched(spec, records=[spec.import_record], message="offline; stored from the supplied BibTeX", conflicts=conflicts)
    if spec.ident:
        records: list[Record] = []
        errors: list[ProviderError] = []
        for ident in [spec.ident, *spec.extra_idents]:
            got, errs = fetch_ident(lg, ident)
            errors += errs
            if not got:
                continue
            reason = _title_conflict(spec, got[0].title) or (record_conflict(records[0], got[0]) if records else None)
            if reason:
                conflicts.append(f"{ident.handle} is {clip(got[0].title, 50)!r} ({reason})")
                _drop_ident(spec, ident)
                continue
            records.extend(r for r in got if not any(r.provider == x.provider and r.ids == x.ids for x in records))
        if records:
            if spec.import_record:
                records.append(spec.import_record)
            return Fetched(spec, records=records, conflicts=conflicts)
        if spec.title and conflicts:  # every identifier named another paper: go by the title instead
            out = match_title(lg, spec)
            out.conflicts = conflicts + out.conflicts
            return out
        if spec.import_record:
            return Fetched(spec, records=[spec.import_record], message="not found by any provider; stored from the supplied BibTeX",
                           conflicts=conflicts)
        if spec.ident and spec.ident.scheme in HINTS and not any(lg.providers.get(p) for p in ORDER.get(spec.ident.scheme, [])):
            return Fetched(spec, status="unavailable", message=HINTS[spec.ident.scheme])
        hard = [e for e in errors if e.kind != "no_match"]
        if hard:
            worst = max(hard, key=lambda e: ERROR_RANK.get(e.kind, 0))
            return Fetched(spec, status=worst.kind, message="; ".join(sorted({_err_text(e) for e in hard})))
        return Fetched(spec, status="no_match", message=f"{spec.ident.handle if spec.ident else spec.raw} not found")
    out = match_title(lg, spec)
    out.conflicts = conflicts + out.conflicts
    return out


# ------------------------------------------------------------------------------------------------ writing
def _persist(lg: Ledger, actor: Actor, f: Fetched, project: str, add: bool, why: str | None) -> Outcome:
    out = Outcome(f.spec.raw, f.status, candidates=f.candidates, message=f.message, conflicts=list(f.conflicts),
                  notes=list(f.spec.notes))
    if f.status != "ok":
        return out
    with lg.db.tx(actor) as tx:
        if f.existing:
            work_id = f.existing
            out.matched_existing = True
        else:
            trusted = [r for r in f.records if provider_family(r.provider) != "import" and not r.extra.get("low_trust")]
            primary = trusted[0] if trusted else f.records[0]
            provider_ids: dict[str, str] = {}
            for r in trusted:
                for s, v in r.ids.items():
                    provider_ids.setdefault(s, v)
            # User-supplied IDs that a provider contradicts are dropped before anything is matched, stored or indexed.
            records = [dataclasses.replace(r, ids={s: v for s, v in r.ids.items() if provider_ids.get(s, v) == v})
                       if provider_family(r.provider) == "import" else r for r in f.records]
            ids: dict[str, str] = {}
            for r in records:
                if not r.extra.get("low_trust"):
                    for s, v in r.ids.items():
                        ids.setdefault(s, v)
            title, authors = primary.title, surnames(primary.csl)
            year = None if primary.extra.get("low_trust") else primary.year
            # Works that already own one of these IDs are the same work only when title, authors and year agree.
            matched: list[str] = []
            seen: set[str] = set()
            for s, v in ids.items():
                row = work_by_ident(tx.conn, Ident(s, v))
                if not row or row["id"] in seen:
                    continue
                seen.add(row["id"])
                reason = work_conflict(tx.conn, row, title, authors, year)
                if reason:
                    out.conflicts.append(f"{s}:{v} belongs to {row['citekey']} ({reason})")
                    records = [dataclasses.replace(r, ids={k: x for k, x in r.ids.items() if (k, x) != (s, v)})
                               if provider_family(r.provider) == "import" else r for r in records]
                else:
                    matched.append(row["id"])
            if not matched:
                first = family_name(primary.csl["author"][0]) if primary.csl.get("author") else None
                cands = fuzzy_candidates(tx.conn, primary.title, first, year, ids)
                if len(cands) == 1:
                    matched = [cands[0]["id"]]
            if matched:
                work_id = matched[0]
                for other in matched[1:]:
                    if not merge_works(tx, work_id, other, "shared identifier"):
                        key = tx.execute("SELECT citekey FROM works WHERE id=?", (other,)).fetchone()["citekey"]
                        out.conflicts.append(f"{key} not merged (marked not a duplicate)")
                store_sources(tx, work_id, records)
                refresh_work(tx, work_id)
                tx.log("work.refresh", {"work": work_id, "sources": sorted({source_key(r) for r in records})}, project="")
                out.matched_existing = True
            else:
                work_id = create_work(tx, records)
                out.created = True
        if add:
            out.added = add_to_project(tx, project, work_id, why)
        row = tx.execute("SELECT citekey, title FROM works WHERE id=?", (work_id,)).fetchone()
        out.work_id, out.citekey, out.title = work_id, row["citekey"], row["title"]
    return out


def _exact(lg: Ledger, f: Fetched) -> Fetched:
    """A title-only match whose title isn't the same (normalised) becomes no_match."""
    if f.status != "ok" or f.spec.ident or not f.spec.title:
        return f
    if f.existing:
        with lg.db.read() as conn:
            found = conn.execute("SELECT title FROM works WHERE id=?", (f.existing,)).fetchone()["title"]
    else:
        trusted = [r for r in f.records if provider_family(r.provider) != "import"]
        found = (trusted or f.records)[0].title if f.records else ""
    if norm_title(found or "") == norm_title(f.spec.title):
        return f
    return Fetched(f.spec, status="no_match", message=f"closest title differs: {clip(found or '', 60)!r}")


def resolve_items(lg: Ledger, actor: Actor, items: list, add: bool = False, tags: list[str] | None = None,
                  why: str | None = None, refresh: bool = False, project: str | None = None,
                  tag_scope: str = "project", exact_title: bool = False) -> tuple[list[Outcome], dict]:
    """Resolve (and with add=True capture) items. Returns outcomes plus tag info {'created','hints'}. exact_title: an
    item without an identifier only matches a paper with the same normalised title (no near matches)."""
    project = project or actor.project
    specs = [parse_spec(i) for i in items if str(i).strip()]
    workers = min(4, max(1, len(specs)))
    parent = contextvars.copy_context()  # the caller's project limits (works.VISIBLE) hold in the pool's threads too
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        fetched = list(pool.map(lambda s: parent.copy().run(fetch, lg, s, project, refresh), specs))
    if exact_title:
        fetched = [_exact(lg, f) for f in fetched]
    outcomes = [_persist(lg, actor, f, project, add, why) for f in fetched]
    tag_info: dict = {"created": [], "hints": {}}
    ok_ids = [o.work_id for o in outcomes if o.status == "ok" and o.work_id]
    if tags and ok_ids:
        with lg.db.tx(actor) as tx:
            tag_info = tagmod.apply(tx, project, tags, "work", ok_ids, tag_scope)
    created = [o for o in outcomes if o.created]
    for o in created:
        lg.enqueue("enrich", {"work": o.work_id})
    if add and lg.settings.fetch_fulltext == "on_add":
        for o in outcomes:
            if o.status == "ok" and o.added:
                lg.enqueue("fetch_fulltext", {"work": o.work_id})
    if created:
        lg.enqueue("relink", {})
    return outcomes, tag_info


# ------------------------------------------------------------------------------------------------ enrichment job
def _retraction_flags(lg: Ledger, ids: dict[str, str]) -> list[str]:
    crossref = lg.providers.get("crossref")
    if not crossref or not ids.get("doi"):
        return []
    try:
        updates = crossref.updates(ids["doi"])
    except Exception:
        return []
    return sorted({(u.get("type") or "update").lower() for u in updates
                   if (u.get("type") or "").lower() in (*RETRACTIONS, "correction", "erratum")})


def enrich(lg: Ledger, payload: dict) -> dict:
    """Background: find the published venue version of preprints (DBLP), so BibTeX can cite the venue."""
    work_id = payload["work"]
    with lg.db.read() as conn:
        row = conn.execute("SELECT * FROM works WHERE id=? AND merged_into IS NULL", (work_id,)).fetchone()
        if not row:
            return {"skipped": "gone"}
        ids = work_ids(conn, work_id)
        has_published = conn.execute("SELECT 1 FROM versions WHERE work_id=? AND kind='published'", (work_id,)).fetchone()
    flags = _retraction_flags(lg, ids)
    if flags:
        with lg.db.tx(SYSTEM) as tx:
            tx.execute("UPDATE works SET trust_flags=? WHERE id=?", (json.dumps(flags), work_id))
            tx.log("work.flag", {"work": work_id, "flags": flags}, project="")
    if has_published or row["type"] in ("software", "webpage"):
        return {"skipped": "has venue", "flags": flags}
    found: list[Record] = []
    dblp = lg.providers.get("dblp")
    if dblp and row["first_author"]:  # without a first author a generic title could pick up another paper's venue
        got, _ = _try(dblp, dblp.match_title, row["title"], None, row["year"])
        for r in got:
            if r.kind == "published" and title_similarity(row["title"], r.title) >= 0.95 and \
                    surnames(r.csl)[:1] == [row["first_author"]] and \
                    (not row["year"] or not r.year or 0 <= r.year - row["year"] <= 2) and \
                    not (r.ids.get("arxiv") and ids.get("arxiv") and r.ids["arxiv"] != ids["arxiv"]):
                found.append(r)
                break
    if not found:
        return {"venue": None}
    with lg.db.tx(SYSTEM) as tx:
        store_sources(tx, work_id, found)
        refresh_work(tx, work_id)
        tx.log("work.enrich", {"work": work_id, "venue": found[0].csl.get("container-title")}, project="")
    return {"venue": found[0].csl.get("container-title")}
