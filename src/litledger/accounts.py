"""Who has an account here, and what each may do (docs/ACCESS.md).

People are admins or members. Admins run the server (sources, sign-in methods, people, everyone's tokens and apps);
members use the library and manage their own account, tokens and connected apps. The root admin ("admin") is the
first-start admin token's person: it signs in with that token, has no email or password, and can't be demoted,
disabled or removed, so there is always an admin. People create their own accounts (as members, when the sign-up
policy lets them) or accept an invitation, and an admin promotes them.

People own agents: API tokens they make, terminals they approve (`litledger login`) and apps they connect over OAuth.
An agent never outranks its owner and stops working while its owner is disabled or removed. Shared agents (tokens
admins make for CI) belong to no one.

Every function here that changes an account, a credential or who may do what writes its audit row (tx.audit) in the
same transaction as the change, from the Ctx its caller passes: routes and the CLI never write the audit log
themselves, so an entry exists exactly when its change does. Sign-ins, sign-ups, invites and resets return the new
session's secret for the caller to put in a cookie."""
from __future__ import annotations

import json
import os
import secrets
from datetime import timedelta
from pathlib import Path
from typing import NamedTuple

from . import auth
from .auth import Denied, Limiter, at, clean_email, digest
from .db import SYSTEM, Actor, Ctx, Database, now, ulid, who_sql
from .textutil import slug

ROLES = ("member", "admin")
ROOT = "bootstrap"  # principals.origin of the root admin
INVITE_DAYS = 7
RESET_MINUTES = 60
EMAIL_HOURS = 24
FOLD = 900  # repeated failures from one address fold into one audit row for this long
NO_CTX = Ctx()
SHELL = Ctx(via="cli")  # the server's shell (`litledger …` in the container)
TAKEN = "That email is already in use here. Sign in, or reset the password to sign everyone else out."
RECLAIMED = "Signed out other devices and removed links and tokens made before you confirmed this email."

# How the audit log shows events: failures (refused sign-ins, limits, reuse of a stolen token) and security events.
FAILURES = ("signin.failed", "signin.refused", "limit.hit", "password.change_failed", "identity.link_failed", "invite.expired",
            "app.token_reuse")
SECURITY = ("role.change", "person.disable", "person.enable", "person.remove", "token.rotate", "password.reset_link",
            "app.token_reuse", "sessions.end", "account.reclaim")
# The audit log's "kind" filter: groups of op prefixes.
KINDS = {"signin": ("signin", "signout", "session", "sessions", "limit"), "account": ("account", "email", "identity", "password"),
         "people": ("role", "person", "invite", "principal"), "tokens": ("token", "terminal"), "apps": ("app",),
         "server": ("settings", "source", "backup")}


def clean_name(name: str, default: str = "agent") -> str:
    return slug(name, keep="._-", limit=40, default=default)


def unique_name(conn, base: str) -> str:
    """`base`, or `base-2`, `base-3`, …: names are never reused, so history keeps pointing at the right principal."""
    name, n = base, 2
    while conn.execute("SELECT 1 FROM principals WHERE name=?", (name,)).fetchone():
        name, n = f"{base}-{n}", n + 1
    return name


def _projects(projects) -> str | None:
    return json.dumps(sorted(set(projects))) if projects else None


def _insert(conn, name: str, kind: str, **cols) -> tuple[str, str]:
    pid, name = "p" + ulid(), unique_name(conn, name)
    cols = {k: v for k, v in cols.items() if v is not None}
    keys = ["id", "name", "kind", "created_at", *cols]
    conn.execute(f"INSERT INTO principals({','.join(keys)}) VALUES({','.join('?' * len(keys))})",
                 (pid, name, kind, now(), *cols.values()))
    return pid, name


def _live(conn, name: str):
    row = conn.execute("SELECT * FROM principals WHERE name=? AND revoked_at IS NULL AND kind<>'system'", (name,)).fetchone()
    if not row:
        raise LookupError(f"No one named {name}.")
    return row


def _by_id(conn, pid: str):
    return conn.execute("SELECT * FROM principals WHERE id=?", (pid,)).fetchone()


def _email_taken(conn, email: str, but: str | None = None) -> bool:
    return bool(conn.execute("SELECT 1 FROM principals WHERE email=? AND kind='human' AND revoked_at IS NULL AND id<>?",
                             (email, but or "")).fetchone())


def _tx(db: Database, ctx: Ctx):
    return db.tx(SYSTEM, ctx)


def get(db: Database, pid: str) -> dict | None:
    with db.read() as conn:
        row = _by_id(conn, pid)
    return dict(row) if row else None


def _limit(db: Database, limit: Limiter | None, keys: list[str], what: str, target: str | None, ctx: Ctx) -> None:
    """Refuse (and record, once per window) when a rate limit is hit."""
    if limit and not limit.ok(*keys):
        with _tx(db, ctx) as tx:
            tx.audit("limit.hit", target, fold=FOLD, what=what)
        raise Denied("Too many attempts. Wait a few minutes.", 429, "limited")


def record(db: Database, ctx: Ctx, op: str, target: str | None = None, *, subject: str | None = None, **detail) -> None:
    """An event that changes nothing in the database (a refused terminal sign-in, a declined app)."""
    with _tx(db, ctx) as tx:
        tx.audit(op, target, subject=subject, **detail)


# ------------------------------------------------------------------------------------------------ audit log
def audit_log(db: Database, limit: int = 100, before: int | None = None, person: str | None = None, kind: str | None = None,
              problems: bool = False) -> list[dict]:
    """Newest first. person: events by them (or their agents) or about them; kind: a KINDS group (or one op prefix);
    problems: only failures and security events."""
    where, args = ["a.seq<?"], [before or 2 ** 62]
    if person:
        where.append("(a.actor_id IN (SELECT id FROM principals WHERE name=? UNION SELECT a2.id FROM principals a2 "
                     "JOIN principals o2 ON o2.id=a2.owner_id WHERE o2.name=?) OR s.name=?)")
        args += [person, person, person]
    if kind:
        prefixes = KINDS.get(kind, (kind,))
        where.append(f"substr(a.op, 1, instr(a.op || '.', '.') - 1) IN ({','.join('?' * len(prefixes))})")
        args += list(prefixes)
    if problems:
        ops = FAILURES + SECURITY
        where.append(f"a.op IN ({','.join('?' * len(ops))})")
        args += list(ops)
    with db.read() as conn:
        rows = conn.execute(f"SELECT a.seq, a.ts, a.op, a.target, a.ip, a.ua, a.count, a.last_at, a.detail, {who_sql()} AS actor, "
                            "s.name AS subject FROM audit a LEFT JOIN principals p ON p.id=a.actor_id "
                            "LEFT JOIN principals o ON o.id=p.owner_id LEFT JOIN principals s ON s.id=a.subject_id "
                            f"WHERE {' AND '.join(where)} ORDER BY a.seq DESC LIMIT ?", (*args, min(limit, 500))).fetchall()
    return [dict(r) | {"detail": json.loads(r["detail"]),
                       "level": "failure" if r["op"] in FAILURES else "security" if r["op"] in SECURITY else "info"} for r in rows]


# ------------------------------------------------------------------------------------------------ people
def _new_person(tx, name: str, email: str | None, role: str = "member", projects=None, hashed: str | None = None,
                verified: bool = False) -> str:
    email = clean_email(email)
    if role not in ROLES:
        raise ValueError("role must be member or admin")
    if email and _email_taken(tx, email):
        raise ValueError(f"{email} already has an account.")
    pid, _ = _insert(tx, clean_name(name, "person"), "human", role=role, email=email, projects=_projects(projects),
                     password_hash=hashed, email_verified_at=now() if email and verified else None)
    return pid


def _hash(password: str | None) -> str | None:
    return auth.hash_password(auth.password_rules(password)) if password else None


def create_person(db: Database, name: str, email: str | None, role: str = "member", projects=None,
                  password: str | None = None, verified: bool = False, ctx: Ctx = NO_CTX) -> str:
    """A person made directly (tests, scripts). verified: the email is known to belong to them."""
    hashed = _hash(password)
    with _tx(db, ctx) as tx:
        pid = _new_person(tx, name, email, role, projects, hashed, verified)
        tx.audit("account.create", clean_email(email), subject=pid, role=role)
    return pid


def _session(tx, pid: str, how: str, ctx: Ctx, audit: bool = True) -> str:
    """A browser session for this person, recorded as a sign-in (unless the caller's own event says it)."""
    raw = auth.new_session(tx, pid, {"how": how, "agent": (ctx.ua or "")[:160], "ip": ctx.ip})
    if audit:
        tx.audit("signin", subject=pid, actor_id=pid, method=how)
    return raw


def root_id(db: Database) -> str | None:
    with db.read() as conn:
        row = conn.execute("SELECT id FROM principals WHERE origin=? AND kind='human' AND revoked_at IS NULL", (ROOT,)).fetchone()
    return row["id"] if row else None


def _not_root(row, what: str) -> None:
    if row and row["origin"] == ROOT:
        raise ValueError(f"The admin account signs in with its token, so it has no {what}.")


def password_signup(settings, can_mail: bool) -> bool:
    """A name, email and password are enough to join with SIGNUP=anyone; with allowed domains the address is checked
    by an emailed link first (so only when the server can send email)."""
    policy = (settings.get("SIGNUP") or "anyone").strip().lower()
    return policy == "anyone" or (policy == "domains" and can_mail)


class Signup(NamedTuple):
    session: str | None  # signed in at once (SIGNUP=anyone) ...
    link: str | None  # ... or a link that makes the account (allowed domains)
    confirm: str | None  # a link that confirms the new account's email (when the server can send email)


def signup(db: Database, settings, name: str, email: str, password: str, can_mail: bool, ctx: Ctx = NO_CTX,
           limit: Limiter | None = None) -> Signup:
    """A new member with a password."""
    email = clean_email(email)
    _limit(db, limit, ["ip:" + (ctx.ip or "?")], "signup", email, ctx)
    if not email:
        raise ValueError("Enter your email.")
    if not password_signup(settings, can_mail) or not may_join(settings, email):
        raise PermissionError("You can't create an account with that email here. Ask an admin for an invite.")
    hashed = auth.hash_password(auth.password_rules(password))
    with _tx(db, ctx) as tx:
        if _email_taken(tx, email):
            # Never "your password": the account may be a stranger's. A reset by email makes whoever controls the
            # address its owner and ends everything set up before (reset_password).
            raise ValueError(TAKEN)
        name = name or email.split("@")[0]
        if (settings.get("SIGNUP") or "anyone").strip().lower() != "anyone":
            raw = auth.issue(tx, root_id(db), "signup", timedelta(hours=EMAIL_HOURS), meta={"name": name, "email": email, "hash": hashed})
            tx.audit("account.join_requested", email)
            return Signup(None, raw, None)
        pid = _new_person(tx, name, email, hashed=hashed)
        confirm = auth.issue(tx, pid, "email", timedelta(hours=EMAIL_HOURS), meta={"email": email}) if can_mail else None
        tx.audit("account.join", email, subject=pid, actor_id=pid, method="password", confirm_sent=bool(confirm))
        return Signup(_session(tx, pid, "signup", ctx, audit=False), None, confirm)


def finish_signup(db: Database, raw: str, ctx: Ctx = NO_CTX) -> str | None:
    """Open a domain sign-up's emailed link: the account is made (its email confirmed) and signed in."""
    with _tx(db, ctx) as tx:
        row = auth.consume(tx, raw, "signup")
        if not row:
            return None
        m = row["meta"]
        pid = _new_person(tx, m["name"], m["email"], hashed=m["hash"], verified=True)
        tx.audit("account.join", m["email"], subject=pid, actor_id=pid, method="email link")
        return _session(tx, pid, "signup", ctx, audit=False)


def sign_in_password(db: Database, email: str, password: str, ctx: Ctx = NO_CTX, limit: Limiter | None = None) -> str:
    email = (email or "").strip().lower()
    _limit(db, limit, [email, "ip:" + (ctx.ip or "?")], "signin", email, ctx)
    with db.read() as conn:
        row = conn.execute("SELECT id, password_hash, disabled_at FROM principals WHERE email=? AND kind='human' "
                           "AND revoked_at IS NULL", (email,)).fetchone()
    ok = auth.password_ok(row["password_hash"] if row else None, password or "")  # the hash runs either way
    with _tx(db, ctx) as tx:
        if not (row and ok):
            tx.audit("signin.failed", email, subject=row["id"] if row else None, fold=FOLD, method="password")
        elif row["disabled_at"]:
            tx.audit("signin.refused", email, subject=row["id"], method="password", reason="disabled")
        else:
            return _session(tx, row["id"], "password", ctx)
    if row and ok:
        raise Denied("This account is disabled. Ask an admin.", 403, "disabled")
    raise Denied("Wrong email or password.", 401, "wrong")


def sign_in_token(db: Database, token: str, ctx: Ctx = NO_CTX, limit: Limiter | None = None) -> str:
    """The root admin signs in with the admin token (only a person's token: never an agent's)."""
    _limit(db, limit, ["ip:" + (ctx.ip or "?")], "token", "admin token", ctx)
    token = (token or "").strip()
    actor = auth.verify(db, token, "urn:litledger:api")
    with _tx(db, ctx) as tx:
        row = _by_id(tx, actor.principal_id) if actor else None
        if row and row["kind"] == "human" and row["token_hash"] == digest(token):
            return _session(tx, row["id"], "token", ctx)
        tx.audit("signin.failed", "admin token", fold=FOLD, method="token")
    raise Denied("That token isn't valid.", 401, "wrong")


def logout(db: Database, raw: str | None, ctx: Ctx = NO_CTX) -> None:
    if not raw:
        return
    with _tx(db, ctx) as tx:
        row = auth.find(tx, raw, "session")
        if row and auth.revoke_credential(tx, raw):
            tx.audit("signout", subject=row["principal_id"], actor_id=row["principal_id"])


def end_session(db: Database, pid: str, sid: str, ctx: Ctx = NO_CTX) -> None:
    """End one of your own sessions (one device)."""
    with _tx(db, ctx) as tx:
        rows = [r for r in auth_sessions(tx, pid) if r["hash"].startswith(sid)] if len(sid) >= 8 else []
        if not rows:
            raise LookupError("No such session.")
        auth.revoke_credential(tx, hash_=rows[0]["hash"])
        meta = json.loads(rows[0]["meta"] or "{}")
        tx.audit("session.end", subject=pid, device=(meta.get("agent") or "")[:80], from_ip=meta.get("ip"))


def end_other_sessions(db: Database, pid: str, keep_raw: str | None, ctx: Ctx = NO_CTX) -> int:
    """Sign out everywhere else."""
    with _tx(db, ctx) as tx:
        n = auth.revoke_kinds(tx, pid, ("session",), keep=digest(keep_raw) if keep_raw else None)
        tx.audit("sessions.end", subject=pid, ended=n, own=True)
    return n


def auth_sessions(conn, pid: str) -> list:
    return conn.execute("SELECT hash, meta FROM credentials WHERE principal_id=? AND kind='session' AND revoked_at IS NULL "
                        "AND expires_at>?", (pid, now())).fetchall()


def _set_password(tx, pid: str, hashed: str, keep: str | None = None) -> int:
    """A new password signs the person out everywhere else and voids outstanding reset links."""
    tx.execute("UPDATE principals SET password_hash=? WHERE id=? AND kind='human'", (hashed, pid))
    return auth.revoke_kinds(tx, pid, ("session", "reset"), keep=keep)


def change_password(db: Database, pid: str, current: str, new: str, keep_raw: str | None = None, ctx: Ctx = NO_CTX) -> None:
    """Set or change your password: the current one is needed when you have one. Your other sessions end."""
    hashed = auth.hash_password(auth.password_rules(new))
    with db.read() as conn:
        row = _by_id(conn, pid)
    _not_root(row, "password")
    wrong = bool(row["password_hash"]) and not auth.password_ok(row["password_hash"], current or "")
    with _tx(db, ctx) as tx:
        if wrong:
            tx.audit("password.change_failed", subject=pid, fold=FOLD)
        else:
            ended = _set_password(tx, pid, hashed, digest(keep_raw) if keep_raw else None)
            tx.audit("password.change", subject=pid, other_sessions_ended=ended)
    if wrong:
        raise Denied("The current password is wrong.", 400)


def set_password(db: Database, pid: str, password: str, ctx: Ctx = NO_CTX) -> None:
    """Set someone's password directly (tests, scripts); every session ends."""
    hashed = auth.hash_password(auth.password_rules(password))
    with _tx(db, ctx) as tx:
        _not_root(_by_id(tx, pid), "password")
        tx.audit("password.change", subject=pid, other_sessions_ended=_set_password(tx, pid, hashed))


def rename(db: Database, pid: str, name: str, ctx: Ctx = NO_CTX) -> str:
    new = clean_name(name, "")
    if not new:
        raise ValueError("Enter a name.")
    with _tx(db, ctx) as tx:
        row = _by_id(tx, pid)
        _not_root(row, "other name")
        if tx.execute("SELECT 1 FROM principals WHERE name=? AND id<>?", (new, pid)).fetchone():
            raise ValueError(f"The name {new} is taken.")
        if new != row["name"]:
            tx.execute("UPDATE principals SET name=? WHERE id=?", (new, pid))
            tx.audit("account.rename", new, subject=pid, old=row["name"])
    return new


def _set_email(tx, pid: str, email: str) -> str:
    email = clean_email(email)
    if not email:
        raise ValueError("Enter an email.")
    _not_root(_by_id(tx, pid), "email")
    if _email_taken(tx, email, pid):
        raise ValueError(f"{email} already has an account.")
    tx.execute("UPDATE principals SET email=?, email_verified_at=? WHERE id=?", (email, now(), pid))
    return email


def set_email(db: Database, pid: str, email: str, ctx: Ctx = NO_CTX) -> str:
    """An admin changes their own email at once (admins vouch for addresses)."""
    with _tx(db, ctx) as tx:
        email = _set_email(tx, pid, email)
        tx.audit("email.change", email, subject=pid)
    return email


def email_token(db: Database, pid: str, email: str, ctx: Ctx = NO_CTX) -> tuple[str, str]:
    """A link, sent to a new address, that changes (and confirms) the email when it's opened."""
    email = clean_email(email)
    if not email:
        raise ValueError("Enter an email.")
    with _tx(db, ctx) as tx:
        _not_root(_by_id(tx, pid), "email")
        if _email_taken(tx, email, pid):
            raise ValueError(f"{email} already has an account.")
        raw = auth.issue(tx, pid, "email", timedelta(hours=EMAIL_HOURS), meta={"email": email})
        tx.audit("email.change_requested", email, subject=pid)
    return email, raw


def confirm_email(db: Database, raw: str, ctx: Ctx = NO_CTX, signed_in: str | None = None) -> str | None:
    """Open an email link: the address is set (if it's new) and confirmed. An account whose email was never confirmed
    may be a stranger's (anyone can sign up with any address), so its link only works in a browser signed in to that
    account (`signed_in`, as GitHub does): confirming then joins the password and the address in one person. Anyone
    else gets PermissionError and the link stays usable; the address's owner takes the account over with a reset."""
    with _tx(db, ctx) as tx:
        row = auth.find(tx, raw, "email")
        if not auth.valid(row):
            return None
        if not _by_id(tx, row["principal_id"])["email_verified_at"] and signed_in != row["principal_id"]:
            raise PermissionError("Sign in to the account in this browser, then open the link again. "
                                  "Didn't make this account? Reset its password instead.")
        if not auth.revoke_credential(tx, raw):  # used a moment ago by another request
            return None
        email = _set_email(tx, row["principal_id"], row["meta"]["email"])
        tx.audit("email.confirm", email, subject=row["principal_id"], actor_id=row["principal_id"],
                 changed=email != row["pemail"])
        return row["principal_id"]


def profile(db: Database, pid: str) -> dict:
    with db.read() as conn:
        p = conn.execute("SELECT name, email, role, projects, created_at, password_hash IS NOT NULL AS has_password, "
                         "email_verified_at IS NOT NULL AS email_verified, origin=? AS root FROM principals WHERE id=?",
                         (ROOT, pid)).fetchone()
        idents = [dict(r) for r in conn.execute("SELECT provider, email, login FROM identities WHERE principal_id=?", (pid,))]
    return {**dict(p), "has_password": bool(p["has_password"]), "email_verified": bool(p["email_verified"]),
            "root": bool(p["root"]), "projects": list(auth.projects_of(p["projects"]) or []) or None,
            "identities": idents}


# ------------------------------------------------------------------------------------------------ password resets
def request_reset(db: Database, email: str, can_mail: bool, ctx: Ctx = NO_CTX, limit: Limiter | None = None) -> str | None:
    """Forgot password: a reset link for an enabled person with this email when the server can send it. Recorded
    either way (the caller answers the same either way)."""
    email = (email or "").strip().lower()
    _limit(db, limit, [email, "ip:" + (ctx.ip or "?")], "reset", email, ctx)
    with _tx(db, ctx) as tx:
        row = tx.execute("SELECT id, disabled_at FROM principals WHERE email=? AND kind='human' AND revoked_at IS NULL",
                         (email,)).fetchone()
        raw = auth.issue(tx, row["id"], "reset", timedelta(minutes=RESET_MINUTES)) if row and not row["disabled_at"] and can_mail else None
        tx.audit("password.reset_requested", email, subject=row["id"] if row else None, known=bool(row), sent=bool(raw))
    return raw


def reset_token_for(db: Database, actor: Actor, name: str, ctx: Ctx = NO_CTX) -> str:
    """A reset link an admin makes for someone, to hand over when the server can't send email (or `litledger
    reset-password` in the container)."""
    with _tx(db, ctx) as tx:
        t = _live(tx, name)
        _check(tx, actor, t, "edit")
        if t["kind"] != "human" or t["origin"] == ROOT:
            raise ValueError("Only people with a password can reset it.")
        raw = auth.issue(tx, t["id"], "reset", timedelta(minutes=RESET_MINUTES))
        tx.audit("password.reset_link", t["name"], subject=t["id"])
    return raw


def reset_info(db: Database, raw: str) -> dict | None:
    row = auth.lookup(db, raw, "reset")
    return {"name": row["name"]} if auth.valid(row) else None


class Reset(NamedTuple):
    session: str
    reclaimed: bool  # the email was never confirmed: what was set up before it ended (_reclaim)


def _reclaim(tx, pid: str) -> None:
    """The first proof that someone controls an account's email (an emailed reset link, or one an admin hands over)
    makes them its owner. Anyone can sign up with any address, so whatever was set up before the address was confirmed
    may be a stranger's: linked Google/GitHub accounts, API tokens, terminals and apps (with their OAuth grants), and
    every session and pending link end, in the same transaction as the reset that proves it."""
    stamp = now()
    linked = tx.execute("DELETE FROM identities WHERE principal_id=?", (pid,)).rowcount
    agents = [r["id"] for r in tx.execute("SELECT id FROM principals WHERE owner_id=? AND revoked_at IS NULL", (pid,))]
    for aid in agents:
        tx.execute("UPDATE principals SET revoked_at=?, token_hash=NULL WHERE id=?", (stamp, aid))
        tx.execute("UPDATE credentials SET revoked_at=? WHERE principal_id=? AND revoked_at IS NULL", (stamp, aid))
    ended = tx.execute("UPDATE credentials SET revoked_at=? WHERE principal_id=? AND revoked_at IS NULL", (stamp, pid)).rowcount
    tx.execute("UPDATE principals SET email_verified_at=? WHERE id=?", (stamp, pid))
    tx.audit("account.reclaim", subject=pid, actor_id=pid, identities=linked, agents=len(agents), sessions_ended=ended)


def reset_password(db: Database, raw: str, password: str, ctx: Ctx = NO_CTX) -> Reset:
    """Open a reset link and choose a new password: every session ends, and this browser is signed in. On an account
    whose email was never confirmed, the reset also confirms it and is a change of owner (_reclaim)."""
    hashed = auth.hash_password(auth.password_rules(password))
    with _tx(db, ctx) as tx:
        row = auth.consume(tx, raw, "reset")
        if not row:
            raise LookupError("This link has expired or was already used. Ask for a new one.")
        pid = row["principal_id"]
        person = _by_id(tx, pid)
        _not_root(person, "password")
        reclaimed = bool(person["email"]) and not person["email_verified_at"]
        if reclaimed:
            _reclaim(tx, pid)
        ended = _set_password(tx, pid, hashed)
        tx.audit("password.reset", subject=pid, actor_id=pid, sessions_ended=ended)
        return Reset(_session(tx, pid, "reset", ctx, audit=False), reclaimed)


# ------------------------------------------------------------------------------------------------ Google and GitHub
def person_by_email(db: Database, email: str) -> dict | None:
    with db.read() as conn:
        row = conn.execute("SELECT id, name, disabled_at, email_verified_at FROM principals WHERE email=? AND kind='human' "
                           "AND revoked_at IS NULL", ((email or "").strip().lower(),)).fetchone()
    return {"id": row["id"], "name": row["name"], "disabled": bool(row["disabled_at"]),
            "verified": bool(row["email_verified_at"])} if row else None


def _link(tx, pid: str, provider: str, subject: str, email: str | None, login: str | None) -> None:
    _not_root(_by_id(tx, pid), "Google or GitHub account")
    other = tx.execute("SELECT 1 FROM identities i JOIN principals p ON p.id=i.principal_id WHERE i.provider=? "
                       "AND i.subject=? AND i.principal_id<>? AND p.revoked_at IS NULL", (provider, subject, pid)).fetchone()
    if other:
        raise ValueError(f"That {provider.title()} account belongs to someone else here.")
    tx.execute("DELETE FROM identities WHERE principal_id=? AND provider=?", (pid, provider))  # one per provider
    tx.execute("INSERT INTO identities(provider,subject,principal_id,email,login,created_at,last_login_at) VALUES(?,?,?,?,?,?,?) "
               "ON CONFLICT(provider,subject) DO UPDATE SET principal_id=excluded.principal_id, email=excluded.email, "
               "login=excluded.login, last_login_at=excluded.last_login_at", (provider, subject, pid, email, login, now(), now()))


def link_identity(db: Database, pid: str, provider: str, subject: str, email: str | None, login: str | None,
                  ctx: Ctx = NO_CTX) -> None:
    """Link a Google/GitHub account to a signed-in person (Settings → Profile → Link)."""
    try:
        with _tx(db, ctx) as tx:
            _link(tx, pid, provider, subject, email, login)
            tx.audit("identity.link", provider, subject=pid, login=login or email)
    except ValueError as exc:
        with _tx(db, ctx) as tx:
            tx.audit("identity.link_failed", provider, subject=pid, login=login or email, reason=str(exc))
        raise


def unlink_identity(db: Database, pid: str, provider: str, ctx: Ctx = NO_CTX) -> int:
    with _tx(db, ctx) as tx:
        p = tx.execute("SELECT password_hash FROM principals WHERE id=?", (pid,)).fetchone()
        others = tx.execute("SELECT count(*) FROM identities WHERE principal_id=? AND provider<>?", (pid, provider)).fetchone()[0]
        if not p or (not p["password_hash"] and not others):
            raise ValueError("Set a password first, or you won't be able to sign in.")
        n = tx.execute("DELETE FROM identities WHERE principal_id=? AND provider=?", (pid, provider)).rowcount
        if n:
            tx.audit("identity.unlink", provider, subject=pid)
        return n


def may_join(settings, email: str | None) -> bool:
    """Whether a new account with this email may join (as a member)."""
    policy = (settings.get("SIGNUP") or "anyone").strip().lower()
    if not email or policy == "invited":
        return False
    if policy == "anyone":
        return True
    return email.rsplit("@", 1)[-1].lower() in signup_domains(settings)


def signup_domains(settings) -> list[str]:
    return [d.strip().lower().lstrip("@") for d in (settings.get("SIGNUP_DOMAINS") or "").split(",") if d.strip()]


def sign_in_identity(db: Database, settings, provider: str, subject: str, email: str | None, login: str | None,
                     invite: str | None = None, ctx: Ctx = NO_CTX) -> str:
    """Coming back from Google/GitHub: the person the account is linked to, else the person whose confirmed email it
    is (linking it), else the invite being accepted, else a new member when the sign-up policy accepts the verified
    email. An unconfirmed address never links: anyone could have typed it at sign-up. Raises Denied(code)."""
    how, refused = provider, None
    with _tx(db, ctx) as tx:
        row = tx.execute("SELECT p.id, p.disabled_at FROM identities i JOIN principals p ON p.id=i.principal_id "
                         "WHERE i.provider=? AND i.subject=? AND p.revoked_at IS NULL", (provider, subject)).fetchone()
        pid = row["id"] if row else None
        if row:
            tx.execute("UPDATE identities SET last_login_at=?, email=COALESCE(?, email), login=COALESCE(?, login) "
                       "WHERE provider=? AND subject=?", (now(), email, login, provider, subject))
        by_email = tx.execute("SELECT id, disabled_at, email_verified_at FROM principals WHERE email=? AND kind='human' "
                              "AND revoked_at IS NULL", (email or "",)).fetchone() if not row and email else None
        if not pid and by_email and by_email["email_verified_at"]:
            pid = by_email["id"]
            _link(tx, pid, provider, subject, email, login)
            tx.audit("identity.link", provider, subject=pid, actor_id=pid, login=login or email, automatic=True)
        elif not pid and invite:
            inv = auth.find(tx, invite, "invite")
            if not auth.valid(inv) or not auth.revoke_credential(tx, invite):
                refused = ("invite", "invite")
            else:
                m = inv["meta"]
                pid = _new_person(tx, login or m["email"].split("@")[0], m["email"], m["role"], m.get("projects"), verified=True)
                _link(tx, pid, provider, subject, email, login)
                tx.audit("invite.accept", m["email"], subject=pid, actor_id=pid, method=provider, role=m["role"])
        elif not pid and by_email:
            refused = ("email_taken", "an unconfirmed account has this email")
        elif not pid and email and may_join(settings, email):
            pid = _new_person(tx, login or email.split("@")[0], email, verified=True)
            _link(tx, pid, provider, subject, email, login)
            tx.audit("account.join", email, subject=pid, actor_id=pid, method=provider)
        elif not pid:
            refused = ("no_account", "no account")
        if pid and not refused:
            disabled = tx.execute("SELECT disabled_at FROM principals WHERE id=?", (pid,)).fetchone()["disabled_at"]
            if not disabled:
                return _session(tx, pid, how, ctx)
            tx.audit("signin.refused", email or login, subject=pid, method=how, reason="disabled")
            refused = ("disabled", None)
        else:
            tx.audit("signin.failed", email or login, fold=FOLD, method=how, reason=refused[1])
    raise Denied(refused[0], 403, refused[0])


def flow_mismatch(db: Database, provider: str, ctx: Ctx = NO_CTX) -> None:
    """A Google/GitHub callback that came back late, or to a browser that didn't start it (refused)."""
    with _tx(db, ctx) as tx:
        tx.audit("signin.failed", None, fold=FOLD, method=provider, reason="state mismatch")


# ------------------------------------------------------------------------------------------------ invitations
def invite(db: Database, inviter: Actor, email: str, role: str = "member", projects=None, ctx: Ctx = NO_CTX,
           limit: Limiter | None = None) -> str:
    """A single-use link for this email, valid INVITE_DAYS; a new invite for the same email replaces the old one."""
    email = clean_email(email)
    _limit(db, limit, [inviter.principal_id or inviter.name], "invite", email, ctx)
    if not email:
        raise ValueError("Enter an email.")
    if role not in ROLES:
        raise ValueError("role must be member or admin")
    with _tx(db, ctx) as tx:
        if _email_taken(tx, email):
            raise ValueError(f"{email} already has an account.")
        replaced = tx.execute("UPDATE credentials SET revoked_at=? WHERE kind='invite' AND revoked_at IS NULL "
                              "AND json_extract(meta, '$.email')=?", (now(), email)).rowcount
        raw = auth.issue(tx, inviter.principal_id, "invite", timedelta(days=INVITE_DAYS),
                         meta={"email": email, "role": role, "projects": sorted(set(projects)) if projects else None})
        tx.audit("invite.create", email, role=role, projects=projects or None, replaced=replaced or None)
    return raw


def invite_info(db: Database, raw: str, ctx: Ctx = NO_CTX) -> dict | None:
    """What an invite link is for; an expired or used one is recorded once (per window) when someone opens it."""
    row = auth.lookup(db, raw, "invite")
    if auth.valid(row):
        return {"email": row["meta"]["email"], "role": row["meta"]["role"], "invited_by": row["name"], "expires_at": row["expires_at"]}
    if row:
        with _tx(db, ctx) as tx:
            tx.audit("invite.expired", row["meta"]["email"], fold=FOLD, used=bool(row["revoked_at"]))
    return None


def accept_invite(db: Database, raw: str, name: str | None, password: str, ctx: Ctx = NO_CTX) -> str:
    """Create the invited person with a password and sign them in; the link is burnt. (With Google/GitHub:
    sign_in_identity.)"""
    hashed = auth.hash_password(auth.password_rules(password or ""))
    with _tx(db, ctx) as tx:
        row = auth.find(tx, raw, "invite")
        if not auth.valid(row) or not auth.revoke_credential(tx, raw):
            raise LookupError("This invite has expired or was already used.")
        m = row["meta"]
        # an emailed invite proves the address; a copied one is the admin vouching
        pid = _new_person(tx, name or m["email"].split("@")[0], m["email"], m["role"], m.get("projects"), hashed, verified=True)
        tx.audit("invite.accept", m["email"], subject=pid, actor_id=pid, method="password", role=m["role"])
        return _session(tx, pid, "invite", ctx, audit=False)


def invites(db: Database) -> list[dict]:
    with db.read() as conn:
        rows = conn.execute("SELECT c.hash, c.meta, c.created_at, c.expires_at, p.name AS invited_by FROM credentials c "
                            "JOIN principals p ON p.id=c.principal_id WHERE c.kind='invite' AND c.revoked_at IS NULL "
                            "AND c.expires_at>? ORDER BY c.created_at DESC", (now(),)).fetchall()
    return [{"id": r["hash"][:16], **{k: json.loads(r["meta"]).get(k) for k in ("email", "role", "projects")},
             "invited_by": r["invited_by"], "created_at": r["created_at"], "expires_at": r["expires_at"]} for r in rows]


def revoke_invite(db: Database, invite_id: str, ctx: Ctx = NO_CTX) -> str:
    with _tx(db, ctx) as tx:
        row = tx.execute("SELECT hash, meta FROM credentials WHERE kind='invite' AND revoked_at IS NULL AND substr(hash, 1, ?)=?",
                         (len(invite_id), invite_id)).fetchone() if len(invite_id) >= 8 else None
        if not row:
            raise LookupError("No such invite.")
        auth.revoke_credential(tx, hash_=row["hash"])
        email = json.loads(row["meta"])["email"]
        tx.audit("invite.revoke", email)
    return email


# ------------------------------------------------------------------------------------------------ what admins do to others
def _check(conn, actor: Actor, t, action: str) -> None:
    """Raise unless `actor` may do `action` (role, disable, remove, edit) to principal row `t`."""
    if t["kind"] != "human":  # agents: their owner or an admin
        if actor.admin or (t["owner_id"] and t["owner_id"] == actor.principal_id):
            return
        raise PermissionError("You can only change your own tokens and apps.")
    if not actor.admin:
        raise PermissionError("Only admins can do this.")
    if t["origin"] == ROOT and action in ("role", "disable", "remove"):
        raise ValueError("The admin account can't be demoted, disabled or removed.")
    if action in ("disable", "remove") and t["id"] == actor.principal_id:
        raise ValueError("You can't disable or remove yourself.")
    if action in ("role", "disable", "remove") and t["role"] == "admin" and not t["disabled_at"]:
        # Never true while the root admin exists (it can't be changed); kept for databases without one.
        left = conn.execute("SELECT count(*) FROM principals WHERE kind='human' AND role='admin' "
                            "AND revoked_at IS NULL AND disabled_at IS NULL AND id<>?", (t["id"],)).fetchone()[0]
        if not left:
            raise ValueError(f"{t['name']} is the last admin. Make someone else an admin first.")


def set_role(db: Database, actor: Actor, name: str, role: str, ctx: Ctx = NO_CTX) -> None:
    if role not in ROLES:
        raise ValueError("role must be member or admin")
    with _tx(db, ctx) as tx:
        t = _live(tx, name)
        if t["kind"] != "human":
            raise ValueError("Tokens and apps keep the access they were made with.")
        _check(tx, actor, t, "role")
        if role != t["role"]:
            tx.execute("UPDATE principals SET role=? WHERE id=?", (role, t["id"]))
            tx.audit("role.change", t["name"], subject=t["id"], old=t["role"], new=role)


def set_disabled(db: Database, actor: Actor, name: str, disabled: bool, ctx: Ctx = NO_CTX) -> None:
    """Disabling signs a person out everywhere and pauses their tokens and apps; enabling lets them sign in again."""
    with _tx(db, ctx) as tx:
        t = _live(tx, name)
        if t["kind"] != "human":
            raise ValueError("Remove a token instead.")
        _check(tx, actor, t, "disable" if disabled else "edit")
        if disabled and not t["disabled_at"]:
            tx.execute("UPDATE principals SET disabled_at=? WHERE id=?", (now(), t["id"]))
            ended = auth.revoke_kinds(tx, t["id"], ("session", "reset", "email"))
            tx.audit("person.disable", t["name"], subject=t["id"], sessions_ended=ended)
        elif not disabled and t["disabled_at"]:
            tx.execute("UPDATE principals SET disabled_at=NULL WHERE id=?", (t["id"],))
            tx.audit("person.enable", t["name"], subject=t["id"])


def end_sessions_of(db: Database, actor: Actor, name: str, ctx: Ctx = NO_CTX) -> int:
    """An admin signs someone out everywhere."""
    with _tx(db, ctx) as tx:
        t = _live(tx, name)
        _check(tx, actor, t, "edit")
        n = auth.revoke_kinds(tx, t["id"], ("session",))
        tx.audit("sessions.end", t["name"], subject=t["id"], ended=n)
        return n


def update(db: Database, actor: Actor, name: str, email: str | None = None, projects="keep", ctx: Ctx = NO_CTX) -> None:
    with _tx(db, ctx) as tx:
        t = _live(tx, name)
        _check(tx, actor, t, "edit")
        if t["origin"] == ROOT:
            raise ValueError("The admin account can't be changed.")
        changes = {}
        if email is not None and t["kind"] == "human":
            email = clean_email(email)
            if email and _email_taken(tx, email, t["id"]):
                raise ValueError(f"{email} already has an account.")
            if not email and t["email"]:  # a password signs in with the email; without either they'd be locked out
                linked = tx.execute("SELECT 1 FROM identities WHERE principal_id=?", (t["id"],)).fetchone()
                if t["password_hash"] or not linked:
                    raise ValueError(f"{t['name']} signs in with this email, so it can't be empty: they would have no way to sign in.")
            if email != t["email"]:  # an admin vouches for the address they set
                tx.execute("UPDATE principals SET email=?, email_verified_at=? WHERE id=?", (email, now() if email else None, t["id"]))
                changes["email"] = email
        if projects != "keep":
            if actor.projects is not None:  # nobody hands out more than they have
                projects = [p for p in (projects or actor.projects) if p in actor.projects] or list(actor.projects)
            tx.execute("UPDATE principals SET projects=? WHERE id=?", (_projects(projects), t["id"]))
            changes["projects"] = sorted(set(projects)) if projects else "all"
        if changes:
            tx.audit("principal.update", t["name"], subject=t["id"], **changes)


def remove(db: Database, actor: Actor, name: str, ctx: Ctx = NO_CTX) -> dict:
    """Remove a person or agent for good: their sign-ins, tokens and the agents a person owns stop working. The row
    stays, so history still says who did what."""
    with _tx(db, ctx) as tx:
        t = _live(tx, name)
        _check(tx, actor, t, "remove")
        ids = [t["id"]] + [r["id"] for r in tx.execute("SELECT id FROM principals WHERE owner_id=? AND revoked_at IS NULL", (t["id"],))]
        for pid in ids:
            tx.execute("UPDATE principals SET revoked_at=?, token_hash=NULL WHERE id=?", (now(), pid))
            tx.execute("UPDATE credentials SET revoked_at=? WHERE principal_id=? AND revoked_at IS NULL", (now(), pid))
        tx.execute("DELETE FROM identities WHERE principal_id=?", (t["id"],))
        op = "person.remove" if t["kind"] == "human" else "app.disconnect" if t["origin"] == "oauth" else "token.revoke"
        tx.audit(op, t["name"], subject=t["id"], with_agents=len(ids) - 1 or None)
    return {"kind": t["kind"], "with": len(ids) - 1}


def promote(db: Database, email: str, ctx: Ctx = SHELL) -> str:
    """Recovery from the server's shell: make this person an admin and enable them again."""
    with _tx(db, ctx) as tx:
        t = tx.execute("SELECT * FROM principals WHERE email=? AND kind='human' AND revoked_at IS NULL",
                       ((email or "").strip().lower(),)).fetchone()
        if not t:
            raise LookupError(f"No one has the email {email}.")
        tx.execute("UPDATE principals SET disabled_at=NULL, role='admin' WHERE id=?", (t["id"],))
        tx.audit("role.change", t["name"], subject=t["id"], old=t["role"], new="admin", enabled=bool(t["disabled_at"]) or None)
    return t["name"]


# ------------------------------------------------------------------------------------------------ agents
class Made(NamedTuple):
    name: str
    token: str


def create_token(db: Database, label: str, projects=None, *, owner: Actor | None = None, admin: bool = False,
                 days: int | None = None, origin: str = "token", rotate: bool = False, ctx: Ctx | None = None) -> Made:
    """A new agent with an API token: a person's own (owner given; named "<label>-<owner>") or a shared one.
    admin=True gives it full access: admin rights while its owner is an admin. rotate=True re-issues the token of the
    live shared agent (or the root admin) with this name instead (`litledger token create`). origin="login": a
    terminal a person approved."""
    ctx = ctx or Ctx(owner.principal_id if owner else None)
    label = clean_name(label)
    raw = "ll_" + secrets.token_urlsafe(32)
    with _tx(db, ctx) as tx:
        row = tx.execute("SELECT id, name FROM principals WHERE name=? AND (kind='agent' OR origin=?) AND owner_id IS NULL "
                         "AND revoked_at IS NULL", (label, ROOT)).fetchone() if rotate and not owner else None
        if row:
            pid, name = row["id"], row["name"]
            op = "token.rotate"
        else:
            if owner and owner.projects is not None:
                projects = [p for p in (projects or owner.projects) if p in owner.projects] or list(owner.projects)
            pid, name = _insert(tx, f"{label}-{owner.name}" if owner else label, "agent", role="admin" if admin else "member",
                                owner_id=owner.principal_id if owner else None, label=label if owner else None,
                                origin=origin, projects=_projects(projects),
                                expires_at=at(timedelta(days=days)) if days else None)
            op = "terminal.approve" if origin == "login" else "token.create"
        tx.execute("UPDATE principals SET token_hash=? WHERE id=?", (digest(raw), pid))
        tx.audit(op, name, subject=pid, access="full" if admin else "library", projects=projects or None,
                 expires=f"{days} days" if days else None, shared=None if owner else True)
    return Made(name, raw)


def app_principal(db: Database, owner: Actor, client_id: str, client_name: str, projects, ctx: Ctx | None = None) -> str:
    """The agent for one (person, OAuth app) pair, e.g. "claude-code-ada". Connecting the app again reuses it."""
    with _tx(db, ctx or Ctx(owner.principal_id)) as tx:
        row = tx.execute("SELECT id, name FROM principals WHERE owner_id=? AND client_id=?", (owner.principal_id, client_id)).fetchone()
        if row:
            tx.execute("UPDATE principals SET revoked_at=NULL, projects=? WHERE id=?", (_projects(projects), row["id"]))
            pid, name = row["id"], row["name"]
        else:
            label = slug(client_name, limit=30, default="app")
            pid, name = _insert(tx, f"{label}-{owner.name}", "agent", owner_id=owner.principal_id, client_id=client_id,
                                label=label, origin="oauth", projects=_projects(projects))
        tx.audit("app.connect", client_name, subject=pid, agent=name, projects=projects or None, again=bool(row) or None)
        return pid


def _agent_view(r) -> dict:
    return {"name": r["name"], "by": r["by"], "label": r["label"] or r["name"], "owner": r["owner"], "origin": r["origin"],
            "client_name": r["client_name"], "projects": list(auth.projects_of(r["projects"]) or []) or None,
            "access": "full" if r["role"] == "admin" else "library", "created_at": r["created_at"],
            "last_seen_at": r["last_seen_at"], "expires_at": r["expires_at"],
            "expired": bool(r["expires_at"] and r["expires_at"] < now())}


def agents(db: Database, owner_id: str | None = None) -> list[dict]:
    """Live agents: one person's, or (owner_id None) everyone's and the shared ones."""
    sql = (f"SELECT p.*, o.name AS owner, c.name AS client_name, {who_sql()} AS by FROM principals p "
           "LEFT JOIN principals o ON o.id=p.owner_id LEFT JOIN oauth_clients c ON c.client_id=p.client_id "
           "WHERE p.kind='agent' AND p.revoked_at IS NULL" + (" AND p.owner_id=?" if owner_id else "") + " ORDER BY p.created_at DESC")
    with db.read() as conn:
        return [_agent_view(r) for r in conn.execute(sql, (owner_id,) if owner_id else ())]


def people(db: Database) -> list[dict]:
    with db.read() as conn:
        rows = conn.execute("SELECT p.*, (SELECT count(*) FROM principals a WHERE a.owner_id=p.id AND a.revoked_at IS NULL) AS agents "
                            "FROM principals p WHERE p.kind='human' AND p.revoked_at IS NULL ORDER BY p.created_at").fetchall()
        idents: dict[str, list[dict]] = {}
        for r in conn.execute("SELECT principal_id, provider, email, login FROM identities"):
            idents.setdefault(r["principal_id"], []).append({"provider": r["provider"], "email": r["email"], "login": r["login"]})
    return [{"name": r["name"], "email": r["email"], "role": r["role"], "disabled": bool(r["disabled_at"]), "root": r["origin"] == ROOT,
             "email_verified": bool(r["email_verified_at"]),
             "has_password": bool(r["password_hash"]), "identities": idents.get(r["id"], []), "agents": r["agents"],
             "projects": list(auth.projects_of(r["projects"]) or []) or None, "created_at": r["created_at"],
             "last_seen_at": r["last_seen_at"]} for r in rows]


# ------------------------------------------------------------------------------------------------ first start
def bootstrap(db: Database, token_dir: Path, ctx: Ctx = Ctx(via="startup")) -> str | None:
    """Make sure the root admin exists. When it is made (the very first start), its token goes to token_dir/admin:
    the admin signs in with it in the browser, and scripts and `litledger approve` use it in the container."""
    if root_id(db):
        return None
    raw = "ll_" + secrets.token_urlsafe(32)
    with _tx(db, ctx) as tx:
        pid, _ = _insert(tx, "admin", "human", role="admin", origin=ROOT, token_hash=digest(raw))
        tx.audit("account.create", "admin", subject=pid, role="admin", root=True)
    write_root_token(token_dir, raw)
    return raw


def write_root_token(token_dir: Path, raw: str) -> None:
    token_dir.mkdir(parents=True, exist_ok=True)
    path = token_dir / "admin"
    path.write_text(raw + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
