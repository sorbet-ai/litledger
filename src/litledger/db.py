"""SQLite storage: schema, connections, and journaled write transactions."""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import sqlite3
import threading
import time
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

SCHEMA_VERSION = 6

# How the schema changed after v1, applied in order by Database._migrate (in one transaction, after a backup):
# columns added (fresh databases get them from this list too, so SCHEMA only holds a table's original columns),
# columns dropped (only where they still exist), then statements run once when a database reaches a version.
ADDED_COLUMNS = [
    (2, "map_nodes", "seq", "REAL"),
    (2, "map_edges", "style", "TEXT NOT NULL DEFAULT '{}'"),
    (2, "maps", "meta", "TEXT NOT NULL DEFAULT '{}'"),
    (2, "entities", "data", "TEXT NOT NULL DEFAULT '{}'"),
    (2, "entities", "aliases", "TEXT NOT NULL DEFAULT '[]'"),
    (3, "maps", "version", "INTEGER NOT NULL DEFAULT 0"),  # bumped by every map edit; editors detect remote changes
    (3, "jobs", "attempts", "INTEGER NOT NULL DEFAULT 0"),
    (3, "jobs", "lease_until", "TEXT"),
    (3, "journal", "subject", "TEXT"),  # the work an entry is about, for per-work history without scanning payloads
    (3, "principals", "email", "TEXT"),  # people: the address magic links and Google/GitHub sign-ins match
    (3, "principals", "owner_id", "TEXT"),  # agents connected by a person through OAuth
    (3, "principals", "client_id", "TEXT"),  # ... and the OAuth client they connected
    (3, "principals", "projects", "TEXT"),  # JSON list of projects this principal may use; NULL = all
    (3, "principals", "last_seen_at", "TEXT"),
    (5, "principals", "password_hash", "TEXT"),  # people who sign in with email + password (scrypt)
    (5, "principals", "role", "TEXT NOT NULL DEFAULT 'member'"),  # admin | member (docs/ACCESS.md)
    (5, "principals", "disabled_at", "TEXT"),  # a disabled person can't sign in, and their agents stop working
    (5, "principals", "expires_at", "TEXT"),  # API tokens may expire
    (5, "principals", "label", "TEXT"),  # an owned agent's short name: "claude-code" in "claude-code via ada"
    (5, "principals", "origin", "TEXT"),  # how an agent came to be: token | login | oauth | bootstrap
    (5, "credentials", "used_at", "TEXT"),  # sessions: last active
    (5, "principals", "email_verified_at", "TEXT"),  # only a verified email links a Google/GitHub account
    (6, "audit", "subject_id", "TEXT"),  # the principal an event is about (the actor is actor_id)
    (6, "audit", "ua", "TEXT"),  # the browser or client (user agent)
    (6, "audit", "count", "INTEGER NOT NULL DEFAULT 1"),  # repeated failures fold into one row ...
    (6, "audit", "last_at", "TEXT"),  # ... with the time of the latest
]
# Never written (v4): sizes in map_layout, per-project settings, a document's version label, a credential's last use,
# a note's character offsets (quote anchors are a passage, not offsets).
DROPPED_COLUMNS = [
    (4, "map_layout", "w"),
    (4, "map_layout", "h"),
    (4, "projects", "settings"),
    (4, "documents", "version_label"),
    (4, "credentials", "last_used_at"),
    (4, "notes", "char_start"),
    (4, "notes", "char_end"),
]
MIGRATION_SQL = {
    3: [
        "CREATE INDEX IF NOT EXISTS journal_subject ON journal(subject, seq)",
        "UPDATE journal SET subject=COALESCE(json_extract(payload,'$.work'), json_extract(payload,'$.survivor'), "
        "json_extract(payload,'$.seed')) WHERE subject IS NULL",
    ],
    4: [
        "DROP TABLE IF EXISTS http_cache",  # provider responses live in cache.sqlite3
        "DROP TABLE IF EXISTS blobs",  # never read: originals are files under blobs/, named by their SHA-256
    ],
    5: [  # Roles. Written to run again on a database a pre-release v5 build already changed (see _migrate).
        # Every person used to be an administrator, and stays one (only when nobody has a role yet).
        "UPDATE principals SET role='admin' WHERE kind='human' AND NOT EXISTS "
        "(SELECT 1 FROM principals WHERE role IN ('owner','admin'))",
        "UPDATE principals SET role='admin' WHERE role='owner'",  # a pre-release had an owner role
        # The first-start admin token's principal is the root admin: a person-level admin who signs in with the token.
        "UPDATE principals SET kind='human', role='admin', origin='bootstrap' WHERE id=(SELECT id FROM principals "
        "WHERE revoked_at IS NULL AND owner_id IS NULL AND email IS NULL AND password_hash IS NULL AND token_hash IS NOT NULL "
        "AND (origin='bootstrap' OR name='admin') ORDER BY origin='bootstrap' DESC, created_at LIMIT 1)",
        # Other "people" with only an API token can't sign in from a browser: shared agents that keep admin rights.
        "UPDATE principals SET kind='agent', role='admin', origin='token' WHERE kind='human' AND email IS NULL "
        "AND password_hash IS NULL AND COALESCE(origin, '')<>'bootstrap' AND id NOT IN (SELECT principal_id FROM identities)",
        "UPDATE principals SET origin='oauth', label=(SELECT lower(replace(c.name, ' ', '-')) FROM oauth_clients c "
        "WHERE c.client_id=principals.client_id) WHERE client_id IS NOT NULL",
        "UPDATE principals SET origin='token' WHERE kind='agent' AND origin IS NULL",
        # People so far were added by an admin or came through Google/GitHub: their emails count as verified.
        "UPDATE principals SET email_verified_at=created_at WHERE kind='human' AND email IS NOT NULL AND email_verified_at IS NULL",
    ],
    6: [
        "CREATE INDEX IF NOT EXISTS audit_actor ON audit(actor_id)",
        "CREATE INDEX IF NOT EXISTS audit_subject ON audit(subject_id)",
    ],
}

# Payload keys that name the work a journal entry is about.
SUBJECT_KEYS = ("work", "survivor", "seed")

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ulid() -> str:
    """Time-sortable 26-char ID (ULID layout)."""
    value = (int(time.time() * 1000) << 80) | int.from_bytes(os.urandom(10), "big")
    out = []
    for _ in range(26):
        out.append(_CROCKFORD[value & 31])
        value >>= 5
    return "".join(reversed(out))


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def hash_id(prefix: str, parts: list) -> str:
    """A content-derived id ('l:…', 'n:…'): the same parts always give the same id, so writes are idempotent."""
    return prefix + hashlib.sha256(dumps(parts).encode()).hexdigest()[:24]


def free_id(conn: sqlite3.Connection, table: str, base: str) -> str:
    """`base`, or `base-2`, `base-3`, … whichever is not yet an id in `table`."""
    candidate, n = base, 1
    while conn.execute(f"SELECT 1 FROM {table} WHERE id=?", (candidate,)).fetchone():
        n += 1
        candidate = f"{base}-{n}"
    return candidate


SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS config(key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_by TEXT, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS principals(
  id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, kind TEXT NOT NULL CHECK(kind IN ('human','agent','system')),
  token_hash TEXT UNIQUE, created_at TEXT NOT NULL, revoked_at TEXT);
-- How people sign in: a verified email (magic link, Google) or a GitHub account, linked to a human principal.
CREATE TABLE IF NOT EXISTS identities(
  provider TEXT NOT NULL, subject TEXT NOT NULL, principal_id TEXT NOT NULL, email TEXT, login TEXT,
  created_at TEXT NOT NULL, last_login_at TEXT, PRIMARY KEY(provider, subject));
CREATE INDEX IF NOT EXISTS identities_principal ON identities(principal_id);
-- Expiring credentials: browser sessions, OAuth access/refresh tokens, magic-link tokens. Only hashes are stored.
CREATE TABLE IF NOT EXISTS credentials(
  hash TEXT PRIMARY KEY, principal_id TEXT NOT NULL, kind TEXT NOT NULL, client_id TEXT, family TEXT,
  resource TEXT, created_at TEXT NOT NULL, expires_at TEXT, revoked_at TEXT, meta TEXT NOT NULL DEFAULT '{}');
CREATE INDEX IF NOT EXISTS credentials_principal ON credentials(principal_id, kind);
CREATE INDEX IF NOT EXISTS credentials_family ON credentials(family);
-- OAuth clients that registered themselves (DCR); metadata-document clients are cached here too.
CREATE TABLE IF NOT EXISTS oauth_clients(
  client_id TEXT PRIMARY KEY, name TEXT NOT NULL, redirect_uris TEXT NOT NULL, meta TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL, last_used_at TEXT);
CREATE TABLE IF NOT EXISTS journal(
  seq INTEGER PRIMARY KEY AUTOINCREMENT, op_id TEXT NOT NULL UNIQUE, ts TEXT NOT NULL,
  principal_id TEXT, agent TEXT, session TEXT, project TEXT, op TEXT NOT NULL, payload TEXT NOT NULL);
-- Account and server events (sign-ins, invites, roles, tokens, settings) for admins; kept out of the journal, which
-- every tab and agent can read. Written by Tx.audit in the transaction of the change it records.
CREATE TABLE IF NOT EXISTS audit(
  seq INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, actor_id TEXT, op TEXT NOT NULL, target TEXT, ip TEXT,
  detail TEXT NOT NULL DEFAULT '{}');

CREATE TABLE IF NOT EXISTS works(
  id TEXT PRIMARY KEY, type TEXT NOT NULL, title TEXT NOT NULL, csl TEXT NOT NULL, year INTEGER,
  citekey TEXT UNIQUE, norm_title TEXT NOT NULL DEFAULT '', first_author TEXT NOT NULL DEFAULT '',
  trust_flags TEXT NOT NULL DEFAULT '[]', merged_into TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS works_norm_title ON works(norm_title);
CREATE TABLE IF NOT EXISTS work_ids(
  scheme TEXT NOT NULL, value TEXT NOT NULL, work_id TEXT NOT NULL REFERENCES works(id),
  trust TEXT NOT NULL DEFAULT 'high', PRIMARY KEY(scheme, value));
CREATE INDEX IF NOT EXISTS work_ids_work ON work_ids(work_id);
CREATE TABLE IF NOT EXISTS work_sources(
  work_id TEXT NOT NULL REFERENCES works(id), provider TEXT NOT NULL, data TEXT NOT NULL, fetched_at TEXT NOT NULL,
  PRIMARY KEY(work_id, provider));
CREATE TABLE IF NOT EXISTS versions(
  id TEXT PRIMARY KEY, work_id TEXT NOT NULL REFERENCES works(id), kind TEXT NOT NULL, label TEXT NOT NULL,
  ids TEXT NOT NULL DEFAULT '{}', date TEXT);
CREATE TABLE IF NOT EXISTS merges(
  id TEXT PRIMARY KEY, survivor TEXT NOT NULL, absorbed TEXT NOT NULL, reason TEXT NOT NULL,
  principal_id TEXT, at TEXT NOT NULL, undone_at TEXT);
CREATE TABLE IF NOT EXISTS not_duplicates(a TEXT NOT NULL, b TEXT NOT NULL, principal_id TEXT, at TEXT NOT NULL,
  PRIMARY KEY(a, b));

CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY, title TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS project_works(
  project TEXT NOT NULL REFERENCES projects(id), work_id TEXT NOT NULL REFERENCES works(id),
  added_by TEXT, added_at TEXT NOT NULL, why TEXT, read_depth TEXT, citekey_override TEXT, removed_at TEXT,
  PRIMARY KEY(project, work_id));
CREATE INDEX IF NOT EXISTS project_works_work ON project_works(work_id);
CREATE TABLE IF NOT EXISTS reading_log(
  id INTEGER PRIMARY KEY, project TEXT NOT NULL, work_id TEXT NOT NULL, document_id TEXT NOT NULL,
  passage_ids TEXT NOT NULL, principal_id TEXT, at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS tags(
  id TEXT PRIMARY KEY, project TEXT NOT NULL DEFAULT '', name TEXT NOT NULL, description TEXT, color TEXT,
  created_by TEXT, created_at TEXT NOT NULL, archived_at TEXT, UNIQUE(project, name));
CREATE TABLE IF NOT EXISTS taggings(
  tag_id TEXT NOT NULL REFERENCES tags(id), target_type TEXT NOT NULL, target_id TEXT NOT NULL,
  project TEXT NOT NULL DEFAULT '', principal_id TEXT, agent TEXT, at TEXT NOT NULL, removed_at TEXT,
  PRIMARY KEY(tag_id, target_type, target_id, project));
CREATE INDEX IF NOT EXISTS taggings_target ON taggings(target_type, target_id);

CREATE TABLE IF NOT EXISTS documents(
  id TEXT PRIMARY KEY, work_id TEXT NOT NULL REFERENCES works(id), source TEXT NOT NULL,
  blob_sha256 TEXT, parser TEXT NOT NULL, parser_version TEXT NOT NULL, status TEXT NOT NULL,
  outline TEXT NOT NULL DEFAULT '[]', n_passages INTEGER NOT NULL DEFAULT 0, chars INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS documents_work ON documents(work_id);
CREATE TABLE IF NOT EXISTS passages(
  id INTEGER PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id), seq INTEGER NOT NULL,
  section TEXT NOT NULL DEFAULT '', page INTEGER, char_start INTEGER NOT NULL, char_end INTEGER NOT NULL,
  text TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS passages_doc ON passages(document_id, seq);
CREATE VIRTUAL TABLE IF NOT EXISTS passages_fts USING fts5(
  text, section, content='passages', content_rowid='id', tokenize='unicode61 remove_diacritics 2');
CREATE TABLE IF NOT EXISTS refs_extracted(
  document_id TEXT NOT NULL, seq INTEGER NOT NULL, label TEXT, raw TEXT NOT NULL, parsed TEXT NOT NULL DEFAULT '{}',
  resolved_work_id TEXT, PRIMARY KEY(document_id, seq));
CREATE TABLE IF NOT EXISTS citation_contexts(
  citing_work TEXT NOT NULL, cited_work TEXT NOT NULL, document_id TEXT NOT NULL, passage_id INTEGER NOT NULL,
  sentence TEXT NOT NULL, PRIMARY KEY(citing_work, cited_work, passage_id));
CREATE INDEX IF NOT EXISTS citation_contexts_cited ON citation_contexts(cited_work);

CREATE TABLE IF NOT EXISTS entities(
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, project TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL DEFAULT '',
  created_by TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS notes(
  id TEXT PRIMARY KEY, project TEXT NOT NULL, subject TEXT NOT NULL, kind TEXT NOT NULL, text TEXT NOT NULL,
  quote TEXT, document_id TEXT, passage_id INTEGER, page INTEGER,
  verification TEXT NOT NULL DEFAULT 'unchecked', principal_id TEXT, agent TEXT, session TEXT, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS notes_subject ON notes(subject);
CREATE TABLE IF NOT EXISTS links(
  id TEXT PRIMARY KEY, project TEXT NOT NULL, source TEXT NOT NULL, target TEXT NOT NULL, relation TEXT NOT NULL,
  qualifier TEXT, evidence_note_id TEXT, principal_id TEXT, agent TEXT, created_at TEXT NOT NULL, retracted_at TEXT);
CREATE INDEX IF NOT EXISTS links_source ON links(source);
CREATE INDEX IF NOT EXISTS links_target ON links(target);

CREATE TABLE IF NOT EXISTS searches(
  id TEXT PRIMARY KEY, project TEXT NOT NULL, query TEXT NOT NULL, norm_query TEXT NOT NULL, sources TEXT NOT NULL,
  filters TEXT NOT NULL, principal_id TEXT, at TEXT NOT NULL, hits TEXT NOT NULL, new_to_project INTEGER NOT NULL,
  degraded TEXT NOT NULL DEFAULT '[]');
CREATE INDEX IF NOT EXISTS searches_norm ON searches(project, norm_query);
CREATE TABLE IF NOT EXISTS snowball(
  project TEXT NOT NULL, handle TEXT NOT NULL, title TEXT NOT NULL DEFAULT '', year INTEGER, work_id TEXT,
  direction TEXT NOT NULL, seeds TEXT NOT NULL DEFAULT '[]', score REAL NOT NULL DEFAULT 0, state TEXT NOT NULL,
  reason TEXT, decided_by TEXT, decided_at TEXT, created_at TEXT NOT NULL, PRIMARY KEY(project, handle));

CREATE TABLE IF NOT EXISTS maps(id TEXT PRIMARY KEY, project TEXT NOT NULL, title TEXT NOT NULL, created_by TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT);
CREATE TABLE IF NOT EXISTS map_nodes(id TEXT PRIMARY KEY, map_id TEXT NOT NULL REFERENCES maps(id), ref TEXT,
  text TEXT, parent TEXT, created_by TEXT, created_at TEXT NOT NULL, deleted_at TEXT);
CREATE TABLE IF NOT EXISTS map_edges(id TEXT PRIMARY KEY, map_id TEXT NOT NULL REFERENCES maps(id), src TEXT NOT NULL,
  dst TEXT NOT NULL, label TEXT NOT NULL DEFAULT '', promoted_link_id TEXT, created_by TEXT, created_at TEXT NOT NULL,
  deleted_at TEXT);
CREATE TABLE IF NOT EXISTS map_layout(map_id TEXT NOT NULL, node_id TEXT NOT NULL, x REAL, y REAL,
  style TEXT NOT NULL DEFAULT '{}', PRIMARY KEY(map_id, node_id));

CREATE TABLE IF NOT EXISTS call_stats(tool TEXT PRIMARY KEY, calls INTEGER NOT NULL DEFAULT 0,
  chars_out INTEGER NOT NULL DEFAULT 0, max_chars_out INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS provider_stats(provider TEXT PRIMARY KEY, ok INTEGER NOT NULL DEFAULT 0,
  errors INTEGER NOT NULL DEFAULT 0, last_error TEXT, last_error_at TEXT, last_ok_at TEXT);
CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, kind TEXT NOT NULL, payload TEXT NOT NULL, state TEXT NOT NULL,
  result TEXT, error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);

CREATE VIRTUAL TABLE IF NOT EXISTS works_fts USING fts5(
  work_id UNINDEXED, citekey, title, authors, abstract, venue, ids, tokenize='unicode61 remove_diacritics 2');
CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
  note_id UNINDEXED, project UNINDEXED, subject UNINDEXED, text, tokenize='unicode61 remove_diacritics 2');
"""


def _safe_wal(version: tuple[int, ...]) -> bool:
    # SQLite versions with the WAL-reset bug fixed.
    return version >= (3, 51, 3) or (3, 50, 7) <= version < (3, 51, 0) or (3, 44, 6) <= version < (3, 45, 0)


@dataclass(frozen=True)
class Ctx:
    """Where a change comes from, for the audit log: who (a principal id; None for someone not signed in) and the
    request (address, user agent), or `via` for the server's own shell and schedules ("cli", "startup", "schedule")."""
    actor_id: str | None = None
    ip: str | None = None
    ua: str | None = None
    via: str | None = None


# Tests set this: a transaction that changed rows must have written a journal entry or an audit row (or said, with
# internal=..., why it needs neither). Production never checks it.
STRICT = False
_MISSING = object()


@dataclass
class Actor:
    """Who is acting: the authenticated principal plus optional free agent/session labels."""
    principal_id: str | None = None
    name: str = "system"
    kind: str = "system"
    agent: str | None = None
    session: str | None = None
    project: str = "default"
    projects: tuple[str, ...] | None = None  # projects this principal may use; None = all
    role: str = "member"  # effective role: admin | member (an agent never outranks the person who owns it)

    def may_use(self, project: str) -> bool:
        return self.projects is None or project in self.projects

    def check_library_change(self, what: str) -> None:
        """The one rule for shared data under project limits (docs/ACCESS.md §3): a principal limited to some projects
        may read and add library-wide items (entities, links, tags, works) but not delete, retract, merge, split,
        rename or archive them, since every project shares them. Raises PermissionError."""
        if self.projects is not None:
            raise PermissionError(f"{what} is shared by all projects; only a sign-in without project limits can change it")

    @property
    def admin(self) -> bool:
        return self.kind == "system" or self.role == "admin"

    @property
    def label(self) -> str:
        return f"{self.name}/{self.agent}" if self.agent else self.name


SYSTEM = Actor(name="system", kind="system")  # background work: jobs, enrichment, account administration


def who_sql(p: str = "p", o: str = "o") -> str:
    """SQL for how a principal is shown: "claude-code via ada" for an agent a person owns, else its name. Join the
    principal as `p` and its owner (principals.owner_id) as `o`."""
    return f"CASE WHEN {o}.name IS NOT NULL THEN COALESCE({p}.label, {p}.name) || ' via ' || {o}.name ELSE {p}.name END"


def _put_nowait(q, item) -> None:
    try:
        q.put_nowait(item)
    except Exception:  # full: a slow tab misses events and replays them on reconnect
        pass


class Events:
    """In-process pub/sub of committed journal entries, feeding the SSE stream."""

    MAX_ASYNC = 200

    def __init__(self) -> None:
        self._async: dict[object, object] = {}  # asyncio.Queue -> its event loop
        self._lock = threading.Lock()

    def subscribe_async(self):
        """An asyncio queue fed from any thread, for SSE clients (no worker thread is held while waiting)."""
        import asyncio
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        with self._lock:
            if len(self._async) >= self.MAX_ASYNC:
                raise OverflowError("too many subscribers")
            self._async[q] = asyncio.get_running_loop()
        return q

    def unsubscribe_async(self, q) -> None:
        with self._lock:
            self._async.pop(q, None)

    def publish(self, items: list[dict]) -> None:
        with self._lock:
            subs = list(self._async.items())
        for q, loop in subs:
            for item in items:
                try:
                    loop.call_soon_threadsafe(_put_nowait, q, item)
                except RuntimeError:  # loop closed
                    pass


class Tx:
    def __init__(self, conn: sqlite3.Connection, actor: Actor, ctx: Ctx | None = None):
        self.conn = conn
        self.actor = actor
        self.ctx = ctx or Ctx(actor.principal_id)
        self.entries: list[dict] = []
        self.audited = 0

    def execute(self, sql: str, params: Any = ()) -> sqlite3.Cursor:
        return self.conn.execute(sql, params)

    def log(self, op: str, payload: dict, project: str | None = None) -> None:
        subject = next((payload[k] for k in SUBJECT_KEYS if isinstance(payload.get(k), str)), None)
        entry = dict(op_id=ulid(), ts=now(), principal_id=self.actor.principal_id, agent=self.actor.agent,
                     session=self.actor.session, project=project if project is not None else self.actor.project,
                     op=op, payload=dumps(payload), subject=subject)
        cur = self.conn.execute(
            "INSERT INTO journal(op_id,ts,principal_id,agent,session,project,op,payload,subject) VALUES(?,?,?,?,?,?,?,?,?)",
            tuple(entry.values()))
        entry["seq"] = cur.lastrowid
        entry["principal"] = self.actor.name
        self.entries.append(entry)

    def audit(self, op: str, target: str | None = None, *, subject: str | None = None, actor_id=_MISSING, fold: int = 0,
              fold_any_target: bool = False, **detail) -> None:
        """An audit row, committed with this transaction's change (or not at all). actor_id defaults to the context's
        (pass it for someone who just signed in); subject is the principal the event is about. fold=N seconds: a
        repeat of the same event (op, target, address) within N seconds counts on the earlier row instead;
        fold_any_target folds repeats from one address whatever their target (the first row's target is kept)."""
        actor = self.ctx.actor_id if actor_id is _MISSING else actor_id
        if self.ctx.via:
            detail.setdefault("via", self.ctx.via)
        self.audited += 1
        stamp = now()
        if fold:
            since = datetime.fromtimestamp(time.time() - fold, timezone.utc).isoformat(timespec="seconds")
            same = "" if fold_any_target else " AND target IS ?"
            row = self.conn.execute(f"SELECT seq FROM audit WHERE op=?{same} AND ip IS ? AND COALESCE(last_at, ts)>? "
                                    "ORDER BY seq DESC LIMIT 1",
                                    (op, self.ctx.ip, since) if fold_any_target else (op, target, self.ctx.ip, since)).fetchone()
            if row:
                self.conn.execute("UPDATE audit SET count=count+1, last_at=? WHERE seq=?", (stamp, row[0]))
                return
        self.conn.execute("INSERT INTO audit(ts,actor_id,op,target,ip,detail,subject_id,ua) VALUES(?,?,?,?,?,?,?,?)",
                          (stamp, actor, op, target, self.ctx.ip, json.dumps(detail, ensure_ascii=False), subject,
                           (self.ctx.ua or "")[:160] or None))


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._write_lock = threading.RLock()
        self.events = Events()
        self.wal = _safe_wal(sqlite3.sqlite_version_info)
        self._migrate()

    def _migrate(self) -> None:
        """Bring the file to SCHEMA_VERSION. A newer file is refused before anything is written; an older one with
        data is copied to <name>.v<old>.bak first, then changed in one transaction (all of it applies, or none)."""
        if self.path.exists():  # look without changing anything (not even the journal mode) before deciding
            peek = sqlite3.connect(f"file:{self.path.as_posix()}?mode=ro", uri=True)
            try:
                found = peek.execute("PRAGMA user_version").fetchone()[0]
            finally:
                peek.close()
            if found > SCHEMA_VERSION:
                raise RuntimeError(f"Database schema v{found} is newer than this litledger (v{SCHEMA_VERSION}); "
                                   "upgrade litledger instead of writing with an older version.")
        with self.connection() as conn:
            current = conn.execute("PRAGMA user_version").fetchone()[0]
            if current > SCHEMA_VERSION:
                raise RuntimeError(f"Database schema v{current} is newer than this litledger (v{SCHEMA_VERSION}); "
                                   "upgrade litledger instead of writing with an older version.")
            fresh = not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' LIMIT 1").fetchone()
            columns = lambda table: {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}  # noqa: E731
            if current == SCHEMA_VERSION and not fresh:
                # A pre-release build may have written this version before all of its columns existed: finish it.
                if all(c in columns(t) for v, t, c, _ in ADDED_COLUMNS if v == SCHEMA_VERSION):
                    return
                current = SCHEMA_VERSION - 1
            if not fresh and current < SCHEMA_VERSION:
                backup = self.path.with_name(f"{self.path.name}.v{current}.bak")
                if not backup.exists():
                    with sqlite3.connect(backup) as dst:
                        conn.backup(dst)
            conn.execute("BEGIN IMMEDIATE")
            try:
                for stmt in re.sub(r"--[^\n]*", "", SCHEMA).split(";"):  # executescript would commit first
                    if stmt.strip():
                        conn.execute(stmt)
                for _version, table, column, decl in ADDED_COLUMNS:
                    if column not in columns(table):
                        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
                for _version, table, column in DROPPED_COLUMNS:
                    if column in columns(table):
                        conn.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
                for version in sorted(MIGRATION_SQL):
                    if fresh or current < version:
                        for stmt in MIGRATION_SQL[version]:
                            conn.execute(stmt)
                conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")

    @contextlib.contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=" + ("WAL" if self.wal else "DELETE"))
        conn.execute("PRAGMA synchronous=NORMAL")
        try:
            yield conn
        finally:
            conn.close()

    @contextlib.contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        with self.connection() as conn:
            yield conn

    @contextlib.contextmanager
    def write(self, internal: str) -> Iterator[sqlite3.Connection]:
        """A connection holding the write lock, for small autocommit writes that need no journal entry or audit row.
        `internal` says why (job bookkeeping, a cache, last seen): library data goes through tx() with tx.log, account
        and security changes through tx() with tx.audit."""
        with self._write_lock, self.connection() as conn:
            yield conn

    @contextlib.contextmanager
    def tx(self, actor: Actor, ctx: Ctx | None = None, internal: str | None = None) -> Iterator[Tx]:
        """One atomic write: table changes plus their journal entries (tx.log) and audit rows (tx.audit). Events publish
        after commit. internal=reason marks a transaction that needs neither (see STRICT)."""
        with self._write_lock, self.connection() as conn:
            before = conn.total_changes
            conn.execute("BEGIN IMMEDIATE")
            tx = Tx(conn, actor, ctx)
            try:
                yield tx
                if STRICT and internal is None and conn.total_changes != before and not tx.entries and not tx.audited:
                    where = next((f"{f.filename}:{f.lineno}" for f in reversed(traceback.extract_stack()[:-1])
                                  if not f.filename.endswith(("db.py", "contextlib.py"))), "?")
                    raise RuntimeError(f"{where}: this transaction changed rows but wrote no journal entry or audit row; "
                                       "use tx.log (library data), tx.audit (accounts, settings) or db.tx(..., internal=reason)")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
        if tx.entries:
            self.events.publish([{k: v for k, v in e.items() if k != "payload"} | {"payload": json.loads(e["payload"])}
                                 for e in tx.entries])

    def meta(self, key: str, default: str | None = None) -> str | None:
        with self.read() as conn:
            row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
            return row[0] if row else default

    def set_meta(self, key: str, value: str) -> None:
        with self.write("bookkeeping marks") as conn:
            conn.execute("INSERT INTO meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
