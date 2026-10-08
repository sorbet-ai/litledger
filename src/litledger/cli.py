"""litledger CLI: a thin client over the REST API (same compact output as MCP) plus local admin commands.

Sign in once per machine with `litledger login` (approve the code in the web UI, under Settings → Connected apps). Client settings:
LITLEDGER_URL (or ~/.litledger/url, default http://127.0.0.1:8765), LITLEDGER_TOKEN (or ~/.litledger/token),
project from --project, LITLEDGER_PROJECT, or the nearest .litledger.toml (`project = "name"`)."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

import httpx

HOME = Path.home() / ".litledger"


def find_project_file(start: Path | None = None) -> Path | None:
    path = (start or Path.cwd()).resolve()
    for p in [path, *path.parents]:
        f = p / ".litledger.toml"
        if f.exists():
            return f
    return None


def project_name(explicit: str | None) -> str:
    if explicit:
        return explicit
    if os.environ.get("LITLEDGER_PROJECT"):
        return os.environ["LITLEDGER_PROJECT"]
    f = find_project_file()
    if f:
        m = re.search(r'^\s*project\s*=\s*"([^"]+)"', f.read_text(encoding="utf-8"), re.M)
        if m:
            return m.group(1)
    return "default"


def default_url() -> str:
    if os.environ.get("LITLEDGER_URL"):
        return os.environ["LITLEDGER_URL"]
    f = HOME / "url"
    return f.read_text(encoding="utf-8").strip() if f.exists() else "http://127.0.0.1:8765"


def token() -> str:
    if os.environ.get("LITLEDGER_TOKEN"):
        return os.environ["LITLEDGER_TOKEN"].strip()
    f = HOME / "token"
    return f.read_text(encoding="utf-8").strip() if f.exists() else ""


class Client:
    def __init__(self, url: str, project: str, agent: str | None = None):
        self.url = url.rstrip("/")
        tok = token()
        if not tok:
            sys.exit("litledger: not signed in — run `litledger login` (or set LITLEDGER_TOKEN)")
        headers = {"Authorization": f"Bearer {tok}", "X-Litledger-Project": project}
        if agent:
            headers["X-Litledger-Agent"] = agent
        self.http = httpx.Client(base_url=self.url, headers=headers, timeout=httpx.Timeout(300.0, connect=10.0))

    def _check(self, r: httpx.Response) -> httpx.Response:
        if r.status_code == 401:
            sys.exit("litledger: unauthorized — the token was revoked or is wrong; run `litledger login`")
        if r.status_code >= 400:
            try:
                detail = r.json().get("detail") or r.text
            except Exception:
                detail = r.text
            sys.exit(f"litledger: HTTP {r.status_code}: {detail}")
        return r

    def tool(self, name: str, args: dict) -> str:
        try:
            r = self.http.post(f"/api/v1/tools/{name}", json=args, headers={"Accept": "text/plain"})
        except httpx.ConnectError:
            sys.exit(f"litledger: cannot reach {self.url} — is the server running? (docker compose up -d)")
        return self._check(r).text

    def get(self, path: str, **params):
        return self._check(self.http.get(path, params=params)).json()

    def upload(self, path: Path, **form) -> dict:
        with path.open("rb") as fh:
            r = self.http.post("/api/v1/uploads", files={"file": (path.name, fh)}, data={k: v for k, v in form.items() if v})
        return self._check(r).json()


def _csv(value: str | None) -> list[str] | None:
    return [v.strip() for v in value.split(",") if v.strip()] if value else None


def _clean(d: dict) -> dict:
    return {k: v for k, v in d.items() if v not in (None, "", [])}


def _client(a) -> Client:
    return Client(a.url, project_name(a.project), a.agent)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="litledger", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", default=default_url())
    p.add_argument("--project", default=None)
    p.add_argument("--agent", default=os.environ.get("LITLEDGER_AGENT"))
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="run the server (inside the container)")
    s.add_argument("--host", default=None)
    s.add_argument("--port", type=int, default=None)
    s = sub.add_parser("doctor", help="setup warnings and source status")
    s.add_argument("--local", action="store_true", help="inspect local config/DB instead of asking the server")
    s = sub.add_parser("login", help="sign this machine in: approve the code it shows in the web UI; the token is saved to ~/.litledger")
    s.add_argument("--name", default=None, help="name for this machine/agent (default: the host name)")
    s.add_argument("--claude", action="store_true", help="also install the Claude Code plugin for this server (it signs in "
                                                         "through the browser, no token is stored in Claude Code)")
    s.add_argument("--no-browser", action="store_true", help="don't open the approval page")
    sub.add_parser("logout", help="forget this machine's saved token (remove it under Settings → Connected apps)")
    s = sub.add_parser("approve", help="approve a `litledger login` code from the server with the admin token: "
                                       "docker compose exec litledger litledger approve ABCD-1234")
    s.add_argument("code")
    s = sub.add_parser("reset-password", help="print a one-time link to set a new password (inside the container)")
    s.add_argument("email")
    s = sub.add_parser("admin", help="recovery inside the container: list people, or make one an admin")
    s.add_argument("action", choices=["list", "promote"])
    s.add_argument("email", nargs="?")

    s = sub.add_parser("backup", help="back up the database and files now (inside the container)")
    s.add_argument("--to", default=None, help="folder (default: the configured backup folder)")
    s = sub.add_parser("restore", help="restore a backup into the data folder (stop the server first)")
    s.add_argument("archive")
    s.add_argument("--yes", action="store_true", help="don't ask")

    s = sub.add_parser("token", help="shared agent tokens, e.g. for CI (inside the container); create re-issues an existing "
                                     "one: `token create admin` replaces the admin token in /data/tokens/admin")
    s.add_argument("action", choices=["create", "list", "revoke"])
    s.add_argument("name", nargs="?")
    s.add_argument("--admin", action="store_true", help="create: full access, including settings and people")

    s = sub.add_parser("find", help="search captured works")
    s.add_argument("query", nargs="?", default="")
    s.add_argument("--tags")
    s.add_argument("--kind", default="works")
    s.add_argument("--library", action="store_true")
    s.add_argument("--year")
    s.add_argument("--limit", type=int, default=10)
    s.add_argument("--abstract", action="store_true")
    s = sub.add_parser("discover", help="search external sources")
    s.add_argument("query")
    s.add_argument("--sources")
    s.add_argument("--year")
    s.add_argument("--limit", type=int, default=10)
    s.add_argument("--abstract", action="store_true")
    s.add_argument("--refresh", action="store_true")
    for name in ("add", "resolve"):
        s = sub.add_parser(name, help="capture works into the project" if name == "add" else "resolve without capturing")
        s.add_argument("items", nargs="+")
        s.add_argument("--tags")
        s.add_argument("--why")
    s = sub.add_parser("work", help="show one work")
    s.add_argument("work")
    s.add_argument("--include")
    s = sub.add_parser("read", help="read a work's text")
    s.add_argument("work")
    s.add_argument("--mode", default="outline")
    s.add_argument("--target")
    s.add_argument("--query")
    s.add_argument("--max-chars", type=int, default=4000)
    s = sub.add_parser("note", help="add a note")
    s.add_argument("work")
    s.add_argument("text")
    s.add_argument("--quote")
    s.add_argument("--kind")
    s.add_argument("--tags")
    s = sub.add_parser("tag", help="tags: list | apply | remove | define | rename | merge | archive")
    s.add_argument("action")
    s.add_argument("tags", nargs="*")
    s.add_argument("--works")
    s.add_argument("--to")
    s.add_argument("--description")
    s.add_argument("--library", action="store_true")
    s = sub.add_parser("export", help="export bibliography / snapshot")
    s.add_argument("--format", default="bibtex")
    s.add_argument("--tags")
    s.add_argument("--works")
    s.add_argument("--ascii", action="store_true")
    s.add_argument("--to")
    s = sub.add_parser("snapshot", help="write litledger/ (library.json, refs.bib, maps) for committing to the repo")
    s.add_argument("--to", default="litledger")
    s = sub.add_parser("import", help="import .bib / CSL-JSON / RIS / ID list / snapshot")
    s.add_argument("file")
    s.add_argument("--tags")
    s.add_argument("--why")
    s = sub.add_parser("upload", help="attach a PDF/HTML/text file to a work (or create one)")
    s.add_argument("file")
    s.add_argument("--work", default="")
    s.add_argument("--tags", default="")
    s.add_argument("--why", default="")
    s = sub.add_parser("check-bib", help="verify a .bib file")
    s.add_argument("file")
    s = sub.add_parser("check-tex", help="\\cite coverage of a .tex file")
    s.add_argument("file")
    s.add_argument("--bib")
    s = sub.add_parser("config", help="show settings, or set them: litledger config set S2_API_KEY=... (same as Admin → Sources)")
    s.add_argument("action", nargs="?", choices=["show", "set", "clear", "test"], default="show")
    s.add_argument("items", nargs="*", help="KEY=VALUE for set; KEY for clear; source id for test")
    s = sub.add_parser("brief", help="print a ready-made task for a subagent, e.g.: litledger brief extract_paper yang2024gated")
    s.add_argument("name")
    s.add_argument("work")
    s.add_argument("--focus", default="")
    sub.add_parser("status")
    s = sub.add_parser("tool", help="call any tool with JSON arguments")
    s.add_argument("name")
    s.add_argument("json", nargs="?", default="{}")
    sub.add_parser("skill", help="print the bundled agent skill (SKILL.md)")
    return p


# ------------------------------------------------------------------------------------------------ commands on the server
def _doctor(a) -> None:
    if a.local:
        return _doctor_local()
    data = _client(a).get("/api/v1/providers")
    for w in data["warnings"]:
        print("WARN", w["headline"])
        print(f"  Impact: {w['impact']}.")
        for i, step in enumerate(w["steps"], 1):
            print(f"    {i}. {step}")
    for prov in data["providers"]:
        print(f"{prov['id']:14} {prov['status']:18} {prov['note']}")


def _find(a) -> None:
    print(_client(a).tool("find", _clean({"query": a.query, "tags": _csv(a.tags), "kind": a.kind, "scope": "library" if a.library else "project",
                                   "year": a.year, "limit": a.limit, "fields": ["abstract"] if a.abstract else None})))


def _discover(a) -> None:
    print(_client(a).tool("discover", _clean({"query": a.query, "sources": _csv(a.sources), "year": a.year, "limit": a.limit,
                                       "fields": ["abstract"] if a.abstract else None, "refresh": a.refresh or None})))


def _resolve(a) -> None:
    print(_client(a).tool("resolve", _clean({"items": a.items, "add": a.cmd == "add", "tags": _csv(a.tags), "why": a.why})))


def _work(a) -> None:
    print(_client(a).tool("work", _clean({"work": a.work, "include": _csv(a.include)})))


def _read(a) -> None:
    print(_client(a).tool("read", _clean({"work": a.work, "mode": a.mode, "target": a.target, "query": a.query, "max_chars": a.max_chars})))


def _note(a) -> None:
    print(_client(a).tool("note", {"items": [_clean({"work": a.work, "text": a.text, "quote": a.quote, "kind": a.kind,
                                                     "tags": _csv(a.tags)})]}))


def _tag(a) -> None:
    print(_client(a).tool("tag", _clean({"action": a.action, "tags": a.tags or None, "works": _csv(a.works), "to": a.to,
                                  "description": a.description, "scope": "library" if a.library else None})))


def _export(a) -> None:
    text = _client(a).tool("export", _clean({"format": a.format, "tags": _csv(a.tags), "works": _csv(a.works), "ascii": a.ascii or None,
                                      "inline": True}))
    if not a.to:
        return print(text)
    Path(a.to).write_text(text.split("\n", 1)[1] if text.startswith("%") else text, encoding="utf-8")
    print(text.split("\n", 1)[0] + f" → {a.to}")


def _snapshot(a) -> None:
    c = _client(a)
    out = Path(a.to)
    (out / "maps").mkdir(parents=True, exist_ok=True)
    snap = c.tool("export", {"format": "snapshot", "inline": True}).split("\n", 1)[1]
    (out / "library.json").write_text(snap, encoding="utf-8")
    (out / "refs.bib").write_text(c.tool("export", {"format": "bibtex", "inline": True}).split("\n", 1)[1], encoding="utf-8")
    for m in json.loads(snap).get("maps", []):
        canvas = c.tool("map_get", {"map": m["id"], "format": "canvas"})
        (out / "maps" / (re.sub(r"[^\w\-]", "_", m["id"].removeprefix("map:")) + ".canvas")).write_text(canvas, encoding="utf-8")
    print(f"wrote {out}/library.json, refs.bib, maps/")


def _import(a) -> None:
    print(json.dumps(_client(a).upload(Path(a.file), tags=a.tags, why=a.why), ensure_ascii=False, indent=1))


def _upload(a) -> None:
    print(json.dumps(_client(a).upload(Path(a.file), work=a.work, tags=a.tags, why=a.why), ensure_ascii=False))


def _check_bib(a) -> None:
    print(_client(a).tool("check", {"kind": "bib", "content": Path(a.file).read_text(encoding="utf-8")}))


def _check_tex(a) -> None:
    args = {"kind": "tex", "content": Path(a.file).read_text(encoding="utf-8")}
    if a.bib:
        args["bib"] = Path(a.bib).read_text(encoding="utf-8")
    print(_client(a).tool("check", args))


def _config(a) -> None:
    c = _client(a)
    if a.action == "show":
        data = c.get("/api/v1/config")
        for f in data["fields"]:
            print(f"{f['key']:22} {(f['value'] or '-'):28} {f['label']}")
        for w in data["warnings"]:
            print("WARN", w["headline"])
        return
    if a.action == "test":
        for pid in a.items:
            r = c._check(c.http.post(f"/api/v1/config/test/{pid}")).json()
            print(f"{pid}: {r['message'] if r['ok'] else 'FAIL ' + r['message']}")
        return
    values: dict[str, str | None] = {}
    for item in a.items:
        key, sep, value = item.partition("=")
        if a.action == "set" and not sep:
            sys.exit(f"litledger: expected KEY=VALUE, got {item!r}")
        values[key.strip().upper()] = value if a.action == "set" else None
    r = c._check(c.http.put("/api/v1/config", json={"values": values})).json()
    print("saved " + ", ".join(r["changed"]))
    for w in r["warnings"]:
        print("WARN", w["headline"])


def _brief(a) -> None:
    c = _client(a)
    print(c._check(c.http.get(f"/api/v1/briefs/{a.name}", params={"work": a.work, "focus": a.focus})).text)


def _status(a) -> None:
    print(_client(a).tool("status", {}))


def _tool(a) -> None:
    print(_client(a).tool(a.name, json.loads(a.json)))


# ------------------------------------------------------------------------------------------------ local commands
def _serve(a) -> None:
    import logging

    import uvicorn

    from .config import Settings
    from .server.app import create_app

    logging.basicConfig(level=logging.INFO, format="%(levelname)-5s %(name)s  %(message)s")
    settings = Settings.from_env()
    # The app reads X-Forwarded-For/-Proto itself, from LITLEDGER_TRUSTED_PROXIES only (server/app.py).
    uvicorn.run(create_app(settings), host=a.host or settings.host, port=a.port or settings.port, log_level="warning",
                proxy_headers=False)


def _save(name: str, value: str) -> Path:
    HOME.mkdir(parents=True, exist_ok=True)
    f = HOME / name
    f.write_text(value + "\n", encoding="utf-8")
    try:
        os.chmod(f, 0o600)
    except OSError:
        pass
    return f


def claude_commands(url: str) -> list[list[str]]:
    """Install the Claude Code plugin pointed at `url` and sign it in through the browser (OAuth): no token in its
    config. The same commands as Settings → Connected apps; each is safe to re-run."""
    return [["claude", "plugin", "marketplace", "add", "sorbet-ai/litledger"],
            ["claude", "plugin", "install", "litledger@litledger", "--config", f"server={url}"],
            ["claude", "mcp", "login", "plugin:litledger:litledger"]]


def _add_to_claude(url: str) -> None:
    import shutil
    import subprocess

    cmds = claude_commands(url)
    if not shutil.which("claude"):
        print("`claude` is not on PATH; run these once it is:")
        for cmd in cmds:
            print("    " + " ".join(cmd))
        return
    for cmd in cmds:  # `claude mcp login` opens the browser and waits, so it runs attached to this terminal
        if subprocess.run(cmd).returncode != 0:
            print(f"`{' '.join(cmd)}` failed; run it again by hand.")
            return
    print("Added litledger to Claude Code; it signs in with your litledger account.")


def _login(a) -> None:
    import platform
    import time
    import webbrowser

    url = a.url.rstrip("/")
    name = a.name or platform.node() or "agent"
    try:
        r = httpx.post(f"{url}/api/v1/login/start", json={"name": name}, timeout=10)
    except httpx.HTTPError:
        sys.exit(f"litledger: cannot reach {url} — is the server running? (docker compose up -d), or pass --url")
    if r.status_code != 200:
        sys.exit(f"litledger: {r.status_code} {r.text[:200]}")
    req = r.json()
    page = url + req["approve_path"]
    print(f"Approve this sign-in in the web UI (Settings → Connected apps):\n\n    code  {req['code']}\n    name  {req['name']}\n\n    {page}\n")
    if not a.no_browser:
        try:
            webbrowser.open(page)
        except Exception:
            pass
    print("Waiting for approval…", flush=True)
    deadline = time.time() + req.get("expires_in", 600)
    while time.time() < deadline:
        time.sleep(req.get("interval", 2))
        try:
            st = httpx.get(f"{url}/api/v1/login/poll/{req['poll']}", timeout=10).json()
        except httpx.HTTPError:
            continue
        if st["status"] == "approved":
            _save("token", st["token"])
            if url != "http://127.0.0.1:8765" or (HOME / "url").exists():
                _save("url", url)
            print(f"Signed in as {st['name']}. Token saved to {HOME / 'token'}.")
            if a.claude:
                _add_to_claude(url)
            else:
                print("For Claude Code, run again with --claude, or: litledger login --claude")
            return
        if st["status"] in ("denied", "expired"):
            sys.exit(f"litledger: sign-in {st['status']}.")
    sys.exit("litledger: sign-in expired; run `litledger login` again.")


def _logout(a) -> None:
    f = HOME / "token"
    if not f.exists():
        return print("Not signed in.")
    f.unlink()
    print("Signed out on this machine. Remove it under Settings → Connected apps if it should stop working everywhere.")


def _approve(a) -> None:
    tok = token()
    if not tok:
        # Inside the container the first admin token sits in the data volume.
        from .config import Settings
        admin = Settings.from_env().token_dir / "admin"
        tok = admin.read_text(encoding="utf-8").strip() if admin.exists() else ""
    if not tok:
        sys.exit("litledger: no token to approve with — run this inside the container, or `litledger login` first")
    try:
        r = httpx.post(f"{a.url.rstrip('/')}/api/v1/logins/{a.code}", json={},
                       headers={"Authorization": f"Bearer {tok}"}, timeout=10)
    except httpx.HTTPError:
        sys.exit(f"litledger: cannot reach {a.url}")
    if r.status_code != 200:
        sys.exit(f"litledger: {r.json().get('detail', r.text) if r.headers.get('content-type', '').startswith('application/json') else r.text}")
    print(f"Approved: {r.json()['name']} is signed in.")


def _local():
    from .config import Settings
    from .ledger import Ledger

    settings = Settings.from_env()
    return settings, Ledger(settings)


def _reset_password(a) -> None:
    from urllib.parse import urlencode

    from . import accounts
    from .db import SYSTEM

    settings, lg = _local()
    person = accounts.person_by_email(lg.db, a.email)
    if not person:
        sys.exit(f"litledger: no one has the email {a.email}")
    raw = accounts.reset_token_for(lg.db, SYSTEM, person["name"], accounts.SHELL)
    base = (lg.settings.get("PUBLIC_URL") or f"http://127.0.0.1:{settings.port}").rstrip("/")
    print(f"Open this link within {accounts.RESET_MINUTES} minutes to set a new password for {person['name']}:\n")
    print(f"{base}/reset?{urlencode({'t': raw})}")
    if person["disabled"]:
        print(f"\n{person['name']} is disabled; `litledger admin promote {a.email}` enables them again.")


def _admin(a) -> None:
    from . import accounts

    _, lg = _local()
    if a.action == "list":
        for p in accounts.people(lg.db):
            print(f"{p['name']:20} {p['role']:7} {p['email'] or '-':32} {'disabled' if p['disabled'] else ''}")
        return
    if not a.email:
        sys.exit("litledger: admin promote needs an email")
    try:
        name = accounts.promote(lg.db, a.email, accounts.SHELL)
    except LookupError as exc:
        sys.exit(f"litledger: {exc}")
    print(f"{name} is now an admin and can sign in.")


def _backup(a) -> None:
    from . import accounts, backup
    from .config import Settings
    from .ledger import Ledger

    settings = Settings.from_env()
    if a.cmd == "backup":
        lg = Ledger(settings)
        dest = Path(a.to) if a.to else backup.backup_dir(lg.settings, settings.db_path.parent)
        path = backup.make(lg.db, dest, lg.settings.number("BACKUP_KEEP"), accounts.SHELL)
        print(f"Wrote {path} ({path.stat().st_size // 1024} KB)")
        return
    # The server holds a lock file in the data folder, and restore takes an exclusive lock on the database: both are
    # seen from a one-off container on the same volume, where the server's port is not.
    busy = backup.in_use(settings.db_path.parent)
    if busy:
        sys.exit(f"litledger: {busy}; stop it first (docker compose stop litledger), then run restore in a one-off "
                 "container: docker compose run --rm --no-deps litledger litledger restore /backups/<file>")
    if not a.yes and input(f"Restore {a.archive} into {settings.db_path.parent}? The current data is moved aside. [y/N] ").lower() != "y":
        return
    try:
        aside = backup.restore(Path(a.archive), settings.db_path.parent)
    except RuntimeError as exc:
        sys.exit(f"litledger: {exc}")
    print(f"Restored. The previous data is in {aside}.")


def _token(a) -> None:
    from . import accounts
    from .config import Settings
    from .db import SYSTEM, Database

    settings = Settings.from_env()
    db = Database(settings.db_path)
    if a.action == "create":
        if not a.name:
            sys.exit("token create needs a name")
        made = accounts.create_token(db, a.name, admin=a.admin, rotate=True, ctx=accounts.SHELL)
        if (accounts.get(db, accounts.root_id(db) or "") or {}).get("name") == made.name:
            accounts.write_root_token(settings.token_dir, made.token)  # the admin's sign-in token, re-issued
        print(made.token)
    elif a.action == "list":
        for pr in accounts.agents(db):
            print(f"{pr['by']:30} {pr['access']:8} {pr['origin'] or '':10} last used {pr['last_seen_at'] or 'never'}")
    else:
        try:
            accounts.remove(db, SYSTEM, a.name or "", accounts.SHELL)
            print("revoked")
        except (LookupError, PermissionError, ValueError) as exc:
            sys.exit(f"litledger: {exc}")


def _doctor_local() -> None:
    from .config import Settings
    from .ledger import Ledger

    lg = Ledger(Settings.from_env())
    for line in lg.providers.warning_text():
        print(line)
    print("(set keys under Admin → Sources on the web UI, or: litledger config set KEY=VALUE)")
    for prov in lg.providers.summary():
        print(f"{prov['id']:14} {prov['status']:18} {prov['note']}")


def _skill(a) -> None:
    print((Path(__file__).parent / "skill" / "SKILL.md").read_text(encoding="utf-8"))


COMMANDS = {
    "serve": _serve, "doctor": _doctor, "login": _login, "logout": _logout, "approve": _approve,
    "reset-password": _reset_password, "admin": _admin, "backup": _backup,
    "restore": _backup, "token": _token, "find": _find, "discover": _discover, "add": _resolve, "resolve": _resolve,
    "work": _work, "read": _read, "note": _note, "tag": _tag, "export": _export, "snapshot": _snapshot, "import": _import,
    "upload": _upload, "check-bib": _check_bib, "check-tex": _check_tex, "config": _config, "brief": _brief,
    "status": _status, "tool": _tool, "skill": _skill,
}


def main(argv: list[str] | None = None) -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    a = parser().parse_args(argv)
    COMMANDS[a.cmd](a)


if __name__ == "__main__":
    main()
