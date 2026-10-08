"""Mind maps: nodes reference anything in the ledger (works, typed entities, notes, tags, other maps) or hold free
text; edges are sketches until promoted to links. Agents read maps as compact outlines; presentation (order,
colours, arrow curves, collapsed state) is stored apart and ignored by them."""
from __future__ import annotations

import json
import re
import sqlite3

from .db import Actor, Tx, now
from .ids import is_work_id
from .knowledge import KINDS, is_entity_id, kind_of
from .knowledge import add_links, public_data
from .ledger import Ledger
from .textutil import clip, title_slug
from .works import ensure_project, resolve_ref

CLIENT_ID = re.compile(r"^[A-Za-z0-9_\-]{1,48}$")


def _map(conn: sqlite3.Connection, project: str, ref: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM maps WHERE (id=? OR id=?) AND project=? AND deleted_at IS NULL",
                       (ref, f"map:{title_slug(ref)}", project)).fetchone()
    if not row:
        row = conn.execute("SELECT * FROM maps WHERE lower(title)=lower(?) AND project=? AND deleted_at IS NULL", (ref, project)).fetchone()
    if not row:
        raise LookupError(f"unknown map {ref!r}")
    return row


def list_maps(lg: Ledger, project: str) -> list[dict]:
    with lg.db.read() as conn:
        return [{"id": r["id"], "title": r["title"], "updated_at": r["updated_at"], "version": r["version"],
                 "nodes": conn.execute("SELECT count(*) FROM map_nodes WHERE map_id=? AND deleted_at IS NULL", (r["id"],)).fetchone()[0]}
                for r in conn.execute("SELECT * FROM maps WHERE project=? AND deleted_at IS NULL ORDER BY updated_at DESC", (project,))]


def _next_id(tx: Tx, map_id: str, table: str, prefix: str) -> str:
    """The next free auto id (n<k> / e<k>). Clients may choose ids too, so a taken one is skipped, never reused."""
    n = tx.execute(f"SELECT count(*) FROM {table} WHERE map_id=?", (map_id,)).fetchone()[0] + 1
    while tx.execute(f"SELECT 1 FROM {table} WHERE id=?", (f"{map_id}#{prefix}{n}",)).fetchone():
        n += 1
    return f"{map_id}#{prefix}{n}"


def _next_node_id(tx: Tx, map_id: str) -> str:
    return _next_id(tx, map_id, "map_nodes", "n")


def _next_edge_id(tx: Tx, map_id: str) -> str:
    return _next_id(tx, map_id, "map_edges", "e")


def _full(map_id: str, short: str | None) -> str | None:
    if not short:
        return None
    return short if "#" in short else f"{map_id}#{short}"


def _short(node_id: str) -> str:
    return node_id.split("#", 1)[1] if "#" in node_id else node_id


def create_map(tx: Tx, project: str, title: str) -> str:
    ensure_project(tx, project)
    map_id = f"map:{title_slug(title) or 'untitled'}"
    base, n = map_id, 1
    while (row := tx.execute("SELECT deleted_at FROM maps WHERE id=?", (map_id,)).fetchone()):
        if row["deleted_at"]:
            purge_map(tx, map_id)
            break
        n += 1
        map_id = f"{base}-{n}"
    tx.execute("INSERT INTO maps(id,project,title,created_by,created_at,updated_at) VALUES(?,?,?,?,?,?)",
               (map_id, project, title, tx.actor.label, now(), now()))
    tx.log("map.create", {"map": map_id, "title": title}, project=project)
    return map_id


def purge_map(tx: Tx, map_id: str) -> None:
    """Remove a deleted map's rows so its id can be used again (the journal keeps the record)."""
    for table in ("map_layout", "map_edges", "map_nodes"):
        tx.execute(f"DELETE FROM {table} WHERE map_id=?", (map_id,))
    tx.execute("DELETE FROM maps WHERE id=?", (map_id,))


def delete_map(lg: Ledger, actor: Actor, project: str, ref: str) -> bool:
    with lg.db.tx(actor) as tx:
        m = _map(tx.conn, project, ref)
        tx.execute("UPDATE maps SET deleted_at=? WHERE id=?", (now(), m["id"]))
        tx.log("map.delete", {"map": m["id"]}, project=project)
    return True


def _ref_id(conn: sqlite3.Connection, project: str, ref: str) -> str:
    """Anything a node can point at: a work (citekey/any ID), a typed entity (method:…, dataset:…, question:…),
    a note (n:…), tag:<name>, or another map (map:…)."""
    ref = ref.strip()
    if is_entity_id(ref):
        if not conn.execute("SELECT 1 FROM entities WHERE id=? AND project IN (?, '')", (ref, project)).fetchone():
            raise LookupError(f"unknown {kind_of(ref)} {ref!r} — create it with entity() first")
        return ref
    if ref.startswith("n:"):
        row = conn.execute("SELECT id FROM notes WHERE id LIKE ? AND project=?", (ref + "%", project)).fetchone()
        if not row:
            raise LookupError(f"unknown note {ref!r}")
        return row["id"]
    if ref.startswith("tag:"):
        name = ref[4:]
        if not conn.execute("SELECT 1 FROM tags WHERE name=? AND (project=? OR project='') AND archived_at IS NULL",
                            (name, project)).fetchone():
            raise LookupError(f"unknown tag {name!r}")
        return ref
    if ref.startswith("map:"):
        if not conn.execute("SELECT 1 FROM maps WHERE id=? AND project=? AND deleted_at IS NULL", (ref, project)).fetchone():
            raise LookupError(f"unknown map {ref!r}")
        return ref
    row = resolve_ref(conn, ref, project)
    if not row:
        raise LookupError(f"unknown reference {ref!r} — capture it first")
    return row["id"]


def _set_style(tx: Tx, map_id: str, node: str, style: dict | None, x=None, y=None) -> None:
    if style is None and x is None:
        return
    row = tx.execute("SELECT style FROM map_layout WHERE map_id=? AND node_id=?", (map_id, node)).fetchone()
    merged = json.loads(row["style"]) if row else {}
    merged.update({k: v for k, v in (style or {}).items()})
    merged = {k: v for k, v in merged.items() if v not in (None, "", [], {})}
    tx.execute("INSERT INTO map_layout(map_id,node_id,x,y,style) VALUES(?,?,?,?,?) ON CONFLICT(map_id,node_id) DO UPDATE SET "
               "x=COALESCE(excluded.x, x), y=COALESCE(excluded.y, y), style=excluded.style",
               (map_id, node, x, y, json.dumps(merged)))


OPS = ("add", "update", "delete", "edge", "edge_update", "unedge", "move", "promote", "expand", "meta")


def _str_field(op: dict, key: str) -> str | None:
    value = op.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    return value


def _num_field(op: dict, key: str) -> float | None:
    value = op.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{key} must be a number")
    return value


def _style_field(op: dict, key: str = "style") -> dict | None:
    value = op.get(key)
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be an object")
    return value


def _problem(exc: Exception) -> str:
    if isinstance(exc, KeyError):
        return f"missing {exc.args[0]!r}" if exc.args else "missing field"
    if isinstance(exc, (LookupError, ValueError)):
        return str(exc)
    if isinstance(exc, sqlite3.IntegrityError):
        return f"rejected by the database ({exc})"
    return f"{type(exc).__name__}: {exc}"


def _live_node(tx: Tx, map_id: str, node: str | None) -> sqlite3.Row:
    row = tx.execute("SELECT * FROM map_nodes WHERE id=? AND map_id=? AND deleted_at IS NULL", (node, map_id)).fetchone() if node else None
    if not row:
        raise LookupError(f"unknown node {_short(node or '') or '(none)'}")
    return row


def _check_parent(tx: Tx, map_id: str, node: str, parent: str | None) -> None:
    """A parent must be a live node of the same map, and not the node itself or anything in its branch."""
    if parent is None:
        return
    if parent == node:
        raise ValueError(f"node {_short(node)} can't be its own parent")
    if not tx.execute("SELECT 1 FROM map_nodes WHERE id=? AND map_id=? AND deleted_at IS NULL", (parent, map_id)).fetchone():
        raise LookupError(f"unknown parent {_short(parent)}")
    cur, seen = parent, set()
    while cur and cur not in seen:
        seen.add(cur)
        row = tx.execute("SELECT parent FROM map_nodes WHERE id=? AND map_id=?", (cur, map_id)).fetchone()
        cur = row["parent"] if row else None
        if cur == node:
            raise ValueError(f"parent {_short(parent)} is inside {_short(node)}'s branch (that would make a loop)")


def _bump(tx: Tx, map_id: str, project: str, n_ops: int) -> None:
    tx.execute("UPDATE maps SET version=version+1, updated_at=? WHERE id=?", (now(), map_id))
    version = tx.execute("SELECT version FROM maps WHERE id=?", (map_id,)).fetchone()[0]
    tx.log("map.edit", {"map": map_id, "ops": n_ops, "version": version}, project=project)


def edit(lg: Ledger, actor: Actor, project: str, map_ref: str | None, ops: list[dict], title: str | None = None,
         base_version: int | None = None) -> dict:
    """ops (each {op: …} or keyed by the op name):
      add{text|ref, parent?, key?|id?, seq?, style?}   update{id, text?, ref?, parent?, seq?, style?}   delete{id}
      edge{src, dst, label?, id?, style?}   edge_update{id, label?, style?}   unedge{id}
      move{id, x, y, style?}   promote{edge, relation?, evidence?}   expand{id|ref, kinds?}   meta{meta}
    `key` names a new node for later ops in the same call; `id` lets a client choose the node/edge id.

    Each op applies on its own: one that fails is rolled back alone and reported in `problems` and `results`
    ([{i, ok, error?}], one per op). `version` goes up once per call that changed something; a `base_version` that
    is not the current version still applies the ops but returns `conflict: true`, so an editor knows to rebase."""
    if not isinstance(ops, list):
        raise ValueError("ops must be a list")
    if title is not None and not isinstance(title, str):
        raise ValueError("title must be a string")
    result: dict = {"map": None, "added": [], "changed": 0, "problems": [], "results": [], "version": 0, "conflict": False}
    results: list[dict | None] = []
    deferred: list[tuple[int, str, dict]] = []
    with lg.db.tx(actor) as tx:
        if map_ref:
            try:
                map_id = _map(tx.conn, project, map_ref)["id"]
            except LookupError:
                map_id = create_map(tx, project, title or map_ref)
        else:
            map_id = create_map(tx, project, title or "untitled map")
        result["map"] = map_id
        before = tx.execute("SELECT version, title FROM maps WHERE id=?", (map_id,)).fetchone()
        if base_version is not None and str(base_version) != str(before["version"]):
            result["conflict"] = True
        keys: dict[str, str] = {}
        dirty = False

        def nid(ref) -> str | None:
            if ref is None or ref == "":
                return None
            if isinstance(ref, bool) or not isinstance(ref, (str, int)):
                raise ValueError("ids must be strings")
            ref = str(ref)
            if ref in keys:
                return keys[ref]
            return None if ref == "root" else _full(map_id, ref)  # "root" = the map itself (UI's central node)

        def need(ref) -> str:
            node = nid(ref)
            if not node:
                raise ValueError("id is required")
            return node

        def apply(kind: str, op: dict) -> tuple[list[str], int, tuple[str, str] | None]:
            """Run one op; returns (ids added, change count, (key, node) to remember)."""
            if kind == "add":
                text, ref_in = _str_field(op, "text"), _str_field(op, "ref")
                style, x, y, seq = _style_field(op), _num_field(op, "x"), _num_field(op, "y"), _num_field(op, "seq")
                if op.get("id") is not None:
                    if not CLIENT_ID.match(str(op["id"])):
                        raise ValueError("id must be 1-48 letters, digits, - or _")
                    node = _full(map_id, str(op["id"]))
                    old = tx.execute("SELECT deleted_at, map_id FROM map_nodes WHERE id=?", (node,)).fetchone()
                    if old and not old["deleted_at"]:
                        raise ValueError(f"node id {op['id']} already exists")
                    if old:  # re-adding a deleted node (an editor's undo) replaces it
                        tx.execute("DELETE FROM map_layout WHERE map_id=? AND node_id=?", (map_id, node))
                        tx.execute("DELETE FROM map_nodes WHERE id=?", (node,))
                else:
                    node = _next_node_id(tx, map_id)
                ref = _ref_id(tx.conn, project, ref_in) if ref_in else None
                if not ref and not text:
                    raise ValueError("add needs text or ref")
                parent = nid(op.get("parent"))
                _check_parent(tx, map_id, node, parent)
                tx.execute("INSERT INTO map_nodes(id,map_id,ref,text,parent,seq,created_by,created_at) VALUES(?,?,?,?,?,?,?,?)",
                           (node, map_id, ref, text or None, parent, seq, actor.label, now()))
                _set_style(tx, map_id, node, style, x, y)
                key = op.get("key")
                return [_short(node)], 0, ((str(key), node) if key else None)
            if kind == "update":
                node = need(op.get("id"))
                _live_node(tx, map_id, node)
                fields, params = [], []
                if "text" in op:
                    fields.append("text=?")
                    params.append(_str_field(op, "text") or None)
                if op.get("ref"):
                    fields.append("ref=?")
                    params.append(_ref_id(tx.conn, project, _str_field(op, "ref") or ""))
                elif "ref" in op and op["ref"] in (None, ""):
                    fields.append("ref=NULL")
                if "parent" in op:
                    parent = nid(op["parent"])
                    _check_parent(tx, map_id, node, parent)
                    fields.append("parent=?")
                    params.append(parent)
                if "seq" in op:
                    fields.append("seq=?")
                    params.append(_num_field(op, "seq"))
                style = _style_field(op)
                changed = bool(fields) and tx.execute(f"UPDATE map_nodes SET {', '.join(fields)} WHERE id=? AND map_id=?",
                                                      (*params, node, map_id)).rowcount > 0
                _set_style(tx, map_id, node, style)
                return [], int(changed or style is not None), None
            if kind == "delete":
                node = need(op.get("id"))
                row = tx.execute("SELECT deleted_at FROM map_nodes WHERE id=? AND map_id=?", (node, map_id)).fetchone()
                if not row:
                    raise LookupError(f"unknown node {_short(node)}")
                if row["deleted_at"]:
                    return [], 0, None  # already gone (deleted elsewhere): nothing to do
                # Deleting a node deletes its subtree, as in any outliner.
                doomed, frontier = {node}, {node}
                while frontier:
                    marks = ",".join("?" * len(frontier))
                    kids = {r["id"] for r in tx.execute(f"SELECT id FROM map_nodes WHERE map_id=? AND parent IN ({marks}) AND deleted_at IS NULL",
                                                        (map_id, *frontier))}
                    frontier = kids - doomed
                    doomed |= kids
                for d in doomed:
                    tx.execute("UPDATE map_nodes SET deleted_at=? WHERE id=? AND map_id=?", (now(), d, map_id))
                    tx.execute("UPDATE map_edges SET deleted_at=? WHERE map_id=? AND (src=? OR dst=?) AND deleted_at IS NULL",
                               (now(), map_id, d, d))
                return [], 1, None
            if kind == "edge":
                label, style = _str_field(op, "label") or "", _style_field(op)
                if op.get("id") is not None:
                    if not CLIENT_ID.match(str(op["id"])):
                        raise ValueError("edge id must be 1-48 letters, digits, - or _")
                    eid = _full(map_id, str(op["id"]))
                    old = tx.execute("SELECT deleted_at FROM map_edges WHERE id=?", (eid,)).fetchone()
                    if old and not old["deleted_at"]:
                        raise ValueError(f"edge id {op['id']} already exists")
                    if old:
                        tx.execute("DELETE FROM map_edges WHERE id=?", (eid,))
                else:
                    eid = _next_edge_id(tx, map_id)
                src, dst = nid(op.get("src")), nid(op.get("dst"))
                for end in (src, dst):
                    _live_node(tx, map_id, end)
                tx.execute("INSERT INTO map_edges(id,map_id,src,dst,label,style,created_by,created_at) VALUES(?,?,?,?,?,?,?,?)",
                           (eid, map_id, src, dst, label, json.dumps(style or {}), actor.label, now()))
                return [_short(eid)], 0, None
            if kind == "edge_update":
                eid = need(op.get("id"))
                if not tx.execute("SELECT 1 FROM map_edges WHERE id=? AND map_id=? AND deleted_at IS NULL", (eid, map_id)).fetchone():
                    raise LookupError(f"unknown edge {_short(eid)}")
                style = _style_field(op)
                if "label" in op:
                    tx.execute("UPDATE map_edges SET label=? WHERE id=? AND map_id=?", (_str_field(op, "label") or "", eid, map_id))
                if style is not None:
                    tx.execute("UPDATE map_edges SET style=? WHERE id=? AND map_id=?", (json.dumps(style), eid, map_id))
                return [], 1, None
            if kind == "unedge":
                eid = need(op.get("id"))
                if not tx.execute("SELECT 1 FROM map_edges WHERE id=? AND map_id=?", (eid, map_id)).fetchone():
                    raise LookupError(f"unknown edge {_short(eid)}")
                tx.execute("UPDATE map_edges SET deleted_at=? WHERE id=? AND map_id=? AND deleted_at IS NULL", (now(), eid, map_id))
                return [], 1, None
            if kind == "move":
                node = need(op.get("id"))
                _live_node(tx, map_id, node)
                x, y = _num_field(op, "x"), _num_field(op, "y")
                _set_style(tx, map_id, node, _style_field(op) or {}, x, y)
                return [], 0, None
            if kind == "meta":
                meta_in = _style_field(op, "meta") or {}
                row = tx.execute("SELECT meta FROM maps WHERE id=?", (map_id,)).fetchone()
                meta = json.loads(row["meta"] or "{}")
                meta.update(meta_in)
                tx.execute("UPDATE maps SET meta=? WHERE id=?", (json.dumps(meta), map_id))
                return [], 1, None
            raise ValueError(f"unknown op {op}")

        for i, op in enumerate(ops):
            if not isinstance(op, dict):
                kind = None
            else:
                kind = op.get("op") or next((k for k in OPS if k in op), None)
            if kind in ("promote", "expand"):
                deferred.append((i, kind, op))
                results.append(None)
                continue
            tx.execute("SAVEPOINT map_op")
            try:
                if kind is None:
                    raise ValueError(f"unknown op {op!r}" if isinstance(op, dict) else "each op must be an object")
                added, changed, key = apply(kind, op)
            except Exception as exc:  # noqa: BLE001 — one bad op must not lose the others
                tx.execute("ROLLBACK TO map_op")
                tx.execute("RELEASE map_op")
                msg = _problem(exc)
                result["problems"].append(f"op {i + 1}: {msg}")
                results.append({"i": i, "ok": False, "error": msg})
                continue
            tx.execute("RELEASE map_op")
            result["added"] += added
            result["changed"] += changed
            if key:
                keys[key[0]] = key[1]
            results.append({"i": i, "ok": True})
            dirty = True
        if title is not None and title.strip() and title != before["title"]:
            tx.execute("UPDATE maps SET title=? WHERE id=?", (title, map_id))
            dirty = True
        if dirty:
            _bump(tx, map_id, project, len(ops))
    promoted = 0
    for i, kind, op in deferred:
        try:
            if kind == "expand":
                result["added"] += expand(lg, actor, project, map_id, op.get("id"), op.get("ref"), op.get("kinds"))
            else:
                promoted += _promote(lg, actor, project, map_id, op)
                result["changed"] += 1
            results[i] = {"i": i, "ok": True}
        except Exception as exc:  # noqa: BLE001
            msg = _problem(exc)
            result["problems"].append(f"{kind}: {msg}")
            results[i] = {"i": i, "ok": False, "error": msg}
    if promoted:
        with lg.db.tx(actor) as tx:
            _bump(tx, map_id, project, promoted)
    with lg.db.read() as conn:
        result["version"] = conn.execute("SELECT version FROM maps WHERE id=?", (map_id,)).fetchone()[0]
    result["results"] = results
    return result


def _promote(lg: Ledger, actor: Actor, project: str, map_id: str, op: dict) -> int:
    """Record a map edge as a typed link. Only a live edge of this map (which `_map` already scoped to the project)
    between live nodes of this map is passed on."""
    ref = op["promote"] if isinstance(op.get("promote"), str) else op.get("edge")
    if not isinstance(ref, str) or not ref:
        raise ValueError("promote needs an edge id")
    with lg.db.read() as conn:
        edge = conn.execute("SELECT * FROM map_edges WHERE id=? AND map_id=? AND deleted_at IS NULL", (_full(map_id, ref), map_id)).fetchone()
        if not edge:
            raise LookupError(f"unknown edge {ref}")
        ends = [conn.execute("SELECT ref FROM map_nodes WHERE id=? AND map_id=? AND deleted_at IS NULL", (end, map_id)).fetchone()
                for end in (edge["src"], edge["dst"])]
    refs = [e["ref"] if e else None for e in ends]
    if not all(refs) or any(r.startswith(("tag:", "map:", "n:")) for r in refs):
        raise ValueError("both ends must be papers or typed items (topic, method, dataset, …)")
    relation = op.get("relation") or edge["label"] or "about"
    if not isinstance(relation, str) or (op.get("evidence") is not None and not isinstance(op.get("evidence"), str)):
        raise ValueError("relation and evidence must be strings")
    res = add_links(lg, actor, project, [{"source": refs[0], "target": refs[1], "relation": relation,
                                          "evidence": op.get("evidence"), "map_edge": edge["id"]}])[0]
    if res["status"] != "ok":
        raise ValueError(res.get("message") or "link rejected")
    return 1


def expand(lg: Ledger, actor: Actor, project: str, map_id: str, node: str | None, ref: str | None,
           kinds: list[str] | None = None) -> list[str]:
    """Add the ledger's linked items under a node (or under a new top-level node for `ref`), grouped by kind — e.g. a
    paper's methods, datasets, benchmarks and questions (results only when kinds includes "result"; they live on
    the leaderboard). Items already on the map are skipped."""
    with lg.db.read() as conn:
        if node:
            row = conn.execute("SELECT * FROM map_nodes WHERE id=? AND map_id=? AND deleted_at IS NULL", (_full(map_id, node), map_id)).fetchone()
            if not row or not row["ref"]:
                raise ValueError("expand needs a node that references a paper or typed item")
            center = row["ref"]
        else:
            center = _ref_id(conn, project, ref or "")
        linked: list[tuple[str, str]] = []  # (kind, ref)
        for r in conn.execute("SELECT source, target, relation FROM links WHERE (source=? OR target=?) AND project IN (?, '') "
                              "AND retracted_at IS NULL ORDER BY created_at", (center, center, project)):
            other = r["target"] if r["source"] == center else r["source"]
            k = kind_of(other) or ("work" if is_work_id(other) else None)
            if k and ((not kinds and k != "result") or (kinds and k in kinds)):
                linked.append((k, other))
        if is_work_id(center):  # papers this one cites, from its stored full text
            for r in conn.execute("SELECT DISTINCT r.resolved_work_id AS w FROM refs_extracted r JOIN documents d ON d.id=r.document_id "
                                  "WHERE d.work_id=? AND r.resolved_work_id IS NOT NULL", (center,)):
                if not kinds or "work" in kinds:
                    linked.append(("work", r["w"]))
        # Anything already somewhere on the map is not added again.
        existing = {r["ref"] for r in conn.execute("SELECT ref FROM map_nodes WHERE map_id=? AND ref IS NOT NULL AND deleted_at IS NULL",
                                                   (map_id,))}
    order = [k for k in KINDS] + ["work"]
    groups: dict[str, list[str]] = {}
    for k, other in linked:
        if other not in existing and other not in groups.setdefault(k, []):
            groups[k].append(other)
    ops: list[dict] = []
    if not node:
        ops.append({"op": "add", "key": "center", "ref": center})
    anchor = node or "center"
    for k in sorted(groups, key=lambda x: order.index(x) if x in order else 99):
        items = groups[k]
        if not items:
            continue
        label = {"work": "papers"}.get(k, k + ("es" if k.endswith("s") else "s"))
        if len(items) == 1:
            ops.append({"op": "add", "ref": items[0], "parent": anchor})
        else:
            ops.append({"op": "add", "key": f"g-{k}", "text": label, "parent": anchor,
                        **({"style": {"expanded": False}} if len(items) > 8 else {})})
            ops += [{"op": "add", "ref": it, "parent": f"g-{k}"} for it in items]
    if len(ops) <= (0 if node else 1):
        return []
    res = edit(lg, actor, project, map_id, ops)
    return res["added"]


def _node_view(conn: sqlite3.Connection, project: str, n: sqlite3.Row) -> dict:
    label, title, kind, extra = None, None, "text", {}
    ref = n["ref"]
    if ref:
        if is_work_id(ref):
            w = conn.execute("SELECT citekey, title, year FROM works WHERE id=?", (ref,)).fetchone()
            label, title, kind = (w["citekey"], w["title"], "work") if w else (ref, None, "work")
            if w:
                extra["year"] = w["year"]
        elif ref.startswith("n:"):
            note = conn.execute("SELECT text, quote, subject, verification FROM notes WHERE id=?", (ref,)).fetchone()
            subj = conn.execute("SELECT citekey FROM works WHERE id=?", (note["subject"],)).fetchone() if note else None
            label, title, kind = ref[:10], (note["quote"] or note["text"]) if note else None, "note"
            if note:
                extra.update(subject=subj["citekey"] if subj else note["subject"], verified=note["verification"] == "verified")
        elif ref.startswith("tag:"):
            count = conn.execute("SELECT count(*) FROM taggings g JOIN tags t ON t.id=g.tag_id WHERE t.name=? AND "
                                 "g.removed_at IS NULL AND (g.project=? OR g.project='')", (ref[4:], project)).fetchone()[0]
            label, title, kind = ref, ref[4:], "tag"
            extra["count"] = count
        elif ref.startswith("map:"):
            other = conn.execute("SELECT title FROM maps WHERE id=?", (ref,)).fetchone()
            label, title, kind = ref, other["title"] if other else None, "map"
        else:
            e = conn.execute("SELECT title, kind, data FROM entities WHERE id=?", (ref,)).fetchone()
            label, title, kind = ref, e["title"] if e else None, e["kind"] if e else (kind_of(ref) or "entity")
            if e and e["data"] and e["data"] != "{}":
                extra["data"] = public_data(e["data"])
    return {"label": label, "title": title, "kind": kind, **extra}


def get_map(lg: Ledger, project: str, ref: str) -> dict:
    with lg.db.read() as conn:
        m = _map(conn, project, ref)
        nodes = conn.execute("SELECT * FROM map_nodes WHERE map_id=? AND deleted_at IS NULL ORDER BY COALESCE(seq, 1e12), created_at",
                             (m["id"],)).fetchall()
        edges = conn.execute("SELECT * FROM map_edges WHERE map_id=? AND deleted_at IS NULL ORDER BY created_at", (m["id"],)).fetchall()
        layout = {r["node_id"]: dict(r) for r in conn.execute("SELECT * FROM map_layout WHERE map_id=?", (m["id"],))}
        out_nodes = []
        for n in nodes:
            view = _node_view(conn, project, n)
            lay = layout.get(n["id"], {})
            out_nodes.append({"id": _short(n["id"]), "ref": view.pop("label"), "title": view.pop("title"), "kind": view.pop("kind"),
                              "text": n["text"], "parent": _short(n["parent"]) if n["parent"] else None, "seq": n["seq"],
                              "by": n["created_by"], "x": lay.get("x"), "y": lay.get("y"),
                              "style": json.loads(lay.get("style") or "{}"), **view})
        out_edges = [{"id": _short(e["id"]), "src": _short(e["src"]), "dst": _short(e["dst"]), "label": e["label"],
                      "promoted": bool(e["promoted_link_id"]), "by": e["created_by"], "style": json.loads(e["style"] or "{}")}
                     for e in edges]
    return {"id": m["id"], "title": m["title"], "nodes": out_nodes, "edges": out_edges, "updated_at": m["updated_at"],
            "meta": json.loads(m["meta"] or "{}"), "version": m["version"]}


def _forest(nodes: list[dict]) -> list[tuple[int, dict]]:
    """Nodes in outline order with their depth. A node whose parent is missing, or that sits in a parent loop (old
    data), shows as a top-level item, so every node appears exactly once."""
    ids = {n["id"] for n in nodes}
    children: dict[str | None, list[dict]] = {}
    for n in nodes:
        parent = n.get("parent")
        children.setdefault(parent if parent in ids and parent != n["id"] else None, []).append(n)
    out: list[tuple[int, dict]] = []
    seen: set[str] = set()

    def walk(root: dict) -> None:
        stack = [(root, 0)]
        while stack:
            n, depth = stack.pop()
            if n["id"] in seen:
                continue
            seen.add(n["id"])
            out.append((depth, n))
            stack += [(k, depth + 1) for k in reversed(children.get(n["id"], [])) if k["id"] not in seen]

    for n in children.get(None, []):
        walk(n)
    for n in nodes:
        if n["id"] not in seen:
            walk(n)
    return out


def outline(data: dict) -> str:
    """Compact indented outline for agents: geometry dropped, item kinds shown, agent-made nodes not marked."""
    lines = [f"{data['id']} · {data['title']} · {len(data['nodes'])} nodes"]

    def label(n: dict) -> str:
        if n["ref"]:
            if n["kind"] == "work":
                body = n["ref"] + (f" ({clip(n['title'], 50)})" if n.get("title") else "")
            elif n["kind"] in ("tag", "map"):
                body = n["ref"]
            elif n["kind"] == "note":
                body = f"{n['ref']} note on {n.get('subject', '?')}: \"{clip(n.get('title') or '', 60)}\""
            else:
                body = n["ref"] + (f" — {clip(n['title'], 60)}" if n.get("title") else "")
        else:
            body = f'"{n["text"]}"'
        if n.get("text") and n["ref"]:
            body += f' "{n["text"]}"'
        return f"{n['id']} {body}"

    for depth, n in _forest(data["nodes"]):
        lines.append("  " * depth + "- " + label(n))
    if data["edges"]:
        lines.append("edges:")
        for e in data["edges"]:
            lines.append(f"  {e['id']} {e['src']} -{e['label'] or ''}-> {e['dst']}" + (" [link]" if e["promoted"] else ""))
    return "\n".join(lines)


def _mermaid_text(text: str) -> str:
    """Text for a quoted Mermaid label: one line, no double quotes or backticks (they would end the string)."""
    text = re.sub(r"\s+", " ", text or "").strip().replace('"', "'").replace("`", "'")
    return text or "…"


def to_mermaid(data: dict) -> str:
    out = ["mindmap", f'  root(("{_mermaid_text(data["title"])}"))']
    for i, (depth, n) in enumerate(_forest(data["nodes"])):
        text = n.get("text") or n.get("title") or n["ref"] or ""
        out.append("  " * (depth + 2) + f'n{i + 1}["{_mermaid_text(text)}"]')
    return "\n".join(out)


def to_canvas(data: dict) -> dict:
    """JSON Canvas 1.0 (jsoncanvas.org)."""
    nodes = []
    ids = {n["id"] for n in data["nodes"]}
    for i, n in enumerate(data["nodes"]):
        text = (f"[[{n['ref']}]] " if n["ref"] else "") + (n.get("text") or n.get("title") or "")
        nodes.append({"id": n["id"], "type": "text", "text": text.strip(), "x": int(n.get("x") or (i % 6) * 280),
                      "y": int(n.get("y") or (i // 6) * 160), "width": 250, "height": 80})
    edges = [{"id": e["id"], "fromNode": e["src"], "toNode": e["dst"], **({"label": e["label"]} if e["label"] else {})} for e in data["edges"]]
    edges += [{"id": f"p-{n['id']}", "fromNode": n["parent"], "toNode": n["id"]} for n in data["nodes"] if n.get("parent") in ids]
    return {"nodes": nodes, "edges": edges}


_XML_BAD = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]")


def to_opml(data: dict) -> str:
    from xml.sax.saxutils import escape, quoteattr

    def clean(s: str) -> str:
        return _XML_BAD.sub("", s or "")

    body: list[str] = []
    items = _forest(data["nodes"])
    open_depth = 0
    for i, (depth, n) in enumerate(items):
        while open_depth > depth:
            open_depth -= 1
            body.append("  " * (open_depth + 2) + "</outline>")
        text = clean((n["ref"] + ": " if n["ref"] else "") + (n.get("text") or n.get("title") or ""))
        has_kids = i + 1 < len(items) and items[i + 1][0] > depth
        body.append("  " * (depth + 2) + f"<outline text={quoteattr(text)}" + (">" if has_kids else "/>"))
        if has_kids:
            open_depth = depth + 1
    while open_depth > 0:
        open_depth -= 1
        body.append("  " * (open_depth + 2) + "</outline>")
    title = escape(clean(data["title"]))
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<opml version="2.0">\n  <head><title>{title}</title></head>\n  <body>\n' + \
        "\n".join(body) + "\n  </body>\n</opml>"


def import_outline(lg: Ledger, actor: Actor, project: str, title: str, text: str) -> dict:
    """Markdown bullet outline -> map. A bullet starting with a known citekey/ID/entity id becomes a reference."""
    ops, stack = [], []
    for i, line in enumerate(text.splitlines()):
        m = re.match(r"^(\s*)[-*+]\s+(.*)$", line)
        if not m:
            continue
        depth = len(m.group(1).replace("\t", "  ")) // 2
        content = m.group(2).strip()
        stack = stack[:depth]
        key = f"k{i}"
        op = {"op": "add", "key": key, "parent": stack[-1] if stack else None}
        token = content.split()[0] if content else ""
        with lg.db.read() as conn:
            try:
                ref = _ref_id(conn, project, token) if token else None
            except LookupError:
                ref = None
        if ref:
            op["ref"] = token
            rest = content[len(token):].strip(" -—:")
            if rest:
                op["text"] = rest
        else:
            op["text"] = content
        ops.append(op)
        stack.append(key)
    return edit(lg, actor, project, None, ops, title=title)
