"""The local citation graph: references in stored text are linked to works as they arrive, incrementally and without
holding the write lock while matching."""
import random
import time

from litledger import citations
from litledger.citations import relink_all

from conftest import ACTOR, add_work, make_ledger


def _library(lg, n_works: int, n_docs: int, refs_per_doc: int, seed: int = 7):
    rng = random.Random(seed)
    vocab = sorted({"".join(rng.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(rng.randint(5, 9))) for _ in range(5000)})
    title = lambda: " ".join(rng.choice(vocab) for _ in range(rng.randint(5, 9))).capitalize()  # noqa: E731
    titles = [title() for _ in range(n_works)]
    missing = [title() for _ in range(n_docs * refs_per_doc)]
    stamp = "2020-01-01T00:00:00+00:00"
    expected = 0
    with lg.db._write_lock, lg.db.connection() as conn:
        conn.execute("BEGIN")
        for i, t in enumerate(titles):
            add_work(conn, f"w{i}", t, stamp=stamp)
        for d in range(n_docs):
            conn.execute("INSERT INTO documents(id,work_id,source,parser,parser_version,status,created_at) VALUES(?,?,?,?,?,?,?)",
                         (f"d{d}", f"w{d}", "pdf", "pdf", "2", "ok", stamp))
            for s in range(refs_per_doc):
                if s % 2 == 0:
                    target = rng.randrange(n_works)
                    expected += target != d
                    t = titles[target]
                else:
                    t = missing[d * refs_per_doc + s]
                raw = f"[{s + 1}] A. Author, B. Writer, and C. Scholar. {t}. In Proceedings of the Conference, pp. 1-10, 2021."
                conn.execute("INSERT INTO refs_extracted(document_id,seq,label,raw,parsed) VALUES(?,?,?,?,?)",
                             (f"d{d}", s + 1, str(s + 1), raw, '{"links": []}'))
        conn.execute("COMMIT")
    return missing, expected


def test_relink_is_incremental_and_scales(tmp_path):
    lg = make_ledger(tmp_path)
    missing, expected = _library(lg, n_works=2000, n_docs=100, refs_per_doc=40)
    t0 = time.perf_counter()
    linked = relink_all(lg)  # first run: every work is new
    first = time.perf_counter() - t0
    assert linked == expected
    assert first < 1.0, f"relink of 2000 works x 100 docs took {first:.2f}s"
    # Nothing changed: the second run is a no-op.
    t0 = time.perf_counter()
    assert relink_all(lg) == 0
    assert time.perf_counter() - t0 < 0.3
    # One new work that three documents cite: only it is matched, quickly.
    with lg.db.tx(ACTOR, internal="test setup") as tx:
        add_work(tx.conn, "wnew", missing[1])
    t0 = time.perf_counter()
    assert relink_all(lg) == 1
    assert time.perf_counter() - t0 < 0.5
    with lg.db.read() as conn:
        assert conn.execute("SELECT count(*) FROM refs_extracted WHERE resolved_work_id='wnew'").fetchone()[0] == 1


def test_relink_does_not_hold_the_write_lock_while_matching(tmp_path, monkeypatch):
    lg = make_ledger(tmp_path)
    _library(lg, n_works=300, n_docs=20, refs_per_doc=20)
    held = []
    original = citations.resolve_reference

    def spy(conn, raw, links):
        held.append(lg.db._write_lock._is_owned())
        return original(conn, raw, links)

    monkeypatch.setattr(citations, "resolve_reference", spy)
    assert relink_all(lg) > 0
    assert held and not any(held)
