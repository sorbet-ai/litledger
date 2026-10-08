"""Job queue (M17): attempts, atomic leased claims, retry with backoff, crash-loop protection, pruning."""
from __future__ import annotations

import threading

import pytest

from litledger import ledger
from litledger.ledger import _iso
from litledger.providers.http import ProviderError

from conftest import make_ledger

CALLS: list[str] = []


def _ok(lg, payload):
    CALLS.append("ok")
    return {"n": payload.get("n")}


def _boom(lg, payload):
    CALLS.append("boom")
    raise RuntimeError("handler bug")


def _flaky(lg, payload):
    CALLS.append("flaky")
    raise ProviderError("s2", "rate_limited", "retry after 60 s")


@pytest.fixture
def lg(tmp_path):
    CALLS.clear()
    lg = make_ledger(tmp_path)
    lg.handlers.update(test_ok=_ok, test_boom=_boom, test_flaky=_flaky)
    return lg


def _job(lg, job_id):
    return lg.job(job_id)


def _expire(lg, job_id):
    with lg.db.connection() as conn:
        conn.execute("UPDATE jobs SET lease_until=? WHERE id=?", (_iso(0), job_id))


def test_success_records_one_attempt(lg):
    jid = lg.enqueue("test_ok", {"n": 1})
    job = _job(lg, jid)
    assert job["state"] == "done" and job["attempts"] == 1 and job["lease_until"] is None


def test_permanent_failure_is_not_retried(lg):
    jid = lg.enqueue("test_boom", {})
    job = _job(lg, jid)
    assert job["state"] == "failed" and job["attempts"] == 1 and "handler bug" in job["error"]
    assert CALLS == ["boom"]


def test_transient_provider_errors_retry_with_backoff_then_fail(lg):
    jid = lg.enqueue("test_flaky", {})
    job = _job(lg, jid)
    assert job["state"] == "queued" and job["attempts"] == 1 and job["lease_until"] > ledger.now()
    assert lg.run_pending() == 0  # waiting out the backoff
    _expire(lg, jid)
    lg.run_pending()
    job = _job(lg, jid)
    assert job["state"] == "queued" and job["attempts"] == 2
    _expire(lg, jid)
    lg.run_pending()
    job = _job(lg, jid)
    assert job["state"] == "failed" and job["attempts"] == 3 and "rate_limited" in job["error"]
    assert CALLS == ["flaky"] * 3


def test_a_job_that_keeps_killing_the_process_is_abandoned(lg):
    jid = lg.enqueue("test_ok", {"n": 2})
    # Simulate three runs that each died mid-job (state left 'running', lease lapsed).
    with lg.db.connection() as conn:
        conn.execute("UPDATE jobs SET state='running', attempts=3, lease_until=? WHERE id=?", (_iso(0), jid))
    CALLS.clear()
    assert lg.run_pending() == 1
    job = _job(lg, jid)
    assert job["state"] == "failed" and "gave up after 3 attempts" in job["error"]
    assert CALLS == []  # not run a fourth time


def test_restart_requeues_interrupted_jobs_without_resetting_attempts(lg):
    lg.run_jobs_inline = False
    jid = lg.enqueue("test_ok", {"n": 3})
    with lg.db.connection() as conn:
        conn.execute("UPDATE jobs SET state='running', attempts=1, lease_until=? WHERE id=?", (_iso(4e9), jid))
    assert lg.run_pending() == 0  # leased by a (supposedly) live worker
    lg._stop.set()  # start_worker's reset runs, its loop exits at once
    lg.start_worker()
    lg._worker.join(5)
    lg._stop.clear()
    job = _job(lg, jid)
    assert job["state"] == "queued" and job["attempts"] == 1
    lg.run_pending()
    assert _job(lg, jid)["attempts"] == 2 and _job(lg, jid)["state"] == "done"


def test_claims_are_atomic(lg):
    lg.run_jobs_inline = False
    ids = [lg.enqueue("test_ok", {"n": i}) for i in range(20)]
    claimed: list[str] = []
    lock = threading.Lock()

    def worker():
        while True:
            row = lg._claim_job()
            if not row:
                return
            with lock:
                claimed.append(row["id"])

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(claimed) == sorted(ids)  # every job exactly once


def test_expired_lease_is_reclaimed(lg):
    lg.run_jobs_inline = False
    jid = lg.enqueue("test_ok", {"n": 4})
    assert lg._claim_job()["id"] == jid
    assert lg._claim_job() is None
    _expire(lg, jid)
    row = lg._claim_job()
    assert row["id"] == jid and row["attempts"] == 2


def test_old_finished_jobs_are_pruned(lg):
    with lg.db.connection() as conn:
        for jid, state, age in (("jold", "done", 40), ("jfail", "failed", 31), ("jnew", "done", 1), ("jq", "queued", 90)):
            stamp = _iso(ledger.time.time() - age * 86400)
            conn.execute("INSERT INTO jobs(id,kind,payload,state,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                         (jid, "test_ok", "{}", state, stamp, stamp))
    assert lg.prune_jobs() == 2
    with lg.db.read() as conn:
        assert {r["id"] for r in conn.execute("SELECT id FROM jobs")} == {"jnew", "jq"}
