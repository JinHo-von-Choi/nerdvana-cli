"""Security-focused tests for the MCP client (TLS, response caps, transport warnings)."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nerdvana_cli.mcp.client import _MAX_RESPONSE_BYTES, McpClient
from nerdvana_cli.mcp.config import McpServerConfig


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
    """`httpx.AsyncClient` must be constructed with `verify=True`."""

    @pytest.mark.asyncio
    async def test_http_client_constructed_with_verify_true(self) -> None:
        client = McpClient(_make_http_config())

        fake_async_client = MagicMock()
        fake_async_client.aclose = AsyncMock()

        with (
            patch("httpx.AsyncClient", return_value=fake_async_client) as ctor,
            patch.object(McpClient, "_send_request", new=AsyncMock(return_value={})),
            patch.object(
                McpClient, "_send_notification", new=AsyncMock(return_value=None)
            ),
        ):
            await client._connect_http()

        ctor.assert_called_once()
        kwargs = ctor.call_args.kwargs
        assert kwargs.get("verify") is True, (
            "httpx.AsyncClient must be created with verify=True for TLS safety"
        )


class TestHttpResponseSizeCap:
    """HTTP responses larger than `_MAX_RESPONSE_BYTES` must raise RuntimeError."""

    @pytest.mark.asyncio
    async def test_oversized_http_response_raises(self) -> None:
        client = McpClient(_make_http_config())

        oversized = b"x" * (_MAX_RESPONSE_BYTES + 1)

        fake_response = MagicMock()
        fake_response.content       = oversized
        fake_response.headers       = {"content-type": "application/json"}
        fake_response.raise_for_status = MagicMock(return_value=None)

        fake_http_client = MagicMock()
        fake_http_client.post = AsyncMock(return_value=fake_response)

        client._http_client = fake_http_client
        client._session_url = "https://example.com/mcp"

        with pytest.raises(RuntimeError, match="exceeds"):
            await client._http_send_request({"jsonrpc": "2.0", "id": 1, "method": "ping"})

    @pytest.mark.asyncio
    async def test_normal_sized_http_response_succeeds(self) -> None:
        """A response well under the cap should parse normally."""
        client = McpClient(_make_http_config())

        body = b'{"jsonrpc":"2.0","id":1,"result":{"ok":true}}'
        fake_response = MagicMock()
        fake_response.content       = body
        fake_response.headers       = {"content-type": "application/json"}
        fake_response.raise_for_status = MagicMock(return_value=None)
        fake_response.json          = MagicMock(
            return_value={"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}
        )

        fake_http_client = MagicMock()
        fake_http_client.post = AsyncMock(return_value=fake_response)

        client._http_client = fake_http_client
        client._session_url = "https://example.com/mcp"

        result = await client._http_send_request(
            {"jsonrpc": "2.0", "id": 1, "method": "ping"}
        )
        assert result == {"ok": True}


class TestSseResponseSizeCap:
    """`_parse_sse_response` rejects oversized text."""

    def test_oversized_sse_text_raises(self) -> None:
        client = McpClient(_make_http_config())
        oversized_text = "x" * (_MAX_RESPONSE_BYTES + 1)

        with pytest.raises(RuntimeError, match="exceeds"):
            client._parse_sse_response(oversized_text)

    def test_small_sse_text_parses(self) -> None:
        client  = McpClient(_make_http_config())
        payload = 'data: {"jsonrpc":"2.0","id":1,"result":{"value":42}}\n'
        result  = client._parse_sse_response(payload)
        assert result == {"value": 42}


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
