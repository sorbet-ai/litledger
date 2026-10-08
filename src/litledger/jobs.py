"""Background job kinds. The Ledger's worker runs them; HANDLERS is handed to the Ledger by whoever starts it."""
from __future__ import annotations

from .citations import relink_all
from .fulltext import fetch_document
from .ledger import Ledger, Handler
from .resolve import enrich


def fetch_fulltext(lg: Ledger, payload: dict) -> dict:
    """Fetch a captured work's full text (FETCH_FULLTEXT=on_add)."""
    with lg.db.read() as conn:
        if conn.execute("SELECT 1 FROM documents WHERE work_id=? AND status='ok'", (payload["work"],)).fetchone():
            return {"skipped": "has document"}
    doc_id, msg = fetch_document(lg, payload["work"])
    return {"document": doc_id, "message": msg}


def relink(lg: Ledger, payload: dict) -> dict:
    """Link stored reference lists to works captured since the last run."""
    return {"linked": relink_all(lg)}


HANDLERS: dict[str, Handler] = {"enrich": enrich, "fetch_fulltext": fetch_fulltext, "relink": relink}
