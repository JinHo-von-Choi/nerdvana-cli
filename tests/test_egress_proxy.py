"""The egress proxy: the domain matcher, the address check, credential injection and refusals.

Everything runs against fake upstream servers on loopback; nothing touches the network.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from nerdvana_cli.core import egress_proxy
from nerdvana_cli.core.agent_scope import apply_write_scope
from nerdvana_cli.core.config.egress_rules import ProxyCredential, normalize_pattern
from nerdvana_cli.core.config.settings import NerdvanaSettings, SettingsLoadError
from nerdvana_cli.core.egress_proxy import (
    EgressProxy,
    PreparedLaunch,
    host_allowed,
    host_matches,
    is_public_address,
    parse_request,
    split_host_port,
)
from nerdvana_cli.core.sandbox import Launch, SandboxPolicy, plan_launch, proxy_environment
from nerdvana_cli.core.signals import EGRESS_DENIED, classify_result


class FakeUpstream:
    """An HTTP server on loopback that records the request heads it receives and answers 200 ``ok``."""

    def __init__(self) -> None:
        self.heads: list[str] = []
        self.port = 0
        self._server: asyncio.Server | None = None

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._serve, "127.0.0.1", 0)
        self.port    = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        assert self._server is not None
        self._server.close()
        await self._server.wait_closed()

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        head = await reader.readuntil(b"\r\n\r\n")
        self.heads.append(head.decode("latin-1"))
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok")
        await writer.drain()
        writer.close()


@pytest.fixture
async def upstream() -> AsyncIterator[FakeUpstream]:
    fake = FakeUpstream()
    await fake.start()
    yield fake
    await fake.stop()


async def _started(domains: list[str], **kwargs: object) -> EgressProxy:
    proxy = EgressProxy(domains, **kwargs)  # type: ignore[arg-type]
    await proxy.start()
    return proxy


def _auth(proxy: EgressProxy) -> str:
    token = proxy.url.split("nerdvana:")[1].split("@")[0]
    return "Proxy-Authorization: Basic " + base64.b64encode(f"nerdvana:{token}".encode()).decode() + "\r\n"


async def _exchange(proxy: EgressProxy, request: str, *, authorized: bool = True) -> str:
    """Send *request* to the proxy (with its password unless told not to) and return everything it answers."""
    reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
    head, _, rest = request.partition("\r\n")
    writer.write((head + "\r\n" + (_auth(proxy) if authorized else "") + rest).encode())
    await writer.drain()
    answer = await asyncio.wait_for(reader.read(), 10)
    writer.close()
    return answer.decode("latin-1")


def _get(host: str, port: int, extra: str = "") -> str:
    return f"GET http://{host}:{port}/path?q=1 HTTP/1.1\r\nHost: {host}:{port}\r\n{extra}\r\n"


# ---------------------------------------------------------------------------
# The matcher
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("host", "pattern", "expected"), [
    ("example.com",         "example.com",   True),
    ("EXAMPLE.com.",        "example.com",   True),
    ("www.example.com",     "example.com",   False),
    ("api.example.com",     "*.example.com", True),
    ("a.b.example.com",     "*.example.com", True),
    ("example.com",         "*.example.com", False),
    ("badexample.com",      "*.example.com", False),
    ("example.com.evil.io", "*.example.com", False),
    ("1.2.3.4",             "*.3.4",         False),
    ("[::1]",               "::1",           True),
])
def test_host_matching(host: str, pattern: str, expected: bool) -> None:
    assert host_matches(host, pattern) is expected


def test_host_allowed_takes_any_pattern() -> None:
    assert host_allowed("pypi.org", ["github.com", "pypi.org"])
    assert not host_allowed("evil.org", ["github.com", "pypi.org"])
    assert not host_allowed("anything.org", [])


@pytest.mark.parametrize("entry", ["https://example.com", "example.com:443", "example.com/path", "*", "*.", "a b", "exa*mple.com", ""])
def test_malformed_patterns_are_rejected(entry: str) -> None:
    with pytest.raises(ValueError, match="not a host name"):
        normalize_pattern(entry)


def test_patterns_are_normalized() -> None:
    assert normalize_pattern(" Example.COM. ") == "example.com"
    assert normalize_pattern("*.PyPI.org") == "*.pypi.org"
    assert normalize_pattern("127.0.0.1") == "127.0.0.1"


@pytest.mark.parametrize(("address", "public"), [
    ("93.184.216.34",        True),
    ("2606:2800:220:1::1",   True),
    ("127.0.0.1",            False),
    ("::1",                  False),
    ("10.1.2.3",             False),
    ("192.168.0.9",          False),
    ("172.16.5.5",           False),
    ("169.254.169.254",      False),
    ("100.64.0.1",           False),
    ("0.0.0.0",              False),  # noqa: S104 - an address under test, not a bind
    ("224.0.0.1",            False),
    ("::ffff:127.0.0.1",     False),
    ("fe80::1%eth0",         False),
])
def test_only_public_addresses_count_as_public(address: str, public: bool) -> None:
    assert is_public_address(address) is public


def test_request_parsing_and_targets() -> None:
    request = parse_request(b"CONNECT example.com:443 HTTP/1.1\r\nHost: example.com:443\r\nX-A:  b \r\n\r\n")
    assert (request.method, request.target, request.header("x-a")) == ("CONNECT", "example.com:443", "b")
    assert split_host_port("example.com", 443) == ("example.com", 443)
    assert split_host_port("[::1]:8080", 443) == ("::1", 8080)
    for bad in (b"GARBAGE\r\n\r\n", b"GET / HTTP/1.1\r\nbroken header\r\n\r\n"):
        with pytest.raises(ValueError, match="malformed"):
            parse_request(bad)
    with pytest.raises(ValueError, match="malformed"):
        split_host_port("example.com:abc", 443)


# ---------------------------------------------------------------------------
# Forwarding, refusals and the address check
# ---------------------------------------------------------------------------


async def test_an_allowed_host_is_forwarded_as_an_origin_request(upstream: FakeUpstream) -> None:
    proxy = await _started(["127.0.0.1"])
    try:
        answer = await _exchange(proxy, _get("127.0.0.1", upstream.port, "Proxy-Connection: keep-alive\r\nX-Keep: yes\r\n"))
    finally:
        await proxy.stop()
    assert answer.startswith("HTTP/1.1 200 OK") and answer.endswith("ok")
    head = upstream.heads[0]
    assert head.startswith("GET /path?q=1 HTTP/1.1\r\n")
    assert f"Host: 127.0.0.1:{upstream.port}" in head
    assert "X-Keep: yes" in head and "Connection: close" in head
    assert "Proxy-" not in head
    assert proxy.denied_total == 0


async def test_a_host_outside_the_list_gets_403_and_is_counted(upstream: FakeUpstream) -> None:
    proxy = await _started(["allowed.example"])
    try:
        answer = await _exchange(proxy, _get("127.0.0.1", upstream.port))
        tunnel = await _exchange(proxy, "CONNECT blocked.example:443 HTTP/1.1\r\nHost: blocked.example:443\r\n\r\n")
    finally:
        await proxy.stop()
    assert answer.startswith("HTTP/1.1 403 Forbidden") and "sandbox.allowed_domains" in answer
    assert tunnel.startswith("HTTP/1.1 403 Forbidden")
    assert upstream.heads == []
    assert proxy.denied_total == 2
    assert list(proxy.recent_denials) == [f"127.0.0.1:{upstream.port}", "blocked.example:443"]


async def test_the_denial_note_carries_the_egress_denied_signal(upstream: FakeUpstream) -> None:
    proxy    = await _started([])
    prepared = PreparedLaunch(Launch(), proxy, proxy.denied_total)
    try:
        assert prepared.denial_note() == ""
        for _ in range(3):
            await _exchange(proxy, _get("127.0.0.1", upstream.port))
    finally:
        await proxy.stop()
    note = prepared.denial_note()
    assert "[egress denied: 3 connection(s) to " in note
    assert classify_result("output" + note, False) == [EGRESS_DENIED] * 3
    assert classify_result("no note here", False) == []


async def test_a_name_that_resolves_to_a_private_address_is_refused(upstream: FakeUpstream) -> None:
    async def rebinding(host: str, port: int) -> list[str]:
        return ["127.0.0.1"]

    async def mixed(host: str, port: int) -> list[str]:
        return ["93.184.216.34", "10.0.0.5"]

    for resolver in (rebinding, mixed):
        proxy = await _started(["rebind.example", "*.wild.example"], resolver=resolver)
        try:
            direct = await _exchange(proxy, _get("rebind.example", upstream.port))
            wild   = await _exchange(proxy, _get("sub.wild.example", upstream.port))
            tunnel = await _exchange(proxy, f"CONNECT rebind.example:{upstream.port} HTTP/1.1\r\nHost: x\r\n\r\n")
        finally:
            await proxy.stop()
        assert all(answer.startswith("HTTP/1.1 403") and "non-public" in answer for answer in (direct, wild, tunnel))
        assert proxy.denied_total == 3
    assert upstream.heads == []


async def test_the_connection_goes_to_the_address_that_was_checked(upstream: FakeUpstream) -> None:
    """A literal entry in the list names its own address; the resolver is not consulted for the connection target."""
    seen: list[str] = []

    async def resolver(host: str, port: int) -> list[str]:
        seen.append(host)
        return [host]

    proxy = await _started(["127.0.0.1"], resolver=resolver)
    try:
        answer = await _exchange(proxy, _get("127.0.0.1", upstream.port))
    finally:
        await proxy.stop()
    assert answer.startswith("HTTP/1.1 200") and seen == ["127.0.0.1"]


async def test_localhost_is_allowed_only_when_listed_and_resolving_to_loopback(upstream: FakeUpstream) -> None:
    async def loopback(host: str, port: int) -> list[str]:
        return ["127.0.0.1"]

    async def elsewhere(host: str, port: int) -> list[str]:
        return ["10.0.0.1"]

    listed = await _started(["localhost"], resolver=loopback)
    unlisted = await _started(["*.example"], resolver=loopback)
    tricked = await _started(["localhost"], resolver=elsewhere)
    try:
        assert (await _exchange(listed, _get("localhost", upstream.port))).startswith("HTTP/1.1 200")
        assert (await _exchange(unlisted, _get("localhost", upstream.port))).startswith("HTTP/1.1 403")
        assert (await _exchange(tricked, _get("localhost", upstream.port))).startswith("HTTP/1.1 403")
    finally:
        for proxy in (listed, unlisted, tricked):
            await proxy.stop()


async def test_a_request_without_the_session_password_gets_407(upstream: FakeUpstream) -> None:
    proxy = await _started(["127.0.0.1"])
    try:
        answer = await _exchange(proxy, _get("127.0.0.1", upstream.port), authorized=False)
    finally:
        await proxy.stop()
    assert answer.startswith("HTTP/1.1 407") and "Proxy-Authenticate" in answer
    assert upstream.heads == [] and proxy.denied_total == 0


async def test_origin_form_and_garbage_requests_get_400() -> None:
    proxy = await _started(["127.0.0.1"])
    try:
        origin  = await _exchange(proxy, "GET /plain HTTP/1.1\r\nHost: x\r\n\r\n")
        garbage = await _exchange(proxy, "NOT HTTP\r\n\r\n")
    finally:
        await proxy.stop()
    assert origin.startswith("HTTP/1.1 400") and garbage.startswith("HTTP/1.1 400")


async def test_a_connect_tunnel_relays_bytes_without_adding_anything() -> None:
    received: list[bytes] = []

    async def echo(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        received.append(await reader.read(5))
        writer.write(b"pong!")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(echo, "127.0.0.1", 0)
    port   = server.sockets[0].getsockname()[1]
    proxy  = await _started(["127.0.0.1"], credentials=[ProxyCredential.parse("127.0.0.1", "TOKEN")], environ={"TOKEN": "secret-value"})
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
        writer.write(f"CONNECT 127.0.0.1:{port} HTTP/1.1\r\nHost: x\r\n{_auth(proxy)}\r\n".encode())
        await writer.drain()
        assert (await reader.readuntil(b"\r\n\r\n")).startswith(b"HTTP/1.1 200")
        writer.write(b"ping!")
        await writer.drain()
        assert await asyncio.wait_for(reader.read(), 10) == b"pong!"
        writer.close()
    finally:
        await proxy.stop()
        server.close()
        await server.wait_closed()
    assert received == [b"ping!"]


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------


def test_credential_specs() -> None:
    bearer = ProxyCredential.parse("api.example.com", "API_TOKEN")
    assert bearer.header_pair({"API_TOKEN": "abc"}) == ("Authorization", "Bearer abc")
    raw = ProxyCredential.parse("api.example.com", "X-Api-Key:API_TOKEN")
    assert raw.header_pair({"API_TOKEN": "abc"}) == ("X-Api-Key", "abc")
    assert raw.header_pair({}) is None
    assert raw.header_pair({"API_TOKEN": "a\r\nInjected: 1"}) is None
    for bad in ("", "has space", "Bad Header:VAR", "1BAD", "X:1BAD"):
        with pytest.raises(ValueError, match="must be VARIABLE|not a header name"):
            ProxyCredential.parse("d.example", bad)


async def test_the_proxy_adds_the_credential_and_replaces_the_one_the_client_sent(upstream: FakeUpstream) -> None:
    credentials = [ProxyCredential.parse("127.0.0.1", "UPSTREAM_TOKEN")]
    proxy       = await _started(["127.0.0.1"], credentials=credentials, environ={"UPSTREAM_TOKEN": "tok-123"})
    try:
        answer = await _exchange(proxy, _get("127.0.0.1", upstream.port, "authorization: Bearer forged\r\n"))
    finally:
        await proxy.stop()
    assert answer.startswith("HTTP/1.1 200")
    head = upstream.heads[0]
    assert head.count("Authorization: Bearer tok-123") == 1
    assert "forged" not in head
    assert "tok-123" not in proxy.url


async def test_a_raw_header_credential_is_sent_as_it_is(upstream: FakeUpstream) -> None:
    proxy = await _started(["127.0.0.1"], credentials=[ProxyCredential.parse("127.0.0.1", "X-Api-Key:KEY_VALUE")], environ={"KEY_VALUE": "k-1"})
    try:
        await _exchange(proxy, _get("127.0.0.1", upstream.port))
    finally:
        await proxy.stop()
    assert "X-Api-Key: k-1" in upstream.heads[0] and "Authorization" not in upstream.heads[0]


async def test_a_credential_is_added_only_for_its_own_domain(upstream: FakeUpstream) -> None:
    credentials = [ProxyCredential.parse("*.api.example", "KEY_VALUE"), ProxyCredential.parse("other.example", "KEY_VALUE")]
    proxy       = await _started(["127.0.0.1"], credentials=credentials, environ={"KEY_VALUE": "k-1"})
    try:
        await _exchange(proxy, _get("127.0.0.1", upstream.port))
    finally:
        await proxy.stop()
    assert "k-1" not in upstream.heads[0]


async def test_an_unset_credential_variable_stops_the_request_before_it_leaves(upstream: FakeUpstream) -> None:
    credentials = [ProxyCredential.parse("127.0.0.1", "MISSING_VARIABLE")]
    proxy       = await _started(["127.0.0.1"], credentials=credentials, environ={})
    try:
        answer = await _exchange(proxy, _get("127.0.0.1", upstream.port))
    finally:
        await proxy.stop()
    assert answer.startswith("HTTP/1.1 502") and "MISSING_VARIABLE" in answer
    assert upstream.heads == []


# ---------------------------------------------------------------------------
# Policy, planning and settings
# ---------------------------------------------------------------------------


def test_the_launcher_may_connect_to_the_proxy_port_only(monkeypatch: pytest.MonkeyPatch) -> None:
    from nerdvana_cli.core import sandbox

    monkeypatch.setattr(sandbox, "landlock_abi", lambda: 4)
    policy = SandboxPolicy("require", False, allowed_domains=("example.com",))
    launch = plan_launch(policy, "curl x", "/work", "http://nerdvana:t0k@127.0.0.1:41234")
    assert launch.argv is not None
    assert launch.argv[launch.argv.index("--connect-port") + 1] == "41234"
    assert "--no-network" in launch.argv
    assert launch.env["HTTPS_PROXY"] == launch.env["https_proxy"] == "http://nerdvana:t0k@127.0.0.1:41234"
    assert launch.env["NO_PROXY"] == "" and launch.env["ALL_PROXY"] == launch.env["HTTP_PROXY"]

    without = plan_launch(policy, "curl x", "/work")
    assert without.argv is not None and "--no-network" in without.argv and "--connect-port" not in without.argv
    assert without.env == {}

    monkeypatch.setattr(sandbox, "landlock_abi", lambda: 3)
    assert plan_launch(policy, "curl x", "/work", "http://127.0.0.1:1").refused
    assert proxy_environment("http://h:1")["HTTP_PROXY"] == "http://h:1"


async def test_no_proxy_is_started_unless_the_policy_and_the_kernel_need_one(monkeypatch: pytest.MonkeyPatch) -> None:
    from nerdvana_cli.core import sandbox

    monkeypatch.setattr(sandbox, "landlock_abi", lambda: 4)
    for policy in (None, SandboxPolicy("off", False, allowed_domains=("a.example",)), SandboxPolicy("auto", True)):
        assert (await egress_proxy.prepare_launch(policy, "ls", "/work")).proxy is None
    monkeypatch.setattr(sandbox, "landlock_abi", lambda: 3)
    assert (await egress_proxy.prepare_launch(SandboxPolicy("auto", False, allowed_domains=()), "ls", "/work")).proxy is None
    monkeypatch.setattr(sandbox, "landlock_abi", lambda: 4)
    try:
        policy   = SandboxPolicy("auto", False, allowed_domains=("a.example",))
        first    = await egress_proxy.prepare_launch(policy, "ls", "/work")
        second   = await egress_proxy.prepare_launch(policy, "ls", "/work")
        assert first.proxy is second.proxy and first.proxy is not None and first.proxy.port > 0
        assert first.launch.env["HTTP_PROXY"] == first.proxy.url
    finally:
        await egress_proxy.close_proxies()


def _load(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str) -> NerdvanaSettings:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    config = tmp_path / "nerdvana.yml"
    config.write_text(text, encoding="utf-8")
    return NerdvanaSettings.load(str(config))


def test_the_allowlist_settings_make_a_policy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _load(tmp_path, monkeypatch, (
        "sandbox:\n  mode: require\n  network: allowlist\n  allowed_domains: [PyPI.org, '*.github.com']\n"
        "secrets:\n  proxy_credentials:\n    api.github.com: GITHUB_TOKEN\n    '*.example.com': 'X-Key:EXAMPLE_KEY'\n"
    ))
    policy = SandboxPolicy.from_config(settings.sandbox, settings.secrets.proxy_credentials)
    assert policy.network is False
    assert policy.allowed_domains == ("pypi.org", "*.github.com")
    assert policy.credentials == (("api.github.com", "GITHUB_TOKEN"), ("*.example.com", "X-Key:EXAMPLE_KEY"))
    plain = SandboxPolicy.from_config(NerdvanaSettings().sandbox, {"a.example": "TOKEN"})
    assert plain.allowed_domains is None and plain.credentials == () and plain.network is True


@pytest.mark.parametrize("text", [
    "sandbox:\n  allowed_domains: ['https://example.com']\n",
    "sandbox:\n  network: sometimes\n",
    "secrets:\n  proxy_credentials: {example.com: 'bad spec'}\n",
    "secrets:\n  proxy_credentials: {'http://example.com': TOKEN}\n",
])
def test_bad_egress_settings_stop_startup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str) -> None:
    with pytest.raises(SettingsLoadError):
        _load(tmp_path, monkeypatch, text)


def test_the_secrets_section_cannot_be_overridden_from_the_command_line() -> None:
    from nerdvana_cli.core.config.settings import apply_settings_overrides

    with pytest.raises(ValueError, match="cannot change"):
        apply_settings_overrides(NerdvanaSettings(), ["secrets.proxy_credentials={a.example: TOKEN}"])


def test_an_agent_cannot_widen_an_allowlist_but_can_narrow_it() -> None:
    class Definition:
        write_scope = ""
        network     = True

    settings = NerdvanaSettings()
    settings.sandbox.network = "allowlist"
    assert apply_write_scope(settings, Definition()) is False and settings.sandbox.network == "allowlist"
    Definition.network = False  # type: ignore[assignment]
    assert apply_write_scope(settings, Definition()) is True and settings.sandbox.network is False


def test_the_doctor_reports_the_egress_mode() -> None:
    settings = NerdvanaSettings()
    assert egress_proxy.describe_egress(settings.sandbox, settings.secrets) == "open"
    settings.sandbox.network = False
    assert egress_proxy.describe_egress(settings.sandbox, settings.secrets) == "refused"
    settings.sandbox.network           = "allowlist"
    settings.sandbox.allowed_domains   = ["a.example", "*.b.example"]
    settings.secrets.proxy_credentials = {"a.example": "TOKEN"}
    assert egress_proxy.describe_egress(settings.sandbox, settings.secrets) == "allowlist (2 domain(s), 1 proxy credential(s))"
