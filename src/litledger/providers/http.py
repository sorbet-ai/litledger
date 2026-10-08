"""Polite, guarded HTTP for providers and full text: per-provider pacing, Retry-After cooldowns, a size-capped response
cache in its own SQLite file, SSRF checks on every hop, and a failure taxonomy."""
from __future__ import annotations

import contextlib
import email.utils
import hashlib
import json
import logging
import math
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpcore
import httpx

from .. import __version__
from ..db import Database, now
from .cache import ResponseCache
from .netguard import BlockedFetch, NetGuard, guarded_transport

log = logging.getLogger("litledger.http")

USER_AGENT = f"litledger/{__version__} (self-hosted literature ledger; +https://github.com/sorbet-ai/litledger)"
MAX_REDIRECTS = 5
REDIRECTS = (301, 302, 303, 307, 308)
RETRYABLE = (429, 500, 502, 503, 504)
MAX_INLINE_WAIT = 15.0  # longer Retry-After values become a cooldown instead of a sleep
MAX_COOLDOWN = 3600.0
# Headers that may follow a redirect to another host; anything else (API keys, tokens) is dropped.
PORTABLE_HEADERS = {"user-agent", "accept", "accept-language"}


class ProviderError(Exception):
    """kind: rate_limited | auth_error | no_match | unavailable | budget | blocked | error"""

    def __init__(self, provider: str, kind: str, message: str = ""):
        super().__init__(f"{provider}: {kind}{' — ' + message if message else ''}")
        self.provider = provider
        self.kind = kind
        self.message = message


@dataclass
class Response:
    status: int
    content: bytes
    headers: dict[str, str] = field(default_factory=dict)
    url: str = ""
    cached: bool = False

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")

    def json(self) -> Any:
        return json.loads(self.content)


class _Pacer:
    def __init__(self, interval: float):
        self.interval = interval
        self.lock = threading.Lock()
        self.next_at = 0.0

    def wait(self) -> None:
        with self.lock:
            delay = self.next_at - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            self.next_at = time.monotonic() + self.interval


class Http:
    """One per process. `configure()` applies new settings in place so pacing, cooldowns and the cache survive a
    settings change, and the client is never closed under in-flight requests."""

    def __init__(self, db: Database, contact_email: str = "", offline: bool = False,
                 transport: httpx.BaseTransport | None = None, allow_private: bool = False,
                 cache_path: Path | None = None):
        self.db = db
        self.guard = NetGuard(allow_private)
        self.contact_email = contact_email
        self.offline = offline
        self.client = httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=httpx.Timeout(20.0, connect=10.0),
                                   follow_redirects=False, transport=transport or guarded_transport(self.guard))
        self.cache = ResponseCache(cache_path or Path(db.path).parent / "cache.sqlite3")
        self._pacers: dict[str, _Pacer] = {}
        self._cooldowns: dict[str, float] = {}  # "provider|host" -> monotonic time requests may resume
        self._strikes: dict[str, int] = {}  # consecutive 429/5xx per "provider|host"
        self.pace = True  # tests replaying recorded responses turn pacing off
        self._local = threading.local()
        self._lock = threading.Lock()

    def configure(self, contact_email: str = "", offline: bool = False, allow_private: bool = False) -> None:
        self.contact_email = contact_email
        self.offline = offline
        self.guard.allow_private = allow_private

    @property
    def polite_user_agent(self) -> str:
        """The User-Agent for APIs that ask for a contact address (Crossref, OpenAlex, Unpaywall, NCBI)."""
        return USER_AGENT[:-1] + f"; mailto:{self.contact_email})" if self.contact_email else USER_AGENT

    @contextlib.contextmanager
    def fresh(self):
        """Within this block requests skip the cache (used to verify credentials)."""
        self._local.bypass = True
        try:
            yield
        finally:
            self._local.bypass = False

    def pacer(self, provider: str, interval: float) -> _Pacer:
        with self._lock:
            if provider not in self._pacers:
                self._pacers[provider] = _Pacer(interval)
            return self._pacers[provider]

    # -- cooldowns ---------------------------------------------------------------------------------------------
    def cooldown_left(self, provider: str, host: str) -> float:
        with self._lock:
            until = self._cooldowns.get(f"{provider}|{host}", 0.0)
        return max(0.0, until - time.monotonic())

    def _penalise(self, ck: str, resp: Response) -> float:
        """Record a 429/5xx; returns the cooldown (seconds) now in force for this provider and host."""
        retry_after = _retry_after(resp.headers.get("retry-after"), None)
        with self._lock:
            strikes = self._strikes.get(ck, 0) + 1
            self._strikes[ck] = strikes
            backoff = min(MAX_COOLDOWN, 5.0 * 2 ** (strikes - 1))
            if retry_after is not None:
                wait = retry_after
            elif resp.status == 429 or strikes >= 2:
                wait = backoff
            else:
                wait = 0.0
            wait = min(MAX_COOLDOWN, max(0.0, wait))
            if wait > 0:
                self._cooldowns[ck] = max(self._cooldowns.get(ck, 0.0), time.monotonic() + wait)
        return wait

    def _recovered(self, ck: str) -> None:
        with self._lock:
            self._strikes.pop(ck, None)
            self._cooldowns.pop(ck, None)

    @staticmethod
    def _key(method: str, url: str, params: dict | None, body: Any) -> str:
        raw = method + " " + url + "?" + urlencode(sorted((params or {}).items()), doseq=True) + json.dumps(body, sort_keys=True)
        return hashlib.sha256(raw.encode()).hexdigest()

    # -- requests ----------------------------------------------------------------------------------------------
    def request(self, provider: str, url: str, *, params: dict | None = None, headers: dict | None = None,
                method: str = "GET", data: Any = None, json_body: Any = None, ttl: float = 7 * 86400,
                interval: float = 1.0, retries: int = 2, ok_404: bool = False, max_bytes: int = 120 * 1024 * 1024,
                mailto: bool = False) -> Response:
        """ttl=0 disables caching. mailto=True adds the contact email to the User-Agent (only for APIs that ask)."""
        try:
            target = self.guard.check_static(url)
        except BlockedFetch as exc:
            raise ProviderError(provider, "blocked", str(exc)) from None
        key = self._key(method, url, params, json_body or data)
        if ttl > 0 and not getattr(self._local, "bypass", False):
            hit = self.cache.get(key)
            if hit:
                status, ctype, body = hit
                if status == 404 and not ok_404:
                    raise ProviderError(provider, "no_match")
                return Response(status, body, {"content-type": ctype or ""}, url, cached=True)
        if self.offline:
            raise ProviderError(provider, "unavailable", "offline mode")
        ck = f"{provider}|{target.host}"
        left = self.cooldown_left(provider, target.host)
        if left > 0:
            raise ProviderError(provider, "rate_limited", f"cooling down after rate limiting; retry after {math.ceil(left)} s")
        headers = dict(headers or {})
        if mailto and self.contact_email and not any(k.lower() == "user-agent" for k in headers):
            headers["User-Agent"] = self.polite_user_agent
        pacer = self.pacer(provider, interval)
        attempt = 0
        while True:
            if self.pace:
                pacer.wait()
            try:
                resp = self._send(provider, method, url, params, headers, data, json_body, max_bytes)
            except BlockedFetch as exc:
                raise ProviderError(provider, "blocked", str(exc)) from None
            except httpx.TimeoutException as exc:
                if attempt < retries:
                    attempt += 1
                    continue
                self._stat(provider, f"timeout: {exc}")
                raise ProviderError(provider, "unavailable", "timeout") from exc
            except httpx.HTTPError as exc:
                if attempt < retries:
                    attempt += 1
                    time.sleep(1.0 + attempt)
                    continue
                self._stat(provider, f"network: {exc}")
                raise ProviderError(provider, "unavailable", str(exc)[:120]) from exc
            if resp.status in RETRYABLE and attempt < retries:
                wait = _retry_after(resp.headers.get("retry-after"), default=2.0 * (attempt + 1))
                if wait <= MAX_INLINE_WAIT:
                    attempt += 1
                    time.sleep(wait)
                    continue
            break
        if resp.status in RETRYABLE:
            wait = self._penalise(ck, resp)
            after = f"retry after {math.ceil(wait)} s" if wait > 0 else ""
            if resp.status == 429:
                self._stat(provider, "429 rate limited")
                raise ProviderError(provider, "rate_limited", after or "retry later")
            self._stat(provider, f"HTTP {resp.status}")
            raise ProviderError(provider, "error", f"HTTP {resp.status}" + (f"; {after}" if after else ""))
        self._recovered(ck)
        if resp.status in (401, 403):
            self._stat(provider, f"{resp.status} auth")
            raise ProviderError(provider, "auth_error", resp.text[:160])
        if resp.status == 404:
            if ttl > 0:
                self.cache.put(key, provider, url, resp.status, resp.headers.get("content-type", ""), resp.content, min(ttl, 86400))
            if ok_404:
                return resp
            self._stat(provider, None)
            raise ProviderError(provider, "no_match")
        if resp.status in REDIRECTS:
            self._stat(provider, f"too many redirects ({MAX_REDIRECTS})")
            raise ProviderError(provider, "error", f"more than {MAX_REDIRECTS} redirects")
        if resp.status >= 400:
            self._stat(provider, f"HTTP {resp.status}")
            raise ProviderError(provider, "error", f"HTTP {resp.status}")
        self._stat(provider, None)
        if ttl > 0:
            self.cache.put(key, provider, url, resp.status, resp.headers.get("content-type", ""), resp.content, ttl)
        return resp

    def get(self, provider: str, url: str, **kw) -> Response:
        return self.request(provider, url, **kw)

    def _send(self, provider: str, method: str, url: str, params: dict | None, headers: dict, data: Any,
              json_body: Any, max_bytes: int) -> Response:
        """One logical request: follows up to MAX_REDIRECTS redirects by hand, vetting every hop."""
        current: str | httpx.URL = url
        for hop in range(MAX_REDIRECTS + 1):
            try:
                target = self.guard.check(current)
            except httpcore.ConnectError as exc:  # the host does not resolve
                raise httpx.ConnectError(str(exc)) from exc
            with self.client.stream(method, target, params=params if hop == 0 else None, headers=headers,
                                    data=data, json=json_body) as r:
                location = r.headers.get("location")
                if r.status_code in REDIRECTS and location and hop < MAX_REDIRECTS:
                    nxt = r.url.join(location)
                    if r.status_code == 303 or (r.status_code in (301, 302) and method not in ("GET", "HEAD")):
                        method, data, json_body = "GET", None, None
                    if nxt.host != r.url.host or nxt.scheme != r.url.scheme:
                        headers = {k: v for k, v in headers.items() if k.lower() in PORTABLE_HEADERS}
                    current = nxt
                    continue
                chunks, size = [], 0
                for chunk in r.iter_bytes():
                    size += len(chunk)
                    if size > max_bytes:
                        raise ProviderError(provider, "error", f"response larger than {max_bytes} bytes")
                    chunks.append(chunk)
                return Response(r.status_code, b"".join(chunks), {k.lower(): v for k, v in r.headers.items()}, str(r.url))
        raise AssertionError("unreachable")

    def _stat(self, provider: str, error: str | None) -> None:
        with self.db.write("source call counts") as conn:
            conn.execute("INSERT OR IGNORE INTO provider_stats(provider) VALUES(?)", (provider,))
            if error:
                conn.execute("UPDATE provider_stats SET errors=errors+1, last_error=?, last_error_at=? WHERE provider=?",
                             (error[:300], now(), provider))
            else:
                conn.execute("UPDATE provider_stats SET ok=ok+1, last_ok_at=? WHERE provider=?", (now(), provider))


def _retry_after(value: str | None, default: float | None) -> float | None:
    """Retry-After as seconds: either delta-seconds or an HTTP date."""
    if not value:
        return default
    value = value.strip()
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = email.utils.parsedate_to_datetime(value)
        return max(0.0, when.timestamp() - time.time())
    except (TypeError, ValueError, IndexError, OverflowError):
        return default
