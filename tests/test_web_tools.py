"""Unit tests for WebFetchTool and WebSearchTool.

DNS resolution is patched via unittest.mock so no real network calls are made.
httpx.AsyncClient is mocked at the module level to intercept HTTP traffic.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nerdvana_cli.core.tool import ToolContext
from nerdvana_cli.tools.web_tools import (
    WebFetchArgs,
    WebFetchTool,
    WebSearchArgs,
    WebSearchTool,
    _check_url,
    _is_private_address,
)

_CTX = ToolContext(cwd=".")


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------

def _mock_http_response(
    status_code: int = 200,
    text: str        = "hello",
    content_type: str = "text/html",
    json_data: dict | None = None,
) -> MagicMock:
    """Return a mock that looks like an httpx.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.text        = text
    resp.headers     = {"content-type": content_type}

    if json_data is not None:
        resp.json = MagicMock(return_value=json_data)
    else:
        resp.json = MagicMock(return_value={})

    # raise_for_status raises only on 4xx/5xx
    if status_code >= 400:
        import httpx
        resp.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError(
                f"{status_code}",
                request=MagicMock(),
                response=MagicMock(
                    status_code=status_code,
                    text=text,
                ),
            )
        )
    else:
        resp.raise_for_status = MagicMock()

    return resp


def _make_client_cm(response: MagicMock) -> MagicMock:
    """Wrap a mock response in an async context manager mock."""
    client           = AsyncMock()
    client.get       = AsyncMock(return_value=response)
    cm               = AsyncMock()
    cm.__aenter__    = AsyncMock(return_value=client)
    cm.__aexit__     = AsyncMock(return_value=False)
    return cm


# ---------------------------------------------------------------------------
# _check_url / _is_private_address unit tests
# ---------------------------------------------------------------------------

class TestCheckUrl:
    def test_http_allowed(self):
        with patch("nerdvana_cli.tools.web_tools._is_private_address", return_value=False):
            assert _check_url("http://example.com/path") is None

    def test_https_allowed(self):
        with patch("nerdvana_cli.tools.web_tools._is_private_address", return_value=False):
            assert _check_url("https://example.com") is None

    def test_file_scheme_blocked(self):
        err = _check_url("file:///etc/passwd")
        assert err is not None
        assert "file" in err

    def test_ftp_scheme_blocked(self):
        err = _check_url("ftp://example.com")
        assert err is not None
        assert "ftp" in err

    def test_loopback_ip_literal_blocked(self):
        err = _check_url("http://127.0.0.1/secret")
        assert err is not None
        assert "private" in err.lower() or "loopback" in err.lower()

    def test_private_ip_literal_blocked(self):
        err = _check_url("http://192.168.1.1/admin")
        assert err is not None

    def test_10_block_blocked(self):
        err = _check_url("http://10.0.0.5/internal")
        assert err is not None

    def test_172_16_block_blocked(self):
        err = _check_url("http://172.16.0.1/")
        assert err is not None

    def test_ipv6_loopback_blocked(self):
        err = _check_url("http://[::1]/secret")
        assert err is not None

    def test_hostname_resolving_to_private_blocked(self):
        with patch("nerdvana_cli.tools.web_tools._is_private_address", return_value=True):
            err = _check_url("http://internal.corp/")
        assert err is not None

    def test_no_hostname_blocked(self):
        err = _check_url("http:///path")
        assert err is not None


class TestIsPrivateAddress:
    def test_localhost_is_private(self):
        # socket.getaddrinfo for "localhost" returns 127.0.0.1
        assert _is_private_address("localhost") is True

    def test_public_domain_not_private(self):
        # example.com → 93.184.216.34 (public); patch to avoid real DNS call
        with patch("socket.getaddrinfo", return_value=[
            (2, 1, 6, "", ("93.184.216.34", 0)),
        ]):
            assert _is_private_address("example.com") is False

    def test_unresolvable_returns_false(self):
        with patch("socket.getaddrinfo", side_effect=OSError("no such host")):
            assert _is_private_address("no-such-host.invalid") is False


# ---------------------------------------------------------------------------
# WebFetchTool
# ---------------------------------------------------------------------------

class TestWebFetchTool:
    def test_name_and_schema(self):
        tool = WebFetchTool()
        assert tool.name == "WebFetch"
        assert "url" in tool.input_schema["properties"]
        assert "url" in tool.input_schema["required"]

    def test_parse_args_defaults(self):
        tool = WebFetchTool()
        args = tool.parse_args({"url": "https://example.com"})
        assert isinstance(args, WebFetchArgs)
        assert args.url == "https://example.com"
        assert args.max_bytes == 1_000_000

    def test_parse_args_custom_max_bytes(self):
        tool = WebFetchTool()
        args = tool.parse_args({"url": "https://example.com", "max_bytes": 512})
        assert args.max_bytes == 512

    @pytest.mark.asyncio
    async def test_200_ok_returns_body(self):
        tool     = WebFetchTool()
        response = _mock_http_response(200, "<html>hello</html>", "text/html")
        cm       = _make_client_cm(response)

        with patch("nerdvana_cli.tools.web_tools._check_url", return_value=None), \
             patch("nerdvana_cli.tools.web_tools.httpx.AsyncClient", return_value=cm):
            result = await tool.call(WebFetchArgs(url="https://example.com"), _CTX)

        assert not result.is_error
        data = json.loads(result.content)
        assert data["status"]    == 200
        assert data["body"]      == "<html>hello</html>"
        assert data["truncated"] is False
        assert "text/html" in data["content_type"]

    @pytest.mark.asyncio
    async def test_404_returns_status_not_error(self):
        """404 responses are returned as normal results (not ToolError)."""
        tool     = WebFetchTool()
        response = _mock_http_response(404, "Not Found", "text/plain")
        # 404 must not trigger raise_for_status in WebFetchTool
        response.raise_for_status = MagicMock()  # reset — fetch tool does not call it
        cm = _make_client_cm(response)

        with patch("nerdvana_cli.tools.web_tools._check_url", return_value=None), \
             patch("nerdvana_cli.tools.web_tools.httpx.AsyncClient", return_value=cm):
            result = await tool.call(WebFetchArgs(url="https://example.com/missing"), _CTX)

        assert not result.is_error
        data = json.loads(result.content)
        assert data["status"] == 404

    @pytest.mark.asyncio
    async def test_max_bytes_truncation(self):
        tool     = WebFetchTool()
        body     = "A" * 2000
        response = _mock_http_response(200, body, "text/plain")
        cm       = _make_client_cm(response)

        with patch("nerdvana_cli.tools.web_tools._check_url", return_value=None), \
             patch("nerdvana_cli.tools.web_tools.httpx.AsyncClient", return_value=cm):
            result = await tool.call(WebFetchArgs(url="https://example.com", max_bytes=500), _CTX)

        assert not result.is_error
        data = json.loads(result.content)
        assert data["truncated"]  is True
        assert len(data["body"])  == 500

    @pytest.mark.asyncio
    async def test_private_ip_blocked(self):
        tool = WebFetchTool()
        result = await tool.call(WebFetchArgs(url="http://127.0.0.1/secret"), _CTX)
        assert result.is_error
        assert "loopback" in result.content.lower() or "private" in result.content.lower()

    @pytest.mark.asyncio
    async def test_file_scheme_blocked(self):
        tool = WebFetchTool()
        result = await tool.call(WebFetchArgs(url="file:///etc/passwd"), _CTX)
        assert result.is_error
        assert "file" in result.content

    @pytest.mark.asyncio
    async def test_request_exception_returns_error(self):
        tool = WebFetchTool()
        cm   = AsyncMock()
        cm.__aenter__ = AsyncMock(side_effect=Exception("network failure"))
        cm.__aexit__  = AsyncMock(return_value=False)

        with patch("nerdvana_cli.tools.web_tools._check_url", return_value=None), \
             patch("nerdvana_cli.tools.web_tools.httpx.AsyncClient", return_value=cm):
            result = await tool.call(WebFetchArgs(url="https://example.com"), _CTX)

        assert result.is_error
        assert "network failure" in result.content


# ---------------------------------------------------------------------------
# WebSearchTool
# ---------------------------------------------------------------------------

class TestWebSearchTool:
    def test_name_and_schema(self):
        tool = WebSearchTool()
        assert tool.name == "WebSearch"
        assert "query" in tool.input_schema["properties"]
        assert "count" in tool.input_schema["properties"]
        assert tool.input_schema["properties"]["count"]["maximum"] == 20

    def test_parse_args_defaults(self):
        tool = WebSearchTool()
        args = tool.parse_args({"query": "python async"})
        assert isinstance(args, WebSearchArgs)
        assert args.query == "python async"
        assert args.count == 5

    def test_count_clamped_to_20(self):
        tool = WebSearchTool()
        args = tool.parse_args({"query": "test", "count": 99})
        assert isinstance(args, WebSearchArgs)
        # WebSearchArgs.__init__ clamps in constructor
        assert args.count == 20

    def test_count_clamped_to_1(self):
        tool = WebSearchTool()
        args = tool.parse_args({"query": "test", "count": 0})
        assert isinstance(args, WebSearchArgs)
        assert args.count == 1

    @pytest.mark.asyncio
    async def test_missing_api_key_returns_error(self):
        tool = WebSearchTool()
        with patch.dict("os.environ", {}, clear=True):
            # Ensure BRAVE_API_KEY is absent
            import os
            os.environ.pop("BRAVE_API_KEY", None)
            result = await tool.call(WebSearchArgs(query="test"), _CTX)

        assert result.is_error
        assert "BRAVE_API_KEY" in result.content

    @pytest.mark.asyncio
    async def test_successful_search_parses_results(self):
        tool = WebSearchTool()
        brave_payload = {
            "web": {
                "results": [
                    {"title": "Example", "url": "https://example.com", "description": "An example site"},
                    {"title": "Python Docs", "url": "https://docs.python.org", "description": "Python documentation"},
                ]
            }
        }
        response = _mock_http_response(200, json_data=brave_payload)
        cm       = _make_client_cm(response)

        with patch.dict("os.environ", {"BRAVE_API_KEY": "test-key"}), \
             patch("nerdvana_cli.tools.web_tools.httpx.AsyncClient", return_value=cm), \
             patch("nerdvana_cli.tools.web_tools._is_private_address", return_value=False):
            result = await tool.call(WebSearchArgs(query="example", count=2), _CTX)

        assert not result.is_error
        data = json.loads(result.content)
        assert data["query"]      == "example"
        assert len(data["results"]) == 2
        assert data["results"][0]["title"]   == "Example"
        assert data["results"][0]["url"]     == "https://example.com"
        assert data["results"][0]["snippet"] == "An example site"

    @pytest.mark.asyncio
    async def test_private_url_in_results_filtered(self):
        """Results pointing to private IPs must be silently dropped."""
        tool = WebSearchTool()
        brave_payload = {
            "web": {
                "results": [
                    {"title": "Public",  "url": "https://example.com",   "description": "ok"},
                    {"title": "Private", "url": "http://192.168.1.5/data", "description": "bad"},
                ]
            }
        }
        response = _mock_http_response(200, json_data=brave_payload)
        cm       = _make_client_cm(response)

        with patch.dict("os.environ", {"BRAVE_API_KEY": "test-key"}), \
             patch("nerdvana_cli.tools.web_tools.httpx.AsyncClient", return_value=cm):
            result = await tool.call(WebSearchArgs(query="test"), _CTX)

        assert not result.is_error
        data = json.loads(result.content)
        # 192.168.1.5 is a private literal IP — must be filtered out
        assert len(data["results"]) == 1
        assert data["results"][0]["title"] == "Public"

    @pytest.mark.asyncio
    async def test_empty_results(self):
        tool = WebSearchTool()
        brave_payload = {"web": {"results": []}}
        response = _mock_http_response(200, json_data=brave_payload)
        cm       = _make_client_cm(response)

        with patch.dict("os.environ", {"BRAVE_API_KEY": "test-key"}), \
             patch("nerdvana_cli.tools.web_tools.httpx.AsyncClient", return_value=cm), \
             patch("nerdvana_cli.tools.web_tools._is_private_address", return_value=False):
            result = await tool.call(WebSearchArgs(query="nothing"), _CTX)

        assert not result.is_error
        data = json.loads(result.content)
        assert data["results"] == []

    @pytest.mark.asyncio
    async def test_api_error_returns_tool_error(self):
        tool = WebSearchTool()

        import httpx
        client      = AsyncMock()
        client.get  = AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "401",
                request=MagicMock(),
                response=MagicMock(status_code=401, text="Unauthorized"),
            )
        )
        cm              = AsyncMock()
        cm.__aenter__   = AsyncMock(return_value=client)
        cm.__aexit__    = AsyncMock(return_value=False)

        with patch.dict("os.environ", {"BRAVE_API_KEY": "bad-key"}), \
             patch("nerdvana_cli.tools.web_tools.httpx.AsyncClient", return_value=cm):
            result = await tool.call(WebSearchArgs(query="test"), _CTX)

        assert result.is_error
        assert "401" in result.content


# ---------------------------------------------------------------------------
# BaseTool protocol compliance
# ---------------------------------------------------------------------------

class TestBaseToolProtocol:
    def test_web_fetch_has_required_attributes(self):
        tool = WebFetchTool()
        assert tool.name
        assert tool.description_text
        assert isinstance(tool.input_schema, dict)
        assert tool.input_schema.get("type") == "object"
        assert "properties" in tool.input_schema
        assert "required" in tool.input_schema

    def test_web_search_has_required_attributes(self):
        tool = WebSearchTool()
        assert tool.name
        assert tool.description_text
        assert isinstance(tool.input_schema, dict)
        assert tool.input_schema.get("type") == "object"
        assert "properties" in tool.input_schema
        assert "required" in tool.input_schema

    def test_web_fetch_parse_args_returns_correct_type(self):
        tool = WebFetchTool()
        args = tool.parse_args({"url": "https://example.com"})
        assert isinstance(args, WebFetchArgs)

    def test_web_search_parse_args_returns_correct_type(self):
        tool = WebSearchTool()
        args = tool.parse_args({"query": "hello"})
        assert isinstance(args, WebSearchArgs)

    @pytest.mark.asyncio
    async def test_web_fetch_returns_tool_result_type(self):
        from nerdvana_cli.types import ToolResult

        tool   = WebFetchTool()
        result = await tool.call(WebFetchArgs(url="http://127.0.0.1/"), _CTX)
        assert isinstance(result, ToolResult)

    @pytest.mark.asyncio
    async def test_web_search_returns_tool_result_type(self):
        from nerdvana_cli.types import ToolResult

        tool   = WebSearchTool()
        with patch.dict("os.environ", {}, clear=True):
            import os
            os.environ.pop("BRAVE_API_KEY", None)
            result = await tool.call(WebSearchArgs(query="test"), _CTX)
        assert isinstance(result, ToolResult)
