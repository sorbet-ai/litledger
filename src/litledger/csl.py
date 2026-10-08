"""CSL-JSON helpers shared by providers, importers and exporters: dates, years and person names."""
from __future__ import annotations


def csl_date(year: int | str | None, month: int | str | None = None, day: int | str | None = None) -> dict | None:
    parts = []
    for p in (year, month, day):
        try:
            if p is None or str(p).strip() == "":
                break
            parts.append(int(str(p)[:4]) if not parts else int(p))
        except ValueError:
            break
    return {"date-parts": [parts]} if parts else None


def csl_date_from_iso(value: str | None) -> dict | None:
    if not value:
        return None
    bits = value[:10].split("-")
    return csl_date(*(bits + [None, None])[:3])


def year_of(csl: dict) -> int | None:
    """The year a CSL item was issued, if it has one."""
    try:
        return int(csl["issued"]["date-parts"][0][0])
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def person(given: str | None = None, family: str | None = None, literal: str | None = None) -> dict:
    if family:
        out = {"family": family.strip()}
        if given:
            out["given"] = given.strip()
        return out
    name = (literal or "").strip()
    if "," in name:
        fam, _, giv = name.partition(",")
        return {"family": fam.strip(), "given": giv.strip()} if giv.strip() else {"family": fam.strip()}
    bits = name.split()
    if len(bits) >= 2:
        # Keep lowercase particles with the family name ("van der Maaten").
        i = len(bits) - 1
        while i > 1 and bits[i - 1][:1].islower():
            i -= 1
        return {"given": " ".join(bits[:i]), "family": " ".join(bits[i:])}
    return {"literal": name} if name else {}
