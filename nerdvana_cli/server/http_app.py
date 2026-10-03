"""HTTP application of the NerdVana MCP server: bearer authentication and quota error middleware.

작성자: 최진호
작성일: 2026-10-03
"""

from __future__ import annotations

import contextvars
import logging
from typing import Any

from mcp.server.mcpserver import MCPServer
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.types import ASGIApp

from nerdvana_cli.server.auth import AuthManager, AuthResult
from nerdvana_cli.server.quota import QuotaExceeded

# Per-request auth context, propagated from the HTTP middleware to dispatch.
request_auth: contextvars.ContextVar[AuthResult | None] = contextvars.ContextVar(
    "request_auth", default=None
)

_quota_log = logging.getLogger("nerdvana.quota")


class BearerAuthMiddleware(BaseHTTPMiddleware):
    """Extract and validate HTTP Authorization: Bearer token per request.

    On success the resolved ``AuthResult`` is stored in ``request_auth``
    context-var so that the server dispatch can consume it.  On failure a 401
    response is returned immediately.
    """

    def __init__(self, app: ASGIApp, auth_manager: AuthManager) -> None:
        super().__init__(app)
        self._auth = auth_manager

    async def dispatch(self, request: Request, call_next: Any) -> Any:
        from starlette.responses import JSONResponse

        auth_header = request.headers.get("Authorization", "")
        if not auth_header.lower().startswith("bearer "):
            return JSONResponse(
                {"error": "missing_bearer_token", "message": "Authorization: Bearer <token> required"},
                status_code=401,
            )
        raw_key = auth_header[len("bearer "):].strip()
        result  = self._auth.authenticate_bearer(raw_key)
        if not result.authenticated:
            return JSONResponse(
                {"error": "invalid_bearer_token", "message": result.reason},
                status_code=401,
            )

        token = request_auth.set(result)
        try:
            response = await call_next(request)
        finally:
            request_auth.reset(token)
        return response


class QuotaErrorMiddleware(BaseHTTPMiddleware):
    """Convert a ``QuotaExceeded`` that reaches the HTTP layer into a 429 response.

    Tool exceptions do not get this far: the tool registration turns ``QuotaExceeded`` into a
    ``ToolError``, which the MCP server sends as a tool result with ``isError:true`` and the
    reason inside an HTTP 200 response. See ``docs/mcp-quota.md``.
    """

    async def dispatch(self, request: Request, call_next: Any) -> Any:
        from starlette.responses import JSONResponse

        try:
            return await call_next(request)
        except QuotaExceeded as exc:
            # Reached only by a quota error raised outside a tool call.
            _quota_log.warning(
                "quota_exceeded",
                extra={
                    "event":       "quota_exceeded",
                    "limit":       exc.limit_name,
                    "retry_after": exc.retry_after_seconds,
                },
            )
            return JSONResponse(
                {
                    "error":                "quota_exceeded",
                    "limit":                exc.limit_name,
                    "retry_after_seconds":  exc.retry_after_seconds,
                },
                status_code = 429,
                headers     = {"Retry-After": str(exc.retry_after_seconds)},
            )


def build_http_app(fmcp: MCPServer, host: str, auth: AuthManager) -> ASGIApp:
    """The streamable-HTTP app behind the quota-error and bearer-auth middleware."""
    from starlette.applications import Starlette
    from starlette.routing import Mount

    starlette_app = fmcp.streamable_http_app(host=host)
    # The session manager starts in the inner app's lifespan; without handing it on,
    # every MCP request fails with "Task group is not initialized".
    protected = Starlette(
        routes=[Mount("/", app=starlette_app)],
        lifespan=starlette_app.router.lifespan_context,
    )
    # Inject middlewares (outermost first: quota error -> auth).
    protected.add_middleware(QuotaErrorMiddleware)
    protected.add_middleware(BearerAuthMiddleware, auth_manager=auth)
    return protected
