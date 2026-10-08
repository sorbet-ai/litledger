"""Backups of everything litledger keeps: a consistent copy of the database (VACUUM INTO, safe while the server
runs) plus downloaded and uploaded files, as one .tar.gz. The server makes one on a schedule and keeps the newest
few; `litledger backup` / `litledger restore` do it by hand. Put the backup folder outside the Docker volume (the
compose file mounts ./backups) and sync that folder anywhere you like."""
from __future__ import annotations

import logging
import os
import shutil
import sqlite3
import tarfile
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import anyio

from .db import SYSTEM, Ctx

log = logging.getLogger("litledger.backup")
PREFIX = "litledger-"
LOCK = "server.lock"
SKIP = {"cache.sqlite3", "cache.sqlite3-wal", "cache.sqlite3-shm", "backups", "exports", "tokens", LOCK}


class DataLock:
    """An OS file lock on <data>/server.lock. The running server holds it for its lifetime; `litledger restore` takes
    it before touching the data, so a restore refuses while a server uses the folder, even from another container on
    the same volume (`docker compose run --rm --no-deps`). The OS drops the lock when its process dies: never stale."""

    def __init__(self, data_dir: Path):
        self.path = Path(data_dir) / LOCK
        self._fh = None

    def acquire(self) -> bool:
        """Take the lock without waiting; False when someone else holds it."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(self.path, "a+b")
        try:
            if os.name == "nt":
                import msvcrt
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            return False
        self._fh = fh
        return True

    def release(self) -> None:
        if self._fh is None:
            return
        try:
            if os.name == "nt":
                import msvcrt
                self._fh.seek(0)
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        self._fh.close()
        self._fh = None


def in_use(data_dir: Path, lock: DataLock | None = None) -> str | None:
    """Why the data folder can't be restored into now (None: free). Checks the server's lock file, then takes an
    exclusive lock on the database itself, which fails while any other connection is writing to it."""
    own = lock is None
    lock = lock or DataLock(data_dir)
    if lock._fh is None and not lock.acquire():
        return "a litledger server is using this data folder"
    try:
        db = Path(data_dir) / "ledger.sqlite3"
        if db.exists():
            conn = sqlite3.connect(db, timeout=1.0, isolation_level=None)
            try:
                conn.execute("BEGIN EXCLUSIVE")
                conn.execute("ROLLBACK")
            except sqlite3.OperationalError as exc:
                if "locked" in str(exc) or "busy" in str(exc):
                    return "the database is in use by another program"
            except sqlite3.DatabaseError:
                pass  # not a database (damaged, say): nothing uses it, and restoring is the cure
            finally:
                conn.close()
        return None
    finally:
        if own:
            lock.release()

def backup_dir(settings, data_dir: Path) -> Path:
    configured = (settings.get("BACKUP_DIR") or "").strip()
    if configured:
        return Path(configured)
    mounted = Path("/backups")
    return mounted if mounted.is_dir() else data_dir / "backups"


def make(db, dest: Path, keep: int, ctx) -> Path:
    """Back up now and record it. The audit row is written in its own small transaction after the archive is safely
    on disk, so a failed backup leaves none (and the archive doesn't contain its own row)."""
    path = create(db.path, dest, keep)
    with db.tx(SYSTEM, ctx) as tx:
        tx.audit("backup.create", path.name, size=path.stat().st_size)
    return path


def create(db_path: Path, dest: Path, keep: int = 7) -> Path:
    """Write <dest>/litledger-<utc time>.tar.gz and prune to the newest `keep`."""
    db_path = Path(db_path)
    data_dir = db_path.parent
    dest.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    final = dest / f"{PREFIX}{stamp}.tar.gz"
    with tempfile.TemporaryDirectory(dir=dest) as tmp:
        copy = Path(tmp) / db_path.name
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("VACUUM INTO ?", (str(copy),))
        finally:
            conn.close()
        part = Path(tmp) / "archive.tar.gz"
        with tarfile.open(part, "w:gz") as tar:
            tar.add(copy, arcname=db_path.name)
            for item in sorted(data_dir.iterdir()):
                if item.name in SKIP or item.name.startswith((db_path.name, "before-restore-")) or item.resolve() == dest.resolve():
                    continue
                tar.add(item, arcname=item.name)
        os.replace(part, final)
    prune(dest, keep)
    return final


def prune(dest: Path, keep: int) -> None:
    olds = sorted(dest.glob(f"{PREFIX}*.tar.gz"))
    for old in olds[:-keep] if keep > 0 else []:
        old.unlink(missing_ok=True)


def listing(dest: Path) -> list[dict]:
    if not dest.is_dir():
        return []
    return [{"name": p.name, "size": p.stat().st_size,
             "at": datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")}
            for p in sorted(dest.glob(f"{PREFIX}*.tar.gz"), reverse=True)]


def restore(archive: Path, data_dir: Path) -> Path:
    """Unpack a backup into data_dir. The current database is moved aside first (never deleted). Refuses
    (RuntimeError) while a server or another program uses the data folder; holds the lock until done."""
    data_dir.mkdir(parents=True, exist_ok=True)
    lock = DataLock(data_dir)
    busy = in_use(data_dir, lock)
    if busy:
        lock.release()
        raise RuntimeError(f"can't restore: {busy}; stop the server first")
    try:
        aside = data_dir / f"before-restore-{int(time.time())}"
        aside.mkdir()
        with tarfile.open(archive, "r:gz") as tar:
            names = tar.getnames()
            for name in {n.split("/", 1)[0] for n in names} - {LOCK}:
                current = data_dir / name
                if current.exists():
                    shutil.move(str(current), aside / name)
            for suffix in ("-wal", "-shm"):
                for side in data_dir.glob(f"*.sqlite3{suffix}"):
                    shutil.move(str(side), aside / side.name)
            tar.extractall(data_dir, filter="data")
        return aside
    finally:
        lock.release()


def problem(exc: Exception, dest: Path) -> str:
    """A failed backup in words, with the usual fix for a folder the server can't write to."""
    text = f"can't write a backup to {dest}: {exc.strerror or exc}" if isinstance(exc, OSError) else f"backup failed: {exc}"
    if isinstance(exc, PermissionError):
        text += " (the server runs as uid 10001: on Linux, `sudo chown 10001 ./backups` on the host)"
    return text


async def schedule(lg, every: float = 600, first: float = 60) -> None:
    """Server task: make a backup whenever the newest one is older than BACKUP_EVERY_HOURS (checked every 10 minutes).
    A failure is logged once, and again only when it changes (or after a success), not every 10 minutes."""
    await anyio.sleep(first)
    last_error = None
    while True:
        try:
            hours = lg.settings.number("BACKUP_EVERY_HOURS")
            if hours > 0:
                dest = backup_dir(lg.settings, lg.db.path.parent)
                try:
                    newest = listing(dest)[:1]
                    age = (time.time() - (dest / newest[0]["name"]).stat().st_mtime) if newest else None
                    if age is None or age > hours * 3600:
                        path = await anyio.to_thread.run_sync(make, lg.db, dest, lg.settings.number("BACKUP_KEEP"), Ctx(via="schedule"))
                        log.info("backup written: %s", path)
                    last_error = None
                except Exception as exc:  # a full disk or an unwritable folder must not stop the server
                    text = problem(exc, dest)
                    if text != last_error:
                        log.warning("scheduled backup failed: %s", text)
                    last_error = text
        except Exception as exc:
            log.warning("backup schedule: %s", exc)
        await anyio.sleep(every)
