"""Curricula: what to read, in what order and why, to learn an idea. An agent builds one by reading the papers (the
`build_curriculum` brief); people follow it in the web UI and can reorder or trim it.

A curriculum has stages in order; a stage has readings (a work, why to read it, what to focus on, how deeply, how
long) and checkpoint questions. How the idea evolved is its lineage: typed links between its works (the newer work
`extends`, `generalizes`, `simplifies`, `fixes`, … the older one), with "what changed" in the link's qualifier and a
verified quote as evidence. Lineage links are ordinary links, so the graph shows them too."""
from __future__ import annotations

import json
import re
import sqlite3

from .db import Actor, Tx, now
from .knowledge import add_links
from .ledger import Ledger
from .textutil import clip, title_slug
from .works import add_to_project, ensure_project, require_work

DEPTHS = ("skim", "read", "study")
# how a newer work relates to the older one it builds on; the order is how they are listed
LINEAGE = ("extends", "generalizes", "simplifies", "fixes", "reinterprets", "outperforms", "contradicts", "compares_to",
           "instance_of", "motivates")
OPS = ("stage", "stage_update", "read", "read_update", "move", "remove", "lineage")


def _cur(conn: sqlite3.Connection, project: str, ref: str) -> sqlite3.Row:
    ref = (ref or "").strip()
    row = conn.execute("SELECT * FROM curricula WHERE (id=? OR id=?) AND project=? AND deleted_at IS NULL",
                       (ref, f"cur:{title_slug(ref)}", project)).fetchone()
    if not row:
        row = conn.execute("SELECT * FROM curricula WHERE lower(title)=lower(?) AND project=? AND deleted_at IS NULL",
                           (ref, project)).fetchone()
    if not row:
        raise LookupError(f"unknown curriculum {ref!r}")
    return row


def list_curricula(lg: Ledger, project: str) -> list[dict]:
    with lg.db.read() as conn:
        out = []
        for r in conn.execute("SELECT * FROM curricula WHERE project=? AND deleted_at IS NULL ORDER BY updated_at DESC", (project,)):
            counts = dict(conn.execute("SELECT kind, count(*) FROM curriculum_parts WHERE curriculum_id=? GROUP BY kind",
                                       (r["id"],)).fetchall())
            minutes = sum(json.loads(p["body"]).get("minutes") or 0 for p in conn.execute(
                "SELECT body FROM curriculum_parts WHERE curriculum_id=? AND kind='reading'", (r["id"],)))
            out.append({"id": r["id"], "title": r["title"], "idea": r["idea"], "audience": r["audience"],
                        "stages": counts.get("stage", 0), "readings": counts.get("reading", 0), "minutes": minutes,
                        "updated_at": r["updated_at"], "created_by": r["created_by"]})
        return out


def create(tx: Tx, project: str, title: str, idea: str = "", audience: str = "", intro: str = "") -> str:
    ensure_project(tx, project)
    cid = base = f"cur:{title_slug(title) or 'untitled'}"
    n = 1
    while (row := tx.execute("SELECT deleted_at FROM curricula WHERE id=?", (cid,)).fetchone()):
        if row["deleted_at"]:  # a deleted curriculum's id is free again (the journal keeps the record)
            tx.execute("DELETE FROM curriculum_parts WHERE curriculum_id=?", (cid,))
            tx.execute("DELETE FROM curricula WHERE id=?", (cid,))
            break
        n += 1
        cid = f"{base}-{n}"
    tx.execute("INSERT INTO curricula(id,project,title,idea,audience,intro,created_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
               (cid, project, title, idea, audience, intro, tx.actor.label, now(), now()))
    tx.log("curriculum.create", {"curriculum": cid, "title": title}, project=project)
    return cid


def delete(lg: Ledger, actor: Actor, project: str, ref: str) -> bool:
    with lg.db.tx(actor) as tx:
        c = _cur(tx.conn, project, ref)
        tx.execute("UPDATE curricula SET deleted_at=? WHERE id=?", (now(), c["id"]))
        tx.log("curriculum.delete", {"curriculum": c["id"]}, project=project)
    return True


# ------------------------------------------------------------------------------------------------------- editing
def _next_id(tx: Tx, cid: str, prefix: str) -> str:
    nums = [int(r["id"][1:]) for r in tx.execute("SELECT id FROM curriculum_parts WHERE curriculum_id=? AND id LIKE ?",
                                                 (cid, prefix + "%")) if r["id"][1:].isdigit()]
    return f"{prefix}{max(nums, default=0) + 1}"


def _part(tx: Tx, cid: str, ref: str, keys: dict[str, str], kind: str | None = None) -> sqlite3.Row:
    ref = keys.get(str(ref), str(ref or ""))
    row = tx.execute("SELECT * FROM curriculum_parts WHERE curriculum_id=? AND id=?", (cid, ref)).fetchone()
    if not row and kind == "stage":  # a stage may be named by its title
        row = tx.execute("SELECT * FROM curriculum_parts WHERE curriculum_id=? AND kind='stage' AND lower(title)=lower(?)",
                         (cid, ref)).fetchone()
    if not row or (kind and row["kind"] != kind):
        raise LookupError(f"unknown {kind or 'stage or reading'} {ref!r}")
    return row


def _last_seq(tx: Tx, cid: str, kind: str, stage: str | None) -> float:
    q = "SELECT max(seq) FROM curriculum_parts WHERE curriculum_id=? AND kind=?" + (" AND stage=?" if stage else "")
    return (tx.execute(q, (cid, kind, stage) if stage else (cid, kind)).fetchone()[0] or 0) + 1


def _clean_list(value) -> list[str]:
    if isinstance(value, str):
        value = [v for v in re.split(r"\n+", value) if v.strip()]
    return [clip(str(v), 300) for v in (value or []) if str(v).strip()][:12]


STAGE_FIELDS = ("goal", "checkpoints")
READING_FIELDS = ("why", "focus", "depth", "minutes", "concepts")


def _body(op: dict, fields: tuple[str, ...], old: dict | None = None) -> dict:
    body = dict(old or {})
    for f in fields:
        if f not in op:
            continue
        v = op[f]
        if f in ("checkpoints", "concepts"):
            body[f] = _clean_list(v)
        elif f == "depth":
            if v not in DEPTHS:
                raise ValueError(f"depth must be one of {', '.join(DEPTHS)}")
            body[f] = v
        elif f == "minutes":
            try:
                body[f] = max(0, min(600, int(v)))
            except (TypeError, ValueError):
                raise ValueError("minutes must be a number") from None
        else:
            body[f] = clip(str(v or ""), 1200)
    return body


def edit(lg: Ledger, actor: Actor, project: str, ref: str | None, ops: list[dict], title: str | None = None,
         idea: str | None = None, audience: str | None = None, intro: str | None = None) -> dict:
    """Apply ops to a curriculum (created when `ref` is empty and a title is given). Each op stands alone: one that
    fails is reported and the rest still apply. Ops:
      stage{title, goal?, checkpoints?, key?}          stage_update{id, title?, goal?, checkpoints?}
      read{stage, work, why, focus?, depth?, minutes?, concepts?, key?}   read_update{id, why?, focus?, …, stage?}
      move{id, stage?, before?}                         remove{id}
      lineage{work, builds_on, relation, change, evidence?}   (work: the newer one; evidence: a note id)"""
    added, problems, lineage = [], [], []
    with lg.db.tx(actor) as tx:
        if ref:
            c = _cur(tx.conn, project, ref)
            cid = c["id"]
        elif title:
            cid = create(tx, project, title, idea or "", audience or "", intro or "")
        else:
            raise ValueError("name a curriculum, or give a title to start one")
        meta = {k: v for k, v in (("title", title), ("idea", idea), ("audience", audience), ("intro", intro)) if v is not None}
        if ref and meta:
            tx.execute(f"UPDATE curricula SET {', '.join(f'{k}=?' for k in meta)} WHERE id=?", (*meta.values(), cid))
        keys: dict[str, str] = {}
        done = 0
        for i, op in enumerate(ops):
            if not isinstance(op, dict) or op.get("op") not in OPS:
                problems.append(f"op {i}: op must be one of {', '.join(OPS)}")
                continue
            try:
                kind = op["op"]
                if kind == "stage":
                    sid = _next_id(tx, cid, "s")
                    tx.execute("INSERT INTO curriculum_parts(curriculum_id,id,kind,seq,title,body,created_by,created_at) "
                               "VALUES(?,?,?,?,?,?,?,?)", (cid, sid, "stage", _last_seq(tx, cid, "stage", None),
                                                           clip(str(op.get("title") or "Untitled stage"), 200),
                                                           json.dumps(_body(op, STAGE_FIELDS)), actor.label, now()))
                    keys[str(op.get("key") or sid)] = sid
                    added.append(sid)
                elif kind == "read":
                    stage = _part(tx, cid, op.get("stage"), keys, "stage")
                    work = require_work(tx.conn, str(op.get("work") or ""), project)
                    add_to_project(tx, project, work["id"])
                    if tx.execute("SELECT 1 FROM curriculum_parts WHERE curriculum_id=? AND kind='reading' AND work_id=?",
                                  (cid, work["id"])).fetchone():
                        raise ValueError(f"{work['citekey']} is already a reading here; read_update or move it")
                    body = _body({"depth": "read", **op}, READING_FIELDS)
                    rid = _next_id(tx, cid, "r")
                    tx.execute("INSERT INTO curriculum_parts(curriculum_id,id,kind,stage,seq,title,body,work_id,created_by,created_at) "
                               "VALUES(?,?,?,?,?,?,?,?,?,?)", (cid, rid, "reading", stage["id"], _last_seq(tx, cid, "reading", stage["id"]),
                                                               work["citekey"] or "", json.dumps(body), work["id"], actor.label, now()))
                    keys[str(op.get("key") or rid)] = rid
                    added.append(rid)
                elif kind in ("stage_update", "read_update"):
                    part = _part(tx, cid, op.get("id"), keys, "stage" if kind == "stage_update" else "reading")
                    body = _body(op, STAGE_FIELDS if part["kind"] == "stage" else READING_FIELDS, json.loads(part["body"]))
                    sets = {"body": json.dumps(body)}
                    if part["kind"] == "stage" and op.get("title"):
                        sets["title"] = clip(str(op["title"]), 200)
                    if part["kind"] == "reading" and op.get("stage"):
                        sets["stage"] = _part(tx, cid, op["stage"], keys, "stage")["id"]
                        sets["seq"] = _last_seq(tx, cid, "reading", sets["stage"])
                    tx.execute(f"UPDATE curriculum_parts SET {', '.join(f'{k}=?' for k in sets)} WHERE curriculum_id=? AND id=?",
                               (*sets.values(), cid, part["id"]))
                elif kind == "move":
                    part = _part(tx, cid, op.get("id"), keys)
                    stage = part["stage"]
                    if part["kind"] == "reading" and op.get("stage"):
                        stage = _part(tx, cid, op["stage"], keys, "stage")["id"]
                    if op.get("before"):
                        before = _part(tx, cid, op["before"], keys, part["kind"])
                        if part["kind"] == "reading":
                            stage = before["stage"]
                        prev = tx.execute("SELECT max(seq) FROM curriculum_parts WHERE curriculum_id=? AND kind=? AND seq<?"
                                          + (" AND stage=?" if part["kind"] == "reading" else ""),
                                          (cid, part["kind"], before["seq"], *([stage] if part["kind"] == "reading" else []))).fetchone()[0]
                        seq = ((prev if prev is not None else before["seq"] - 1) + before["seq"]) / 2
                    else:  # to the end (of its stage, for a reading)
                        seq = _last_seq(tx, cid, part["kind"], stage if part["kind"] == "reading" else None)
                    tx.execute("UPDATE curriculum_parts SET seq=?, stage=? WHERE curriculum_id=? AND id=?", (seq, stage, cid, part["id"]))
                elif kind == "remove":
                    part = _part(tx, cid, op.get("id"), keys)
                    if part["kind"] == "stage" and tx.execute("SELECT 1 FROM curriculum_parts WHERE curriculum_id=? AND stage=?",
                                                              (cid, part["id"])).fetchone():
                        raise ValueError(f"stage {part['id']} still has readings: move or remove them first")
                    tx.execute("DELETE FROM curriculum_parts WHERE curriculum_id=? AND id=?", (cid, part["id"]))
                elif kind == "lineage":
                    rel = str(op.get("relation") or "extends")
                    if rel not in LINEAGE:
                        raise ValueError(f"relation must be one of {', '.join(LINEAGE)}")
                    lineage.append({"source": op.get("work"), "target": op.get("builds_on"), "relation": rel,
                                    "qualifier": clip(str(op.get("change") or ""), 300) or None, "evidence": op.get("evidence"), "i": i})
                    continue
                done += 1
            except (LookupError, ValueError, PermissionError) as exc:
                problems.append(f"op {i} ({op.get('op')}): {exc}")
        tx.execute("UPDATE curricula SET updated_at=? WHERE id=?", (now(), cid))
        tx.log("curriculum.edit", {"curriculum": cid, "ops": done, **({"meta": sorted(meta)} if meta else {})}, project=project)
    if lineage:  # ordinary links (their own transaction): the newer work → the older one it builds on
        res = add_links(lg, actor, project, [{k: v for k, v in item.items() if k != "i" and v is not None} for item in lineage])
        for item, r in zip(lineage, res):
            if r["status"] == "ok":
                done += 1
            else:
                problems.append(f"op {item['i']} (lineage): {r.get('message') or r['status']}")
    return {"curriculum": cid, "added": added, "changed": done, "problems": problems}


# ------------------------------------------------------------------------------------------------------- reading
def get(lg: Ledger, project: str, ref: str) -> dict:
    with lg.db.read() as conn:
        c = _cur(conn, project, ref)
        parts = conn.execute("SELECT * FROM curriculum_parts WHERE curriculum_id=? ORDER BY seq", (c["id"],)).fetchall()
        stages = [{"id": p["id"], "title": p["title"], **json.loads(p["body"]), "readings": []} for p in parts if p["kind"] == "stage"]
        by_stage = {s["id"]: s for s in stages}
        work_ids = [p["work_id"] for p in parts if p["kind"] == "reading"]
        works = {}
        for w in conn.execute(f"SELECT w.id, w.citekey, w.title, w.year, w.csl, pw.read_depth, pw.citekey_override FROM works w "
                              f"LEFT JOIN project_works pw ON pw.work_id=w.id AND pw.project=? WHERE w.id IN ({','.join('?' * len(work_ids)) or 'NULL'})",
                              (project, *work_ids)):
            csl = json.loads(w["csl"] or "{}")
            names = [a.get("family") or a.get("literal") or "" for a in csl.get("author", [])]
            works[w["id"]] = {"id": w["id"], "citekey": w["citekey_override"] or w["citekey"], "title": w["title"], "year": w["year"],
                              "authors": (names[0] + (" et al." if len(names) > 2 else f" & {names[1]}" if len(names) == 2 else "")) if names else "",
                              "venue": csl.get("container-title") or "", "read": w["read_depth"] or ""}
        for p in parts:
            if p["kind"] == "reading" and p["stage"] in by_stage:
                by_stage[p["stage"]]["readings"].append({"id": p["id"], **json.loads(p["body"]), "work": works.get(p["work_id"])})
        lineage, cites = [], []
        if work_ids:
            marks = ",".join("?" * len(work_ids))
            for r in conn.execute(f"SELECT l.*, n.text AS note_text, n.quote AS note_quote FROM links l LEFT JOIN notes n ON n.id=l.evidence_note_id "
                                  f"WHERE l.source IN ({marks}) AND l.target IN ({marks}) AND l.project IN (?, '') AND l.retracted_at IS NULL",
                                  (*work_ids, *work_ids, project)):
                if r["relation"] == "cites":
                    cites.append({"from": r["source"], "to": r["target"]})
                else:
                    lineage.append({"id": r["id"], "work": r["source"], "builds_on": r["target"], "relation": r["relation"],
                                    "change": r["qualifier"] or "", "evidence": r["note_quote"] or r["note_text"] or "",
                                    "agent": r["agent"] or ""})
        lineage.sort(key=lambda e: (LINEAGE.index(e["relation"]) if e["relation"] in LINEAGE else 99))
        return {"id": c["id"], "title": c["title"], "idea": c["idea"], "audience": c["audience"], "intro": c["intro"],
                "created_by": c["created_by"], "updated_at": c["updated_at"], "stages": stages, "lineage": lineage, "cites": cites,
                "works": works}


def outline(data: dict) -> str:
    """The compact form an agent reads back: one line per reading, lineage as edges."""
    key = {wid: w["citekey"] for wid, w in data["works"].items()}
    n = sum(len(s["readings"]) for s in data["stages"])
    minutes = sum(r.get("minutes") or 0 for s in data["stages"] for r in s["readings"])
    out = [f"{data['id']} · {data['title']} · {len(data['stages'])} stages, {n} readings, ~{minutes} min"]
    if data["audience"]:
        out.append(f"for: {data['audience']}")
    if data["idea"]:
        out.append(f"idea: {clip(data['idea'], 300)}")
    for s in data["stages"]:
        out.append(f"{s['id']} {s['title']}" + (f" — {clip(s.get('goal', ''), 160)}" if s.get("goal") else ""))
        for r in s["readings"]:
            w = r.get("work") or {}
            out.append(f"  {r['id']} {w.get('citekey', '?')} ({w.get('year') or '?'}) [{r.get('depth', 'read')}"
                       + (f", {r['minutes']}m" if r.get("minutes") else "") + f"] {clip(r.get('why', ''), 140)}"
                       + (f" | focus: {clip(r['focus'], 80)}" if r.get("focus") else ""))
        if s.get("checkpoints"):
            out.append(f"  checkpoints: {len(s['checkpoints'])}")
    if data["lineage"]:
        out.append(f"lineage ({len(data['lineage'])}):")
        out += [f"  {key.get(e['work'], e['work'])} {e['relation']} {key.get(e['builds_on'], e['builds_on'])}"
                + (f": {clip(e['change'], 120)}" if e["change"] else "") + ("" if e["evidence"] else " (no evidence)")
                for e in data["lineage"]]
    return "\n".join(out)


def markdown(data: dict) -> str:
    """A syllabus to hand to a learner."""
    key = {wid: w["citekey"] for wid, w in data["works"].items()}
    minutes = sum(r.get("minutes") or 0 for s in data["stages"] for r in s["readings"])
    out = [f"# {data['title']}", ""]
    if data["audience"]:
        out += [f"*For {data['audience']}.*", ""]
    if data["idea"]:
        out += [data["idea"], ""]
    if data["intro"]:
        out += [data["intro"], ""]
    if minutes:
        out += [f"About {round(minutes / 60, 1)} hours of reading.", ""]
    for i, s in enumerate(data["stages"], 1):
        out += [f"## {i}. {s['title']}", ""]
        if s.get("goal"):
            out += [s["goal"], ""]
        for j, r in enumerate(s["readings"], 1):
            w = r.get("work") or {}
            head = f"{i}.{j} **{w.get('title', '?')}**" + (f" — {w['authors']}" if w.get("authors") else "") + (f", {w['year']}" if w.get("year") else "")
            out.append(f"{head} `[{w.get('citekey', '')}]` · {r.get('depth', 'read')}" + (f" · ~{r['minutes']} min" if r.get("minutes") else ""))
            if r.get("why"):
                out.append(f"   - Why: {r['why']}")
            if r.get("focus"):
                out.append(f"   - Focus: {r['focus']}")
            if r.get("concepts"):
                out.append(f"   - Concepts: {', '.join(r['concepts'])}")
        out.append("")
        if s.get("checkpoints"):
            out += ["**Check yourself**", ""] + [f"- {q}" for q in s["checkpoints"]] + [""]
    if data["lineage"]:
        out += ["## How the idea evolved", ""]
        out += [f"- `{key.get(e['work'], e['work'])}` {e['relation'].replace('_', ' ')} `{key.get(e['builds_on'], e['builds_on'])}`"
                + (f": {e['change']}" if e["change"] else "") for e in data["lineage"]]
        out.append("")
    return "\n".join(out)
