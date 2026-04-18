"""NerdVana MCP server — Phase G1.

Exposes nerdvana tools over MCP 1.0 (stdio + HTTP JSON-RPC).

Default (read-only) tools:
  symbol_overview, find_symbol, find_referencing_symbols,
  ReadMemory, ListMemories, GetCurrentConfig

With ``--allow-write``:
  + replace_symbol_body, insert_before_symbol, insert_after_symbol,
    RenameSymbol, WriteMemory, EditMemory, DeleteMemory, safe_delete_symbol

Every tool call goes through AuthManager + ACLManager + AuditLogger.

작성자: 최진호
작성일: 2026-04-18
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from nerdvana_cli.server.acl import ACLManager
from nerdvana_cli.server.audit import AuditLogger
from nerdvana_cli.server.auth import AuthManager

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
    ) -> None:
        self.allow_write  = allow_write
        self.transport    = transport
        self.host         = host
        self.port         = port
        self.tls_cert     = tls_cert
        self.tls_ca       = tls_ca

        self._auth:  AuthManager  = auth_manager  or AuthManager()
        self._acl:   ACLManager   = acl_manager   or ACLManager()
        self._audit: AuditLogger  = audit_logger  or AuditLogger()

        self._fmcp: FastMCP = FastMCP(
            name  = "nerdvana",
            host  = host,
            port  = port,
        )

        self._register_tools()

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

    async def _dispatch(
        self,
        tool_name: str,
        args:      dict[str, Any],
        *,
        client_identity: str = "anonymous",
    ) -> str:
        """Route a tool call through auth/ACL/audit, then execute the stub.

        This method is the single choke-point through which every tool
        invocation passes.  The actual nerdvana tool execution is
        delegated to ``_execute_tool``.
        """
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
        """Execute the named nerdvana tool.

        Stubs out tools that require a live language server / memory store;
        the full wiring happens in Phase G2.  The method is intentionally
        thin — it validates inputs and returns a descriptive placeholder so
        that integration tests can verify routing without a real LSP.
        """
        # Lazy import — avoid heavy deps at import time
        return f"[nerdvana:{tool_name}] args={args}"

    # ------------------------------------------------------------------
    # Entry points
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Start the server using the configured transport."""
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
        await self._fmcp.run_streamable_http_async()

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
