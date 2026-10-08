"""Turning a secret into an Actor, and the secrets themselves.

A request authenticates with one of:
- an API token (`ll_…`): an agent's, i.e. a person's token, a terminal signed in with `litledger login`, or a shared
  agent such as the first-start admin token
- a browser session cookie: a person who signed in
- an OAuth access token: an app a person connected over MCP (valid on /mcp only)

Tokens, session cookies and one-time links are stored as SHA-256 hashes, passwords as scrypt hashes. Who exists and
what each may do lives in accounts.py; docs/ACCESS.md describes the whole model."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import threading
import time
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from .db import Actor, Database, now

SESSION_DAYS = 30
ACCESS_SECONDS = 3600
REFRESH_DAYS = 90
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def at(delta: timedelta) -> str:
    return (datetime.now(timezone.utc) + delta).isoformat(timespec="seconds")


def clean_email(email: str | None) -> str | None:
    email = (email or "").strip().lower()
    if not email:
        return None
    if not _EMAIL.match(email):
        raise ValueError(f"not an email address: {email}")
    return email


def projects_of(raw: str | None) -> tuple[str, ...] | None:
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except ValueError:
        return None
    return tuple(value) if isinstance(value, list) else None


# ------------------------------------------------------------------------------------------------ passwords
MIN_PASSWORD, MAX_PASSWORD = 8, 512
_SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1, "dklen": 32}
_NO_HASH = "scrypt$AAAAAAAAAAAAAAAAAAAAAA==$"


def password_rules(password: str) -> str:
    if len(password) < MIN_PASSWORD:
        raise ValueError(f"Use at least {MIN_PASSWORD} characters.")
    if len(password) > MAX_PASSWORD:
        raise ValueError("That password is too long.")
    return password


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    got = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(got).decode()


def password_ok(stored: str | None, password: str) -> bool:
    """Runs the hash even without a stored one, so timing doesn't reveal which accounts have passwords."""
    try:
        _, salt, want = (stored or _NO_HASH).split("$")
        got = hashlib.scrypt((password or "")[:MAX_PASSWORD].encode(), salt=base64.b64decode(salt), **_SCRYPT)
        return bool(stored) and hmac.compare_digest(got, base64.b64decode(want))
    except ValueError:
        return False


# ------------------------------------------------------------------------------------------------ verification
def effective_role(kind: str, role: str | None, owner_role: str | None, owned: bool) -> str:
    """People have their role. An agent owned by a person is a member, or an admin when it was made with full access
    and its owner is still an admin. A shared agent has its own role."""
    if kind == "human" or not owned:
        return "admin" if role == "admin" else "member"
    return "admin" if role == "admin" and owner_role == "admin" else "member"


# A principal acts only while it, and the person who owns it, are neither removed nor disabled, and it hasn't expired.
_LIVE = ("p.revoked_at IS NULL AND p.disabled_at IS NULL AND (p.expires_at IS NULL OR p.expires_at>?) "
         "AND (p.owner_id IS NULL OR (o.revoked_at IS NULL AND o.disabled_at IS NULL))")
_WHO = ("p.id, p.name, p.kind, p.role, p.projects, p.owner_id, o.role AS owner_role FROM {src} "
        "LEFT JOIN principals o ON o.id=p.owner_id")


def verify(db: Database, token: str | None, resource: str | tuple[str, ...] | None = None) -> Actor | None:
    """The principal behind an API token, session cookie or OAuth access token (None if invalid, expired or blocked).
    OAuth tokens are bound to the resource they were issued for: pass the MCP URL (or the URLs this server answers
    as, e.g. its configured address and the request's own origin) to enforce it."""
    resources = (resource,) if isinstance(resource, str) else tuple(resource or ())
    if not token:
        return None
    h, t = digest(token.strip()), now()
    with db.read() as conn:
        row = conn.execute(f"SELECT p.token_hash, {_WHO.format(src='principals p')} WHERE p.token_hash=? AND {_LIVE}",
                           (h, t)).fetchone()
        cred = None
        if row and not hmac.compare_digest(row["token_hash"], h):
            return None
        if not row:
            cred = conn.execute(
                f"SELECT c.kind AS ckind, c.expires_at AS cexp, c.resource, c.used_at, "
                f"{_WHO.format(src='credentials c JOIN principals p ON p.id=c.principal_id')} "
                f"WHERE c.hash=? AND c.revoked_at IS NULL AND c.kind IN ('session','access') AND {_LIVE}", (h, t)).fetchone()
            if not cred or (cred["cexp"] and cred["cexp"] < t):
                return None
            if cred["ckind"] == "access" and resources and cred["resource"] and not any(
                    same_resource(cred["resource"], r) for r in resources):
                return None
            row = cred
    _touch(db, row["id"], h if cred is not None and cred["ckind"] == "session" else None)
    return Actor(principal_id=row["id"], name=row["name"], kind=row["kind"], projects=projects_of(row["projects"]),
                 session="browser" if cred is not None and cred["ckind"] == "session" else None,
                 role=effective_role(row["kind"], row["role"], row["owner_role"], bool(row["owner_id"])))


def same_resource(a: str, b: str) -> bool:
    """Resource URLs match; loopback host names are interchangeable (localhost / 127.0.0.1 / [::1])."""
    loop = {"localhost", "127.0.0.1", "::1", "[::1]"}
    x, y = urlsplit(a.rstrip("/")), urlsplit(b.rstrip("/"))
    hx, hy = (x.hostname or ""), (y.hostname or "")
    same_host = hx == hy or (hx in loop and hy in loop)
    return same_host and x.port == y.port and x.path == y.path and x.scheme == y.scheme


_seen: OrderedDict[str, float] = OrderedDict()  # least recently written first
SEEN_MAX = 10_000
_lock = threading.Lock()


def _touch(db: Database, pid: str, session_hash: str | None) -> None:
    """Last seen (a principal) and last active (a session), at most every five minutes each. The memory of recent
    writes is an LRU of SEEN_MAX keys (forgetting one only means one extra write)."""
    t = time.time()
    keys = [k for k in (pid, session_hash) if k]
    with _lock:
        keys = [k for k in keys if t - _seen.get(k, 0) >= 300]
        for k in keys:
            _seen[k] = t
            _seen.move_to_end(k)
        while len(_seen) > SEEN_MAX:
            _seen.popitem(last=False)
    if not keys:
        return
    try:
        with db.write("last seen") as conn:
            if pid in keys:
                conn.execute("UPDATE principals SET last_seen_at=? WHERE id=?", (now(), pid))
            if session_hash in keys:
                conn.execute("UPDATE credentials SET used_at=? WHERE hash=?", (now(), session_hash))
    except Exception:
        pass


# ------------------------------------------------------------------------------------------------ expiring credentials
# Kinds: session (browser), access/refresh (OAuth), invite, reset (password), email (confirm an address), signup (a
# domain sign-up waiting for its link). These helpers take a connection or a Tx, so a credential changes in the same
# transaction as the audit row that records it (accounts.py, oauth.py).
class Denied(Exception):
    """A refused sign-in or change, already recorded in the audit log. The message is safe to show; `code` names it
    for redirects and `status` is the HTTP status."""

    def __init__(self, message: str, status: int = 401, code: str = ""):
        super().__init__(message)
        self.status, self.code = status, code


def issue(conn, principal_id: str, kind: str, ttl: timedelta, client_id: str | None = None,
          family: str | None = None, resource: str | None = None, meta: dict | None = None, prefix: str = "ll") -> str:
    raw = f"{prefix}_{kind[0]}_" + secrets.token_urlsafe(32)
    conn.execute("INSERT INTO credentials(hash,principal_id,kind,client_id,family,resource,created_at,expires_at,meta) "
                 "VALUES(?,?,?,?,?,?,?,?,?)", (digest(raw), principal_id, kind, client_id, family, resource, now(),
                                               at(ttl), json.dumps(meta or {})))
    return raw


def find(conn, raw: str, kind: str) -> dict | None:
    """A credential with its principal; `blocked` when the principal (or its owner) was removed or disabled."""
    row = conn.execute(
        "SELECT c.*, p.name, p.kind AS pkind, p.email AS pemail, (p.revoked_at IS NOT NULL OR p.disabled_at IS NOT NULL "
        "OR o.revoked_at IS NOT NULL OR o.disabled_at IS NOT NULL) AS blocked FROM credentials c "
        "JOIN principals p ON p.id=c.principal_id LEFT JOIN principals o ON o.id=p.owner_id WHERE c.hash=? AND c.kind=?",
        (digest(raw or ""), kind)).fetchone()
    return dict(row) | {"meta": json.loads(row["meta"] or "{}"), "blocked": bool(row["blocked"])} if row else None


def lookup(db: Database, raw: str, kind: str) -> dict | None:
    with db.read() as conn:
        return find(conn, raw, kind)


def valid(row: dict | None) -> bool:
    return bool(row) and not row["revoked_at"] and not row["blocked"] and not (row["expires_at"] and row["expires_at"] < now())


def consume(conn, raw: str, kind: str) -> dict | None:
    """A one-time link: returns it (with its meta) and burns it, or None if it is unknown, used or expired."""
    row = find(conn, raw, kind)
    if not valid(row) or not revoke_credential(conn, raw):  # revoke fails when another request used it a moment ago
        return None
    return row


def revoke_credential(conn, raw: str | None = None, *, hash_: str | None = None, family: str | None = None) -> int:
    if family:
        return conn.execute("UPDATE credentials SET revoked_at=? WHERE family=? AND revoked_at IS NULL", (now(), family)).rowcount
    return conn.execute("UPDATE credentials SET revoked_at=? WHERE hash=? AND revoked_at IS NULL",
                        (now(), hash_ or digest(raw or ""))).rowcount


def revoke_kinds(conn, principal_id: str, kinds: tuple[str, ...], keep: str | None = None) -> int:
    """Revoke a principal's credentials of these kinds (all sessions, say), except the one with hash `keep`."""
    marks = ",".join("?" * len(kinds))
    return conn.execute(f"UPDATE credentials SET revoked_at=? WHERE principal_id=? AND kind IN ({marks}) AND revoked_at IS NULL "
                        "AND hash<>?", (now(), principal_id, *kinds, keep or "")).rowcount


def new_session(conn, principal_id: str, meta: dict | None = None) -> str:
    return issue(conn, principal_id, "session", timedelta(days=SESSION_DAYS), meta=meta)


def sessions(db: Database, principal_id: str) -> list[dict]:
    with db.read() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT hash, created_at, expires_at, used_at, meta FROM credentials WHERE principal_id=? AND kind='session' "
            "AND revoked_at IS NULL AND expires_at>? ORDER BY COALESCE(used_at, created_at) DESC", (principal_id, now()))]


# ------------------------------------------------------------------------------------------------ short-lived state
class TTLStore:
    """In-memory items under random keys that expire after `ttl` seconds: sign-in state, OAuth requests and codes,
    terminal sign-in requests. Nothing here survives a restart, which is fine for anything that lives minutes.

    Items sit in insertion order, which is also expiry order (one TTL per store), so pruning pops expired items off the
    front: O(1) amortised per call. At most `cap` items are kept; a new one past the cap evicts the oldest, so a flood
    of requests costs bounded memory (the routes that fill a store are rate-limited per address as well)."""

    def __init__(self, ttl: float, key_bytes: int = 24, cap: int = 1000):
        self.ttl, self.key_bytes, self.cap = ttl, key_bytes, cap
        self.lock = threading.RLock()  # also held by callers that read-modify-write an item
        self._items: OrderedDict[str, tuple[float, object]] = OrderedDict()

    def _prune(self) -> None:
        cutoff = time.monotonic() - self.ttl
        while self._items:
            key, (t, _) = next(iter(self._items.items()))
            if t >= cutoff:
                return
            del self._items[key]

    def __len__(self) -> int:
        with self.lock:
            self._prune()
            return len(self._items)

    def put(self, value) -> str:
        with self.lock:
            self._prune()
            while len(self._items) >= self.cap:
                self._items.popitem(last=False)
            key = secrets.token_urlsafe(self.key_bytes)
            self._items[key] = (time.monotonic(), value)
            return key

    def get(self, key: str):
        with self.lock:
            self._prune()
            item = self._items.get(key)
            return item[1] if item else None

    def pop(self, key: str):
        with self.lock:
            self._prune()
            item = self._items.pop(key, None)
            return item[1] if item else None

    def items(self) -> list[tuple[str, object]]:
        with self.lock:
            self._prune()
            return [(k, v) for k, (_, v) in self._items.items()]


class Limiter:
    """At most n hits per key in a sliding window (sign-in attempts per email and per address, and so on)."""

    def __init__(self, n: int, seconds: int):
        self.n, self.seconds = n, seconds
        self._lock = threading.Lock()
        self.hits: dict[str, list[float]] = {}

    def ok(self, *keys: str) -> bool:
        with self._lock:
            t = time.time()
            recent = {k: [h for h in self.hits.get(k, []) if h > t - self.seconds] for k in keys}
            if any(len(v) >= self.n for v in recent.values()):
                self.hits.update(recent)
                return False
            for k, v in recent.items():
                self.hits[k] = v + [t]
            if len(self.hits) > 10_000:  # forget idle keys
                self.hits = {k: v for k, v in self.hits.items() if v and v[-1] > t - self.seconds}
            return True
