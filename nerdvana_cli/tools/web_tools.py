"""Web tools — WebFetch and WebSearch."""

from __future__ import annotations

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


def _is_private_address(hostname: str) -> bool:
    """Return True when hostname resolves to a private/loopback/link-local address.

    Raises socket.gaierror when DNS resolution fails (propagated to caller).
    """
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
        if addr.is_loopback or addr.is_private or addr.is_link_local:
            return True
    return False


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

    # Block bare IP literals that are private/loopback.
    try:
        addr = ipaddress.ip_address(hostname)
        if addr.is_loopback or addr.is_private or addr.is_link_local:
            return f"Access to private/loopback address '{hostname}' is not allowed."
    except ValueError:
        pass  # Not a bare IP — proceed to DNS resolution check.

    if _is_private_address(hostname):
        return f"Hostname '{hostname}' resolves to a private/loopback address."

    return None


class WebFetchArgs:
    def __init__(self, url: str, max_bytes: int = _DEFAULT_MAX_BYTES) -> None:
        self.url       = url
        self.max_bytes = max_bytes


class WebFetchTool(BaseTool[WebFetchArgs]):
    name             = "WebFetch"
    description_text = (
        "Fetch a URL and return the response body as text.\n"
        "HTTP/HTTPS only. Private/loopback IPs are blocked. Default max_bytes 1 MiB."
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

    async def call(
        self,
        args: WebFetchArgs,
        context: ToolContext,
        can_use_tool: Any = None,
        on_progress: Any  = None,
    ) -> ToolResult:
        err = _check_url(args.url)
        if err:
            return ToolResult(tool_use_id="", content=err, is_error=True)

        max_bytes = max(1, args.max_bytes) if args.max_bytes else _DEFAULT_MAX_BYTES

        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, follow_redirects=True) as client:
                response = await client.get(args.url)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(tool_use_id="", content=f"Request failed: {exc}", is_error=True)

        body        = response.text
        truncated   = len(body) > max_bytes
        if truncated:
            body = body[:max_bytes]

        payload = {
            "status":       response.status_code,
            "content_type": response.headers.get("content-type", ""),
            "body":         body,
            "truncated":    truncated,
        }
        return ToolResult(tool_use_id="", content=json.dumps(payload))


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
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
                response = await client.get(
                    self._BRAVE_SEARCH_URL,
                    params=params,
                    headers=headers,
                )
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
            url     = item.get("url", "")
            # Block results that point to private addresses.
            parsed  = urlparse(url)
            hostname = parsed.hostname or ""
            blocked = False
            try:
                addr = ipaddress.ip_address(hostname)
                if addr.is_loopback or addr.is_private or addr.is_link_local:
                    blocked = True
            except ValueError:
                if hostname and _is_private_address(hostname):
                    blocked = True

            if blocked:
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
