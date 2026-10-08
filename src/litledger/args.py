"""Arguments as agents and the web UI write them, shared by the MCP tools and the REST API: project names, tag
expressions and year ranges."""
from __future__ import annotations

import re


def project_slug(value: str) -> str:
    """A project id from a name. Project ids keep this original rule (one '-' per other character, nothing trimmed;
    textutil.slug collapses and trims) so every existing project keeps the id its name always mapped to."""
    return re.sub(r"[^a-z0-9_\-.]", "-", value.strip().lower())[:60] or "default"


def projects_arg(values) -> list[str] | None:
    """A list of project names as project ids; an empty list means all projects (None)."""
    return [project_slug(str(x)) for x in values or []] or None


def tag_expr(values) -> tuple[list[str], list[str], list[str]]:
    """Tag filters ['a', 'b|c', '-d'] -> (all of, any of, none of)."""
    all_t, any_t, not_t = [], [], []
    for v in values or []:
        v = str(v).strip()
        if not v:
            continue
        if v.startswith("-"):
            not_t.append(v[1:])
        elif "|" in v:
            any_t.extend(p for p in v.split("|") if p)
        else:
            all_t.append(v)
    return all_t, any_t, not_t


def year_range(value) -> tuple[int | None, int | None]:
    """'2020-2024', '2021-', '-2019' or '2022' -> (from, to)."""
    if not value:
        return None, None
    m = re.match(r"^\s*(\d{4})?\s*(-)?\s*(\d{4})?\s*$", str(value))
    if not m:
        return None, None
    a, dash, b = m.group(1), m.group(2), m.group(3)
    if a and not dash:
        return int(a), int(a)
    return (int(a) if a else None), (int(b) if b else None)
