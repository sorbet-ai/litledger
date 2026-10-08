"""The Ledger: settings, database, HTTP, providers and a small background job queue in one object. The job kinds
and their handlers are listed in jobs.py and handed to the Ledger by whoever starts it (server, tests)."""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
import traceback
from datetime import datetime, timezone
from typing import Callable

import httpx

from .config import FIELDS, Settings
from .db import Actor, Ctx, Database, dumps, now, ulid
from .providers import Registry
from .providers.http import Http, ProviderError

log = logging.getLogger("litledger")

Handler = Callable[["Ledger", dict], "dict | None"]
JOB_MAX_ATTEMPTS = 3
JOB_LEASE_SECONDS = 15 * 60  # a running job whose lease lapsed is assumed dead and may be claimed again
JOB_RETRY_BASE = 30.0  # seconds before the first retry of a transient failure; doubles per attempt
JOB_KEEP_DAYS = 30


def _iso(t: float) -> str:
    """A Unix time in the same ISO form as db.now() (so stored times compare as strings)."""
    return datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="seconds")


class Ledger:
    def __init__(self, settings: Settings | None = None, transport: httpx.BaseTransport | None = None,
                 run_jobs_inline: bool = False, handlers: dict[str, Handler] | None = None):
        self.base_settings = settings or Settings.from_env()
        self.transport = transport
        base = self.base_settings
        base.data_dir.mkdir(parents=True, exist_ok=True)
        base.blob_dir.mkdir(parents=True, exist_ok=True)
        base.export_dir.mkdir(parents=True, exist_ok=True)
        self.db = Database(base.db_path)
        self.reload()
        self.run_jobs_inline = run_jobs_inline
        self.handlers = dict(handlers or {})
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._worker: threading.Thread | None = None

    # -- runtime configuration (web UI) -----------------------------------------------------------------
    def config_values(self) -> dict[str, str]:
        with self.db.read() as conn:
            return {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM config")}

    def reload(self) -> None:
        """Rebuild settings and the provider registry from the settings saved in the database.

        The Http object is created once and reconfigured in place: pacing, cooldowns, the response cache and the
        connection pool survive a settings change, and no client is closed under an in-flight request."""
        settings = self.base_settings.with_values(self.config_values())
        self.settings = settings
        if getattr(self, "http", None) is None:
            self.http = Http(self.db, settings.contact_email, settings.offline, self.transport, settings.allow_private_fetch)
        else:
            self.http.configure(settings.contact_email, settings.offline, settings.allow_private_fetch)
        self.providers = Registry(settings, self.http, getattr(self, "providers", None))  # runtime state carries over

    def set_config(self, actor: Actor, values: dict[str, str | None], ctx: Ctx | None = None, op: str = "settings.change",
                   target: str | None = None) -> list[str]:
        """Save (or clear, with None/"") runtime settings. Only keys the registry declares are accepted. The change is
        journaled (key names, never values) and audited in the same transaction."""
        fields = {f["key"]: f for f in self.all_fields()}
        unknown = sorted(set(values) - set(fields))
        if unknown:
            raise ValueError(f"unknown setting(s): {', '.join(unknown)}")
        for key, value in values.items():
            value = (value or "").strip()
            choices = fields[key].get("choices")
            if value and choices and not all(v.strip() in choices for v in value.split(",")):
                raise ValueError(f"{key} must be {' / '.join(choices)}" + (" (comma-separated)" if fields[key].get("multi") else ""))
            if value and fields[key].get("type") == "int" and not value.isdigit():
                raise ValueError(f"{key} must be a whole number")
        changed = []
        with self.db.tx(actor, ctx) as tx:
            for key, value in values.items():
                value = (value or "").strip()
                if value:
                    tx.execute("INSERT INTO config VALUES(?,?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
                               "updated_by=excluded.updated_by, updated_at=excluded.updated_at", (key, value, actor.label, now()))
                else:
                    tx.execute("DELETE FROM config WHERE key=?", (key,))
                changed.append(key)
            tx.log("config.set", {"keys": changed}, project="")  # never the values
            tx.audit(op, target or ", ".join(changed), keys=changed if target else None)
        self.reload()
        return changed

    def set_source(self, actor: Actor, pid: str, action: str, values: dict[str, str | None] | None = None,
                   forget_keys: bool = False, ctx: Ctx | None = None) -> list[str]:
        """Add (enable, with its keys) or remove (disable) a source; one settings write, applied at once."""
        st = self.providers.states.get(pid)
        if not st:
            raise LookupError(f"unknown source {pid}")
        p = st.provider
        allowed = set(p.credential_vars) | {f["key"] for f in self.providers.config_fields() if f.get("provider") == pid}
        values = {k: v for k, v in (values or {}).items() if v not in (None, "")}
        bad = sorted(set(values) - allowed)
        if bad:
            raise ValueError(f"{', '.join(bad)} is not a setting of {p.name}")
        enable = [x for x in self.settings.enable if x != pid]
        disable = [x for x in self.settings.disable if x != pid]
        if action == "add":
            keyed = all(values.get(v) or self.settings.get(v) for v in p.credential_vars)
            if p.default == "optin" or (pid == "s2" and not keyed):
                enable.append(pid)
            if p.auth == "email" and not self.settings.contact_email and not values.get("CONTACT_EMAIL"):
                raise ValueError(f"{p.name} needs a contact email (Admin → Server)")
        elif action == "remove":
            if p.default != "optin":
                disable.append(pid)
            if forget_keys:
                values = {k: None for k in allowed}
        else:
            raise ValueError("action must be add or remove")
        return self.set_config(actor, {**values, "ENABLE": ",".join(enable) or None, "DISABLE": ",".join(disable) or None},
                               ctx, f"source.{action}", pid)

    def all_fields(self) -> list[dict]:
        return self.providers.config_fields() + [dict(f) for f in FIELDS]

    def config_view(self) -> list[dict]:
        """Settings fields with their current values; secrets are masked, never returned."""
        saved = self.config_values()
        out = []
        for f in self.all_fields():
            value = saved.get(f["key"], "")
            shown = ("•••• " + value[-4:] if len(value) > 8 else "••••") if f["secret"] and value else value
            out.append({**f, "set": bool(value), "value": shown})
        return out

    # -- jobs ------------------------------------------------------------------------------------------
    def enqueue(self, kind: str, payload: dict, dedupe: bool = True) -> str:
        key = dumps(payload)
        with self.db.write("job bookkeeping") as conn:
            if dedupe:
                row = conn.execute("SELECT id FROM jobs WHERE kind=? AND payload=? AND state IN ('queued','running')",
                                   (kind, key)).fetchone()
                if row:
                    return row["id"]
            job_id = "j" + ulid()
            conn.execute("INSERT INTO jobs(id,kind,payload,state,created_at,updated_at) VALUES(?,?,?,?,?,?)", (job_id, kind, key, "queued", now(), now()))
        if self.run_jobs_inline:
            self.run_pending()
        else:
            self._wake.set()
        return job_id

    def _claim_job(self) -> sqlite3.Row | None:
        """Atomically take the oldest runnable job: queued and not waiting out a retry delay, or running with an
        expired lease (its worker died). Every claim counts as an attempt."""
        stamp = _iso(time.time())
        with self.db.write("job bookkeeping") as conn:
            rows = conn.execute(
                "UPDATE jobs SET state='running', attempts=attempts+1, lease_until=?, updated_at=? WHERE id=("
                "SELECT id FROM jobs WHERE (state='queued' AND (lease_until IS NULL OR lease_until<=?)) "
                "OR (state='running' AND lease_until IS NOT NULL AND lease_until<?) ORDER BY created_at LIMIT 1) "
                "AND (state='queued' OR lease_until<?) RETURNING *",
                (_iso(time.time() + JOB_LEASE_SECONDS), stamp, stamp, stamp, stamp)).fetchall()
        return rows[0] if rows else None

    def _finish_job(self, job_id: str, state: str, result: str | None, error: str | None,
                    retry_at: float | None = None) -> None:
        with self.db.write("job bookkeeping") as conn:
            conn.execute("UPDATE jobs SET state=?, result=?, error=?, lease_until=?, updated_at=? WHERE id=?",
                         (state, result, error, _iso(retry_at) if retry_at else None, now(), job_id))

    def prune_jobs(self, days: int = JOB_KEEP_DAYS) -> int:
        """Forget finished (done/failed) jobs older than `days`."""
        self._jobs_pruned_at = time.time()
        with self.db.write("job bookkeeping") as conn:
            return conn.execute("DELETE FROM jobs WHERE state IN ('done','failed') AND updated_at<?",
                                (_iso(time.time() - days * 86400),)).rowcount

    def run_pending(self, limit: int = 100) -> int:
        if time.time() - getattr(self, "_jobs_pruned_at", 0.0) > 3600:
            self.prune_jobs()
        done = 0
        while done < limit:
            row = self._claim_job()
            if not row:
                return done
            done += 1
            if row["attempts"] > JOB_MAX_ATTEMPTS:  # it keeps failing or taking the process down with it
                log.warning("job %s (%s) abandoned after %d attempts", row["id"], row["kind"], JOB_MAX_ATTEMPTS)
                self._finish_job(row["id"], "failed", None,
                                 f"gave up after {JOB_MAX_ATTEMPTS} attempts" + (f"; last error: {row['error']}" if row["error"] else ""))
                continue
            handler = self.handlers.get(row["kind"])
            try:
                if not handler:
                    raise RuntimeError(f"no handler for job kind {row['kind']}")
                result = handler(self, json.loads(row["payload"]))
                self._finish_job(row["id"], "done", dumps(result or {}), None)
            except Exception as exc:  # jobs must never kill the worker
                transient = (isinstance(exc, ProviderError) and exc.kind in ("rate_limited", "unavailable")) or \
                    (isinstance(exc, sqlite3.OperationalError) and "locked" in str(exc))
                if transient and row["attempts"] < JOB_MAX_ATTEMPTS:
                    delay = JOB_RETRY_BASE * 2 ** (row["attempts"] - 1)
                    log.info("job %s (%s) will retry in %.0f s: %s", row["id"], row["kind"], delay, exc)
                    self._finish_job(row["id"], "queued", None, str(exc)[:500], retry_at=time.time() + delay)
                else:
                    log.warning("job %s (%s) failed: %s", row["id"], row["kind"], exc)
                    log.debug(traceback.format_exc())
                    self._finish_job(row["id"], "failed", None, str(exc)[:500])
        return done

    def start_worker(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        with self.db.write("job bookkeeping") as conn:
            # Interrupted by a restart: runnable again now (attempts are kept, so a job that crashes the process
            # is abandoned after JOB_MAX_ATTEMPTS instead of crash-looping).
            conn.execute("UPDATE jobs SET state='queued', lease_until=NULL WHERE state='running'")
        self.prune_jobs()

        def loop():
            while not self._stop.is_set():
                try:
                    if self.run_pending() == 0:
                        self._wake.wait(timeout=30)
                        self._wake.clear()
                except Exception as exc:
                    log.warning("job worker error: %s", exc)
                    self._stop.wait(5)

        self._worker = threading.Thread(target=loop, name="litledger-jobs", daemon=True)
        self._worker.start()

    def stop_worker(self) -> None:
        self._stop.set()
        self._wake.set()

    def job(self, job_id: str) -> dict | None:
        with self.db.read() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return dict(row) if row else None
