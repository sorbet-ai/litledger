"""General-purpose tags: project or library-wide, namespaced by convention (`phase:baselines`)."""
from __future__ import annotations

import re
import sqlite3

from rapidfuzz.distance import Levenshtein

from .db import Tx, now, ulid

TARGET_TYPES = ("work", "note", "entity", "passage", "map")


def norm_tag(name: str) -> str:
    name = re.sub(r"\s+", "-", (name or "").strip().lower())
    name = re.sub(r"[^a-z0-9:_\-./+#]", "", name)
    return name.strip("-:")


def _find(conn: sqlite3.Connection, project: str, name: str, include_archived: bool = False) -> sqlite3.Row | None:
    arch = "" if include_archived else " AND archived_at IS NULL"
    row = conn.execute(f"SELECT * FROM tags WHERE project=? AND name=?{arch}", (project, name)).fetchone()
    return row or conn.execute(f"SELECT * FROM tags WHERE project='' AND name=?{arch}", (name,)).fetchone()


def visible_tags(conn: sqlite3.Connection, project: str) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM tags WHERE (project=? OR project='') AND archived_at IS NULL ORDER BY name",
                        (project,)).fetchall()


def suggest(conn: sqlite3.Connection, project: str, name: str) -> str | None:
    """Closest existing tag when `name` looks like a variant (edit distance ≤2, or singular/plural)."""
    best, best_d = None, 3
    for row in visible_tags(conn, project):
        other = row["name"]
        if other == name:
            return None
        d = Levenshtein.distance(name, other)
        if other.rstrip("s") == name.rstrip("s"):
            d = min(d, 1)
        if d < best_d and (":" not in name or name.split(":")[0] == other.split(":")[0] or d == 1):
            best, best_d = other, d
    return best


def ensure_tag(tx: Tx, project: str, name: str, scope: str = "project", description: str | None = None) -> tuple[str, bool]:
    """Tag id plus whether it was created. scope='library' creates a library-wide tag."""
    name = norm_tag(name)
    if not name:
        raise ValueError("empty tag name")
    row = _find(tx.conn, project, name, include_archived=True)
    if row:
        if row["archived_at"]:
            tx.execute("UPDATE tags SET archived_at=NULL WHERE id=?", (row["id"],))
        if description and not row["description"]:
            tx.execute("UPDATE tags SET description=? WHERE id=?", (description, row["id"]))
        return row["id"], False
    tag_id = "t" + ulid()
    tag_project = "" if scope == "library" else project
    tx.execute("INSERT INTO tags(id,project,name,description,created_by,created_at) VALUES(?,?,?,?,?,?)",
               (tag_id, tag_project, name, description, tx.actor.label, now()))
    tx.log("tag.create", {"tag": name, "scope": scope, "description": description}, project=project)
    return tag_id, True


def apply(tx: Tx, project: str, names: list[str], target_type: str, target_ids: list[str], scope: str = "project") -> dict:
    """Apply tags; returns {'created': [...], 'hints': {new: existing}}."""
    if target_type not in TARGET_TYPES:
        raise ValueError(f"target_type must be one of {', '.join(TARGET_TYPES)}")
    names = [names] if isinstance(names, str) else list(names or [])  # "phase:x" is one tag, never its letters
    created, hints = [], {}
    for raw in names:
        name = norm_tag(raw)
        if not name:
            continue
        hint = suggest(tx.conn, project, name) if not _find(tx.conn, project, name) else None
        tag_id, was_created = ensure_tag(tx, project, name, scope)
        if was_created:
            created.append(name)
            if hint:
                hints[name] = hint
        tag_project = tx.execute("SELECT project FROM tags WHERE id=?", (tag_id,)).fetchone()["project"]
        for target in target_ids:
            tx.execute("INSERT INTO taggings(tag_id,target_type,target_id,project,principal_id,agent,at) VALUES(?,?,?,?,?,?,?) "
                       "ON CONFLICT(tag_id,target_type,target_id,project) DO UPDATE SET removed_at=NULL",
                       (tag_id, target_type, target, tag_project, tx.actor.principal_id, tx.actor.agent, now()))
        tx.log("tag.apply", {"tag": name, "type": target_type, "targets": target_ids}, project=project)
    return {"created": created, "hints": hints}


def remove(tx: Tx, project: str, names: list[str], target_type: str, target_ids: list[str]) -> int:
    names = [names] if isinstance(names, str) else list(names or [])
    n = 0
    for raw in names:
        row = _find(tx.conn, project, norm_tag(raw))
        if not row:
            continue
        for target in target_ids:
            n += tx.execute("UPDATE taggings SET removed_at=? WHERE tag_id=? AND target_type=? AND target_id=? AND removed_at IS NULL",
                            (now(), row["id"], target_type, target)).rowcount
        tx.log("tag.remove", {"tag": row["name"], "type": target_type, "targets": target_ids}, project=project)
    return n


def define(tx: Tx, project: str, name: str, description: str | None = None, color: str | None = None,
           scope: str = "project") -> str:
    tag_id, _ = ensure_tag(tx, project, name, scope, description)
    if description is not None:
        tx.execute("UPDATE tags SET description=? WHERE id=?", (description, tag_id))
    if color is not None:
        tx.execute("UPDATE tags SET color=? WHERE id=?", (color, tag_id))
    tx.log("tag.define", {"tag": norm_tag(name), "description": description, "color": color}, project=project)
    return tag_id


def _shared_change(tx: Tx, row) -> None:
    """Renaming, merging away or archiving a library-wide tag changes every project (Actor.check_library_change)."""
    if row["project"] == "":
        tx.actor.check_library_change(f"library tag {row['name']}")


def rename(tx: Tx, project: str, old: str, new: str) -> None:
    row = _find(tx.conn, project, norm_tag(old))
    if not row:
        raise ValueError(f"unknown tag {old}")
    _shared_change(tx, row)
    new = norm_tag(new)
    if _find(tx.conn, project, new):
        merge(tx, project, old, new)
        return
    tx.execute("UPDATE tags SET name=? WHERE id=?", (new, row["id"]))
    tx.log("tag.rename", {"from": row["name"], "to": new}, project=project)


def merge(tx: Tx, project: str, source: str, target: str) -> int:
    src = _find(tx.conn, project, norm_tag(source))
    dst = _find(tx.conn, project, norm_tag(target))
    if not src or not dst:
        raise ValueError("both tags must exist")
    _shared_change(tx, src)
    moved = 0
    for t in tx.execute("SELECT * FROM taggings WHERE tag_id=? AND removed_at IS NULL", (src["id"],)).fetchall():
        moved += tx.execute("INSERT OR IGNORE INTO taggings(tag_id,target_type,target_id,project,principal_id,agent,at) "
                            "VALUES(?,?,?,?,?,?,?)", (dst["id"], t["target_type"], t["target_id"], dst["project"],
                                                      t["principal_id"], t["agent"], t["at"])).rowcount
    tx.execute("UPDATE taggings SET removed_at=? WHERE tag_id=? AND removed_at IS NULL", (now(), src["id"]))
    tx.execute("UPDATE tags SET archived_at=? WHERE id=?", (now(), src["id"]))
    tx.log("tag.merge", {"from": src["name"], "to": dst["name"], "moved": moved}, project=project)
    return moved


def archive(tx: Tx, project: str, name: str) -> None:
    row = _find(tx.conn, project, norm_tag(name))
    if not row:
        raise ValueError(f"unknown tag {name}")
    _shared_change(tx, row)
    tx.execute("UPDATE tags SET archived_at=? WHERE id=?", (now(), row["id"]))
    tx.log("tag.archive", {"tag": row["name"]}, project=project)


def listing(conn: sqlite3.Connection, project: str) -> list[dict]:
    out = []
    for row in visible_tags(conn, project):
        count = conn.execute("SELECT count(*) FROM taggings WHERE tag_id=? AND removed_at IS NULL AND (project=? OR project='')",
                             (row["id"], project)).fetchone()[0]
        out.append({"name": row["name"], "scope": "library" if row["project"] == "" else "project", "count": count,
                    "description": row["description"], "color": row["color"]})
    return out


def tags_for(conn: sqlite3.Connection, project: str, target_type: str, ids: list[str]) -> dict[str, list[str]]:
    if not ids:
        return {}
    out: dict[str, list[str]] = {i: [] for i in ids}
    marks = ",".join("?" * len(ids))
    for r in conn.execute(f"SELECT g.target_id, t.name FROM taggings g JOIN tags t ON t.id=g.tag_id "
                          f"WHERE g.target_type=? AND g.target_id IN ({marks}) AND g.removed_at IS NULL "
                          f"AND t.archived_at IS NULL AND (g.project=? OR g.project='') ORDER BY t.name",
                          (target_type, *ids, project)):
        out[r["target_id"]].append(r["name"])
    return out


def _match_clause(name: str) -> tuple[str, str]:
    wildcard = name.strip().endswith("*")
    name = norm_tag(name.strip().rstrip("*"))
    if wildcard:
        return "t.name LIKE ?", name.replace("%", "") + "%"
    return "t.name = ?", name


def filter_sql(project: str, all_tags: list[str] | None = None, any_tags: list[str] | None = None,
               none_tags: list[str] | None = None, target_type: str = "work", column: str = "w.id") -> tuple[str, list]:
    """SQL condition (and params) restricting `column` by tag expressions; `ns:*` matches a namespace."""
    clauses: list[str] = []
    params: list = []
    base = ("SELECT 1 FROM taggings g JOIN tags t ON t.id=g.tag_id WHERE g.target_type=? AND g.target_id=" + column +
            " AND g.removed_at IS NULL AND t.archived_at IS NULL AND (g.project=? OR g.project='') AND ")
    for name in all_tags or []:
        cond, value = _match_clause(name)
        clauses.append(f"EXISTS({base}{cond})")
        params += [target_type, project, value]
    if any_tags:
        conds, values = zip(*(_match_clause(n) for n in any_tags))
        clauses.append(f"EXISTS({base}({' OR '.join(conds)}))")
        params += [target_type, project, *values]
    for name in none_tags or []:
        cond, value = _match_clause(name)
        clauses.append(f"NOT EXISTS({base}{cond})")
        params += [target_type, project, value]
    return " AND ".join(clauses), params
