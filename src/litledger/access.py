"""The account API behind Settings and Admin (docs/ACCESS.md).

Everyone: /api/v1/me/* (profile, password, sessions, linked accounts, their own tokens and apps) and approving their
own terminal sign-ins. Admins: people and invitations, everyone's tokens and apps, the audit log. The server enforces
every rule (accounts.py), and each action records itself in the audit log; the web app only hides what you can't
use.

Terminal sign-in: `litledger login` asks for a short code and polls; the person approves it under Settings → Connected
apps (or an admin runs `litledger approve CODE` in the container), and the waiting CLI receives a new API token of an
agent the approver owns. A token is only handed out after a person said yes."""
from __future__ import annotations

import json
import secrets
import time
from dataclasses import dataclass, field

from fastapi import FastAPI, HTTPException, Request

from . import accounts, auth
from .args import projects_arg
from .auth import Limiter, TTLStore
from .db import Actor, Ctx
from .ledger import Ledger
from .server.rest import actor_from, client_ip, ctx_of, guard, read_body, require_admin, require_person, run
from .signin import COOKIE, can_mail, link, send_mail, try_mail

PENDING_TTL = 600
MAX_PENDING = 20  # waiting requests, server-wide
MAX_PENDING_PER_IP = 3  # waiting requests from one address
SHOWN = 5  # the approval list shows the newest few; any waiting code can still be approved by typing it
START_LIMIT = (10, 600)  # requests started per address
_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # sign-in codes: no 0/O, 1/I/L
TOKEN_DAYS = {"30": 30, "90": 90, "365": 365, "never": None}


# ------------------------------------------------------------------------------------------------ terminal sign-in
@dataclass
class Pairing:
    """One `litledger login` waiting for a person to approve it."""
    code: str
    name: str
    ip: str
    client: str
    at: float = field(default_factory=time.time)
    status: str = "pending"  # pending | approved | denied
    token: str | None = None


class Pairings:
    """Requests live in memory under their poll key and expire after ten minutes."""

    def __init__(self) -> None:
        self.store = TTLStore(PENDING_TTL, cap=MAX_PENDING * 5)
        self.limit = Limiter(*START_LIMIT)

    def start(self, name: str, ip: str, client: str) -> tuple[str, Pairing]:
        """A new request; refused (ValueError) past the per-address rate, the per-address cap or the server-wide cap."""
        if not self.limit.ok("ip:" + ip):
            raise ValueError("too many sign-in requests from this address; wait a few minutes")
        with self.store.lock:
            waiting = [r for _, r in self.store.items() if r.status == "pending"]
            if sum(r.ip == ip for r in waiting) >= MAX_PENDING_PER_IP:
                raise ValueError("too many sign-in requests from this address are waiting; approve or deny them, or wait 10 minutes")
            if len(waiting) >= MAX_PENDING:
                raise ValueError("too many sign-in requests waiting; approve or deny some under Settings → Connected apps")
            raw = "".join(secrets.choice(_ALPHABET) for _ in range(8))
            req = Pairing(code=f"{raw[:4]}-{raw[4:]}", name=accounts.clean_name(name), ip=ip, client=client[:120])
            return self.store.put(req), req

    def poll(self, poll: str) -> dict:
        """What the waiting CLI sees. The token is returned exactly once."""
        with self.store.lock:
            req = self.store.get(poll)
            if not req:
                return {"status": "expired"}
            if req.status != "pending":
                self.store.pop(poll)
            if req.status == "approved":
                return {"status": "approved", "token": req.token, "name": req.name}
            return {"status": req.status}

    def pending(self) -> list[dict]:
        """The newest few waiting requests (anyone can start one, so the list stays short; the code the terminal shows
        is what to approve)."""
        waiting = [r for _, r in self.store.items() if r.status == "pending"][::-1]  # the store keeps them oldest first
        return [{"code": r.code, "name": r.name, "ip": r.ip, "client": r.client, "age": int(time.time() - r.at)}
                for r in waiting[:SHOWN]]

    def _find(self, code: str) -> Pairing:
        code = code.strip().upper()
        for _, r in self.store.items():
            if r.code == code and r.status == "pending":
                return r
        raise LookupError("No sign-in request with that code. It may have expired; run `litledger login` again.")

    def approve(self, db, approver: Actor, code: str, name: str | None, ctx: Ctx) -> str:
        """The terminal gets an agent of its own: owned by the person who approved, or shared when a shared admin token
        approved it."""
        with self.store.lock:
            req = self._find(code)
            made = accounts.create_token(db, name or req.name, owner=approver if approver.kind == "human" else None,
                                         origin="login", ctx=ctx)
            req.token, req.name, req.status = made.token, made.name, "approved"
            return made.name

    def deny(self, db, code: str, ctx: Ctx) -> None:
        with self.store.lock:
            req = self._find(code)
            req.status = "denied"
            accounts.record(db, ctx, "terminal.deny", req.name, from_ip=req.ip, client=req.client[:80])


def _approver(request: Request) -> Actor:
    a = actor_from(request)
    if a.kind != "human" and not a.admin:
        raise HTTPException(403, "Only a person or an admin can approve sign-ins.")
    return a


# ------------------------------------------------------------------------------------------------ routes
def register_access(app: FastAPI, lg: Ledger) -> None:
    """Every route calls one action in accounts.py with the request's context; the action writes its own audit rows."""
    pairings = Pairings()
    invite_limit = Limiter(30, 3600)

    # -- your account ----------------------------------------------------------------------------------------
    @app.get("/api/v1/me/account")
    async def my_account(request: Request):
        me = require_person(request)
        prof = await run(accounts.profile, lg.db, me.principal_id)
        return {**prof, "email_change": "none" if prof["root"] else "direct" if me.admin else "confirm" if can_mail(lg) else "admin"}

    @app.patch("/api/v1/me/account")
    async def my_account_edit(request: Request):
        """Rename yourself, or change your email: admins at once, others through a link sent to the new address."""
        me = require_person(request)
        body = await read_body(request)
        ctx = ctx_of(request, me)
        out: dict = {}
        if body.get("name"):
            out["name"] = await guard(lambda: accounts.rename(lg.db, me.principal_id, str(body["name"]), ctx))
        if body.get("email"):
            if me.admin:
                out["email"] = await guard(lambda: accounts.set_email(lg.db, me.principal_id, str(body["email"]), ctx))
            elif not can_mail(lg):
                raise HTTPException(400, "This server can't send email, so ask an admin to change it.")
            else:
                email, raw = await guard(lambda: accounts.email_token(lg.db, me.principal_id, str(body["email"]), ctx))
                text = (f"Confirm your new litledger email:\n\n{link(lg, request, '/auth/email/confirm', raw)}\n\n"
                        f"The link expires in {accounts.EMAIL_HOURS} hours. If you didn't ask for this, ignore this email.")
                await run(try_mail, lg, email, "Confirm your litledger email", text)
                out["confirm"] = email
        return out

    @app.put("/api/v1/me/password")
    async def my_password(request: Request):
        """Set or change your password (the current one is needed when you have one). Signs out your other sessions."""
        me = require_person(request)
        body = await read_body(request)
        await guard(lambda: accounts.change_password(lg.db, me.principal_id, str(body.get("current") or ""),
                                                     str(body.get("new") or ""), request.cookies.get(COOKIE), ctx_of(request, me)))
        return {"ok": True}

    @app.get("/api/v1/me/sessions")
    async def my_sessions(request: Request):
        me = require_person(request)
        current = request.cookies.get(COOKIE)
        cur = auth.digest(current) if current else None
        return [{"id": r["hash"][:16], "created_at": r["created_at"], "used_at": r["used_at"], "expires_at": r["expires_at"],
                 "current": r["hash"] == cur, **{k: v for k, v in json.loads(r["meta"] or "{}").items()
                                                 if k in ("how", "agent", "ip")}}
                for r in await run(auth.sessions, lg.db, me.principal_id)]

    @app.delete("/api/v1/me/sessions/{sid}")
    async def my_session_end(sid: str, request: Request):
        me = require_person(request)
        await guard(lambda: accounts.end_session(lg.db, me.principal_id, sid, ctx_of(request, me)))
        return {"ok": True}

    @app.delete("/api/v1/me/sessions")
    async def my_sessions_end_others(request: Request):
        me = require_person(request)
        return {"ended": await run(accounts.end_other_sessions, lg.db, me.principal_id, request.cookies.get(COOKIE), ctx_of(request, me))}

    @app.delete("/api/v1/me/identities/{provider}")
    async def my_identity_unlink(provider: str, request: Request):
        me = require_person(request)
        return {"removed": await guard(lambda: accounts.unlink_identity(lg.db, me.principal_id, provider, ctx_of(request, me)))}

    # -- your tokens and apps --------------------------------------------------------------------------------
    @app.get("/api/v1/me/agents")
    async def my_agents(request: Request):
        me = require_person(request)
        return await run(accounts.agents, lg.db, me.principal_id)

    @app.post("/api/v1/me/tokens")
    async def my_token(request: Request):
        """A personal API token: named, optionally expiring and limited to projects, shown once."""
        me = require_person(request)
        body = await read_body(request)
        days = str(body.get("expires") or "90")
        if days not in TOKEN_DAYS:
            raise HTTPException(400, "expires must be 30, 90, 365 or never")
        full = body.get("access") == "full"
        if full and not me.admin:
            raise HTTPException(403, "Only admins can make full-access tokens.")
        made = await guard(lambda: accounts.create_token(lg.db, str(body.get("name") or "token"), projects_arg(body.get("projects")),
                                                         owner=me, admin=full, days=TOKEN_DAYS[days], ctx=ctx_of(request, me)))
        return {"name": made.name, "token": made.token}

    # -- terminal sign-ins ---------------------------------------------------------------------------------
    @app.post("/api/v1/login/start")
    async def login_start(request: Request):
        body = await read_body(request)
        try:
            poll, req = pairings.start(str(body.get("name") or "agent"), client_ip(request) or "?",
                                       request.headers.get("user-agent", ""))
        except ValueError as exc:
            raise HTTPException(429, str(exc))
        return {"code": req.code, "poll": poll, "name": req.name, "expires_in": PENDING_TTL, "interval": 2,
                "approve_path": f"/settings/apps?code={req.code}"}

    @app.get("/api/v1/login/poll/{poll}")
    async def login_poll(poll: str):
        return pairings.poll(poll)

    @app.get("/api/v1/logins")
    async def logins_pending(request: Request):
        _approver(request)
        return pairings.pending()

    @app.post("/api/v1/logins/{code}")
    async def login_approve(code: str, request: Request):
        me = _approver(request)
        body = await read_body(request)
        return {"name": await guard(lambda: pairings.approve(lg.db, me, code, body.get("name"), ctx_of(request, me)))}

    @app.delete("/api/v1/logins/{code}")
    async def login_deny(code: str, request: Request):
        me = _approver(request)
        await guard(lambda: pairings.deny(lg.db, code, ctx_of(request, me)))
        return {"denied": True}

    # -- people, agents and apps (admins; owners for their own agents) -------------------------------------
    @app.get("/api/v1/principals")
    async def principals(request: Request):
        require_admin(request)
        return {"people": await run(accounts.people, lg.db), "agents": await run(accounts.agents, lg.db)}

    @app.post("/api/v1/principals")
    async def shared_agent(request: Request):
        """A shared agent (no owner) with an API token, e.g. for CI. Full access makes it an admin."""
        me = require_admin(request)
        body = await read_body(request)
        days = str(body.get("expires") or "never")
        if days not in TOKEN_DAYS:
            raise HTTPException(400, "expires must be 30, 90, 365 or never")
        full = body.get("access") == "full"
        made = await guard(lambda: accounts.create_token(lg.db, str(body.get("name") or ""), projects_arg(body.get("projects")),
                                                         admin=full, days=TOKEN_DAYS[days], ctx=ctx_of(request, me)))
        return {"name": made.name, "token": made.token}

    @app.patch("/api/v1/principals/{name}")
    async def principal_update(name: str, request: Request):
        """role, disabled, email (people; admins), projects (people: admins; agents: their owner or an admin)."""
        me = actor_from(request)
        body = await read_body(request)
        ctx = ctx_of(request, me)
        if "role" in body:
            await guard(lambda: accounts.set_role(lg.db, me, name, str(body["role"]), ctx))
        if "disabled" in body:
            await guard(lambda: accounts.set_disabled(lg.db, me, name, bool(body["disabled"]), ctx))
        if "email" in body or "projects" in body:
            projects = projects_arg(body.get("projects")) if "projects" in body else "keep"
            await guard(lambda: accounts.update(lg.db, me, name, body.get("email"), projects, ctx))
        return {"ok": True}

    @app.delete("/api/v1/principals/{name}")
    async def principal_remove(name: str, request: Request):
        me = actor_from(request)
        await guard(lambda: accounts.remove(lg.db, me, name, ctx_of(request, me)))
        return {"removed": name}

    @app.post("/api/v1/principals/{name}/reset-link")
    async def principal_reset_link(name: str, request: Request):
        """A one-time password link for someone else, to hand over when the server can't send email."""
        me = require_admin(request)
        raw = await guard(lambda: accounts.reset_token_for(lg.db, me, name, ctx_of(request, me)))
        return {"link": link(lg, request, "/reset", raw), "minutes": accounts.RESET_MINUTES}

    @app.post("/api/v1/principals/{name}/sign-out")
    async def principal_sign_out(name: str, request: Request):
        me = require_admin(request)
        return {"ended": await guard(lambda: accounts.end_sessions_of(lg.db, me, name, ctx_of(request, me)))}

    # -- invitations -----------------------------------------------------------------------------------------
    @app.get("/api/v1/invites")
    async def invites(request: Request):
        require_admin(request)
        return await run(accounts.invites, lg.db)

    @app.post("/api/v1/invites")
    async def invite(request: Request):
        """Invite someone by email. The link is emailed when the server can send email, and always returned so the
        admin can pass it on."""
        me = require_admin(request)
        body = await read_body(request)
        email = str(body.get("email") or "").strip().lower()
        raw = await guard(lambda: accounts.invite(lg.db, me, email, str(body.get("role") or "member"),
                                                  projects_arg(body.get("projects")), ctx_of(request, me), invite_limit))
        url = link(lg, request, "/invite", raw)
        text = (f"{me.name} invited you to their litledger, a shared library of papers.\n\nJoin here:\n{url}\n\n"
                f"The link works once and expires in {accounts.INVITE_DAYS} days.")
        sent = can_mail(lg) and await run(try_mail, lg, email, "You're invited to litledger", text)
        return {"link": url, "sent": bool(sent), "days": accounts.INVITE_DAYS}

    @app.delete("/api/v1/invites/{invite_id}")
    async def invite_revoke(invite_id: str, request: Request):
        me = require_admin(request)
        await guard(lambda: accounts.revoke_invite(lg.db, invite_id, ctx_of(request, me)))
        return {"ok": True}

    # -- audit log and email check -------------------------------------------------------------------------
    @app.get("/api/v1/audit")
    async def audit_log(request: Request, limit: int = 100, before: int | None = None, person: str = "", kind: str = "",
                        problems: bool = False):
        require_admin(request)
        return await run(accounts.audit_log, lg.db, limit, before, person or None, kind or None, problems)

    @app.post("/api/v1/mail/test")
    async def mail_test(request: Request):
        me = require_admin(request)
        email = (await run(accounts.get, lg.db, me.principal_id) or {}).get("email") if me.principal_id else None
        if not email:
            raise HTTPException(400, "Add an email to your profile first.")
        try:
            ok = await run(send_mail, lg, email, "litledger test email", "Email from litledger works.")
        except Exception as exc:
            return {"ok": False, "message": f"{type(exc).__name__}: {str(exc)[:200]}"}
        return {"ok": ok, "message": f"Sent to {email}." if ok else "Set the SMTP server first."}
