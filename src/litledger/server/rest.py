"""REST API at /api/v1 (the web app and the CLI use it) and the live event stream at /events, plus the request helpers
every route shares: who is acting, in which project, and how errors map to HTTP."""
from __future__ import annotations

import json
import re
import sqlite3
import time

import anyio
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse

from .. import __version__, backup, knowledge, library, maps, search, tools
from .. import tags as tagmod
from ..args import project_slug, tag_expr, year_range
from ..auth import Denied
from ..db import Actor, Ctx
from ..discover import search_log
from ..formats import export_project
from ..importer import upload
from ..ledger import Ledger
from ..prompts import BRIEFS
from ..reading import passage_page
from ..snowball import pending


# ------------------------------------------------------------------------------------------------ request helpers
def actor_from(request: Request | None) -> Actor:
    """Copy of the authenticated actor with project/agent/session from headers or query."""
    src = (getattr(request.state, "actor", None) if request is not None else None) or Actor(name="anonymous")
    a = Actor(principal_id=src.principal_id, name=src.name, kind=src.kind, projects=src.projects, session=src.session,
              role=src.role)
    if request is not None:
        h = request.headers
        project = h.get("x-litledger-project") or request.query_params.get("project") or ""
        if not project and h.get("x-litledger-workspace"):
            guess = _workspace_project(request, h["x-litledger-workspace"]) or ""
            project = guess if guess and src.may_use(guess) else ""
        if not project:
            project = src.projects[0] if src.projects else "default"
        a.project = project_slug(project)
        a.agent = h.get("x-litledger-agent") or None
        a.session = h.get("mcp-session-id") or h.get("x-litledger-session") or src.session
    return a


def _workspace_project(request: Request, workspace: str) -> str | None:
    """A client's working folder (e.g. D:/code/thesis) names its project when a project with that name exists. The
    project list is cached for 30 seconds on the app (one cache per server, never shared between ledgers)."""
    name = re.split(r"[\\/]", workspace.rstrip("\\/"))[-1] if workspace.strip() else ""
    slug = project_slug(name) if name else ""
    if not slug:
        return None
    state = request.app.state
    lg = getattr(state, "ledger", None)
    if lg is None:
        return None
    cache = getattr(state, "project_ids", None)
    if cache is None or time.time() - cache[0] > 30:
        with lg.db.read() as conn:
            cache = state.project_ids = (time.time(), frozenset(r["id"] for r in conn.execute("SELECT id FROM projects")))
    return slug if slug in cache[1] else None


def require_admin(request: Request) -> Actor:
    """Admins (and agents made with full access by an admin) run the server: settings, sources, people, backups."""
    a = actor_from(request)
    if not a.admin:
        raise HTTPException(403, "Only admins can do this.")
    return a


def require_person(request: Request) -> Actor:
    """Things only a signed-in person does for themselves: their password and sessions, connecting an app."""
    a = actor_from(request)
    if a.kind != "human":
        raise HTTPException(403, "Only a person can do this, not an agent or app.")
    return a


def client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def ctx_of(request: Request, actor: Actor | None = None) -> Ctx:
    """Who is asking and from where, for the audit rows the action writes (the signed-in principal, if any)."""
    a = actor or getattr(request.state, "actor", None)
    return Ctx(a.principal_id if a else None, client_ip(request), request.headers.get("user-agent", "")[:160] or None)


async def read_body(request: Request) -> dict:
    """The JSON body as a dict ({} when it is missing, malformed or not an object)."""
    try:
        body = await request.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def run(fn, *args, **kw):
    return anyio.to_thread.run_sync(lambda: fn(*args, **kw))


async def guard(fn):
    """Run fn in a worker thread; a LookupError becomes 404, a PermissionError 403, a ValueError 400 and a refused
    sign-in (Denied) its own status."""
    try:
        return await anyio.to_thread.run_sync(fn)
    except Denied as exc:
        raise HTTPException(exc.status, str(exc))
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except PermissionError as exc:
        raise HTTPException(403, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


def refresh_instructions(request: Request, lg: Ledger) -> None:
    """New MCP sessions see setup warnings as they are after a settings change."""
    server = getattr(request.app.state, "mcp_server", None)
    if server is not None and hasattr(server, "instructions"):
        server.instructions = tools.instructions(lg)


# ------------------------------------------------------------------------------------------------ routes
def register_rest(app: FastAPI, lg: Ledger) -> None:
    @app.get("/api/v1/health")
    async def health():
        return {"ok": True, "version": __version__}

    @app.get("/api/v1/me")
    async def me(request: Request):
        a = actor_from(request)
        return {"name": a.name, "kind": a.kind, "role": a.role, "admin": a.admin, "project": a.project,
                "warnings": lg.providers.warnings() if a.admin else []}

    # -- settings and sources --------------------------------------------------------------------------
    @app.get("/api/v1/config")
    async def config_get(request: Request):
        a = actor_from(request)
        fields = await run(lg.config_view)
        if not a.admin:
            # Members and agents see the library's settings (sources, toolsets, upload size) and only whether a key or
            # personal setting is set; the server's own (sign-in, mail, addresses, backups) and setup warnings are
            # for admins.
            fields = [{**f, "value": ("set" if f["set"] else "") if f["secret"] or f.get("personal") else f["value"]}
                      for f in fields if f.get("group") not in ("access", "backup")]
            return {"fields": fields, "editable": False, "warnings": []}
        return {"fields": fields, "editable": True, "warnings": lg.providers.warnings()}

    @app.put("/api/v1/config")
    async def config_put(request: Request):
        a = require_admin(request)
        values = (await read_body(request)).get("values")
        if not isinstance(values, dict):
            raise HTTPException(400, "body must be {values: {KEY: value|null}}")
        changed = await guard(lambda: lg.set_config(a, values, ctx_of(request)))
        refresh_instructions(request, lg)
        return {"changed": changed, "fields": await run(lg.config_view), "warnings": lg.providers.warnings(),
                "providers": lg.providers.summary()}

    @app.post("/api/v1/config/test/{provider}")
    async def config_test(provider: str, request: Request):
        require_admin(request)
        st = lg.providers.states.get(provider)
        if not st:
            raise HTTPException(404, f"unknown source {provider}")
        if not st.enabled:
            return {"ok": False, "message": f"{st.status}: {st.note}"}

        def test():
            try:
                with lg.http.fresh():
                    return {"ok": True, "message": st.provider.selftest()[:200]}
            except Exception as exc:
                return {"ok": False, "message": f"{type(exc).__name__}: {str(exc)[:200]}"}
        return await run(test)

    @app.post("/api/v1/sources/{pid}")
    async def source_set(pid: str, request: Request):
        a = require_admin(request)
        body = await read_body(request)
        values = body.get("values") if isinstance(body.get("values"), dict) else {}
        changed = await guard(lambda: lg.set_source(a, pid, str(body.get("action") or ""), values, bool(body.get("forget_keys")),
                                                    ctx_of(request)))
        refresh_instructions(request, lg)
        st = lg.providers.states[pid]
        return {"changed": changed, "status": st.status, "note": st.note, "warnings": lg.providers.warnings()}

    @app.get("/api/v1/providers")
    async def providers():
        def q():
            with lg.db.read() as conn:
                stats = {r["provider"]: dict(r) for r in conn.execute("SELECT * FROM provider_stats")}
            return {"providers": [p | {"stats": stats.get(p["id"])} for p in lg.providers.summary()], "warnings": lg.providers.warnings()}
        return await run(q)

    # -- backups -----------------------------------------------------------------------------------------
    def backup_folder():
        return backup.backup_dir(lg.settings, lg.db.path.parent)

    @app.get("/api/v1/backups")
    async def backups_list(request: Request):
        require_admin(request)
        return {"folder": str(backup_folder()), "backups": backup.listing(backup_folder())}

    @app.post("/api/v1/backups")
    async def backups_run(request: Request):
        require_admin(request)
        dest = backup_folder()
        try:
            path = await run(backup.make, lg.db, dest, lg.settings.number("BACKUP_KEEP"), ctx_of(request))
        except (OSError, sqlite3.Error) as exc:  # an unwritable or full folder: say so, never a 500
            raise HTTPException(400, backup.problem(exc, dest))
        return {"name": path.name, "size": path.stat().st_size}

    @app.get("/api/v1/backups/{name}")
    async def backups_get(name: str, request: Request):
        require_admin(request)
        dest = backup_folder()
        path = (dest / name).resolve()
        if not name.startswith(backup.PREFIX) or path.parent != dest.resolve() or not path.is_file():
            raise HTTPException(404)
        return FileResponse(path, filename=name, media_type="application/gzip")

    # -- tools (the CLI) ---------------------------------------------------------------------------------
    @app.post("/api/v1/tools/{name}")
    async def call_tool(name: str, request: Request):
        try:  # tools.call answers in text (unknown tool, bad project, failures); this only catches the unexpected
            text = await run(tools.call, lg, actor_from(request), name, await read_body(request))
        except Exception as exc:
            text = tools.error_text(name, exc)
        if "text/plain" in request.headers.get("accept", ""):
            return PlainTextResponse(text)
        return {"text": text}

    # -- library -----------------------------------------------------------------------------------------
    @app.get("/api/v1/projects")
    async def projects():
        return await run(search.projects, lg)

    @app.get("/api/v1/works")
    async def works(request: Request, q: str = "", tags: str = "", scope: str = "project", limit: int = 50, offset: int = 0,
                    read: str | None = None, year: str | None = None):
        a = actor_from(request)
        all_t, any_t, not_t = tag_expr([t for t in tags.split(",") if t])
        y0, y1 = year_range(year)
        return await run(search.find, lg, a.project, q, scope, "works", all_t, any_t, not_t, limit, offset, None, y0, y1, read)

    @app.get("/api/v1/works/{ref:path}/passages")
    async def passages(ref: str, request: Request, start: int = 1, end: int = 40):
        a = actor_from(request)
        return await guard(lambda: passage_page(lg, a.project, ref, start, end))

    @app.get("/api/v1/works/{ref:path}")
    async def work(ref: str, request: Request):
        a = actor_from(request)
        return await guard(lambda: library.work_detail(lg, a.project, ref, ["notes", "links", "outline", "projects", "citations",
                                                                             "history", "sources", "knowledge"]))

    @app.get("/api/v1/tags")
    async def tags_list(request: Request):
        a = actor_from(request)

        def q():
            with lg.db.read() as conn:
                return tagmod.listing(conn, a.project)
        return await run(q)

    @app.get("/api/v1/network")
    async def network(request: Request, tags: str = ""):
        """Local citation + link network among the project's works (for the UI graph view)."""
        a = actor_from(request)
        return await run(search.project_network, lg, a.project, *tag_expr([t for t in tags.split(",") if t]))

    @app.get("/api/v1/refs")
    async def refs(request: Request, q: str = "", limit: int = 6):
        """Everything a mind-map node (or a link) can point at, for the UI's reference picker."""
        a = actor_from(request)
        return await run(search.search_refs, lg, a.project, q, limit)

    @app.get("/api/v1/searches")
    async def searches(request: Request, limit: int = 30):
        a = actor_from(request)
        return await run(search_log, lg, a.project, limit)

    @app.get("/api/v1/snowball")
    async def snowball(request: Request, state: str = "pending", limit: int = 50, offset: int = 0):
        a = actor_from(request)
        return await run(pending, lg, a.project, limit, offset, state)

    # -- knowledge and maps ----------------------------------------------------------------------------
    @app.get("/api/v1/entities")
    async def entities(request: Request, kind: str | None = None, q: str = "", work: str | None = None, limit: int = 200):
        a = actor_from(request)
        return await guard(lambda: knowledge.list_entities(lg, a.project, kind, q, work, limit))

    @app.get("/api/v1/results")
    async def results(request: Request, benchmark: str | None = None, metric: str | None = None, method: str | None = None):
        a = actor_from(request)
        return await run(knowledge.results_table, lg, a.project, benchmark, metric, method)

    @app.get("/api/v1/kinds")
    async def kinds():
        return {"kinds": [{"name": k.name, "scope": k.scope, "meaning": k.meaning, "fields": list(k.fields), "group": k.group}
                          for k in knowledge.KINDS.values()],
                "relations": [{"name": r.name, "meaning": r.meaning} for r in knowledge.RELATIONS.values()]}

    @app.get("/api/v1/briefs/{name}")
    async def brief(name: str, work: str = "", focus: str = ""):
        if name not in BRIEFS:
            raise HTTPException(404, "unknown brief")
        return PlainTextResponse(BRIEFS[name][0](work, focus))

    @app.get("/api/v1/maps")
    async def maps_list(request: Request):
        a = actor_from(request)
        return await run(maps.list_maps, lg, a.project)

    @app.get("/api/v1/maps/{map_id:path}")
    async def map_get(map_id: str, request: Request):
        a = actor_from(request)
        return await guard(lambda: maps.get_map(lg, a.project, map_id))

    @app.delete("/api/v1/maps/{map_id:path}")
    async def map_delete(map_id: str, request: Request):
        a = actor_from(request)
        return await guard(lambda: {"deleted": maps.delete_map(lg, a, a.project, map_id)})

    @app.post("/api/v1/maps")
    async def map_edit(request: Request):
        a = actor_from(request)
        body = await read_body(request)
        bv = body.get("base_version")
        return await guard(lambda: maps.edit(lg, a, a.project, body.get("map"), body.get("ops") or [], body.get("title"),
                                             base_version=bv if isinstance(bv, int) else None))

    # -- activity, files -------------------------------------------------------------------------------
    @app.get("/api/v1/jobs")
    async def jobs(request: Request, limit: int = 30):
        if actor_from(request).projects is not None:
            return []  # jobs span projects

        def q():
            with lg.db.read() as conn:
                return [dict(r) for r in conn.execute("SELECT id, kind, payload, state, error, created_at, updated_at FROM jobs "
                                                      "ORDER BY created_at DESC LIMIT ?", (limit,))]
        return await run(q)

    @app.get("/api/v1/journal")
    async def journal(request: Request, since: int = 0, limit: int = 100, all_projects: bool = False):
        a = actor_from(request)
        project = None if all_projects and a.projects is None else a.project
        return await run(search.journal, lg, project, since, limit, own_only=a.projects is not None)

    @app.get("/api/v1/exports/{name}")
    async def exports(name: str, request: Request):
        """A saved export, for callers that may use the project it was made in (unlimited callers only for files from
        before names carried the project)."""
        a = actor_from(request)
        path = (lg.settings.export_dir / name).resolve()
        owner = export_project(name)
        allowed = a.projects is None or (owner is not None and a.may_use(owner))
        if path.parent != lg.settings.export_dir.resolve() or not path.exists() or not allowed:
            raise HTTPException(404)
        return FileResponse(path, filename=name)

    @app.post("/api/v1/uploads")
    async def uploads(request: Request, file: UploadFile = File(...), work: str = Form(""), tags: str = Form(""), why: str = Form("")):
        a = actor_from(request)
        max_mb = lg.settings.number("MAX_UPLOAD_MB")
        chunks, size = [], 0
        while chunk := await file.read(1 << 20):
            size += len(chunk)
            if size > max_mb * 1024 * 1024:
                raise HTTPException(413, f"upload larger than {max_mb} MB")
            chunks.append(chunk)
        tag_list = [t for t in tags.split(",") if t.strip()]
        return await guard(lambda: upload(lg, a, a.project, file.filename or "upload", b"".join(chunks), work, tag_list, why or None))

    @app.get("/events")
    async def events(request: Request):
        a = actor_from(request)
        try:
            q = lg.db.events.subscribe_async()
        except OverflowError:
            raise HTTPException(503, "too many live connections; close some litledger tabs")
        # EventSource resends the id of the last event it saw on its own reconnects; a page reopening the stream itself
        # can't set headers, so it passes ?last_id= instead.
        last = request.headers.get("last-event-id") or request.query_params.get("last_id")

        async def stream():
            try:
                yield "retry: 3000\n\n"
                if last and last.isdigit():  # replay what a reconnecting tab missed
                    for item in await run(search.journal, lg, a.project, int(last), 200, True):
                        yield f"id: {item['seq']}\ndata: {json.dumps(item, ensure_ascii=False, default=str)}\n\n"
                while not await request.is_disconnected():
                    try:
                        with anyio.fail_after(15):
                            item = await q.get()
                    except TimeoutError:
                        yield ": keepalive\n\n"
                        continue
                    if item.get("project") not in (a.project, "", None) or (a.projects is not None and item.get("project") != a.project):
                        continue
                    yield f"id: {item.get('seq', '')}\ndata: {json.dumps(item, ensure_ascii=False, default=str)}\n\n"
            finally:
                lg.db.events.unsubscribe_async(q)
        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
