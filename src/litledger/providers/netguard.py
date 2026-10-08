"""Outbound request guard (SSRF): only public http(s) hosts on standard ports, re-checked on every redirect hop and
again at connect time (the socket goes to the address that was vetted, so DNS rebinding cannot swap it).

`ALLOW_PRIVATE_FETCH=1` lifts the address and port checks for people who deliberately fetch from intranet hosts."""
from __future__ import annotations

import ipaddress
import re
import socket
from typing import Callable, Iterable

import httpcore
import httpx

Address = ipaddress.IPv4Address | ipaddress.IPv6Address
Resolver = Callable[[str, int], list[str]]

ALLOWED_SCHEMES = ("http", "https")
DEFAULT_PORTS = {None, 80, 443}
_CGNAT = ipaddress.ip_network("100.64.0.0/10")
_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_NUMERIC_HOST = re.compile(r"^(?:0x[0-9a-f]*|[0-9]+)(?:\.(?:0x[0-9a-f]*|[0-9]+)){0,3}\.?$", re.I)
HINT = "set ALLOW_PRIVATE_FETCH=1 (Admin → Server) to allow intranet hosts"


class BlockedFetch(Exception):
    """The request was refused before anything was sent; `reason` says why in words an agent can act on."""

    def __init__(self, url: str, reason: str):
        super().__init__(f"refused to fetch {url[:120]}: {reason}")
        self.url = url
        self.reason = reason


def system_resolver(host: str, port: int) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise httpcore.ConnectError(f"cannot resolve {host}: {exc}") from exc
    out: list[str] = []
    for info in infos:
        ip = info[4][0].split("%", 1)[0]  # drop an IPv6 zone id
        if ip not in out:
            out.append(ip)
    return out


def parse_numeric_ipv4(host: str) -> ipaddress.IPv4Address | None:
    """inet_aton forms that resolvers accept but look harmless: 2130706433, 0177.0.0.1, 0x7f.1, 127.1."""
    if not _NUMERIC_HOST.match(host):
        return None
    parts = host.rstrip(".").split(".")
    try:
        nums = [int(p, 16) if p.lower().startswith("0x") else int(p, 8) if len(p) > 1 and p.startswith("0") else int(p)
                for p in parts]
    except ValueError:
        return None
    *head, last = nums
    if any(n > 255 for n in head) or last >= 256 ** (4 - len(head)):
        return None
    value = 0
    for n in head:
        value = value * 256 + n
    value = value * 256 ** (4 - len(head)) + last
    return ipaddress.IPv4Address(value)


def ip_literal(host: str) -> Address | None:
    host = host.strip("[]")
    try:
        return ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return parse_numeric_ipv4(host)


def blocked_reason(ip: Address) -> str | None:
    """Why this address must not be fetched from (None when it is a public unicast address)."""
    if isinstance(ip, ipaddress.IPv6Address):
        embedded = ip.ipv4_mapped or ip.sixtofour or (ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF) if ip in _NAT64 else None)
        if embedded is not None:
            inner = blocked_reason(embedded)
            return f"{inner} (embedded in {ip})" if inner else None
    checks = [(ip.is_unspecified, "an unspecified address"), (ip.is_loopback, "a loopback address"),
              (ip.is_link_local, "a link-local address (cloud metadata lives here)"), (ip.is_multicast, "a multicast address"),
              (isinstance(ip, ipaddress.IPv4Address) and ip in _CGNAT, "a carrier-grade NAT address (100.64/10)"),
              (isinstance(ip, ipaddress.IPv6Address) and ip in ipaddress.ip_network("fc00::/7"), "a unique-local address"),
              (ip.is_private, "a private-network address"), (ip.is_reserved, "a reserved address"),
              (not ip.is_global, "not a public address")]
    for hit, reason in checks:
        if hit:
            return f"{ip} is {reason}"
    return None


class NetGuard:
    def __init__(self, allow_private: bool = False, resolver: Resolver | None = None):
        self.allow_private = allow_private
        self.resolver: Resolver = resolver or system_resolver

    # -- URL level ------------------------------------------------------------------------------------------
    def check_static(self, url: str | httpx.URL) -> httpx.URL:
        """Scheme, port and literal-address checks (no DNS). Returns the parsed URL."""
        try:
            u = url if isinstance(url, httpx.URL) else httpx.URL(url)
        except Exception as exc:
            raise BlockedFetch(str(url), f"not a valid URL ({exc})") from exc
        if u.scheme not in ALLOWED_SCHEMES:
            raise BlockedFetch(str(url), f"only http and https URLs are fetched, not {u.scheme or 'no scheme'}")
        host = u.raw_host.decode("ascii", "replace")
        if not host:
            raise BlockedFetch(str(url), "the URL has no host")
        if self.allow_private:
            return u
        name = host.lower().rstrip(".")
        if name == "localhost" or name.endswith((".localhost", ".local", ".internal")):
            raise BlockedFetch(str(url), f"{host} is a local host name; {HINT}")
        ip = ip_literal(host)
        if ip is not None:
            reason = blocked_reason(ip)
            if reason:
                raise BlockedFetch(str(url), f"{reason}; {HINT}")
        if u.port not in DEFAULT_PORTS:
            raise BlockedFetch(str(url), f"port {u.port} is not allowed (only 80 and 443); {HINT}")
        return u

    def check(self, url: str | httpx.URL) -> httpx.URL:
        """Full check: static rules plus every address the host resolves to."""
        u = self.check_static(url)
        if not self.allow_private:
            host = u.raw_host.decode("ascii", "replace")
            self.addresses(host, u.port or (443 if u.scheme == "https" else 80), url=str(url))
        return u

    # -- address level ------------------------------------------------------------------------------------------
    def addresses(self, host: str, port: int, url: str | None = None) -> list[str]:
        """The vetted addresses for host (all of them must be public, or the host is refused)."""
        literal = ip_literal(host)
        ips = [str(literal)] if literal is not None else self.resolver(host, port)
        if not ips:
            raise httpcore.ConnectError(f"cannot resolve {host}")
        if not self.allow_private:
            for raw in ips:
                ip = ip_literal(raw)
                reason = blocked_reason(ip) if ip is not None else f"{raw} is not an IP address"
                if reason:
                    raise BlockedFetch(url or host, f"{host} resolves to {raw}, which is not allowed ({reason}); {HINT}")
        return ips


class GuardedBackend(httpcore.NetworkBackend):
    """Resolves the host itself, refuses non-public addresses, connects to the vetted IP and re-checks the peer."""

    def __init__(self, guard: NetGuard, inner: httpcore.NetworkBackend | None = None):
        self.guard = guard
        self.inner = inner or httpcore.SyncBackend()

    def connect_tcp(self, host: str, port: int, timeout: float | None = None, local_address: str | None = None,
                    socket_options: Iterable | None = None) -> httpcore.NetworkStream:
        last: Exception | None = None
        for ip in self.guard.addresses(host, port):
            try:
                stream = self.inner.connect_tcp(ip, port, timeout=timeout, local_address=local_address,
                                                socket_options=socket_options)
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                last = exc
                continue
            peer = stream.get_extra_info("server_addr")
            peer_ip = ip_literal(str(peer[0])) if peer else None
            if not self.guard.allow_private and (peer_ip is None or blocked_reason(peer_ip)):
                stream.close()
                raise BlockedFetch(host, f"connected peer {peer} is not a public address; {HINT}")
            return stream
        raise last or httpcore.ConnectError(f"cannot connect to {host}")

    def connect_unix_socket(self, path: str, timeout: float | None = None, socket_options: Iterable | None = None):
        raise BlockedFetch(path, "unix sockets are never fetched")

    def sleep(self, seconds: float) -> None:
        self.inner.sleep(seconds)


def guarded_transport(guard: NetGuard) -> httpx.HTTPTransport:
    """The production transport: httpx's own, with every TCP connect going through the guard."""
    transport = httpx.HTTPTransport()
    pool = transport._pool  # httpcore.ConnectionPool; connections read the backend when they are created
    pool._network_backend = GuardedBackend(guard)
    return transport
