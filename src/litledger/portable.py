"""Per-project snapshots (committable to a repo) and their import into an empty or existing server.

A snapshot holds the project's works (plus works its notes, links and maps point at, flagged `removed`), its notes
with their anchors, its own entities plus the library entities around its works, every link (retracted ones too)
whose two ends are in the snapshot, and its maps. Importing into another project re-keys everything that belongs to a
project (notes, project entities, maps with their nodes/edges/layout); library entities keep their ids and merge."""
from __future__ import annotations

import json
import sqlite3

from . import tags as tagmod
from .db import Actor, dumps, free_id, hash_id, now
from .formats import export_rows
from .ids import Ident, is_work_id
from .knowledge import (ensure_entity_keys, find_by_alias, is_entity_id, kind_of, link_id, link_scope, public_data,
                        with_keys)
from .ledger import Ledger
from .library import insert_note
from .maps import purge_map
from .providers.base import Record
from .textutil import title_slug
from .works import (add_to_project, create_work, ensure_project, index_work, refresh_work, resolve_ref, store_sources, work_by_ident,
                    work_ids)

SNAPSHOT_VERSION = 1  # later additions (anchors, retracted links, removed works, link keys) are optional fields
_CHUNK = 400


def _rows_in(conn: sqlite3.Connection, sql: str, ids, params: tuple = ()) -> list[sqlite3.Row]:
    """Run `sql` (with one `{}` for an IN list placed after `params`) over `ids` in chunks."""
    ids, out = list(ids), []
    for i in range(0, len(ids), _CHUNK):
        part = ids[i:i + _CHUNK]
        out += conn.execute(sql.format(",".join("?" * len(part))), (*params, *part)).fetchall()
    return out


# ------------------------------------------------------------------------------------------------ export
def export_snapshot(lg: Ledger, project: str, works: list[str] | None = None, all_tags=None, any_tags=None,
                    not_tags=None) -> dict:
    """The export tool's snapshot format: the whole project, or only the named/tagged works and what hangs on them."""
    with lg.db.read() as conn:
        rows = export_rows(conn, project, works, all_tags, any_tags, not_tags)
        data = project_snapshot(conn, project, [r["id"] for r, _ in rows] if (works or all_tags or any_tags or not_tags) else None)
    return {"format": "snapshot", "count": len(data["works"]), "content": json.dumps(data, ensure_ascii=False, indent=1)}


def project_snapshot(conn: sqlite3.Connection, project: str, only: list[str] | None = None) -> dict:
    full = only is None
    live = [r["work_id"] for r in conn.execute("SELECT work_id FROM project_works WHERE project=? AND removed_at IS NULL "
                                               "ORDER BY added_at, work_id", (project,))]
    base = live if full else list(dict.fromkeys(only))
    works_set = set(base)
    notes = conn.execute("SELECT id, subject, kind, text, quote, document_id, passage_id, page, "
                         "verification, agent, created_at FROM notes WHERE project=? ORDER BY created_at, id", (project,)).fetchall()
    plinks = conn.execute("SELECT id, project, source, target, relation, qualifier, evidence_note_id, agent, created_at, "
                          "retracted_at FROM links WHERE project=? ORDER BY created_at, id", (project,)).fetchall()
    maps = conn.execute("SELECT id, title, meta FROM maps WHERE project=? AND deleted_at IS NULL ORDER BY id",
                        (project,)).fetchall() if full else []
    map_nodes = {m["id"]: conn.execute("SELECT id, ref, text, parent, seq FROM map_nodes WHERE map_id=? AND deleted_at IS NULL "
                                       "ORDER BY id", (m["id"],)).fetchall() for m in maps}
    if full:  # works the project's notes, links and maps point at come along even when removed from the project
        refs = [n["subject"] for n in notes] + [x for l in plinks for x in (l["source"], l["target"])] + \
               [n["ref"] for ns in map_nodes.values() for n in ns if n["ref"]]
        extra = [r for r in dict.fromkeys(refs) if is_work_id(r) and r not in works_set]
        extra = [r["id"] for r in _rows_in(conn, "SELECT id FROM works WHERE id IN ({})", extra)]
        works_set |= set(extra)
        base = base + [w for w in extra if w not in base]
    else:
        notes = [n for n in notes if n["subject"] in works_set or not is_work_id(n["subject"])]
        plinks = [l for l in plinks if all(not is_work_id(x) or x in works_set for x in (l["source"], l["target"]))]

    # Entities: the project's own, those its notes/links/maps name, library entities linked to its works, and what
    # those point at (result_of, measured_on, extends, part_of …), following outgoing links only.
    ents: set[str] = {r["id"] for r in conn.execute("SELECT id FROM entities WHERE project=?", (project,))} if full else set()
    named = [n["subject"] for n in notes] + [x for l in plinks for x in (l["source"], l["target"])] + \
            [n["ref"] for ns in map_nodes.values() for n in ns if n["ref"]]
    ents |= {x for x in named if is_entity_id(x)}
    for col, other in (("source", "target"), ("target", "source")):
        ents |= {r[other] for r in _rows_in(conn, f"SELECT {other} FROM links WHERE project='' AND {col} IN ({{}})", works_set)
                 if is_entity_id(r[other])}
    frontier = set(ents)
    for _ in range(4):
        found = {r["target"] for r in _rows_in(conn, "SELECT target FROM links WHERE project='' AND source IN ({})", frontier)
                 if is_entity_id(r["target"])} - ents
        if not found:
            break
        ents |= found
        frontier = found
    ent_rows = _rows_in(conn, "SELECT id, kind, project, title, body, data, aliases, created_at FROM entities WHERE id IN ({})",
                        sorted(ents))
    ent_rows.sort(key=lambda e: e["id"])
    ents = {e["id"] for e in ent_rows}
    nodes = works_set | ents
    llinks = [l for l in _rows_in(conn, "SELECT id, project, source, target, relation, qualifier, evidence_note_id, agent, "
                                        "created_at, retracted_at FROM links WHERE project='' AND source IN ({})", nodes)
              if l["target"] in nodes]
    links = sorted({l["id"]: l for l in list(plinks) + llinks}.values(), key=lambda l: (l["created_at"], l["id"]))

    keyed = {r["id"]: r["citekey"] for r in _rows_in(conn, "SELECT id, citekey FROM works WHERE id IN ({})",
                                                       works_set | {x for x in named if is_work_id(x)})}

    def ref(node: str | None) -> str | None:
        return keyed.get(node, node) if node and is_work_id(node) else node

    def link_key(l) -> list:
        return [ref(l["source"]), ref(l["target"]), l["relation"], l["qualifier"]]

    works = []
    live_set = set(live)
    for wid in base:
        w = conn.execute("SELECT id, citekey, csl FROM works WHERE id=?", (wid,)).fetchone()
        if not w:
            continue
        pw = conn.execute("SELECT why, read_depth, citekey_override, removed_at FROM project_works WHERE project=? AND work_id=?",
                          (project, wid)).fetchone()
        sources = {r["provider"]: json.loads(r["data"]) for r in conn.execute(
            "SELECT provider, data FROM work_sources WHERE work_id=? ORDER BY provider", (wid,))}
        entry = {"citekey": w["citekey"], "ids": work_ids(conn, wid), "csl": json.loads(w["csl"]), "sources": sources,
                 "why": pw["why"] if pw else None, "read": pw["read_depth"] if pw else None,
                 "citekey_override": pw["citekey_override"] if pw else None,
                 "tags": tagmod.tags_for(conn, project, "work", [wid]).get(wid, [])}
        if (pw is not None and pw["removed_at"]) or (full and wid not in live_set):
            entry["removed"] = True
        works.append(entry)

    note_tags = tagmod.tags_for(conn, project, "note", [n["id"] for n in notes])
    out_notes = []
    for n in notes:
        item = {"id": n["id"], "subject": ref(n["subject"]), "kind": n["kind"], "text": n["text"], "quote": n["quote"],
                "page": n["page"], "verification": n["verification"], "agent": n["agent"], "created_at": n["created_at"]}
        if n["document_id"]:
            seq = conn.execute("SELECT seq FROM passages WHERE id=?", (n["passage_id"],)).fetchone() if n["passage_id"] else None
            item["anchor"] = {"document": n["document_id"], "seq": seq["seq"] if seq else None}
        if note_tags.get(n["id"]):
            item["tags"] = note_tags[n["id"]]
        out_notes.append(item)

    ent_tags = tagmod.tags_for(conn, project, "entity", sorted(ents))
    entities = []
    for e in ent_rows:
        item = {"id": e["id"], "kind": e["kind"], "title": e["title"], "body": e["body"], "created_at": e["created_at"],
                "scope": "library" if e["project"] == "" else "project", "data": public_data(e["data"]),
                "aliases": json.loads(e["aliases"] or "[]")}
        if ent_tags.get(e["id"]):
            item["tags"] = ent_tags[e["id"]]
        entities.append(item)

    out_links = []
    for l in links:
        item = {"source": ref(l["source"]), "target": ref(l["target"]), "relation": l["relation"], "qualifier": l["qualifier"],
                "evidence": l["evidence_note_id"], "scope": "library" if l["project"] == "" else "project",
                "agent": l["agent"], "created_at": l["created_at"]}
        if l["retracted_at"]:
            item["retracted_at"] = l["retracted_at"]
        out_links.append(item)
    link_keys = {l["id"]: link_key(l) for l in links}

    out_maps = []
    for m in maps:
        nodes_ = [{"id": n["id"], "ref": ref(n["ref"]), "text": n["text"], "parent": n["parent"], "seq": n["seq"]}
                  for n in map_nodes[m["id"]]]
        edges = []
        for e in conn.execute("SELECT id, src, dst, label, style, promoted_link_id FROM map_edges WHERE map_id=? AND deleted_at IS NULL "
                              "ORDER BY id", (m["id"],)):
            edge = {"id": e["id"], "src": e["src"], "dst": e["dst"], "label": e["label"], "style": json.loads(e["style"] or "{}")}
            if e["promoted_link_id"]:
                key = link_keys.get(e["promoted_link_id"])
                if not key:
                    l = conn.execute("SELECT source, target, relation, qualifier FROM links WHERE id=?", (e["promoted_link_id"],)).fetchone()
                    key = link_key(l) if l else None
                edge["link"] = key
            edges.append(edge)
        layout = {r["node_id"]: {"x": r["x"], "y": r["y"], "style": json.loads(r["style"] or "{}")}
                  for r in conn.execute("SELECT node_id, x, y, style FROM map_layout WHERE map_id=? ORDER BY node_id", (m["id"],))}
        out_maps.append({"id": m["id"], "title": m["title"], "nodes": nodes_, "edges": edges, "layout": layout,
                         "meta": json.loads(m["meta"] or "{}")})
    return {"litledger_snapshot": SNAPSHOT_VERSION, "project": project, "exported_at": now(), "works": works,
            "tags": tagmod.listing(conn, project), "notes": out_notes, "entities": entities, "links": out_links, "maps": out_maps}


# ------------------------------------------------------------------------------------------------ import
def _merge_entity(tx, eid: str, e: dict) -> None:
    """Fold a snapshot entity into an existing one: aliases unite, missing data fields and an empty body are filled."""
    row = tx.execute("SELECT kind, title, body, data, aliases FROM entities WHERE id=?", (eid,)).fetchone()
    data = public_data(row["data"])
    for k, v in public_data(e.get("data") or {}).items():
        data.setdefault(k, v)
    aliases = sorted(set(json.loads(row["aliases"] or "[]")) | set(e.get("aliases") or []) |
                     ({e["title"]} if e.get("title") and e["title"] != row["title"] else set()))
    tx.execute("UPDATE entities SET body=COALESCE(NULLIF(body,''), ?), data=?, aliases=?, updated_at=? WHERE id=?",
               (e.get("body") or "", with_keys(data, row["kind"], row["title"], aliases), dumps(aliases), now(), eid))


def import_snapshot(lg: Ledger, actor: Actor, data: dict, project: str | None = None) -> dict:
    """Idempotent: works are matched by identifiers; notes, project entities and maps keep their ids in their own
    project and are re-keyed (deterministically) in any other; library entities keep their ids and merge."""
    project = project or data.get("project") or actor.project
    counts = {"works_new": 0, "works_matched": 0, "notes": 0, "entities": 0, "links": 0, "maps": 0}
    keymap: dict[str, str] = {}   # snapshot citekey -> local work id
    ents: dict[str, str] = {}     # snapshot entity id -> local id
    notes_map: dict[str, str] = {}
    maps_map: dict[str, str] = {}
    ensure_entity_keys(lg.db)
    with lg.db.tx(actor) as tx:
        ensure_project(tx, project)
        for t in data.get("tags", []):
            tagmod.define(tx, project, t["name"], t.get("description"), t.get("color"), t.get("scope", "project"))
        for w in data.get("works", []):
            existing = None
            for s, v in (w.get("ids") or {}).items():
                existing = work_by_ident(tx.conn, Ident(s, v))
                if existing:
                    break
            records = [Record(p, src.get("ids", {}), src.get("csl", {}), src.get("kind", "unknown"), src.get("version"), src.get("extra", {}))
                       for p, src in (w.get("sources") or {}).items()] or [Record("import", w.get("ids", {}), w.get("csl", {}), "unknown")]
            if existing:
                wid = existing["id"]
                store_sources(tx, wid, records)
                refresh_work(tx, wid)
                counts["works_matched"] += 1
            else:
                wid = create_work(tx, records)
                if w.get("citekey") and not tx.execute("SELECT 1 FROM works WHERE citekey=? AND id!=?", (w["citekey"], wid)).fetchone():
                    tx.execute("UPDATE works SET citekey=? WHERE id=?", (w["citekey"], wid))
                    index_work(tx.conn, wid)  # create_work indexed the automatic key
                counts["works_new"] += 1
            keymap[w.get("citekey") or wid] = wid
            pw = tx.execute("SELECT removed_at FROM project_works WHERE project=? AND work_id=?", (project, wid)).fetchone()
            if w.get("removed"):
                if not pw:  # known to the project (its notes/links point at it) but not part of it
                    tx.execute("INSERT INTO project_works(project,work_id,added_by,added_at,why,removed_at) VALUES(?,?,?,?,?,?)",
                               (project, wid, actor.label, now(), w.get("why"), now()))
            else:
                add_to_project(tx, project, wid, w.get("why"))
            if w.get("read"):
                tx.execute("UPDATE project_works SET read_depth=? WHERE project=? AND work_id=?", (w["read"], project, wid))
            key = w.get("citekey_override")
            if key and not tx.execute("SELECT 1 FROM works WHERE citekey=? AND id!=?", (key, wid)).fetchone() and \
                    not tx.execute("SELECT 1 FROM project_works WHERE project=? AND citekey_override=? AND work_id!=?",
                                   (project, key, wid)).fetchone():
                tx.execute("UPDATE project_works SET citekey_override=? WHERE project=? AND work_id=?", (key, project, wid))
            if w.get("tags"):
                tagmod.apply(tx, project, w["tags"], "work", [wid])

        for e in data.get("entities", []):
            old = e["id"]
            kind = e.get("kind") or kind_of(old) or "topic"
            owner = "" if e.get("scope") == "library" else project
            row = tx.execute("SELECT project FROM entities WHERE id=?", (old,)).fetchone()
            target = old if row and row["project"] == owner else None
            if not target:
                hit = find_by_alias(tx.conn, project, kind, e.get("title") or "")
                target = hit["id"] if hit and hit["project"] == owner else None
            if target:
                _merge_entity(tx, target, e)
            else:
                target = free_id(tx.conn, "entities", old if not row else f"{old}-{title_slug(project)}")
                aliases = sorted(set(e.get("aliases") or []))
                tx.execute("INSERT INTO entities(id,kind,project,title,body,data,aliases,created_by,created_at,updated_at) "
                           "VALUES(?,?,?,?,?,?,?,?,?,?)", (target, kind, owner, e["title"], e.get("body", ""),
                                                           with_keys(e.get("data") or {}, kind, e["title"], aliases), dumps(aliases),
                                                           actor.label, e.get("created_at") or now(), now()))
                counts["entities"] += 1
            ents[old] = target
            if e.get("tags"):
                tagmod.apply(tx, project, e["tags"], "entity", [target])

        def node(ref: str | None) -> str | None:
            if not ref:
                return ref
            if ref in keymap:
                return keymap[ref]
            if ref in ents:
                return ents[ref]
            if ref in notes_map:
                return notes_map[ref]
            if ref in maps_map:
                return maps_map[ref]
            if not is_entity_id(ref) and not ref.startswith(("n:", "map:", "tag:")):
                local = resolve_ref(tx.conn, ref, project)  # older snapshots name works outside the project by citekey
                if local:
                    return local["id"]
            return ref

        for n in data.get("notes", []):
            old = n["id"]
            row = tx.execute("SELECT project FROM notes WHERE id=?", (old,)).fetchone()
            new = old if not row or row["project"] == project else hash_id("n:", [project, old])
            notes_map[old] = new
            if tx.execute("SELECT 1 FROM notes WHERE id=?", (new,)).fetchone():
                continue  # already imported here
            subject = node(n["subject"])
            doc = passage = None
            anchor = n.get("anchor") or {}
            if anchor.get("document") and tx.execute("SELECT 1 FROM documents WHERE id=? AND work_id=?",
                                                     (anchor["document"], subject)).fetchone():
                doc = anchor["document"]
                p = tx.execute("SELECT id FROM passages WHERE document_id=? AND seq=?", (doc, anchor.get("seq"))).fetchone()
                passage = p["id"] if p else None
            insert_note(tx, {"id": new, "project": project, "subject": subject, "kind": n["kind"], "text": n["text"],
                             "quote": n.get("quote"), "document_id": doc, "passage_id": passage, "page": n.get("page"),
                             "verification": n.get("verification", "unchecked"), "principal_id": actor.principal_id,
                             "agent": n.get("agent"), "created_at": n.get("created_at") or now()})
            if n.get("tags"):
                tagmod.apply(tx, project, n["tags"], "note", [new])
            counts["notes"] += 1

        link_ids: dict[str, str] = {}
        for link in data.get("links", []):
            src, dst = node(link["source"]), node(link["target"])
            evidence = notes_map.get(link.get("evidence"), link.get("evidence")) if link.get("evidence") else None
            if evidence and not tx.execute("SELECT 1 FROM notes WHERE id=?", (evidence,)).fetchone():
                evidence = None
            scope = link_scope(tx.conn, project, src, dst, evidence)
            lid = link_id(scope, src, dst, link["relation"], link.get("qualifier"))
            link_ids[dumps([link["source"], link["target"], link["relation"], link.get("qualifier")])] = lid
            if not tx.execute("SELECT 1 FROM links WHERE id=?", (lid,)).fetchone():
                tx.execute("INSERT INTO links(id,project,source,target,relation,qualifier,evidence_note_id,principal_id,agent,created_at,"
                           "retracted_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                           (lid, scope, src, dst, link["relation"], link.get("qualifier"), evidence, actor.principal_id,
                            link.get("agent", actor.agent), link.get("created_at") or now(), link.get("retracted_at")))
                counts["links"] += 1

        todo = []
        for m in data.get("maps", []):
            old = m["id"]
            row = tx.execute("SELECT project, deleted_at FROM maps WHERE id=?", (old,)).fetchone()
            new = old if not row or row["project"] == project else f"{old}-{title_slug(project)}"
            cur = tx.execute("SELECT project, deleted_at FROM maps WHERE id=?", (new,)).fetchone()
            if cur and cur["project"] != project:
                new = free_id(tx.conn, "maps", new)
                cur = None
            maps_map[old] = new
            if cur and not cur["deleted_at"]:
                continue  # already imported here
            if cur:
                purge_map(tx, new)
            todo.append((old, new, m))
        for old, new, m in todo:
            def nid(x: str | None, old=old, new=new) -> str | None:
                return new + x[len(old):] if x and x.startswith(old + "#") else x
            tx.execute("INSERT INTO maps(id,project,title,created_by,created_at,updated_at,meta) VALUES(?,?,?,?,?,?,?)",
                       (new, project, m["title"], actor.label, now(), now(), json.dumps(m.get("meta") or {})))
            for n in m.get("nodes", []):
                tx.execute("INSERT INTO map_nodes(id,map_id,ref,text,parent,seq,created_by,created_at) VALUES(?,?,?,?,?,?,?,?)",
                           (nid(n["id"]), new, node(n.get("ref")), n.get("text"), nid(n.get("parent")), n.get("seq"), actor.label, now()))
            for e in m.get("edges", []):
                promoted = link_ids.get(dumps(e["link"])) if e.get("link") else None
                tx.execute("INSERT INTO map_edges(id,map_id,src,dst,label,style,promoted_link_id,created_by,created_at) "
                           "VALUES(?,?,?,?,?,?,?,?,?)", (nid(e["id"]), new, nid(e["src"]), nid(e["dst"]), e.get("label", ""),
                                                         json.dumps(e.get("style") or {}), promoted, actor.label, now()))
            for node_id, lay in (m.get("layout") or {}).items():
                tx.execute("INSERT OR REPLACE INTO map_layout(map_id,node_id,x,y,style) VALUES(?,?,?,?,?)",
                           (new, nid(node_id), lay.get("x"), lay.get("y"), json.dumps(lay.get("style") or {})))
            counts["maps"] += 1
        tx.log("snapshot.import", counts, project=project)
    return counts
