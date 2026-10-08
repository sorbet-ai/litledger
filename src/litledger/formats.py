"""Bibliographic formats in and out: a tolerant BibTeX parser and a BibTeX/BibLaTeX writer, RIS both ways, CSL-JSON,
and `export`, which writes a project's (or selected/tagged) works in any of them (portable.py writes snapshots)."""
from __future__ import annotations

import json
import re
import sqlite3

from pylatexenc.latex2text import LatexNodes2Text
from pylatexenc.latexencode import UnicodeToLatexEncoder

from .csl import csl_date, person, year_of
from .ids import norm_doi, safe_url
from .ledger import Ledger
from .search import work_filter
from .works import project_state, require_work, work_ids

_l2t = LatexNodes2Text(math_mode="verbatim", strict_latex_spaces=True)
_u2l = UnicodeToLatexEncoder(unknown_char_policy="keep", replacement_latex_protection="braces-all")

MONTHS = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}


# ------------------------------------------------------------------------------------------------ BibTeX in
def _read_value(text: str, i: int, strings: dict[str, str]) -> tuple[str, int]:
    parts = []
    while i < len(text):
        while i < len(text) and text[i] in " \t\r\n":
            i += 1
        if i >= len(text):
            break
        ch = text[i]
        if ch == "{":
            depth, j = 1, i + 1
            while j < len(text) and depth:
                if text[j] == "\\":
                    j += 2
                    continue
                depth += {"{": 1, "}": -1}.get(text[j], 0)
                j += 1
            parts.append(text[i + 1:j - 1])
            i = j
        elif ch == '"':
            depth, j = 0, i + 1
            while j < len(text):
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == "{":
                    depth += 1
                elif text[j] == "}":
                    depth -= 1
                elif text[j] == '"' and depth == 0:
                    break
                j += 1
            parts.append(text[i + 1:j])
            i = j + 1
        else:
            m = re.match(r"[\w\-:.]+", text[i:])
            if not m:
                break
            word = m.group(0)
            parts.append(strings.get(word.lower(), word))
            i += len(word)
        while i < len(text) and text[i] in " \t\r\n":
            i += 1
        if i < len(text) and text[i] == "#":
            i += 1
            continue
        break
    return "".join(parts), i


def parse_bibtex(text: str) -> list[dict]:
    """Entries as {'ENTRYTYPE','ID', field: raw value} (raw LaTeX kept; see entry_to_csl)."""
    entries, strings = [], {}
    for m in re.finditer(r"@(\w+)\s*([{(])", text):
        etype = m.group(1).lower()
        start = m.end()
        # find matching close
        depth, j = 1, start
        open_ch, close_ch = m.group(2), "}" if m.group(2) == "{" else ")"
        while j < len(text) and depth:
            if text[j] == "\\":
                j += 2
                continue
            if text[j] == open_ch:
                depth += 1
            elif text[j] == close_ch:
                depth -= 1
            j += 1
        body = text[start:j - 1]
        if etype in ("comment", "preamble"):
            continue
        if etype == "string":
            sm = re.match(r"\s*([\w\-]+)\s*=\s*", body)
            if sm:
                value, _ = _read_value(body, sm.end(), strings)
                strings[sm.group(1).lower()] = value
            continue
        key, _, rest = body.partition(",")
        entry = {"ENTRYTYPE": etype, "ID": key.strip()}
        i = 0
        while i < len(rest):
            fm = re.compile(r"\s*,?\s*([\w\-:.]+)\s*=\s*").match(rest, i)
            if not fm:
                break
            value, i = _read_value(rest, fm.end(), strings)
            entry[fm.group(1).lower()] = value.strip()
        entries.append(entry)
    return entries


def latex_to_text(value: str) -> str:
    if not value:
        return ""
    try:
        out = _l2t.latex_to_text(value)
    except Exception:
        out = value.replace("{", "").replace("}", "")
    return re.sub(r"\s+", " ", out).strip()


def split_authors(value: str) -> list[dict]:
    names = [n.strip() for n in re.split(r"\s+and\s+", value or "", flags=re.I) if n.strip()]
    out = []
    for name in names:
        if name.lower() == "others":
            continue
        if name.startswith("{") and name.endswith("}") and name.count("{") == 1:
            out.append({"literal": latex_to_text(name)})
            continue
        out.append(person(literal=latex_to_text(name)))
    return [a for a in out if a]


BIB_TO_CSL = {"article": "article-journal", "inproceedings": "paper-conference", "conference": "paper-conference",
              "incollection": "chapter", "inbook": "chapter", "book": "book", "phdthesis": "thesis",
              "mastersthesis": "thesis", "techreport": "report", "misc": "article", "unpublished": "manuscript",
              "online": "webpage", "software": "software", "dataset": "dataset", "thesis": "thesis", "report": "report"}


def entry_to_csl(entry: dict) -> tuple[dict, dict[str, str]]:
    """CSL fields and identifiers from one parsed BibTeX entry."""
    f = entry
    csl = {"type": BIB_TO_CSL.get(f["ENTRYTYPE"], "article"), "title": latex_to_text(f.get("title", "")),
           "author": split_authors(f.get("author", "")) or split_authors(f.get("editor", ""))}
    year = re.search(r"\d{4}", f.get("year", "") or f.get("date", ""))
    month = MONTHS.get((f.get("month") or "")[:3].lower()) or (int(f["month"]) if (f.get("month") or "").isdigit() else None)
    if year:
        csl["issued"] = csl_date(year.group(0), month)
    container = f.get("journal") or f.get("journaltitle") or f.get("booktitle")
    if container:
        csl["container-title"] = latex_to_text(container)
    for src, dst in (("volume", "volume"), ("number", "issue"), ("pages", "page"), ("publisher", "publisher"),
                     ("abstract", "abstract"), ("url", "URL"), ("isbn", "ISBN"), ("issn", "ISSN")):
        if f.get(src):
            csl[dst] = latex_to_text(f[src]) if dst not in ("URL",) else f[src].strip()  # unsafe URLs: dropped by resolve
    if csl.get("page"):
        csl["page"] = csl["page"].replace("--", "-")
    ids: dict[str, str] = {}
    if f.get("doi"):
        doi = norm_doi(f["doi"])
        if doi.startswith("10.48550/arxiv."):
            ids["arxiv"] = doi.split("arxiv.", 1)[1]
        else:
            ids["doi"] = doi
            csl["DOI"] = doi
    eprint = f.get("eprint") or ""
    if eprint and ((f.get("archiveprefix") or f.get("eprinttype") or "").lower() == "arxiv" or re.match(r"^\d{4}\.\d{4,5}", eprint)):
        ids["arxiv"] = re.sub(r"v\d+$", "", eprint.strip())
    jr = f.get("journal", "")
    m = re.search(r"arxiv[:\s]*(?:abs/)?(\d{4}\.\d{4,5})", jr + " " + f.get("note", "") + " " + f.get("url", ""), re.I)
    if m and "arxiv" not in ids:
        ids["arxiv"] = m.group(1)
    if ids.get("arxiv") and csl["type"] == "article" and not csl.get("container-title"):
        csl["container-title"] = "arXiv"
    return {k: v for k, v in csl.items() if v}, ids


# ------------------------------------------------------------------------------------------------ BibTeX out
_SPECIAL = str.maketrans({"\\": r"\textbackslash{}", "{": r"\{", "}": r"\}", "&": r"\&", "%": r"\%", "$": r"\$",
                          "#": r"\#", "_": r"\_", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"})
# Inline math as arXiv titles carry it ("Linear $\mathcal{O}(n)$ Attention"): kept verbatim, never escaped.
_MATH = re.compile(r"(?<!\\)\$(?:\\.|[^$\\])+?(?<!\\)\$")


def _escape_text(value: str, ascii_only: bool) -> str:
    value = value.translate(_SPECIAL)
    if ascii_only:
        value = _u2l.unicode_to_latex(value)
    return value


def _segments(value: str) -> list[tuple[bool, str]]:
    """(is_math, text) pieces of a value; an unpaired $ is plain text."""
    out, pos = [], 0
    for m in _MATH.finditer(value):
        if m.start() > pos:
            out.append((False, value[pos:m.start()]))
        out.append((True, m.group(0)))
        pos = m.end()
    if pos < len(value):
        out.append((False, value[pos:]))
    return out


def _escape(value: str, ascii_only: bool) -> str:
    """Escape BibTeX/LaTeX specials outside $…$ math spans; whitespace (newlines too) collapses to single spaces."""
    value = re.sub(r"\s+", " ", value or "").strip()
    return "".join(text if math else _escape_text(text, ascii_only) for math, text in _segments(value))


def _protect_word(word: str, ascii_only: bool) -> str:
    core = word.strip(":,.;!?()")
    if core and (sum(c.isupper() for c in core) >= 2 or (core[:1].islower() and any(c.isupper() for c in core))
                 or (any(c.isdigit() for c in core) and any(c.isalpha() for c in core))):
        i = word.index(core)
        return _escape_text(word[:i], ascii_only) + "{" + _escape_text(core, ascii_only) + "}" + \
            _escape_text(word[i + len(core):], ascii_only)
    return _escape_text(word, ascii_only)


def _protect_title(title: str, ascii_only: bool) -> str:
    """Brace words BibTeX styles would wrongly lowercase (acronyms, CamelCase, words with digits) and math spans,
    which stay verbatim."""
    title = re.sub(r"\s+", " ", title or "").strip()
    out = []
    for math, text in _segments(title):
        if math:
            out.append("{" + text + "}")
        else:
            out.extend(part if part.isspace() else _protect_word(part, ascii_only) for part in re.split(r"(\s+)", text) if part)
    return "".join(out)


def _names(authors: list[dict], ascii_only: bool) -> str:
    out = []
    for a in authors:
        if a.get("family"):
            out.append(_escape(a["family"] + (", " + a["given"] if a.get("given") else ""), ascii_only))
        else:
            out.append("{" + _escape(a.get("literal", ""), ascii_only) + "}")
    return " and ".join(out)


def csl_to_bibtex(key: str, csl: dict, ids: dict[str, str], biblatex: bool = False, ascii_only: bool = False,
                  arxiv_class: str | None = None) -> str:
    ctype = csl.get("type", "article")
    year = year_of(csl)
    fields: list[tuple[str, str]] = [("title", _protect_title(csl.get("title", ""), ascii_only))]
    if csl.get("author"):
        fields.append(("author", _names(csl["author"], ascii_only)))
    container = csl.get("container-title", "")
    is_arxiv = bool(ids.get("arxiv")) and (ctype == "article" or container.lower() in ("arxiv", "corr", ""))
    if ctype == "paper-conference":
        etype = "inproceedings"
        if container:
            fields.append(("booktitle", _escape(container, ascii_only)))
    elif ctype in ("article-journal", "article-magazine", "article-newspaper") and not is_arxiv:
        etype = "article"
        if container:
            fields.append(("journaltitle" if biblatex else "journal", _escape(container, ascii_only)))
    elif ctype == "chapter":
        etype = "incollection"
        if container:
            fields.append(("booktitle", _escape(container, ascii_only)))
    elif ctype == "book":
        etype = "book"
    elif ctype == "thesis":
        etype = "phdthesis" if not biblatex else "thesis"
    elif ctype == "report":
        etype = "techreport" if not biblatex else "report"
    elif ctype == "software":
        etype = "software" if biblatex else "misc"
    elif ctype == "webpage":
        etype = "online" if biblatex else "misc"
    else:
        etype = "misc" if not biblatex else ("online" if is_arxiv else "misc")
    if year:
        fields.append(("date" if biblatex else "year", str(year)))
    for src, dst in (("volume", "volume"), ("issue", "number"), ("page", "pages"), ("publisher", "publisher")):
        if csl.get(src) and not (dst == "publisher" and is_arxiv):
            value = str(csl[src]).replace("-", "--") if dst == "pages" else str(csl[src])
            fields.append((dst, _escape(value, ascii_only)))
    if ids.get("doi") and not is_arxiv:
        fields.append(("doi", ids["doi"]))
    if is_arxiv:
        if biblatex:
            fields += [("eprint", ids["arxiv"]), ("eprinttype", "arxiv")]
        else:
            fields += [("eprint", ids["arxiv"]), ("archivePrefix", "arXiv")]
            if not any(k == "journal" for k, _ in fields):
                fields.append(("journal", f"arXiv preprint arXiv:{ids['arxiv']}"))
        if arxiv_class:
            fields.append(("eprintclass" if biblatex else "primaryClass", arxiv_class))
    url = csl.get("URL") or (f"https://arxiv.org/abs/{ids['arxiv']}" if ids.get("arxiv") else "")
    if url and (etype in ("misc", "online", "software") or is_arxiv or not ids.get("doi")):
        fields.append(("url", url))
    if ctype == "software" and csl.get("version"):
        fields.append(("version", str(csl["version"])))
    body = ",\n".join(f"  {k} = {{{v}}}" for k, v in fields if v)
    return f"@{etype}{{{key},\n{body}\n}}"


# ------------------------------------------------------------------------------------------------ RIS
RIS_IN = {"JOUR": "article-journal", "CPAPER": "paper-conference", "CONF": "paper-conference", "CHAP": "chapter",
             "BOOK": "book", "THES": "thesis", "RPRT": "report", "COMP": "software", "ELEC": "webpage", "DATA": "dataset",
             "UNPB": "article", "GEN": "article"}


def parse_ris(text: str) -> list[dict]:
    items, cur = [], None
    for line in text.splitlines():
        m = re.match(r"^([A-Z][A-Z0-9])\s{2}-\s?(.*)$", line)
        if not m:
            continue
        tag, value = m.group(1), m.group(2).strip()
        if tag == "TY":
            cur = {"type": RIS_IN.get(value, "article"), "author": []}
        elif cur is None:
            continue
        elif tag == "ER":
            items.append(cur)
            cur = None
        elif tag in ("TI", "T1"):
            cur["title"] = value
        elif tag in ("AU", "A1"):
            cur["author"].append(person(literal=value))
        elif tag in ("PY", "Y1", "DA"):
            year = re.search(r"\d{4}", value)
            if year:
                cur["issued"] = csl_date(year.group(0))
        elif tag in ("T2", "JO", "JF", "BT"):
            cur.setdefault("container-title", value)
        elif tag == "VL":
            cur["volume"] = value
        elif tag == "IS":
            cur["issue"] = value
        elif tag == "SP":
            cur["page"] = value
        elif tag == "EP" and cur.get("page"):
            cur["page"] += "-" + value
        elif tag == "DO":
            cur["DOI"] = value
        elif tag == "UR":
            cur.setdefault("URL", value)
        elif tag == "AB":
            cur["abstract"] = value
        elif tag == "PB":
            cur["publisher"] = value
        elif tag == "N1" and re.match(r"arxiv:", value, re.I):
            cur["arxiv"] = value.split(":", 1)[1].strip()
    return items


RIS_OUT = {"article-journal": "JOUR", "paper-conference": "CPAPER", "chapter": "CHAP", "book": "BOOK", "thesis": "THES",
             "report": "RPRT", "software": "COMP", "webpage": "ELEC", "dataset": "DATA", "article": "UNPB"}


def _ris_value(value) -> str:
    """One RIS line's value: RIS is line-based, so embedded newlines (which could start a fake tag) become spaces."""
    return re.sub(r"\s+", " ", str(value)).strip()


def csl_to_ris(key: str, csl: dict, ids: dict[str, str]) -> str:
    lines = [f"TY  - {RIS_OUT.get(csl.get('type', ''), 'GEN')}", f"ID  - {_ris_value(key)}",
             f"TI  - {_ris_value(csl.get('title', ''))}"]
    for a in csl.get("author", []):
        name = f"{a['family']}, {a.get('given', '')}".strip(", ") if a.get("family") else a.get("literal", "")
        lines.append("AU  - " + _ris_value(name))
    if year_of(csl):
        lines.append(f"PY  - {year_of(csl)}")
    for field, tag in (("container-title", "T2"), ("volume", "VL"), ("issue", "IS"), ("publisher", "PB"), ("abstract", "AB")):
        if csl.get(field):
            lines.append(f"{tag}  - {_ris_value(csl[field])}")
    if safe_url(csl.get("URL")):
        lines.append(f"UR  - {_ris_value(csl['URL'])}")
    if csl.get("page"):
        sp, _, ep = _ris_value(csl["page"]).partition("-")
        lines.append(f"SP  - {sp}")
        if ep:
            lines.append(f"EP  - {ep}")
    if ids.get("doi"):
        lines.append(f"DO  - {_ris_value(ids['doi'])}")
    if ids.get("arxiv"):
        lines.append(f"N1  - arXiv:{_ris_value(ids['arxiv'])}")
    lines.append("ER  - ")
    return "\n".join(lines)


# ------------------------------------------------------------------------------------------------ export
def export(lg: Ledger, project: str, fmt: str = "bibtex", works: list[str] | None = None, all_tags=None, any_tags=None,
           not_tags=None, ascii_only: bool = False) -> dict:
    fmt = fmt.lower()
    with lg.db.read() as conn:
        rows = export_rows(conn, project, works, all_tags, any_tags, not_tags)
        if fmt in ("bibtex", "biblatex"):
            entries, unverified = [], 0
            for row, state in rows:
                csl = json.loads(row["csl"])
                ids = work_ids(conn, row["id"])
                cls = None
                src = conn.execute("SELECT data FROM work_sources WHERE work_id=? AND provider='arxiv'", (row["id"],)).fetchone()
                if src:
                    cls = json.loads(src["data"]).get("extra", {}).get("primary_class")
                key = (state or {}).get("citekey_override") or row["citekey"]
                entries.append(csl_to_bibtex(key, csl, ids, biblatex=fmt == "biblatex", ascii_only=ascii_only, arxiv_class=cls))
                if not conn.execute("SELECT 1 FROM notes WHERE subject=? AND project=? AND verification='verified'", (row["id"], project)).fetchone():
                    unverified += 1
            return {"format": fmt, "count": len(entries), "content": "\n\n".join(entries) + ("\n" if entries else ""),
                    "unverified": unverified}
        if fmt in ("csl", "csl-json", "csljson"):
            items = []
            for row, state in rows:
                csl = dict(json.loads(row["csl"]))
                csl["id"] = (state or {}).get("citekey_override") or row["citekey"]
                csl["citation-key"] = csl["id"]
                items.append(csl)
            return {"format": "csl", "count": len(items), "content": json.dumps(items, ensure_ascii=False, indent=1)}
        if fmt == "ris":
            out = []
            for row, state in rows:
                out.append(csl_to_ris((state or {}).get("citekey_override") or row["citekey"], json.loads(row["csl"]), work_ids(conn, row["id"])))
            return {"format": "ris", "count": len(out), "content": "\n".join(out)}
    raise ValueError("format must be bibtex, biblatex, csl, ris or snapshot")


def export_rows(conn: sqlite3.Connection, project: str, works=None, all_tags=None, any_tags=None,
                not_tags=None) -> list[tuple[sqlite3.Row, dict | None]]:
    """The works an export covers (named ones, else the tagged or all project works), each with its project state."""
    if works:
        rows = [require_work(conn, ref, project) for ref in works]
    else:
        join, join_params, where, params = work_filter(project, "project", all_tags, any_tags, not_tags)
        rows = conn.execute(f"SELECT w.* FROM works w {join} WHERE {' AND '.join(where)} ORDER BY w.citekey",
                            (*join_params, *params)).fetchall()
    states = project_state(conn, project, [r["id"] for r in rows])
    return [(r, states.get(r["id"])) for r in rows]


# ------------------------------------------------------------------------------------------------ saved export files
EXPORT_KEEP_DAYS = 7
EXPORT_KEEP = 200


def save_export(export_dir, project: str, fmt: str, ext: str, content: str) -> str:
    """An export too big to answer inline, saved as <project>~<format>-<random>.<ext>: the name carries the project it
    was made in (project ids never contain '~'), so it is served only to callers that may use that project
    (export_project). Files older than EXPORT_KEEP_DAYS, or past the newest EXPORT_KEEP, are deleted."""
    import secrets
    name = f"{project}~{fmt}-{secrets.token_hex(6)}.{ext}"
    (export_dir / name).write_text(content, encoding="utf-8")
    prune_exports(export_dir)
    return name


def export_project(name: str) -> str | None:
    """The project a saved export belongs to (None for files from before names carried it)."""
    return name.split("~", 1)[0] if "~" in name else None


def prune_exports(export_dir, keep_days: int | None = None, keep: int | None = None) -> int:
    import time
    keep_days = EXPORT_KEEP_DAYS if keep_days is None else keep_days
    keep = EXPORT_KEEP if keep is None else keep
    files = sorted((p for p in export_dir.iterdir() if p.is_file()), key=lambda p: p.stat().st_mtime, reverse=True)
    cutoff = time.time() - keep_days * 86400
    gone = 0
    for i, p in enumerate(files):
        if i >= keep or p.stat().st_mtime < cutoff:
            p.unlink(missing_ok=True)
            gone += 1
    return gone
