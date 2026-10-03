"""NerdVana MCP server.

Exposes nerdvana tools over MCP 1.0 (stdio + HTTP JSON-RPC).

Default (read-only) tools:
  symbol_overview, find_symbol, find_referencing_symbols, FileRead,
  ReadMemory, ListMemories, GetCurrentConfig

With ``--allow-write``:
  + replace_symbol_body, insert_before_symbol, insert_after_symbol, FileEdit,
    WriteMemory, EditMemory, DeleteMemory, safe_delete_symbol

FileRead tags every line with an anchor and FileEdit refuses a file that was not read or has changed since,
so another agent can use this server as its edit backend. Each client has a read ledger of its own.

Every tool call goes through AuthManager + ACLManager + AuditLogger.

The HTTP middleware lives in ``http_app``, TLS validation in ``tls`` and the MCP-visible tool
signatures in ``catalog``; this module wires them around the dispatch.

작성자: 최진호
작성일: 2026-04-18
"""

from __future__ import annotations

import asyncio
import json
import logging as _logging
import sys
import time
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from starlette.types import ASGIApp

from nerdvana_cli.core.tool import BaseTool, ToolContext
from nerdvana_cli.server.acl import ACLManager
from nerdvana_cli.server.audit import AuditLogger, Decision
from nerdvana_cli.server.auth import AuthManager
from nerdvana_cli.server.catalog import ToolCatalog
from nerdvana_cli.server.http_app import build_http_app, request_auth
from nerdvana_cli.server.quota import QuotaExceeded, QuotaPolicyResolver, QuotaStore
from nerdvana_cli.server.tls import TlsConfigurationError, TlsSettings, resolve_tls

__all__ = ["NerdvanaMcpServer", "TlsConfigurationError"]

_quota_log = _logging.getLogger("nerdvana.quota")


class NerdvanaMcpServer:
    """Thin orchestrator that wires auth/ACL/audit around an MCPServer instance.

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
        Path to the server certificate (PEM).  Enables TLS on the http
        transport.  May be a bundle that also carries the private key.
    tls_key:
        Path to the private key (PEM).  Required unless ``tls_cert`` already
        contains the key.
    tls_ca:
        Path to CA certificate for peer verification.  Setting it turns on
        client-certificate verification (mTLS).
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
        allow_write:      bool                     = False,
        transport:        str                      = "stdio",
        host:             str                      = "127.0.0.1",
        port:             int                      = 10830,
        tls_cert:         Path | None              = None,
        tls_key:          Path | None              = None,
        tls_ca:           Path | None              = None,
        auth_manager:     AuthManager | None       = None,
        acl_manager:      ACLManager  | None       = None,
        audit_logger:     AuditLogger | None       = None,
        quota_resolver:   QuotaPolicyResolver | None = None,
        quota_store:      QuotaStore | None        = None,
        # External project subprocess support
        project_path:     Path | None              = None,
        mode:             str  | None              = None,
    ) -> None:
        self.allow_write   = allow_write
        self.transport     = transport
        self.host          = host
        self.port          = port
        self.tls_cert      = tls_cert
        self.tls_key       = tls_key
        self.tls_ca        = tls_ca
        # Working directory override and mode name.
        self.project_path  = project_path
        self.mode          = mode

        # Validated at construction, applied in _run_http. Plaintext only when no TLS option was supplied.
        self._tls: TlsSettings = resolve_tls(transport, tls_cert, tls_key, tls_ca)

        self._auth:           AuthManager          = auth_manager   or AuthManager()
        self._acl:            ACLManager           = acl_manager    or ACLManager()
        self._audit:          AuditLogger          = audit_logger   or AuditLogger()
        # Quota: empty resolver → unconstrained policy → no enforcement until configured.
        self._quota_resolver: QuotaPolicyResolver  = quota_resolver or QuotaPolicyResolver()
        self._quota_store:    QuotaStore           = quota_store    or QuotaStore()

        self._fmcp: MCPServer = MCPServer(name="nerdvana")

        # stdio: resolved once at startup; http: resolved per-request from middleware
        self._stdio_identity: str = "anonymous"

        # name → BaseTool instance map; populated by _build_tool_map()
        self._tool_map: dict[str, BaseTool[Any]] = {}
        self._build_tool_map()
        ToolCatalog(self._fmcp, self._dispatch, self._check_write_confirm).register(allow_write)

    # ------------------------------------------------------------------
    # Tool map, live BaseTool instances for _execute_tool routing
    # ------------------------------------------------------------------

    def _build_tool_map(self) -> None:
        """Instantiate memory, config, and (if available) symbol tools.

        Symbol tools require a live language server; they are registered only
        when ``LspClient.has_any_server()`` returns True.  Missing LSP is not
        an error, the server degrades gracefully to memory + config tools only.
        """
        from nerdvana_cli.core.safety.profiles import ProfileManager
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
        self._client_contexts: dict[str, ToolContext] = {}
        self._cwd = cwd
        self._lsp: Any = None
        from nerdvana_cli.tools.file_tools import FileEditTool, FileReadTool

        self._tool_map["FileRead"] = FileReadTool()
        self._tool_map["FileEdit"] = FileEditTool()

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

        # Symbol tools, only when a language server is available
        try:
            from nerdvana_cli.codeintel.lsp_client import LspClient
            lsp = LspClient()
            if lsp.has_any_server():
                self._lsp = lsp
                from nerdvana_cli.codeintel.code_editor import CodeEditor
                from nerdvana_cli.codeintel.symbol import LanguageServerSymbolRetriever
                from nerdvana_cli.tools.symbol_tools import create_symbol_tools
                retriever = LanguageServerSymbolRetriever(client=lsp)
                editor    = CodeEditor(project_root=lsp._project_root)  # noqa: SLF001
                for sym_tool in create_symbol_tools(
                    client=lsp, retriever=retriever, editor=editor
                ):
                    self._tool_map[sym_tool.name] = sym_tool
        except Exception:  # noqa: BLE001, LSP absent or misconfigured; degrade silently
            pass

    # ------------------------------------------------------------------
    # Dispatch, auth → ACL → quota → audit → actual tool
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

        For HTTP transport the identity is extracted from ``request_auth``
        context-var (populated by ``BearerAuthMiddleware``).  For stdio the
        server enforces UID equality at start-up; during a live stdio session
        all calls are considered to originate from the authenticated local user.
        """
        if self.transport == "http":
            auth = request_auth.get()
            if auth is None or not auth.authenticated:
                raise PermissionError("unauthenticated: missing or invalid bearer token")
            return auth.client_identity
        if self.transport == "stdio":
            # stdio identity is validated once at server start via _verify_stdio_auth();
            # return the cached identity.
            return self._stdio_identity
        # Unknown transport, deny
        raise PermissionError(f"unauthenticated: unsupported transport {self.transport!r}")

    def _record_call(
        self,
        client_identity: str,
        tool_name:       str,
        args:            dict[str, Any],
        decision:        Decision,
        start_ms:        int,
        error_class:     str | None = None,
    ) -> None:
        """Write one audit row for the call that began at ``start_ms``."""
        self._audit.record(
            client_identity = client_identity,
            transport       = self.transport,
            tool_name       = tool_name,
            args            = args,
            decision        = decision,
            duration_ms     = int(time.monotonic() * 1000) - start_ms,
            error_class     = error_class,
        )

    def _enforce_acl(self, client_identity: str, tool_name: str, args: dict[str, Any], start_ms: int) -> None:
        """Raise ``PermissionError`` (and audit the denial) when the ACL refuses the call."""
        acl_decision = self._acl.check(client_identity, tool_name)
        if not acl_decision.allowed:
            self._record_call(client_identity, tool_name, args, "denied", start_ms)
            raise PermissionError(f"ACL denied: {acl_decision.reason}")

    def _enforce_quota(self, client_identity: str, tool_name: str, args: dict[str, Any], start_ms: int) -> None:
        """Take a quota slot, or raise ``QuotaExceeded`` (and audit the denial).

        ``ACLManager.effective_roles()`` provides the public roles API used to resolve the policy.
        """
        quota_policy   = self._quota_resolver.resolve(client_identity, roles=self._acl.effective_roles(client_identity))
        quota_decision = self._quota_store.check(client_identity, quota_policy)
        if quota_decision.allowed:
            return
        self._record_call(
            client_identity, tool_name, args, "denied", start_ms,
            error_class=f"quota_denied:{quota_decision.limit_name}",
        )
        # Structured warning for operators: grep logs for event=quota_exceeded.
        if self.transport == "http":
            _quota_log.warning(
                "quota_exceeded",
                extra={
                    "event":       "quota_exceeded",
                    "tenant":      client_identity,
                    "tool":        tool_name,
                    "limit":       quota_decision.limit_name,
                    "retry_after": quota_decision.retry_after_seconds,
                },
            )
        raise QuotaExceeded(
            reason               = quota_decision.reason,
            retry_after_seconds  = quota_decision.retry_after_seconds,
            limit_name           = quota_decision.limit_name,
        )

    async def _dispatch(
        self,
        tool_name: str,
        args:      dict[str, Any],
        *,
        client_identity: str | None = None,
    ) -> str:
        """Route a tool call through auth → ACL → quota → audit → execute.

        ``client_identity`` may be supplied directly (e.g. from tests); when
        *None* the identity is resolved from the active transport context via
        ``_resolve_identity``.
        """
        if client_identity is None:
            client_identity = self._resolve_identity()
        start_ms = int(time.monotonic() * 1000)
        try:
            self._enforce_acl(client_identity, tool_name, args, start_ms)
            self._enforce_quota(client_identity, tool_name, args, start_ms)

            # Execute, release the quota slot in the finally block.
            # _call_tool_raw returns a ToolResult so tokens can be extracted
            # before conversion to str.  On error raw_result stays None and
            # we release with tokens=0.
            raw_result: Any = None
            try:
                raw_result = await self._call_tool_raw(tool_name, args, client_identity)
            finally:
                tokens_used = getattr(raw_result, "tokens", 0) if raw_result is not None else 0
                self._quota_store.release(client_identity, tokens=tokens_used)

            self._record_call(client_identity, tool_name, args, "allowed", start_ms)
            return str(raw_result.content)

        except (PermissionError, QuotaExceeded):
            raise
        except Exception as exc:
            self._record_call(client_identity, tool_name, args, "error", start_ms, error_class=type(exc).__name__)
            raise

    def _context_for(self, client_identity: str | None) -> ToolContext:
        """The tool context of one client: its own read ledger, so one client's reads never vouch for another's edits."""
        if client_identity is None:
            return self._tool_context
        if client_identity not in self._client_contexts:
            context = ToolContext(cwd=self._cwd)
            context.state["session_id"] = f"mcp:{client_identity}"
            self._client_contexts[client_identity] = context
        return self._client_contexts[client_identity]

    async def _error_messages(self, path: str) -> list[str] | None:
        """Messages of the error-level diagnostics of a file, None when no language server could say."""
        if self._lsp is None:
            return None
        try:
            found = await asyncio.wait_for(self._lsp.diagnostics(path), timeout=5.0)
        except Exception:  # noqa: BLE001 - a slow or broken server must not fail an edit
            return None
        return [str(d.get("message", "")) for d in found if d.get("severity") == "error"]

    async def _call_tool_raw(self, tool_name: str, args: dict[str, Any], client_identity: str | None = None) -> Any:
        """Execute the named tool and return the raw ``ToolResult`` (with tokens).

        Internal method used by ``_dispatch`` so it can read ``ToolResult.tokens``
        before converting the result to ``str``.  All validation logic lives here;
        ``_execute_tool`` delegates to this and converts to ``str`` for callers
        that expect a plain string (tests, legacy call sites).

        Raises
        ------
        KeyError
            When *tool_name* is not present in the tool map.
        ValueError
            When argument validation fails.
        """
        from nerdvana_cli.types import ToolResult as _ToolResult

        tool = self._tool_map.get(tool_name)
        if tool is None:
            available = sorted(self._tool_map)
            raise KeyError(
                f"tool {tool_name!r} not available in this server instance "
                f"(available: {available})"
            )

        # Parse and validate args
        parsed = tool.parse_args(args)
        ctx    = self._context_for(client_identity)
        error  = tool.validate_input(parsed, ctx)
        if error:
            raise ValueError(f"invalid args for {tool_name!r}: {error}")

        target   = str(Path(ctx.cwd) / args["path"]) if tool_name == "FileEdit" else ""
        baseline = await self._error_messages(target) if target else None
        result   = await tool.call(parsed, ctx, can_use_tool=None)
        if target and baseline is not None and not result.is_error:
            fresh = [m for m in (await self._error_messages(target) or []) if m not in baseline]
            if fresh:
                result.content += "\n\nNew errors reported by the language server after this edit:\n" + "\n".join(f"- {m}" for m in fresh[:5])

        if result.is_error:
            # Wrap JSON error payload in a ToolResult so tokens is accessible.
            return _ToolResult(
                tool_use_id = result.tool_use_id,
                content     = json.dumps({"error": result.content}),
                is_error    = True,
                tokens      = getattr(result, "tokens", 0),
            )
        return result

    async def _execute_tool(self, tool_name: str, args: dict[str, Any]) -> str:
        """Execute the named nerdvana tool via ToolRegistry lookup.

        Looks up the tool in the pre-built ``_tool_map``, validates and parses
        ``args`` via ``BaseTool.parse_args``, then delegates to
        ``BaseTool.call``.  The ``ToolResult.content`` string is returned.

        Raises
        ------
        KeyError
            When *tool_name* is not present in the tool map (no LSP, or
            write tool requested while allow_write=False already checked by
            ``_check_write_confirm``).
        ValueError
            When argument validation fails (``BaseTool.validate_input``).
        """
        result = await self._call_tool_raw(tool_name, args)
        return str(result.content)

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

    def _http_app(self) -> ASGIApp:
        """The streamable-HTTP app behind the quota-error and bearer-auth middleware."""
        return build_http_app(self._fmcp, self.host, self._auth)

    async def _run_http(self) -> None:
        if self.host == "0.0.0.0":
            print(
                "WARNING: server bound to 0.0.0.0, all network interfaces exposed. "
                "Ensure firewall rules are in place.",
                file=sys.stderr,
            )
        import uvicorn
        config = uvicorn.Config(
            app       = self._http_app(),
            host      = self.host,
            port      = self.port,
            log_level = "warning",
            **self._tls.uvicorn_options(),
        )
        server = uvicorn.Server(config)
        await server.serve()

    # ------------------------------------------------------------------
    # Properties (for tests)
    # ------------------------------------------------------------------

    @property
    def fmcp(self) -> MCPServer:
        """Underlying MCPServer instance (for introspection / testing)."""
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
