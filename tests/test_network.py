"""Outbound HTTP: SSRF guard (H1), response cache (H5), politeness (M16), User-Agent (M3). No real network or DNS."""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import httpcore
import httpx
import pytest

from litledger import tools
from litledger.config import Settings
from litledger.ledger import Ledger
from litledger.db import Actor
from litledger.providers.cache import ResponseCache
from litledger.providers.http import Http, ProviderError
from litledger.providers.netguard import BlockedFetch, GuardedBackend, NetGuard, blocked_reason, ip_literal

PUBLIC = "93.184.216.34"


class FakeTransport(httpx.BaseTransport):
    """Routes by exact URL (without query) to (status, headers, body); records every request it receives."""

    def __init__(self, routes: dict | None = None):
        self.routes = routes or {}
        self.requests: list[httpx.Request] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url).split("?", 1)[0]
        status, headers, body = self.routes.get(url, (404, {}, b"not found"))
        return httpx.Response(status, headers=headers, content=body, request=request)

    def urls(self) -> list[str]:
        return [str(r.url) for r in self.requests]


def make(tmp_path: Path, routes: dict | None = None, dns: dict | None = None, **config) -> tuple[Ledger, FakeTransport]:
    transport = FakeTransport(routes)
    lg = Ledger(Settings.from_env({"LITLEDGER_DATA": str(tmp_path / "data")}), transport=transport, run_jobs_inline=True)
    lg.http.pace = False
    dns = dns or {}
    lg.http.guard.resolver = lambda host, port: dns.get(host, [PUBLIC])
    if config:
        lg.set_config(Actor(name="test", kind="human"), {k.upper(): v for k, v in config.items()})
    return lg, transport


def blocked(lg: Ledger, url: str, provider: str = "web") -> ProviderError:
    with pytest.raises(ProviderError) as info:
        lg.http.get(provider, url, ttl=0)
    assert info.value.kind == "blocked", info.value
    return info.value


ACTOR = Actor(principal_id=None, name="tester", kind="agent", project="t")


# ------------------------------------------------------------------------------------------------ H1: SSRF
@pytest.mark.parametrize("url", [
    "http://10.0.0.5/admin", "http://192.168.1.1/", "http://172.16.0.1/", "http://127.0.0.1/", "http://169.254.169.254/latest/meta-data/",
    "http://100.64.0.1/", "http://0.0.0.0/", "http://224.0.0.1/", "http://240.0.0.1/",
    "http://[::1]/", "http://[::ffff:127.0.0.1]/", "http://[::ffff:a00:5]/", "http://[fe80::1]/", "http://[fd00::1]/",
    "http://[64:ff9b::a00:5]/", "http://2130706433/", "http://0177.0.0.1/", "http://0x7f.1/", "http://127.1/",
    "http://localhost/", "http://LOCALHOST./x", "http://metadata.google.internal/",
])
def test_private_and_disguised_addresses_are_refused(tmp_path, url):
    lg, transport = make(tmp_path)
    err = blocked(lg, url)
    assert "refused to fetch" in err.message
    assert "ALLOW_PRIVATE_FETCH" in err.message or "not a valid URL" in err.message  # httpx itself rejects 0177.0.0.1
    assert transport.requests == []


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.org/x", "gopher://example.org/", "javascript:alert(1)"])
def test_non_http_schemes_are_refused(tmp_path, url):
    lg, transport = make(tmp_path)
    err = blocked(lg, url)
    assert "only http and https" in err.message or "not a valid URL" in err.message
    assert transport.requests == []


def test_hostname_resolving_to_private_address_is_refused(tmp_path):
    lg, transport = make(tmp_path, dns={"intranet.example": ["10.1.2.3"], "mixed.example": [PUBLIC, "127.0.0.1"]})
    assert "resolves to 10.1.2.3, which is not allowed (10.1.2.3 is a private-network address)" in blocked(lg, "https://intranet.example/x").message
    assert "127.0.0.1" in blocked(lg, "https://mixed.example/x").message
    assert transport.requests == []


def test_non_standard_port_is_refused_unless_allowed(tmp_path):
    lg, transport = make(tmp_path, routes={"http://example.org:8080/": (200, {"content-type": "text/plain"}, b"ok")})
    assert "port 8080" in blocked(lg, "http://example.org:8080/").message
    lg.set_config(Actor(name="t", kind="human"), {"ALLOW_PRIVATE_FETCH": "1"})
    assert lg.http.get("web", "http://example.org:8080/", ttl=0).content == b"ok"


def test_redirect_to_private_address_is_refused(tmp_path):
    routes = {"https://public.example/paper": (302, {"location": "http://169.254.169.254/latest/meta-data/"}, b""),
              "https://hop.example/a": (301, {"location": "https://inward.example/b"}, b""),
              "http://169.254.169.254/latest/meta-data/": (200, {}, b"SECRET"),
              "https://inward.example/b": (200, {}, b"SECRET")}
    lg, transport = make(tmp_path, routes=routes, dns={"inward.example": ["192.168.0.10"]})
    assert "169.254.169.254 is a link-local address" in blocked(lg, "https://public.example/paper").message
    assert "inward.example resolves to 192.168.0.10" in blocked(lg, "https://hop.example/a").message
    assert transport.urls() == ["https://public.example/paper", "https://hop.example/a"]


def test_redirects_are_followed_by_hand_up_to_five_hops(tmp_path):
    routes = {f"https://r.example/{i}": (302, {"location": f"/{i + 1}"}, b"") for i in range(7)}
    routes["https://r.example/3"] = (200, {"content-type": "text/plain"}, b"landed")
    lg, transport = make(tmp_path, routes=routes)
    resp = lg.http.get("web", "https://r.example/0", ttl=0)
    assert resp.content == b"landed" and resp.url == "https://r.example/3"
    routes["https://r.example/3"] = (302, {"location": "/4"}, b"")
    with pytest.raises(ProviderError) as info:
        lg.http.get("web", "https://r.example/0", ttl=0)
    assert "redirect" in info.value.message


def test_redirect_to_another_host_drops_credentials(tmp_path):
    routes = {"https://api.example/x": (302, {"location": "https://other.example/y"}, b""),
              "https://other.example/y": (200, {}, b"ok")}
    lg, transport = make(tmp_path, routes=routes)
    lg.http.get("s2", "https://api.example/x", headers={"x-api-key": "SECRET", "Accept": "application/json"}, ttl=0)
    first, second = transport.requests
    assert first.headers.get("x-api-key") == "SECRET"
    assert "x-api-key" not in second.headers and second.headers.get("accept") == "application/json"


def test_resolve_and_read_of_a_private_url_are_refused(tmp_path):
    lg, transport = make(tmp_path)
    out = tools.call(lg, ACTOR, "resolve", {"items": ["http://10.0.0.5/admin"], "add": True})
    assert "blocked" in out and "10.0.0.5" not in "".join(transport.urls())
    assert transport.requests == []


def test_citation_pdf_url_pointing_inward_is_not_fetched(tmp_path):
    page = (b'<html><head><meta name="citation_title" content="A Public Paper About Guarded Fetching">'
            b'<meta name="citation_author" content="Ada Lovelace"><meta name="citation_publication_date" content="2024/01/02">'
            b'<meta name="citation_pdf_url" content="http://127.0.0.1:8765/api/v1/export"></head><body>x</body></html>')
    routes = {"https://papers.example/p1": (200, {"content-type": "text/html"}, page)}
    lg, transport = make(tmp_path, routes=routes)
    out = tools.call(lg, ACTOR, "resolve", {"items": ["https://papers.example/p1"], "add": True})
    assert out.startswith("ok"), out
    del routes["https://papers.example/p1"]  # the landing page is gone, so the PDF link is the only location
    with lg.db.read() as conn:
        citekey = conn.execute("SELECT citekey FROM works").fetchone()["citekey"]
    read = tools.call(lg, ACTOR, "read", {"work": citekey, "mode": "full"})
    assert "127.0.0.1" not in " ".join(transport.urls())
    assert "blocked" in read and "loopback" in read, read


def test_allow_private_fetch_setting(tmp_path):
    lg, transport = make(tmp_path, routes={"http://10.0.0.5/admin": (200, {"content-type": "text/plain"}, b"router")})
    blocked(lg, "http://10.0.0.5/admin")
    lg.set_config(Actor(name="t", kind="human"), {"ALLOW_PRIVATE_FETCH": "1"})
    assert lg.http.get("web", "http://10.0.0.5/admin", ttl=0).content == b"router"
    lg.set_config(Actor(name="t", kind="human"), {"ALLOW_PRIVATE_FETCH": None})
    blocked(lg, "http://10.0.0.5/admin")


def test_address_classification():
    assert ip_literal("2130706433") == ip_literal("127.0.0.1") == ip_literal("0x7f000001") == ip_literal("0177.0.0.01")
    assert ip_literal("example.org") is None and ip_literal("1.2.3.4.5") is None
    assert blocked_reason(ip_literal(PUBLIC)) is None
    assert blocked_reason(ip_literal("2606:4700::1111")) is None
    for bad in ("10.0.0.1", "100.100.1.1", "169.254.1.1", "::", "::ffff:192.168.1.1", "fc00::1", "ff02::1", "2002:7f00:1::"):
        assert blocked_reason(ip_literal(bad)), bad


class _FakeStream(httpcore.NetworkStream):
    def __init__(self, peer):
        self.peer = peer
        self.closed = False

    def get_extra_info(self, info):
        return (self.peer, 443) if info == "server_addr" else None

    def close(self):
        self.closed = True


class _FakeBackend(httpcore.NetworkBackend):
    def __init__(self, peer=None):
        self.peer = peer
        self.calls: list[str] = []

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        self.calls.append(host)
        return _FakeStream(self.peer or host)


def test_connect_time_guard_pins_the_vetted_address():
    """DNS rebinding: the socket goes to the IP that was checked, and the connected peer is checked again."""
    answers = iter([[PUBLIC], ["127.0.0.1"]])
    guard = NetGuard(resolver=lambda host, port: next(answers))
    inner = _FakeBackend()
    backend = GuardedBackend(guard, inner)
    backend.connect_tcp("rebind.example", 443)
    assert inner.calls == [PUBLIC]  # connected by IP, not by name (no second, unchecked lookup)
    with pytest.raises(BlockedFetch):
        backend.connect_tcp("rebind.example", 443)  # the second answer is private: nothing is connected
    assert inner.calls == [PUBLIC]
    sneaky = GuardedBackend(NetGuard(resolver=lambda h, p: [PUBLIC]), _FakeBackend(peer="10.0.0.1"))
    with pytest.raises(BlockedFetch):
        sneaky.connect_tcp("proxy.example", 443)


def test_production_transport_uses_the_guarded_backend(tmp_path):
    from litledger.db import Database
    http = Http(Database(tmp_path / "ledger.sqlite3"))
    assert isinstance(http.client._transport._pool._network_backend, GuardedBackend)
    http.cache.close()


# ------------------------------------------------------------------------------------------------ H5: cache
def test_provider_responses_are_cached_in_their_own_file(tmp_path):
    routes = {"https://api.example/works": (200, {"content-type": "application/json"}, b'{"ok": 1}')}
    lg, transport = make(tmp_path, routes=routes)
    assert lg.http.get("openalex", "https://api.example/works").json() == {"ok": 1}
    again = lg.http.get("openalex", "https://api.example/works")
    assert again.cached and len(transport.requests) == 1
    assert (tmp_path / "data" / "cache.sqlite3").exists()
    assert lg.http.cache.stats()["entries"] == 1
    with lg.db.read() as conn:  # nothing of it in the ledger database
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='http_cache'").fetchone()


def test_full_text_bodies_are_not_cached(tmp_path):
    html = b"<html><body><article>" + b"<p>" + b"Readable paragraph text for the guarded fetch test. " * 20 + b"</p></article></body></html>"
    lg, transport = make(tmp_path, routes={"https://blog.example/post": (200, {"content-type": "text/html"}, html)})
    with lg.db.tx(Actor(), internal="test setup") as tx:
        tx.execute("INSERT INTO works(id,type,title,csl,citekey,norm_title,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                   ("wpost", "webpage", "A post", '{"URL": "https://blog.example/post"}', "post2024", "a post", "x", "x"))
    from litledger.fulltext import fetch_document
    doc_id, msg = fetch_document(lg, "wpost")
    assert doc_id, msg
    assert lg.http.cache.stats()["entries"] == 0


def test_cache_is_size_capped_and_expires(tmp_path):
    cache = ResponseCache(tmp_path / "c.sqlite3", max_bytes=10_000, max_entry=4_000)
    assert not cache.put("huge", "p", "u", 200, "", b"x" * 5000, 60)  # over the per-entry limit
    for i in range(10):
        cache.put(f"k{i}", "p", "u", 200, "", b"x" * 3000, 60)
    cache.prune()
    assert cache.stats()["bytes"] <= 10_000
    assert cache.get("k9") and not cache.get("k0")  # oldest go first
    cache.put("short", "p", "u", 200, "", b"y", 0.01)
    time.sleep(0.05)
    assert cache.get("short") is None
    cache.prune()
    with sqlite3.connect(tmp_path / "c.sqlite3") as conn:
        assert not conn.execute("SELECT 1 FROM cache WHERE key='short'").fetchone()
    cache.close()


def test_corrupt_cache_file_is_recreated(tmp_path):
    path = tmp_path / "c.sqlite3"
    path.write_bytes(b"this is not a database" * 100)
    cache = ResponseCache(path)
    assert cache.put("k", "p", "u", 200, "", b"body", 60) and cache.get("k")[2] == b"body"
    cache.close()


# ------------------------------------------------------------------------------------------------ M16: politeness
def test_long_retry_after_starts_a_cooldown(tmp_path):
    routes = {"https://api.example/x": (429, {"retry-after": "120"}, b"slow down")}
    lg, transport = make(tmp_path, routes=routes)
    with pytest.raises(ProviderError) as info:
        lg.http.get("s2", "https://api.example/x", ttl=0)
    assert info.value.kind == "rate_limited" and "retry after 120 s" in info.value.message
    assert len(transport.requests) == 1  # > 15 s is not slept through inline
    with pytest.raises(ProviderError) as info:
        lg.http.get("s2", "https://api.example/x", ttl=0)
    assert info.value.kind == "rate_limited" and "retry after" in info.value.message
    assert len(transport.requests) == 1  # the cooldown answered without a request
    routes["https://api.example/y"] = (200, {}, b"ok")
    assert lg.http.get("other", "https://api.example/y", ttl=0).content == b"ok"  # other providers unaffected


def test_http_date_retry_after_and_repeated_5xx_back_off(tmp_path):
    from email.utils import formatdate
    routes = {"https://a.example/x": (503, {"retry-after": formatdate(time.time() + 600, usegmt=True)}, b""),
              "https://b.example/x": (500, {}, b"")}
    lg, transport = make(tmp_path, routes=routes)
    with pytest.raises(ProviderError) as info:
        lg.http.get("p1", "https://a.example/x", ttl=0)
    assert "retry after" in info.value.message
    assert lg.http.cooldown_left("p1", "a.example") > 500
    with pytest.raises(ProviderError):
        lg.http.request("p2", "https://b.example/x", ttl=0, retries=0)
    assert lg.http.cooldown_left("p2", "b.example") == 0  # one 5xx is not yet a pattern
    with pytest.raises(ProviderError):
        lg.http.request("p2", "https://b.example/x", ttl=0, retries=0)
    assert lg.http.cooldown_left("p2", "b.example") > 0


def test_pacing_cooldowns_and_cache_survive_a_settings_change(tmp_path):
    routes = {"https://api.example/x": (429, {"retry-after": "300"}, b"")}
    lg, transport = make(tmp_path, routes=routes)
    http, client, cache = lg.http, lg.http.client, lg.http.cache
    pacer = http.pacer("s2", 1.0)
    with pytest.raises(ProviderError):
        http.get("s2", "https://api.example/x", ttl=0)
    lg.set_config(Actor(name="t", kind="human"), {"CONTACT_EMAIL": "me@example.org"})
    assert lg.http is http and lg.http.client is client and lg.http.cache is cache
    assert http.pacer("s2", 1.0) is pacer and http.cooldown_left("s2", "api.example") > 200
    assert http.contact_email == "me@example.org"
    assert all(st.provider.http is http for st in lg.providers.states.values())
    assert not client.is_closed


# ------------------------------------------------------------------------------------------------ M3: User-Agent
def test_contact_email_only_goes_to_apis_that_ask_for_it(tmp_path):
    lg, transport = make(tmp_path, routes={}, contact_email="me@example.org")
    for pid in ("crossref", "openalex", "arxiv", "dblp", "hf", "s2", "unpaywall", "pubmed"):
        st = lg.providers.states[pid]
        try:
            st.provider.get(f"https://{pid}.example/probe", ttl=0)
        except ProviderError:
            pass
    ua = {r.url.host.split(".")[0]: r.headers["user-agent"] for r in transport.requests}
    for pid in ("crossref", "openalex", "unpaywall", "pubmed"):
        assert "mailto:me@example.org" in ua[pid], pid
    for pid in ("arxiv", "dblp", "hf", "s2"):
        assert "me@example.org" not in ua[pid] and ua[pid].startswith("litledger/"), (pid, ua[pid])
