"""Security-focused tests for the MCP client (TLS, response caps, transport warnings)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from unittest.mock import patch

import httpx2
import pytest

from nerdvana_cli.mcp.client import _MAX_RESPONSE_BYTES, McpClient
from nerdvana_cli.mcp.config import McpServerConfig
from nerdvana_cli.mcp.transport import ResponseTooLargeError, _CappedTransport, capped_http_client


def _make_http_config(
    name: str = "test-http",
    url: str  = "https://example.com/mcp",
) -> McpServerConfig:
    return McpServerConfig(
        name=name,
        transport="http",
        url=url,
    )


class TestTlsVerifyExplicit:
    """The HTTP transport must be created with TLS verification on."""

    def test_http_transport_constructed_with_verify_true(self) -> None:
        with patch("httpx2.AsyncHTTPTransport") as ctor:
            capped_http_client({})

        ctor.assert_called_once()
        assert ctor.call_args.kwargs.get("verify") is True, (
            "the HTTP transport must be created with verify=True for TLS safety"
        )

    def test_client_headers_carry_the_configured_auth(self) -> None:
        client = capped_http_client({"Authorization": "Bearer k"})

        assert client.headers["authorization"] == "Bearer k"


def _client_over(handler: Callable[[httpx2.Request], httpx2.Response], limit: int) -> httpx2.AsyncClient:
    transport = _CappedTransport(httpx2.MockTransport(handler), limit)
    return httpx2.AsyncClient(transport=transport)


class TestHttpResponseSizeCap:
    """HTTP response bodies larger than the limit must raise instead of being buffered."""

    @pytest.mark.asyncio
    async def test_oversized_http_response_raises(self) -> None:
        body   = b"x" * (_MAX_RESPONSE_BYTES + 1)
        client = _client_over(lambda request: httpx2.Response(200, content=body), _MAX_RESPONSE_BYTES)

        with pytest.raises(ResponseTooLargeError, match="exceeds"):
            await client.post("https://example.com/mcp", json={})

    @pytest.mark.asyncio
    async def test_oversized_declared_length_is_refused_before_reading(self) -> None:
        response = httpx2.Response(200, headers={"content-length": str(_MAX_RESPONSE_BYTES + 1)}, content=b"x")
        client   = _client_over(lambda request: response, _MAX_RESPONSE_BYTES)

        with pytest.raises(ResponseTooLargeError, match="exceeds"):
            await client.post("https://example.com/mcp", json={})

    @pytest.mark.asyncio
    async def test_normal_sized_http_response_succeeds(self) -> None:
        """A response well under the cap should parse normally."""
        body   = b'{"jsonrpc":"2.0","id":1,"result":{"ok":true}}'
        client = _client_over(lambda request: httpx2.Response(200, content=body), _MAX_RESPONSE_BYTES)

        response = await client.post("https://example.com/mcp", json={})

        assert response.json()["result"] == {"ok": True}


class TestSseResponseSizeCap:
    """An event stream is not capped as a whole, because it lasts as long as the server keeps it open."""

    @pytest.mark.asyncio
    async def test_event_stream_longer_than_the_limit_is_delivered(self) -> None:
        events = b'data: {"jsonrpc":"2.0","id":1,"result":{"value":42}}\n\n' * 20
        client = _client_over(
            lambda request: httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=events),
            100,
        )

        response = await client.post("https://example.com/mcp", json={})

        assert response.content == events

    def test_single_event_size_is_capped_at_the_message_limit(self) -> None:
        with patch("nerdvana_cli.mcp.client.streamable_http_client") as transport:
            McpClient(_make_http_config())._build_client()

        assert transport.call_args.kwargs["max_sse_event_size"] == _MAX_RESPONSE_BYTES


class TestInsecureTransportWarning:
    """`http://` URLs to non-local hosts must emit a WARNING; loopback is exempt."""

    def _connect_http_dry_run(
        self, client: McpClient, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Run the URL classification path without performing real I/O."""
        caplog.set_level(logging.WARNING, logger="nerdvana_cli.mcp.client")
        client._warn_if_insecure_transport(client._config.url)

    def test_http_remote_host_logs_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        client = McpClient(_make_http_config(url="http://example.com/mcp"))
        self._connect_http_dry_run(client, caplog)

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("insecure http://" in r.getMessage() for r in warnings), (
            f"expected insecure-transport warning, got: {[r.getMessage() for r in warnings]}"
        )

    def test_https_remote_host_no_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        client = McpClient(_make_http_config(url="https://example.com/mcp"))
        self._connect_http_dry_run(client, caplog)

        for r in caplog.records:
            assert "insecure http://" not in r.getMessage()

    @pytest.mark.parametrize(
        "url",
        [
            "http://localhost:8080/mcp",
            "http://127.0.0.1:8080/mcp",
            "http://[::1]:8080/mcp",
        ],
    )
    def test_http_localhost_no_warning(
        self, url: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        client = McpClient(_make_http_config(url=url))
        self._connect_http_dry_run(client, caplog)

        for r in caplog.records:
            assert "insecure http://" not in r.getMessage(), (
                f"loopback host {url!r} must not trigger warning"
            )


class TestStdioLineCapBoundary:
    """Sanity-check the constant exists and is the documented 10 MB."""

    def test_constant_value(self) -> None:
        assert _MAX_RESPONSE_BYTES == 10 * 1024 * 1024

    def test_constant_exposed_to_caller(self) -> None:
        """Importable from the module so other tools can share it."""
        from nerdvana_cli.mcp import client as client_module

        assert hasattr(client_module, "_MAX_RESPONSE_BYTES")
