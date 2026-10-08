"""Snapshots: consistent while the server runs, pruned to BACKUP_KEEP, restorable, listed and downloadable by admins only."""
import sqlite3
import time

from fastapi.testclient import TestClient

from litledger import accounts, backup
from litledger.app import create_app

from conftest import make_ledger


def test_backup_prune_and_restore(tmp_path):
    lg = make_ledger(tmp_path)
    (lg.db.path.parent / "blobs").mkdir(exist_ok=True)
    (lg.db.path.parent / "blobs" / "x.pdf").write_bytes(b"%PDF-1.7 test")
    accounts.create_token(lg.db, "someone")
    dest = tmp_path / "backups"
    paths = []
    for _ in range(3):
        paths.append(backup.create(lg.db.path, dest, keep=2))
        time.sleep(1.05)  # names are per second
    names = [b["name"] for b in backup.listing(dest)]
    assert len(names) == 2 and paths[0].name not in names
    target = tmp_path / "restored"
    target.mkdir()
    (target / "ledger.sqlite3").write_text("old")
    aside = backup.restore(paths[-1], target)
    assert (aside / "ledger.sqlite3").read_text() == "old" and (target / "blobs" / "x.pdf").read_bytes().startswith(b"%PDF")
    with sqlite3.connect(target / lg.db.path.name) as conn:
        assert conn.execute("SELECT count(*) FROM principals WHERE name='someone'").fetchone()[0] == 1


def test_backup_api_is_for_admins(tmp_path):
    lg = make_ledger(tmp_path, BACKUP_DIR=str(tmp_path / "bk"))
    app = create_app(ledger=lg)
    with TestClient(app) as c:
        admin = (lg.settings.token_dir / "admin").read_text().strip()
        agent = accounts.create_token(lg.db, "bot").token
        assert c.post("/api/v1/backups", headers={"Authorization": f"Bearer {agent}"}).status_code == 403
        made = c.post("/api/v1/backups", headers={"Authorization": f"Bearer {admin}"}).json()
        listed = c.get("/api/v1/backups", headers={"Authorization": f"Bearer {admin}"}).json()
        assert listed["backups"][0]["name"] == made["name"]
        r = c.get(f"/api/v1/backups/{made['name']}", headers={"Authorization": f"Bearer {admin}"})
        assert r.status_code == 200 and r.content[:2] == b"\x1f\x8b"
        assert c.get("/api/v1/backups/..%2Fledger.sqlite3", headers={"Authorization": f"Bearer {admin}"}).status_code == 404


def test_restore_refuses_while_the_data_is_in_use(tmp_path):
    lg = make_ledger(tmp_path)
    archive = backup.create(lg.db.path, tmp_path / "bk")
    data = lg.db.path.parent
    # another connection holds the database (a server mid-write, seen from a one-off container)
    holder = sqlite3.connect(lg.db.path, isolation_level=None)
    holder.execute("BEGIN EXCLUSIVE")
    try:
        assert backup.in_use(data) == "the database is in use by another program"
        try:
            backup.restore(archive, data)
            raise AssertionError("restored over a database in use")
        except RuntimeError as exc:
            assert "in use" in str(exc)
    finally:
        holder.execute("ROLLBACK")
        holder.close()
    # a running server holds the data folder's lock file for its whole life, even when idle
    server = backup.DataLock(data)
    assert server.acquire()
    try:
        assert backup.in_use(data) == "a litledger server is using this data folder"
    finally:
        server.release()
    assert backup.in_use(data) is None
    assert not list(data.glob("before-restore-*"))  # nothing was moved while refused


def test_the_server_holds_the_lock_and_the_cli_refuses(tmp_path, monkeypatch):
    from litledger import cli
    lg = make_ledger(tmp_path)
    archive = backup.create(lg.db.path, tmp_path / "bk")
    monkeypatch.setenv("LITLEDGER_DATA", str(lg.settings.data_dir))
    with TestClient(create_app(ledger=lg)):
        try:
            cli.main(["restore", str(archive), "--yes"])
            raise AssertionError("restore ran while the server was up")
        except SystemExit as exc:
            assert "server is using this data folder" in str(exc)
    cli.main(["restore", str(archive), "--yes"])  # stopped: it works
    assert list(lg.settings.data_dir.glob("before-restore-*"))


def test_an_unwritable_backup_folder_is_a_readable_error(tmp_path, monkeypatch, caplog):
    import anyio
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("x")
    lg = make_ledger(tmp_path, BACKUP_DIR=str(blocker))
    app = create_app(ledger=lg)
    with TestClient(app) as c:
        admin = {"Authorization": "Bearer " + (lg.settings.token_dir / "admin").read_text().strip()}
        r = c.post("/api/v1/backups", headers=admin)
        assert r.status_code == 400 and r.json()["detail"].startswith(f"can't write a backup to {blocker}")
        monkeypatch.setattr(backup, "create", lambda *a, **k: (_ for _ in ()).throw(PermissionError(13, "Permission denied")))
        r = c.post("/api/v1/backups", headers=admin)
        assert r.status_code == 400 and "sudo chown 10001" in r.json()["detail"]
    monkeypatch.undo()
    other = tmp_path / "also-not-a-folder"
    other.write_text("x")

    async def run_schedule():
        async with anyio.create_task_group() as tg:
            tg.start_soon(lambda: backup.schedule(lg, every=0.01, first=0))
            await anyio.sleep(0.2)
            lg.set_config(accounts.SYSTEM, {"BACKUP_DIR": str(other)})  # a different failure: logged again
            await anyio.sleep(0.2)
            tg.cancel_scope.cancel()

    with caplog.at_level("WARNING", logger="litledger.backup"):
        anyio.run(run_schedule)
    failures = [r.getMessage() for r in caplog.records if "scheduled backup failed" in r.getMessage()]
    assert len(failures) == 2 and str(blocker) in failures[0] and str(other) in failures[1]
