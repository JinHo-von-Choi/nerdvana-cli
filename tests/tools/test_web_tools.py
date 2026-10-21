"""Unit tests for WebFetchTool and WebSearchTool.

DNS resolution is patched via unittest.mock so no real network calls are made.
HTTP traffic goes to an ``httpx.MockTransport`` handed to the tool.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from unittest.mock import patch

import httpx
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


def _transport(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


def _answer(status: int = 200, text: str = "hello", content_type: str = "text/html") -> httpx.MockTransport:
    return _transport(lambda request: httpx.Response(status, text=text, headers={"content-type": content_type}))


def _public_dns() -> object:
    """Every hostname resolves to a public address."""
    return patch("nerdvana_cli.tools.web_tools._is_private_address", return_value=False)


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
        tool = WebFetchTool(transport=_answer(200, "<html>hello</html>", "text/html"))
        with _public_dns():
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
        tool = WebFetchTool(transport=_answer(404, "Not Found", "text/plain"))
        with _public_dns():
            result = await tool.call(WebFetchArgs(url="https://example.com/missing"), _CTX)

        assert not result.is_error
        data = json.loads(result.content)
        assert data["status"] == 404

    @pytest.mark.asyncio
    async def test_max_bytes_truncation(self):
        tool = WebFetchTool(transport=_answer(200, "A" * 2000, "text/plain"))
        with _public_dns():
            result = await tool.call(WebFetchArgs(url="https://example.com", max_bytes=500), _CTX)

        assert not result.is_error
        data = json.loads(result.content)
        assert data["truncated"]  is True
        assert len(data["body"])  == 500

    @pytest.mark.asyncio
    async def test_body_exactly_max_bytes_is_not_truncated(self):
        tool = WebFetchTool(transport=_answer(200, "A" * 500, "text/plain"))
        with _public_dns():
            result = await tool.call(WebFetchArgs(url="https://example.com", max_bytes=500), _CTX)

        data = json.loads(result.content)
        assert data["truncated"] is False
        assert len(data["body"]) == 500

    @pytest.mark.asyncio
    async def test_reading_stops_at_max_bytes(self):
        """The body is streamed: chunks past max_bytes are never pulled from the server."""
        pulled: list[int] = []

        async def body():
            for index in range(100):
                pulled.append(index)
                yield b"B" * 1000

        tool = WebFetchTool(transport=_transport(lambda request: httpx.Response(200, content=body())))
        with _public_dns():
            result = await tool.call(WebFetchArgs(url="https://example.com", max_bytes=2500), _CTX)

        data = json.loads(result.content)
        assert data["truncated"] is True
        assert len(data["body"]) == 2500
        assert len(pulled) < 10

    @pytest.mark.asyncio
    async def test_public_redirect_is_followed(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/old":
                return httpx.Response(302, headers={"location": "/new"})
            return httpx.Response(200, text="moved here")

        tool = WebFetchTool(transport=_transport(handler))
        with _public_dns():
            result = await tool.call(WebFetchArgs(url="https://example.com/old"), _CTX)

        data = json.loads(result.content)
        assert data["status"] == 200
        assert data["body"]   == "moved here"

    @pytest.mark.asyncio
    async def test_redirect_to_loopback_is_refused(self):
        reached: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            reached.append(str(request.url))
            return httpx.Response(302, headers={"location": "http://127.0.0.1/admin"})

        tool = WebFetchTool(transport=_transport(handler))
        with _public_dns():
            result = await tool.call(WebFetchArgs(url="https://example.com/"), _CTX)

        assert result.is_error
        assert "127.0.0.1" in result.content
        assert reached == ["https://example.com/"]

    @pytest.mark.asyncio
    async def test_redirect_to_metadata_address_is_refused(self):
        tool = WebFetchTool(transport=_transport(
            lambda request: httpx.Response(301, headers={"location": "http://169.254.169.254/latest/meta-data"})
        ))
        with _public_dns():
            result = await tool.call(WebFetchArgs(url="https://example.com/"), _CTX)

        assert result.is_error
        assert "169.254.169.254" in result.content

    @pytest.mark.asyncio
    async def test_redirect_loop_stops(self):
        tool = WebFetchTool(transport=_transport(lambda request: httpx.Response(302, headers={"location": "/again"})))
        with _public_dns():
            result = await tool.call(WebFetchArgs(url="https://example.com/again"), _CTX)

        assert result.is_error
        assert "Too many redirects" in result.content

    @pytest.mark.asyncio
    async def test_connection_is_reused_across_calls(self):
        tool = WebFetchTool(transport=_answer())
        with _public_dns():
            await tool.call(WebFetchArgs(url="https://example.com/a"), _CTX)
            first = tool._clients.get()
            await tool.call(WebFetchArgs(url="https://example.com/b"), _CTX)
        assert tool._clients.get() is first

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
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("network failure", request=request)

        tool = WebFetchTool(transport=_transport(handler))
        with _public_dns():
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
        brave_payload = {
            "web": {
                "results": [
                    {"title": "Example", "url": "https://example.com", "description": "An example site"},
                    {"title": "Python Docs", "url": "https://docs.python.org", "description": "Python documentation"},
                ]
            }
        }
        tool = WebSearchTool(transport=_transport(lambda request: httpx.Response(200, json=brave_payload)))

        with patch.dict("os.environ", {"BRAVE_API_KEY": "test-key"}), _public_dns():
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
        brave_payload = {
            "web": {
                "results": [
                    {"title": "Public",  "url": "https://example.com",   "description": "ok"},
                    {"title": "Private", "url": "http://192.168.1.5/data", "description": "bad"},
                ]
            }
        }
        tool = WebSearchTool(transport=_transport(lambda request: httpx.Response(200, json=brave_payload)))

        with patch.dict("os.environ", {"BRAVE_API_KEY": "test-key"}), _public_dns():
            result = await tool.call(WebSearchArgs(query="test"), _CTX)

        assert not result.is_error
        data = json.loads(result.content)
        # 192.168.1.5 is a private literal IP — must be filtered out
        assert len(data["results"]) == 1
        assert data["results"][0]["title"] == "Public"

    @pytest.mark.asyncio
    async def test_empty_results(self):
        brave_payload = {"web": {"results": []}}
        tool = WebSearchTool(transport=_transport(lambda request: httpx.Response(200, json=brave_payload)))

        with patch.dict("os.environ", {"BRAVE_API_KEY": "test-key"}), _public_dns():
            result = await tool.call(WebSearchArgs(query="nothing"), _CTX)

        assert not result.is_error
        data = json.loads(result.content)
        assert data["results"] == []

    @pytest.mark.asyncio
    async def test_api_error_returns_tool_error(self):
        tool = WebSearchTool(transport=_transport(lambda request: httpx.Response(401, text="Unauthorized")))

        with patch.dict("os.environ", {"BRAVE_API_KEY": "bad-key"}):
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
