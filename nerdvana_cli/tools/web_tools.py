"""Web tools — WebFetch and WebSearch."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import socket
from typing import Any, ClassVar
from urllib.parse import urlparse

import httpx

from nerdvana_cli.core.tool import BaseTool, ToolCategory, ToolContext, ToolSideEffect
from nerdvana_cli.types import ToolResult

_ALLOWED_SCHEMES = {"http", "https"}

_DEFAULT_MAX_BYTES = 1_000_000
_HTTP_TIMEOUT      = 10.0
_MAX_REDIRECTS     = 5
_REDIRECT_CODES    = frozenset({301, 302, 303, 307, 308})


def _is_blocked_ip(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True for every address a fetch must not reach: loopback, private, link-local, unspecified, reserved, multicast."""
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    return (
        addr.is_loopback or addr.is_private or addr.is_link_local
        or addr.is_unspecified or addr.is_reserved or addr.is_multicast
    )


def _is_private_address(hostname: str) -> bool:
    """Return True when hostname resolves to an address `_is_blocked_ip` refuses."""
    try:
        resolved = socket.getaddrinfo(hostname, None)
    except OSError:
        # Cannot resolve — treat as safe to let httpx produce a proper error.
        return False

    for _family, _type, _proto, _canonname, sockaddr in resolved:
        raw_ip = sockaddr[0]
        try:
            addr = ipaddress.ip_address(raw_ip)
        except ValueError:
            continue
        if _is_blocked_ip(addr):
            return True
    return False


def _host_error(hostname: str) -> str | None:
    """Why *hostname* may not be fetched, or None when it may."""
    try:
        addr = ipaddress.ip_address(hostname)
    except ValueError:
        if _is_private_address(hostname):
            return f"Hostname '{hostname}' resolves to a private/loopback address."
        return None
    if _is_blocked_ip(addr):
        return f"Access to private/loopback address '{hostname}' is not allowed."
    return None


def _check_url(url: str) -> str | None:
    """Validate URL scheme and private-IP block.

    Returns an error message string when the URL is disallowed, otherwise None.
    """
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        return f"Unsupported URL scheme '{parsed.scheme}'. Only http and https are allowed."

    hostname = parsed.hostname or ""
    if not hostname:
        return "URL contains no hostname."

    return _host_error(hostname)


class _LoopBoundClient:
    """One ``httpx.AsyncClient`` per event loop, so calls in a session reuse their connections."""

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport
        self._client: httpx.AsyncClient | None = None
        self._loop:   asyncio.AbstractEventLoop | None = None

    def get(self) -> httpx.AsyncClient:
        loop = asyncio.get_running_loop()
        if self._client is None or self._client.is_closed or self._loop is not loop:
            self._client = httpx.AsyncClient(timeout=_HTTP_TIMEOUT, transport=self._transport)
            self._loop   = loop
        return self._client


async def _read_limited(response: httpx.Response, max_bytes: int) -> tuple[bytes, bool]:
    """At most *max_bytes* of the body, and whether more was left unread."""
    chunks: list[bytes] = []
    size = 0
    async for chunk in response.aiter_bytes():
        room = max_bytes - size
        if len(chunk) > room:
            chunks.append(chunk[:room])
            return b"".join(chunks), True
        chunks.append(chunk)
        size += len(chunk)
    return b"".join(chunks), False


def _decode(raw: bytes, encoding: str | None) -> str:
    try:
        return raw.decode(encoding or "utf-8", errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


class WebFetchArgs:
    def __init__(self, url: str, max_bytes: int = _DEFAULT_MAX_BYTES) -> None:
        self.url       = url
        self.max_bytes = max_bytes


class WebFetchTool(BaseTool[WebFetchArgs]):
    name             = "WebFetch"
    # Pages are mostly boilerplate; a tighter cap keeps them from crowding the window.
    max_result_tokens = 10_000
    description_text = (
        "Fetch a URL and return the response body as text.\n"
        "HTTP/HTTPS only. Private/loopback IPs are blocked. Default max_bytes 1 MiB. "
        "Use a URL the user gave or WebSearch returned. Example: url: \"https://example.com/docs/setup\", max_bytes: 200000."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "url":       {"type": "string"},
            "max_bytes": {"type": "integer", "default": _DEFAULT_MAX_BYTES},
        },
        "required": ["url"],
    }

    is_concurrency_safe               = True
    args_class                        = WebFetchArgs
    category:    ClassVar[ToolCategory]    = ToolCategory.READ
    side_effects: ClassVar[ToolSideEffect] = ToolSideEffect.NETWORK
    tags:         ClassVar[frozenset[str]] = frozenset({"web", "fetch"})
    requires_confirmation              = False

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._clients = _LoopBoundClient(transport)

    async def call(
        self,
        args: WebFetchArgs,
        context: ToolContext,
        can_use_tool: Any = None,
        on_progress: Any  = None,
    ) -> ToolResult:
        max_bytes = max(1, args.max_bytes) if args.max_bytes else _DEFAULT_MAX_BYTES
        url       = args.url
        client    = self._clients.get()

        try:
            # Redirects are followed here, not by httpx, so that every hop passes _check_url.
            for _hop in range(_MAX_REDIRECTS + 1):
                err = _check_url(url)
                if err:
                    return ToolResult(tool_use_id="", content=err, is_error=True)
                async with client.stream("GET", url) as response:
                    location = response.headers.get("location")
                    if response.status_code in _REDIRECT_CODES and location:
                        url = str(response.url.join(location))
                        continue
                    raw, truncated = await _read_limited(response, max_bytes)
                    payload = {
                        "status":       response.status_code,
                        "content_type": response.headers.get("content-type", ""),
                        "body":         _decode(raw, response.encoding),
                        "truncated":    truncated,
                    }
                    return ToolResult(tool_use_id="", content=json.dumps(payload))
        except (httpx.HTTPError, httpx.InvalidURL) as exc:
            return ToolResult(tool_use_id="", content=f"Request failed: {exc}", is_error=True)
        return ToolResult(tool_use_id="", content=f"Too many redirects (more than {_MAX_REDIRECTS}).", is_error=True)


class WebSearchArgs:
    def __init__(self, query: str, count: int = 5) -> None:
        self.query = query
        self.count = max(1, min(count, 20))


class WebSearchTool(BaseTool[WebSearchArgs]):
    name             = "WebSearch"
    description_text = (
        "Search the web via Brave Search API. Requires\n"
        "BRAVE_API_KEY env var. Returns a list of {title, url, snippet}."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "count": {"type": "integer", "default": 5, "maximum": 20},
        },
        "required": ["query"],
    }

    is_concurrency_safe               = True
    args_class                        = WebSearchArgs
    category:    ClassVar[ToolCategory]    = ToolCategory.READ
    side_effects: ClassVar[ToolSideEffect] = ToolSideEffect.NETWORK
    tags:         ClassVar[frozenset[str]] = frozenset({"web", "search"})
    requires_confirmation              = False

    _BRAVE_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._clients = _LoopBoundClient(transport)

    async def call(
        self,
        args: WebSearchArgs,
        context: ToolContext,
        can_use_tool: Any = None,
        on_progress: Any  = None,
    ) -> ToolResult:
        api_key = os.environ.get("BRAVE_API_KEY")
        if not api_key:
            return ToolResult(
                tool_use_id="",
                content="BRAVE_API_KEY not set",
                is_error=True,
            )

        params  = {"q": args.query, "count": str(args.count)}
        headers = {"X-Subscription-Token": api_key, "Accept": "application/json"}

        try:
            response = await self._clients.get().get(self._BRAVE_SEARCH_URL, params=params, headers=headers)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            return ToolResult(
                tool_use_id="",
                content=f"Brave Search API error {exc.response.status_code}: {exc.response.text}",
                is_error=True,
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult(tool_use_id="", content=f"Request failed: {exc}", is_error=True)

        try:
            data         = response.json()
            raw_results  = data.get("web", {}).get("results", [])
        except Exception as exc:  # noqa: BLE001
            return ToolResult(
                tool_use_id="",
                content=f"Failed to parse Brave API response: {exc}",
                is_error=True,
            )

        results: list[dict[str, str]] = []
        for item in raw_results:
            url      = item.get("url", "")
            # Block results that point to private addresses.
            hostname = urlparse(url).hostname or ""
            if hostname and _host_error(hostname):
                continue

            results.append({
                "title":   item.get("title", ""),
                "url":     url,
                "snippet": item.get("description", ""),
            })

        payload = {"results": results, "query": args.query}
        return ToolResult(tool_use_id="", content=json.dumps(payload))


__all__ = [
    "WebFetchTool",
    "WebSearchTool",
    "WebFetchArgs",
    "WebSearchArgs",
]
