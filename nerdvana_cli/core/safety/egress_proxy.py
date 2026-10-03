"""A local forward proxy that lets a confined command reach only the domains it is allowed to.

Author: 최진호
Date:   2026-10-03

Landlock can refuse TCP connections by port but not by host name. With ``sandbox.network: allowlist``
the confined command may therefore connect to one port only, that of this proxy, and the proxy decides
which hosts the command may reach. It listens on 127.0.0.1 on an ephemeral port, one proxy per set of
domains and credentials in this process, and answers HTTP forward requests and ``CONNECT`` tunnels.

A request is refused with ``403`` unless its host matches ``sandbox.allowed_domains`` (an exact name, or
``*.suffix`` for any subdomain of the suffix). The name is resolved here, and the connection goes to the
address that was checked, so a name that later resolves elsewhere cannot be used to reach a private
network: an address that is loopback, private, link-local or otherwise not public is refused. The one
exception is a host written as an IP address, or ``localhost``, as an exact entry of the domain list.

``secrets.proxy_credentials`` maps a domain to an environment variable of this process; the proxy adds
that credential to a plain HTTP request for the domain, so the token never enters the command's
environment. A ``CONNECT`` tunnel carries TLS the proxy cannot read, so it cannot add a header to it.

Other local processes cannot borrow the proxy: it asks for a random per-session password, which is part
of the proxy URL handed to the command.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import hmac
import ipaddress
import logging
import os
import secrets as token_source
import socket
from collections import deque
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from nerdvana_cli.core.config.egress_rules import ProxyCredential, normalize_host, normalize_pattern
from nerdvana_cli.core.safety import sandbox
from nerdvana_cli.core.safety.sandbox import Launch, SandboxPolicy, plan_launch

logger = logging.getLogger(__name__)

_HEAD_LIMIT        = 65536
_HEAD_TIMEOUT      = 30.0
_CONNECT_TIMEOUT   = 15.0
_CHUNK             = 65536
_HOP_BY_HOP        = frozenset({"proxy-authorization", "proxy-connection", "connection", "keep-alive"})
_RECENT_DENIALS    = 50

Resolver = Callable[[str, int], Awaitable[list[str]]]


class RefusalError(Exception):
    """A request the proxy answers with an error status instead of forwarding."""

    def __init__(self, status: int, reason: str) -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason


@dataclass(frozen=True)
class Request:
    """The request line and headers of one proxied request."""

    method:  str
    target:  str
    headers: list[tuple[str, str]]

    def header(self, name: str) -> str:
        """The value of the first header called *name* (case-insensitive), empty when absent."""
        lowered = name.lower()
        return next((value for key, value in self.headers if key.lower() == lowered), "")


def host_matches(host: str, pattern: str) -> bool:
    """True when *host* is *pattern*, or a subdomain of the suffix when the pattern is ``*.suffix``."""
    host = normalize_host(host)
    if pattern.startswith("*."):
        return host.endswith(pattern[1:]) and len(host) > len(pattern) - 1 and not _is_literal(host)
    return host == pattern


def host_allowed(host: str, patterns: Iterable[str]) -> bool:
    """True when any of *patterns* lets *host* through."""
    return any(host_matches(host, pattern) for pattern in patterns)


def is_public_address(address: str) -> bool:
    """True for an address on the public internet: not loopback, private, link-local, shared, reserved or multicast."""
    ip = ipaddress.ip_address(address.split("%")[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def _is_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(normalize_host(host))
    except ValueError:
        return False
    return True


def parse_request(head: bytes) -> Request:
    """Parse the request line and headers of *head*; ValueError when they are malformed."""
    lines = head.decode("latin-1").split("\r\n")
    parts = lines[0].split(" ")
    if len(parts) != 3 or not parts[2].startswith("HTTP/1."):
        raise ValueError("malformed request line")
    headers = []
    for line in lines[1:]:
        if not line:
            continue
        name, colon, value = line.partition(":")
        if not colon or not name or name != name.strip():
            raise ValueError("malformed header")
        headers.append((name, value.strip()))
    return Request(parts[0].upper(), parts[1], headers)


def split_host_port(target: str, default_port: int) -> tuple[str, int]:
    """Split ``host``, ``host:port`` or ``[v6]:port`` into the host and the port; ValueError when malformed."""
    if target.startswith("["):
        host, _, rest = target[1:].partition("]")
        port = rest.removeprefix(":")
    else:
        host, _, port = target.partition(":")
    if not host or (port and not port.isdigit()) or not 0 < int(port or default_port) < 65536:
        raise ValueError(f"malformed host and port '{target}'")
    return host, int(port or default_port)


async def _system_resolver(host: str, port: int) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(str(info[4][0]) for info in infos))


async def _pipe(source: asyncio.StreamReader, sink: asyncio.StreamWriter) -> None:
    """Copy *source* to *sink* until the source ends, then pass the end on; an error closes the sink."""
    try:
        while chunk := await source.read(_CHUNK):
            sink.write(chunk)
            await sink.drain()
        if sink.can_write_eof():
            sink.write_eof()
    except OSError:
        sink.close()


class EgressProxy:
    """The proxy: start it once, hand ``url`` to the confined command, read ``denied_total`` afterwards."""

    def __init__(
        self,
        allowed_domains: Iterable[str],
        credentials:     Iterable[ProxyCredential] = (),
        *,
        environ:  Mapping[str, str] | None = None,
        resolver: Resolver | None          = None,
    ) -> None:
        self._domains     = tuple(normalize_pattern(entry) for entry in allowed_domains)
        self._credentials = tuple(credentials)
        self._environ     = environ if environ is not None else os.environ
        self._resolve     = resolver or _system_resolver
        self._token       = token_source.token_urlsafe(18)
        self._server: asyncio.Server | None = None
        self._loop:   asyncio.AbstractEventLoop | None = None
        self._writers: set[asyncio.StreamWriter] = set()
        self.denied_total = 0
        self.recent_denials: deque[str] = deque(maxlen=_RECENT_DENIALS)

    @property
    def port(self) -> int:
        """The port the proxy listens on; 0 before it has started."""
        return self._server.sockets[0].getsockname()[1] if self._server and self._server.sockets else 0

    @property
    def url(self) -> str:
        """The proxy URL for a command's ``HTTP_PROXY``, with the session password in it."""
        return f"http://nerdvana:{self._token}@127.0.0.1:{self.port}"

    def serves(self, loop: asyncio.AbstractEventLoop) -> bool:
        """True while the proxy is listening in *loop*."""
        return self._loop is loop and self._server is not None and self._server.is_serving()

    async def start(self) -> None:
        """Listen on 127.0.0.1 on a free port."""
        self._loop   = asyncio.get_running_loop()
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0, limit=_HEAD_LIMIT)

    async def stop(self) -> None:
        """Stop listening and close the connections still open."""
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        for writer in list(self._writers):
            writer.close()
        self._server = None

    # ------------------------------------------------------------------ requests

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._writers.add(writer)
        try:
            head    = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), _HEAD_TIMEOUT)
            request = parse_request(head)
            self._authenticate(request)
            if request.method == "CONNECT":
                await self._tunnel(request, reader, writer)
            else:
                await self._forward(request, reader, writer)
        except RefusalError as refusal:
            await self._respond(writer, refusal)
        except (ValueError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, TimeoutError):
            await self._respond(writer, RefusalError(400, "malformed request"))
        except OSError as exc:
            logger.debug("egress proxy connection ended: %s", exc)
        finally:
            self._writers.discard(writer)
            writer.close()

    def _authenticate(self, request: Request) -> None:
        scheme, _, encoded = request.header("Proxy-Authorization").partition(" ")
        try:
            given = base64.b64decode(encoded).decode("latin-1") if scheme.lower() == "basic" else ""
        except (binascii.Error, ValueError):
            given = ""
        if not hmac.compare_digest(given, f"nerdvana:{self._token}"):
            raise RefusalError(407, "proxy authentication required")

    async def _respond(self, writer: asyncio.StreamWriter, refusal: RefusalError) -> None:
        body    = f"nerdvana egress proxy: {refusal.reason}\n".encode()
        extra   = b'Proxy-Authenticate: Basic realm="nerdvana"\r\n' if refusal.status == 407 else b""
        phrase  = {400: "Bad Request", 403: "Forbidden", 407: "Proxy Authentication Required", 502: "Bad Gateway"}[refusal.status]
        head    = f"HTTP/1.1 {refusal.status} {phrase}\r\nContent-Type: text/plain\r\nContent-Length: {len(body)}\r\nConnection: close\r\n".encode()
        with contextlib.suppress(OSError):
            writer.write(head + extra + b"\r\n" + body)
            await writer.drain()

    def _deny(self, host: str, port: int, reason: str) -> RefusalError:
        """Count and log a refused connection; the returned refusal is raised by the caller."""
        self.denied_total += 1
        self.recent_denials.append(f"{host}:{port}")
        logger.warning("egress_denied %s:%d (%s)", host, port, reason)
        return RefusalError(403, f"connection to {host}:{port} refused ({reason})")

    async def _open_upstream(self, host: str, port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        """Connect to *host* if the allow list lets it through, checking the address it resolves to."""
        if not host_allowed(host, self._domains):
            raise self._deny(host, port, "host is not in sandbox.allowed_domains")
        try:
            addresses = await self._resolve(host, port)
        except OSError:
            raise RefusalError(502, f"cannot resolve {host}") from None
        problem = self._address_problem(host, addresses)
        if problem:
            raise self._deny(host, port, problem)
        for address in addresses:
            with contextlib.suppress(OSError, TimeoutError):
                return await asyncio.wait_for(asyncio.open_connection(address, port), _CONNECT_TIMEOUT)
        raise RefusalError(502, f"cannot connect to {host}:{port}")

    def _address_problem(self, host: str, addresses: list[str]) -> str:
        """Why the addresses *host* resolved to may not be used; empty when they may.

        Only public addresses are used, except for a host the domain list names exactly as an IP address (used as
        written) or as ``localhost`` (which must resolve to loopback).
        """
        name = normalize_host(host)
        if name in self._domains and _is_literal(name):
            return ""
        if name in self._domains and name == "localhost":
            loopback = all(ipaddress.ip_address(address.split("%")[0]).is_loopback for address in addresses)
            return "" if loopback else "localhost resolves to a non-loopback address"
        return "" if all(is_public_address(address) for address in addresses) else "resolves to a non-public address"

    async def _relay(
        self,
        client: tuple[asyncio.StreamReader, asyncio.StreamWriter],
        upstream: tuple[asyncio.StreamReader, asyncio.StreamWriter],
        *,
        until_response_ends: bool,
    ) -> None:
        """Copy both ways until the upstream is done (a forwarded request) or either side is (a tunnel)."""
        uplink   = asyncio.create_task(_pipe(client[0], upstream[1]))
        downlink = asyncio.create_task(_pipe(upstream[0], client[1]))
        try:
            await asyncio.wait({downlink} if until_response_ends else {uplink, downlink}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in (uplink, downlink):
                task.cancel()
            await asyncio.gather(uplink, downlink, return_exceptions=True)

    async def _tunnel(self, request: Request, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            host, port = split_host_port(request.target, 443)
        except ValueError:
            raise RefusalError(400, "malformed CONNECT target") from None
        up_reader, up_writer = await self._open_upstream(host, port)
        self._writers.add(up_writer)
        try:
            writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
            await writer.drain()
            await self._relay((reader, writer), (up_reader, up_writer), until_response_ends=False)
        finally:
            self._writers.discard(up_writer)
            up_writer.close()

    def _credential_header(self, host: str) -> tuple[str, str] | None:
        """The header to add for *host*, None when no credential is configured for it."""
        for credential in self._credentials:
            if host_matches(host, credential.domain):
                pair = credential.header_pair(self._environ)
                if pair is None:
                    raise RefusalError(502, f"the credential variable {credential.variable} for {host} is unset or unusable")
                return pair
        return None

    async def _forward(self, request: Request, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        url = urlsplit(request.target)
        if url.scheme != "http" or not url.hostname:
            raise RefusalError(400, "only absolute http:// requests and CONNECT are proxied")
        host, port  = url.hostname, url.port or 80
        added       = self._credential_header(host) if host_allowed(host, self._domains) else None
        up_reader, up_writer = await self._open_upstream(host, port)
        self._writers.add(up_writer)
        try:
            up_writer.write(self._outgoing_head(request, url.netloc.rpartition("@")[2], url.path or "/", url.query, added))
            await up_writer.drain()
            await self._relay((reader, writer), (up_reader, up_writer), until_response_ends=True)
        finally:
            self._writers.discard(up_writer)
            up_writer.close()

    @staticmethod
    def _outgoing_head(request: Request, netloc: str, path: str, query: str, added: tuple[str, str] | None) -> bytes:
        """The request as the origin server should see it: origin-form target, no proxy headers, one request per connection."""
        replaced = {added[0].lower()} if added else set()
        lines    = [f"{request.method} {path}{'?' + query if query else ''} HTTP/1.1", f"Host: {netloc}"]
        lines   += [f"{name}: {value}" for name, value in request.headers if name.lower() not in _HOP_BY_HOP | replaced | {"host"}]
        if added:
            lines.append(f"{added[0]}: {added[1]}")
        lines.append("Connection: close")
        return ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1")


def describe_egress(sandbox: Any, secrets: Any) -> str:
    """How the network of a confined command is configured, from the ``sandbox`` and ``secrets`` sections (for ``doctor``)."""
    if sandbox.network == "allowlist":
        return f"allowlist ({len(sandbox.allowed_domains)} domain(s), {len(secrets.proxy_credentials)} proxy credential(s))"
    return "open" if sandbox.network else "refused"


# ---------------------------------------------------------------------- per-process registry

_PROXIES: dict[tuple[tuple[str, ...], tuple[tuple[str, str], ...]], EgressProxy] = {}


async def ensure_proxy(policy: SandboxPolicy | None) -> EgressProxy | None:
    """The running proxy for *policy*, started on first use; None when the policy does not use one."""
    if policy is None or policy.mode == "off" or policy.allowed_domains is None:
        return None
    key  = (policy.allowed_domains, policy.credentials)
    loop = asyncio.get_running_loop()
    proxy = _PROXIES.get(key)
    if proxy is None or not proxy.serves(loop):
        proxy = EgressProxy(policy.allowed_domains, [ProxyCredential.parse(d, s) for d, s in policy.credentials])
        await proxy.start()
        _PROXIES[key] = proxy
    return proxy


async def close_proxies() -> None:
    """Stop every proxy this process started."""
    proxies = list(_PROXIES.values())
    _PROXIES.clear()
    for proxy in proxies:
        await proxy.stop()


@dataclass(frozen=True)
class PreparedLaunch:
    """A launch plan together with the proxy it goes through, for reporting what the proxy refused."""

    launch: Launch
    proxy:  EgressProxy | None = None
    mark:   int                = 0

    def denial_note(self) -> str:
        """A line naming the connections the proxy refused since the launch was prepared; empty when none."""
        if self.proxy is None or self.proxy.denied_total <= self.mark:
            return ""
        count = self.proxy.denied_total - self.mark
        hosts = ", ".join(dict.fromkeys(list(self.proxy.recent_denials)[-count:]))
        return f"\n[egress denied: {count} connection(s) to {hosts} refused by sandbox.allowed_domains]"


async def prepare_launch(policy: SandboxPolicy | None, command: str, cwd: str) -> PreparedLaunch:
    """Plan how to start *command* under *policy*, starting the egress proxy first when the policy needs it.

    This is the entry point for anything that spawns a process under the sandbox policy (the Bash tool, and
    a stdio MCP server through ``shlex.join(argv)`` as the command): add ``launch.env`` to the process
    environment, run ``launch.argv`` when it is set, and append ``denial_note()`` to what the caller reports.
    """
    proxy = await ensure_proxy(policy) if sandbox.landlock_abi() >= 4 else None
    return PreparedLaunch(plan_launch(policy, command, cwd, proxy.url if proxy else ""), proxy, proxy.denied_total if proxy else 0)
