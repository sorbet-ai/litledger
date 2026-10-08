"""Provider response cache in its own SQLite file (`<data>/cache.sqlite3`), separate from the ledger.

The cache is disposable: it is size-capped (oldest entries go first), expired rows are pruned when it opens and
periodically, and a corrupt file is simply recreated. Full-text bodies are never cached here (the blob store keeps them)."""
from __future__ import annotations

import logging
import sqlite3
import threading
import time
from pathlib import Path

log = logging.getLogger("litledger.cache")

MAX_BYTES = 200 * 1024 * 1024
MAX_ENTRY = 4 * 1024 * 1024  # larger responses are not worth keeping
PRUNE_EVERY_WRITES = 200
PRUNE_EVERY_SECONDS = 600.0

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cache(key TEXT PRIMARY KEY, provider TEXT NOT NULL, url TEXT NOT NULL, status INTEGER NOT NULL,
  content_type TEXT, body BLOB NOT NULL, size INTEGER NOT NULL, stored_at REAL NOT NULL, expires_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS cache_expires ON cache(expires_at);
CREATE INDEX IF NOT EXISTS cache_stored ON cache(stored_at);
"""


class ResponseCache:
    def __init__(self, path: Path, max_bytes: int = MAX_BYTES, max_entry: int = MAX_ENTRY):
        self.path = Path(path)
        self.max_bytes = max_bytes
        self.max_entry = max_entry
        self._lock = threading.Lock()
        self._writes = 0
        self._pruned_at = 0.0
        self._conn: sqlite3.Connection | None = None
        try:
            self._open()
        except sqlite3.DatabaseError as exc:  # corrupt or not a database: it is only a cache
            log.warning("response cache %s unusable (%s); recreating it", self.path, exc)
            self._discard()
            self._open()

    def _open(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=10, isolation_level=None, check_same_thread=False)
        try:
            conn.execute("PRAGMA busy_timeout=10000")
            conn.execute("PRAGMA synchronous=OFF")
            conn.execute("PRAGMA auto_vacuum=INCREMENTAL")  # takes effect on a fresh file; lets prune give space back
            conn.executescript(_SCHEMA)
        except sqlite3.DatabaseError:
            conn.close()
            raise
        self._conn = conn
        self.prune()

    def _discard(self) -> None:
        for suffix in ("", "-wal", "-shm", "-journal"):
            try:
                Path(str(self.path) + suffix).unlink(missing_ok=True)
            except OSError:
                pass

    def get(self, key: str) -> tuple[int, str | None, bytes] | None:
        """(status, content_type, body) of a live entry, else None."""
        with self._lock:
            if self._conn is None:
                return None
            try:
                row = self._conn.execute("SELECT status, content_type, body, expires_at FROM cache WHERE key=?", (key,)).fetchone()
            except sqlite3.DatabaseError as exc:
                log.warning("response cache read failed: %s", exc)
                return None
        if not row or row[3] <= time.time():
            return None
        return row[0], row[1], row[2]

    def put(self, key: str, provider: str, url: str, status: int, content_type: str, body: bytes, ttl: float) -> bool:
        if ttl <= 0 or len(body) > self.max_entry:
            return False
        t = time.time()
        with self._lock:
            if self._conn is None:
                return False
            try:
                self._conn.execute("INSERT OR REPLACE INTO cache VALUES(?,?,?,?,?,?,?,?,?)",
                                   (key, provider, url[:2000], status, content_type, body, len(body), t, t + ttl))
            except sqlite3.DatabaseError as exc:
                log.warning("response cache write failed: %s", exc)
                return False
            self._writes += 1
            due = self._writes % PRUNE_EVERY_WRITES == 0 or t - self._pruned_at > PRUNE_EVERY_SECONDS
        if due:
            self.prune()
        return True

    def prune(self) -> int:
        """Drop expired entries, then the oldest ones until the cache is under ~90% of its cap."""
        with self._lock:
            if self._conn is None:
                return 0
            t = time.time()
            self._pruned_at = t
            try:
                removed = self._conn.execute("DELETE FROM cache WHERE expires_at <= ?", (t,)).rowcount
                total = self._conn.execute("SELECT COALESCE(SUM(size), 0) FROM cache").fetchone()[0]
                if total > self.max_bytes:
                    target, drop = total - int(self.max_bytes * 0.9), []
                    for key, size in self._conn.execute("SELECT key, size FROM cache ORDER BY stored_at"):
                        if target <= 0:
                            break
                        drop.append((key,))
                        target -= size
                    self._conn.executemany("DELETE FROM cache WHERE key=?", drop)
                    removed += len(drop)
                if removed > 1000:
                    self._conn.execute("PRAGMA incremental_vacuum")
            except sqlite3.DatabaseError as exc:
                log.warning("response cache prune failed: %s", exc)
                return 0
            return removed

    def stats(self) -> dict:
        with self._lock:
            if self._conn is None:
                return {"entries": 0, "bytes": 0}
            n, size = self._conn.execute("SELECT count(*), COALESCE(SUM(size), 0) FROM cache").fetchone()
        return {"entries": n, "bytes": size}

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None
