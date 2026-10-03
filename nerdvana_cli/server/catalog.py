"""Tool catalogue of the NerdVana MCP server.

Declares the MCP-visible signature of each tool and forwards the call to the server dispatch.
The signatures and docstrings are what MCP clients see, so they are part of the wire contract.

작성자: 최진호
작성일: 2026-10-03
"""

from __future__ import annotations

import functools
from collections.abc import Awaitable, Callable
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from nerdvana_cli.server.quota import QuotaExceeded

Dispatch       = Callable[[str, dict[str, Any]], Awaitable[str]]
ConfirmWrite   = Callable[[bool], None]

READ_ONLY_TOOLS: frozenset[str] = frozenset({
    "FileRead",
    "symbol_overview",
    "find_symbol",
    "find_referencing_symbols",
    "ReadMemory",
    "ListMemories",
    "GetCurrentConfig",
})

WRITE_TOOLS: frozenset[str] = frozenset({
    "FileEdit",
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


class ToolCatalog:
    """Registers the MCP tools of one server on its ``MCPServer``.

    Parameters
    ----------
    fmcp:
        The MCPServer the tools are added to.
    dispatch:
        Server dispatch that every tool call goes through (auth, ACL, quota, audit).
    confirm_write:
        Raises ``PermissionError`` when the server is read-only or ``confirm`` is not true.
    """

    def __init__(self, fmcp: MCPServer, dispatch: Dispatch, confirm_write: ConfirmWrite) -> None:
        self._fmcp          = fmcp
        self._dispatch      = dispatch
        self._confirm_write = confirm_write

    def register(self, allow_write: bool) -> None:
        """Register all MCP tools according to the allow_write flag."""
        self._register_read_only_tools()
        if allow_write:
            self._register_symbol_edit_tools()
            self._register_memory_write_tools()
            self._register_symbol_structure_tools()
            self._register_file_edit()

    def _add_tool(self, fn: Any) -> None:
        """Register ``fn`` so a refusal reaches the client with its reason.

        MCPServer reports an exception it does not know as ``Error executing tool <name>``
        and keeps the text on the server; a ``ToolError`` keeps its message. A denied, rate
        limited or malformed call is the client's to read and correct, so those become
        ``ToolError``.
        """
        @functools.wraps(fn)
        async def surfaced(*args: Any, **kwargs: Any) -> Any:
            try:
                return await fn(*args, **kwargs)
            except (PermissionError, QuotaExceeded, ValueError, KeyError) as exc:
                raise ToolError(str(exc)) from exc

        self._fmcp.add_tool(surfaced, name=fn.__name__, description=fn.__doc__ or "")

    def _register_read_only_tools(self) -> None:
        """Register the default read-only tools."""
        dispatch = self._dispatch

        async def symbol_overview(relative_path: str, depth: int = 0, with_graph: bool = False) -> str:
            """List symbols in a source file at the given path."""
            return await dispatch(
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
            return await dispatch(
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
            return await dispatch(
                "find_referencing_symbols",
                {"name_path": name_path, "relative_path": relative_path},
            )

        async def ReadMemory(name: str) -> str:  # noqa: N802
            """Read a stored memory by name."""
            return await dispatch("ReadMemory", {"name": name})

        async def ListMemories(topic: str = "") -> str:  # noqa: N802
            """List all stored memories, optionally filtered by topic."""
            return await dispatch("ListMemories", {"topic": topic})

        async def GetCurrentConfig() -> str:  # noqa: N802
            """Return the current NerdVana configuration as JSON."""
            return await dispatch("GetCurrentConfig", {})

        async def FileRead(path: str, offset: int = 0, limit: int = 0) -> str:  # noqa: N802
            """Read a file; every line is prefixed with an anchor N#hhhhhh (line number and content hash) that FileEdit accepts."""
            return await dispatch("FileRead", {"path": path, "offset": offset, "limit": limit})

        for fn in (symbol_overview, find_symbol, find_referencing_symbols, FileRead,
                   ReadMemory, ListMemories, GetCurrentConfig):
            self._add_tool(fn)

    def _register_symbol_edit_tools(self) -> None:
        """Register the symbol body write tools (require allow_write=True AND confirm=true in call)."""
        dispatch = self._dispatch
        confirm_write = self._confirm_write

        async def replace_symbol_body(
            name_path: str,
            new_body: str,
            relative_path: str = "",
            confirm: bool = False,
        ) -> str:
            """Replace the body of a symbol. Requires confirm=true."""
            confirm_write(confirm)
            return await dispatch(
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
            confirm_write(confirm)
            return await dispatch(
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
            confirm_write(confirm)
            return await dispatch(
                "insert_after_symbol",
                {"name_path": name_path, "content": content,
                 "relative_path": relative_path or None, "confirm": confirm},
            )

        for fn in (replace_symbol_body, insert_before_symbol, insert_after_symbol):
            self._add_tool(fn)

    def _register_memory_write_tools(self) -> None:
        """Register the memory write tools (require allow_write=True AND confirm=true in call)."""
        dispatch = self._dispatch
        confirm_write = self._confirm_write

        async def WriteMemory(name: str, content: str, scope: str, confirm: bool = False) -> str:  # noqa: N802
            """Write a memory entry. scope is one of project_rule, project_knowledge, user_global, agent_experience. Requires confirm=true."""
            confirm_write(confirm)
            return await dispatch(
                "WriteMemory",
                {"name": name, "content": content, "scope": scope, "confirm": confirm},
            )

        async def EditMemory(name: str, needle: str, repl: str, mode: str = "literal", confirm: bool = False) -> str:  # noqa: N802
            """Replace every occurrence of needle with repl in an existing memory (mode literal or regex). Requires confirm=true."""
            confirm_write(confirm)
            return await dispatch(
                "EditMemory",
                {"name": name, "needle": needle, "repl": repl, "mode": mode, "confirm": confirm},
            )

        async def DeleteMemory(name: str, confirm: bool = False) -> str:  # noqa: N802
            """Delete a memory entry. Requires confirm=true."""
            confirm_write(confirm)
            return await dispatch(
                "DeleteMemory",
                {"name": name, "confirm": confirm},
            )

        for fn in (WriteMemory, EditMemory, DeleteMemory):
            self._add_tool(fn)

    def _register_symbol_structure_tools(self) -> None:
        """Register RenameSymbol, safe_delete_symbol and restart_language_server (require allow_write=True AND confirm=true)."""
        dispatch = self._dispatch
        confirm_write = self._confirm_write

        async def RenameSymbol(  # noqa: N802
            name_path: str,
            new_name: str,
            relative_path: str = "",
            confirm: bool = False,
        ) -> str:
            """Rename a symbol project-wide. Requires confirm=true."""
            confirm_write(confirm)
            return await dispatch(
                "RenameSymbol",
                {"name_path": name_path, "new_name": new_name,
                 "relative_path": relative_path or None, "confirm": confirm},
            )

        async def safe_delete_symbol(
            name_path: str,
            relative_path: str = "",
            confirm: bool = False,
        ) -> str:
            """Safely delete a symbol after verifying no remaining references. Requires confirm=true."""
            confirm_write(confirm)
            return await dispatch(
                "safe_delete_symbol",
                {"name_path": name_path, "relative_path": relative_path or None, "confirm": confirm},
            )

        async def restart_language_server(confirm: bool = False) -> str:
            """Restart the language server. Requires confirm=true."""
            confirm_write(confirm)
            return await dispatch(
                "restart_language_server",
                {"confirm": confirm},
            )

        for fn in (RenameSymbol, safe_delete_symbol, restart_language_server):
            self._add_tool(fn)

    def _register_file_edit(self) -> None:
        """Register FileEdit, the write counterpart of FileRead (requires allow_write=True AND confirm=true in call)."""
        dispatch = self._dispatch
        confirm_write = self._confirm_write

        async def FileEdit(  # noqa: N802
            path: str,
            new_string: str,
            anchor_hash: str = "",
            old_string: str = "",
            replace_all: bool = False,
            confirm: bool = False,
        ) -> str:
            """Edit a file read with FileRead: replace the line named by anchor_hash (N#hhhhhh), or an exact old_string. Refused when the file changed since this client read it. Requires confirm=true."""
            confirm_write(confirm)
            return await dispatch(
                "FileEdit",
                {"path": path, "new_string": new_string, "anchor_hash": anchor_hash or None,
                 "old_string": old_string or None, "replace_all": replace_all},
            )

        self._add_tool(FileEdit)
