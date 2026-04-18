"""NerdVana MCP server — Phase G1.

Exposes nerdvana tools over MCP 1.0 (stdio + HTTP JSON-RPC).

Default (read-only) tools:
  symbol_overview, find_symbol, find_referencing_symbols,
  ReadMemory, ListMemories, GetCurrentConfig

With ``--allow-write``:
  + replace_symbol_body, insert_before_symbol, insert_after_symbol,
    WriteMemory, EditMemory, DeleteMemory, safe_delete_symbol

Every tool call goes through AuthManager + ACLManager + AuditLogger.

작성자: 최진호
작성일: 2026-04-18
"""

from __future__ import annotations

import contextvars
import json
import sys
import time
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.types import ASGIApp

from nerdvana_cli.core.tool import BaseTool, ToolContext
from nerdvana_cli.server.acl import ACLManager
from nerdvana_cli.server.audit import AuditLogger
from nerdvana_cli.server.auth import AuthManager, AuthResult

# ---------------------------------------------------------------------------
# Per-request auth context — propagated from HTTP middleware to dispatch
# ---------------------------------------------------------------------------

_request_auth: contextvars.ContextVar[AuthResult | None] = contextvars.ContextVar(
    "_request_auth", default=None
)

# ---------------------------------------------------------------------------
# HTTP Bearer authentication middleware
# ---------------------------------------------------------------------------


class _BearerAuthMiddleware(BaseHTTPMiddleware):
    """Extract and validate HTTP Authorization: Bearer token per request.

    On success the resolved ``AuthResult`` is stored in ``_request_auth``
    context-var so that ``_dispatch`` can consume it.  On failure a 401
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

        token = _request_auth.set(result)
        try:
            response = await call_next(request)
        finally:
            _request_auth.reset(token)
        return response


# ---------------------------------------------------------------------------
# Read-only tool list (v3 §7.2)
# ---------------------------------------------------------------------------

_READ_ONLY_TOOLS: frozenset[str] = frozenset({
    "symbol_overview",
    "find_symbol",
    "find_referencing_symbols",
    "ReadMemory",
    "ListMemories",
    "GetCurrentConfig",
})

_WRITE_TOOLS: frozenset[str] = frozenset({
    "replace_symbol_body",
    "insert_before_symbol",
    "insert_after_symbol",
    "RenameSymbol",
    "WriteMemory",
    "EditMemory",
    "DeleteMemory",
    "safe_delete_symbol",
    "restart_language_server",
})


# ---------------------------------------------------------------------------
# NerdvanaMcpServer
# ---------------------------------------------------------------------------


class NerdvanaMcpServer:
    """Thin orchestrator that wires auth/ACL/audit around a FastMCP instance.

    Parameters
    ----------
    allow_write:
        When *True* the full write-tool suite is exposed (still gated by ACL).
    transport:
        ``"stdio"`` or ``"http"``.
    host:
        Bind address for HTTP transport (default ``"127.0.0.1"``).
    port:
        Listen port for HTTP transport.
    tls_cert:
        Path to TLS certificate file (PEM).  Enables mTLS when set.
    tls_ca:
        Path to CA certificate for peer verification.
    auth_manager:
        Injected AuthManager (default: ``AuthManager()``).
    acl_manager:
        Injected ACLManager (default: ``ACLManager()``).
    audit_logger:
        Injected AuditLogger (default: ``AuditLogger()``).
    """

    def __init__(
        self,
        *,
        allow_write:   bool              = False,
        transport:     str               = "stdio",
        host:          str               = "127.0.0.1",
        port:          int               = 10830,
        tls_cert:      Path | None       = None,
        tls_ca:        Path | None       = None,
        auth_manager:  AuthManager | None  = None,
        acl_manager:   ACLManager  | None  = None,
        audit_logger:  AuditLogger | None  = None,
        # Phase H extensions — external project subprocess support
        project_path:  Path | None       = None,
        mode:          str  | None       = None,
    ) -> None:
        self.allow_write   = allow_write
        self.transport     = transport
        self.host          = host
        self.port          = port
        self.tls_cert      = tls_cert
        self.tls_ca        = tls_ca
        # Phase H: working directory override and mode name.
        self.project_path  = project_path
        self.mode          = mode

        self._auth:  AuthManager  = auth_manager  or AuthManager()
        self._acl:   ACLManager   = acl_manager   or ACLManager()
        self._audit: AuditLogger  = audit_logger  or AuditLogger()

        self._fmcp: FastMCP = FastMCP(
            name  = "nerdvana",
            host  = host,
            port  = port,
        )

        # stdio: resolved once at startup; http: resolved per-request from middleware
        self._stdio_identity: str = "anonymous"

        # name → BaseTool instance map; populated by _build_tool_map()
        self._tool_map: dict[str, BaseTool[Any]] = {}
        self._build_tool_map()
        self._register_tools()

    # ------------------------------------------------------------------
    # Tool map — live BaseTool instances for _execute_tool routing
    # ------------------------------------------------------------------

    def _build_tool_map(self) -> None:
        """Instantiate memory, config, and (if available) symbol tools.

        Symbol tools require a live language server; they are registered only
        when ``LspClient.has_any_server()`` returns True.  Missing LSP is not
        an error — the server degrades gracefully to memory + config tools only.
        """
        from nerdvana_cli.core.profiles import ProfileManager
        from nerdvana_cli.tools.memory_tools import (
            DeleteMemoryTool,
            EditMemoryTool,
            ListMemoriesTool,
            ReadMemoryTool,
            WriteMemoryTool,
        )
        from nerdvana_cli.tools.profile_tools import GetCurrentConfigTool

        cwd = str(self.project_path) if self.project_path else "."

        self._tool_context = ToolContext(cwd=cwd)

        # Memory tools (no external deps)
        for tool in (
            ReadMemoryTool(),
            ListMemoriesTool(),
            WriteMemoryTool(),
            EditMemoryTool(),
            DeleteMemoryTool(),
        ):
            self._tool_map[tool.name] = tool

        # Config tool (needs ProfileManager)
        pm = ProfileManager(cwd=cwd)
        self._tool_map["GetCurrentConfig"] = GetCurrentConfigTool(profile_manager=pm)

        # Symbol tools — only when a language server is available
        try:
            from nerdvana_cli.core.lsp_client import LspClient
            lsp = LspClient()
            if lsp.has_any_server():
                from nerdvana_cli.core.code_editor import CodeEditor
                from nerdvana_cli.core.symbol import LanguageServerSymbolRetriever
                from nerdvana_cli.tools.symbol_tools import create_symbol_tools
                retriever = LanguageServerSymbolRetriever(client=lsp)
                editor    = CodeEditor(project_root=lsp._project_root)  # noqa: SLF001
                for sym_tool in create_symbol_tools(
                    client=lsp, retriever=retriever, editor=editor
                ):
                    self._tool_map[sym_tool.name] = sym_tool
        except Exception:  # noqa: BLE001 — LSP absent or misconfigured; degrade silently
            pass

    # ------------------------------------------------------------------
    # Tool registration
    # ------------------------------------------------------------------

    def _register_tools(self) -> None:
        """Register all MCP tools according to the allow_write flag."""
        self._register_read_only_tools()
        if self.allow_write:
            self._register_write_tools()

    def _register_read_only_tools(self) -> None:
        """Register the six default read-only tools."""
        server = self

        async def symbol_overview(relative_path: str, depth: int = 0, with_graph: bool = False) -> str:
            """List symbols in a source file at the given path."""
            return await server._dispatch(
                "symbol_overview",
                {"relative_path": relative_path, "depth": depth, "with_graph": with_graph},
            )

        async def find_symbol(
            name_path: str,
            substring_matching: bool = False,
            include_body: bool = False,
            within_relative_path: str = "",
        ) -> str:
            """Find a symbol by qualified name or substring."""
            return await server._dispatch(
                "find_symbol",
                {
                    "name_path":             name_path,
                    "substring_matching":    substring_matching,
                    "include_body":          include_body,
                    "within_relative_path":  within_relative_path or None,
                },
            )

        async def find_referencing_symbols(name_path: str, relative_path: str) -> str:
            """Find all symbols that reference the given symbol."""
            return await server._dispatch(
                "find_referencing_symbols",
                {"name_path": name_path, "relative_path": relative_path},
            )

        async def ReadMemory(name: str) -> str:  # noqa: N802
            """Read a stored memory by name."""
            return await server._dispatch("ReadMemory", {"name": name})

        async def ListMemories(topic: str = "") -> str:  # noqa: N802
            """List all stored memories, optionally filtered by topic."""
            return await server._dispatch("ListMemories", {"topic": topic})

        async def GetCurrentConfig() -> str:  # noqa: N802
            """Return the current NerdVana configuration as JSON."""
            return await server._dispatch("GetCurrentConfig", {})

        for fn in (symbol_overview, find_symbol, find_referencing_symbols,
                   ReadMemory, ListMemories, GetCurrentConfig):
            self._fmcp.add_tool(fn, name=fn.__name__, description=fn.__doc__ or "")

    def _register_write_tools(self) -> None:
        """Register write tools (requires allow_write=True AND confirm=true in call)."""
        server = self

        async def replace_symbol_body(
            name_path: str,
            new_body: str,
            relative_path: str = "",
            confirm: bool = False,
        ) -> str:
            """Replace the body of a symbol. Requires confirm=true."""
            server._check_write_confirm(confirm)
            return await server._dispatch(
                "replace_symbol_body",
                {"name_path": name_path, "new_body": new_body,
                 "relative_path": relative_path or None, "confirm": confirm},
            )

        async def insert_before_symbol(
            name_path: str,
            content: str,
            relative_path: str = "",
            confirm: bool = False,
        ) -> str:
            """Insert content before a symbol. Requires confirm=true."""
            server._check_write_confirm(confirm)
            return await server._dispatch(
                "insert_before_symbol",
                {"name_path": name_path, "content": content,
                 "relative_path": relative_path or None, "confirm": confirm},
            )

        async def insert_after_symbol(
            name_path: str,
            content: str,
            relative_path: str = "",
            confirm: bool = False,
        ) -> str:
            """Insert content after a symbol. Requires confirm=true."""
            server._check_write_confirm(confirm)
            return await server._dispatch(
                "insert_after_symbol",
                {"name_path": name_path, "content": content,
                 "relative_path": relative_path or None, "confirm": confirm},
            )

        async def RenameSymbol(  # noqa: N802
            name_path: str,
            new_name: str,
            relative_path: str = "",
            confirm: bool = False,
        ) -> str:
            """Rename a symbol project-wide. Requires confirm=true."""
            server._check_write_confirm(confirm)
            return await server._dispatch(
                "RenameSymbol",
                {"name_path": name_path, "new_name": new_name,
                 "relative_path": relative_path or None, "confirm": confirm},
            )

        async def WriteMemory(name: str, content: str, scope: str = "local", confirm: bool = False) -> str:  # noqa: N802
            """Write a memory entry. Requires confirm=true."""
            server._check_write_confirm(confirm)
            return await server._dispatch(
                "WriteMemory",
                {"name": name, "content": content, "scope": scope, "confirm": confirm},
            )

        async def EditMemory(name: str, new_content: str, confirm: bool = False) -> str:  # noqa: N802
            """Edit an existing memory entry. Requires confirm=true."""
            server._check_write_confirm(confirm)
            return await server._dispatch(
                "EditMemory",
                {"name": name, "new_content": new_content, "confirm": confirm},
            )

        async def DeleteMemory(name: str, confirm: bool = False) -> str:  # noqa: N802
            """Delete a memory entry. Requires confirm=true."""
            server._check_write_confirm(confirm)
            return await server._dispatch(
                "DeleteMemory",
                {"name": name, "confirm": confirm},
            )

        async def safe_delete_symbol(
            name_path: str,
            relative_path: str = "",
            confirm: bool = False,
        ) -> str:
            """Safely delete a symbol after verifying no remaining references. Requires confirm=true."""
            server._check_write_confirm(confirm)
            return await server._dispatch(
                "safe_delete_symbol",
                {"name_path": name_path, "relative_path": relative_path or None, "confirm": confirm},
            )

        async def restart_language_server(confirm: bool = False) -> str:
            """Restart the language server. Requires confirm=true."""
            server._check_write_confirm(confirm)
            return await server._dispatch(
                "restart_language_server",
                {"confirm": confirm},
            )

        for fn in (replace_symbol_body, insert_before_symbol, insert_after_symbol,
                   RenameSymbol, WriteMemory, EditMemory, DeleteMemory,
                   safe_delete_symbol, restart_language_server):
            self._fmcp.add_tool(fn, name=fn.__name__, description=fn.__doc__ or "")

    # ------------------------------------------------------------------
    # Dispatch — auth → ACL → audit → actual tool stub
    # ------------------------------------------------------------------

    def _check_write_confirm(self, confirm: bool) -> None:
        """Raise if server is read-only or confirm flag is missing (v3 §7.5)."""
        if not self.allow_write:
            raise PermissionError("server started in read-only mode")
        if not confirm:
            raise PermissionError(
                "write tool requires 'confirm: true' in the request payload"
            )

    def _resolve_identity(self) -> str:
        """Determine the client identity for the current invocation.

        For HTTP transport the identity is extracted from ``_request_auth``
        context-var (populated by ``_BearerAuthMiddleware``).  For stdio the
        server enforces UID equality at start-up; during a live stdio session
        all calls are considered to originate from the authenticated local user.
        """
        if self.transport == "http":
            auth = _request_auth.get()
            if auth is None or not auth.authenticated:
                raise PermissionError("unauthenticated: missing or invalid bearer token")
            return auth.client_identity
        if self.transport == "stdio":
            # stdio identity is validated once at server start via _verify_stdio_auth();
            # return the cached identity.
            return self._stdio_identity
        # Unknown transport — deny
        raise PermissionError(f"unauthenticated: unsupported transport {self.transport!r}")

    async def _dispatch(
        self,
        tool_name: str,
        args:      dict[str, Any],
        *,
        client_identity: str | None = None,
    ) -> str:
        """Route a tool call through auth → ACL → audit → execute.

        ``client_identity`` may be supplied directly (e.g. from tests); when
        *None* the identity is resolved from the active transport context via
        ``_resolve_identity``.
        """
        if client_identity is None:
            client_identity = self._resolve_identity()
        start_ms = int(time.monotonic() * 1000)
        try:
            # ACL check
            decision = self._acl.check(client_identity, tool_name)
            if not decision.allowed:
                self._audit.record(
                    client_identity = client_identity,
                    transport       = self.transport,
                    tool_name       = tool_name,
                    args            = args,
                    decision        = "denied",
                    duration_ms     = int(time.monotonic() * 1000) - start_ms,
                )
                raise PermissionError(f"ACL denied: {decision.reason}")

            # Execute
            result = await self._execute_tool(tool_name, args)

            self._audit.record(
                client_identity = client_identity,
                transport       = self.transport,
                tool_name       = tool_name,
                args            = args,
                decision        = "allowed",
                duration_ms     = int(time.monotonic() * 1000) - start_ms,
            )
            return str(result)

        except PermissionError:
            raise
        except Exception as exc:
            self._audit.record(
                client_identity = client_identity,
                transport       = self.transport,
                tool_name       = tool_name,
                args            = args,
                decision        = "error",
                duration_ms     = int(time.monotonic() * 1000) - start_ms,
                error_class     = type(exc).__name__,
            )
            raise

    async def _execute_tool(self, tool_name: str, args: dict[str, Any]) -> Any:
        """Execute the named nerdvana tool via ToolRegistry lookup.

        Looks up the tool in the pre-built ``_tool_map``, validates and parses
        ``args`` via ``BaseTool.parse_args``, then delegates to
        ``BaseTool.call``.  The raw ``ToolResult.content`` string is returned.

        Raises
        ------
        KeyError
            When *tool_name* is not present in the tool map (no LSP, or
            write tool requested while allow_write=False already checked by
            ``_check_write_confirm``).
        ValueError
            When argument validation fails (``BaseTool.validate_input``).
        """
        tool = self._tool_map.get(tool_name)
        if tool is None:
            available = sorted(self._tool_map)
            raise KeyError(
                f"tool {tool_name!r} not available in this server instance "
                f"(available: {available})"
            )

        # Parse and validate args
        parsed = tool.parse_args(args)
        ctx    = self._tool_context
        error  = tool.validate_input(parsed, ctx)
        if error:
            raise ValueError(f"invalid args for {tool_name!r}: {error}")

        result = await tool.call(parsed, ctx, can_use_tool=None)

        if result.is_error:
            # Surface tool errors as structured error payloads rather than
            # opaque strings so that MCP clients can detect failure.
            return json.dumps({"error": result.content})
        return result.content

    # ------------------------------------------------------------------
    # Entry points
    # ------------------------------------------------------------------

    def _verify_stdio_auth(self) -> None:
        """Authenticate the stdio caller via Unix socket UID check.

        Sets ``_stdio_identity`` on success.  Raises ``PermissionError`` on
        failure so that the server refuses to start rather than silently
        serving an unauthenticated session.
        """
        result = self._auth.authenticate_stdio()
        if not result.authenticated:
            raise PermissionError(
                f"stdio authentication failed: {result.reason}. "
                "The Unix socket must be owned by the current user with mode 0600."
            )
        self._stdio_identity = result.client_identity

    async def run(self) -> None:
        """Start the server using the configured transport."""
        if self.transport == "stdio":
            # Authenticate before opening the audit DB (fail-fast).
            self._verify_stdio_auth()
        self._audit.open()
        try:
            if self.transport == "stdio":
                await self._run_stdio()
            elif self.transport == "http":
                await self._run_http()
            else:
                raise ValueError(f"unknown transport: {self.transport!r}")
        finally:
            self._audit.close()

    async def _run_stdio(self) -> None:
        await self._fmcp.run_stdio_async()

    async def _run_http(self) -> None:
        if self.host == "0.0.0.0":
            print(
                "WARNING: server bound to 0.0.0.0 — all network interfaces exposed. "
                "Ensure firewall rules are in place.",
                file=sys.stderr,
            )
        # Wrap the FastMCP ASGI app with bearer-auth middleware so that every
        # HTTP request is authenticated before reaching tool handlers.
        import uvicorn
        starlette_app = self._fmcp.streamable_http_app()
        from starlette.applications import Starlette
        from starlette.routing import Mount
        protected = Starlette(
            routes=[Mount("/", app=starlette_app)],
        )
        # Inject the auth middleware
        protected.add_middleware(_BearerAuthMiddleware, auth_manager=self._auth)
        config = uvicorn.Config(
            app       = protected,
            host      = self.host,
            port      = self.port,
            log_level = "warning",
        )
        server = uvicorn.Server(config)
        await server.serve()

    # ------------------------------------------------------------------
    # Properties (for tests)
    # ------------------------------------------------------------------

    @property
    def fmcp(self) -> FastMCP:
        """Underlying FastMCP instance (for introspection / testing)."""
        return self._fmcp

    @property
    def auth(self) -> AuthManager:
        return self._auth

    @property
    def acl(self) -> ACLManager:
        return self._acl

    @property
    def audit(self) -> AuditLogger:
        return self._audit
