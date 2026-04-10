"""LSP-backed tools: diagnostics, goto-definition, find-references, rename."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from nerdvana_cli.core.tool import BaseTool, ToolContext
from nerdvana_cli.types import ToolResult

if TYPE_CHECKING:
    from nerdvana_cli.core.lsp_client import LspClient


# ── Arg classes ─────────────────────────────────────────────────────────


class LspDiagnosticsArgs:
    def __init__(self, file_path: str):
        self.file_path = file_path


class LspPositionArgs:
    def __init__(self, file_path: str, line: int, symbol: str):
        self.file_path = file_path
        self.line      = line
        self.symbol    = symbol


class LspRenameArgs:
    def __init__(self, file_path: str, line: int, symbol: str, new_name: str):
        self.file_path = file_path
        self.line      = line
        self.symbol    = symbol
        self.new_name  = new_name


# ── Tool classes ─────────────────────────────────────────────────────────


class LspDiagnosticsTool(BaseTool[LspDiagnosticsArgs]):
    name             = "lsp_diagnostics"
    description_text = (
        "Run the language server's diagnostics on a file and return errors/warnings."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "Path to the source file"},
        },
        "required": ["file_path"],
    }
    is_concurrency_safe = True
    is_read_only        = True
    args_class          = LspDiagnosticsArgs

    def __init__(self, client: LspClient):
        super().__init__()
        self._client = client

    async def call(
        self,
        args:         LspDiagnosticsArgs,
        context:      ToolContext,
        can_use_tool: Any = None,
        on_progress:  Any = None,
    ) -> ToolResult:
        try:
            diags = await self._client.diagnostics(args.file_path)
        except Exception as e:
            return ToolResult(tool_use_id="", content=f"LSP error: {e}", is_error=True)

        if not diags:
            return ToolResult(
                tool_use_id="",
                content=f"No issues found in {args.file_path}",
            )

        lines = [
            f"[{d['severity']}] line {d['line']}:{d['col']} — {d['message']}"
            for d in diags
        ]
        return ToolResult(tool_use_id="", content="\n".join(lines))


class LspGotoDefinitionTool(BaseTool[LspPositionArgs]):
    name             = "lsp_goto_definition"
    description_text = (
        "Jump to the definition of a symbol using the language server."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string"},
            "line":      {"type": "integer", "description": "1-based line number"},
            "symbol":    {"type": "string",  "description": "Symbol name to look up"},
        },
        "required": ["file_path", "line", "symbol"],
    }
    is_concurrency_safe = True
    is_read_only        = True
    args_class          = LspPositionArgs

    def __init__(self, client: LspClient):
        super().__init__()
        self._client = client

    async def call(
        self,
        args:         LspPositionArgs,
        context:      ToolContext,
        can_use_tool: Any = None,
        on_progress:  Any = None,
    ) -> ToolResult:
        try:
            loc = await self._client.goto_definition(
                args.file_path, args.line, args.symbol,
            )
        except Exception as e:
            return ToolResult(tool_use_id="", content=f"LSP error: {e}", is_error=True)

        if not loc:
            return ToolResult(
                tool_use_id="",
                content=f"No definition found for '{args.symbol}'",
            )

        return ToolResult(
            tool_use_id="",
            content=f"Definition: {loc['file']}:{loc['line']}:{loc['col']}",
        )


class LspFindReferencesTool(BaseTool[LspPositionArgs]):
    name             = "lsp_find_references"
    description_text = "Find all references to a symbol using the language server."
    input_schema = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string"},
            "line":      {"type": "integer", "description": "1-based line number"},
            "symbol":    {"type": "string",  "description": "Symbol name to look up"},
        },
        "required": ["file_path", "line", "symbol"],
    }
    is_concurrency_safe = True
    is_read_only        = True
    args_class          = LspPositionArgs

    def __init__(self, client: LspClient):
        super().__init__()
        self._client = client

    async def call(
        self,
        args:         LspPositionArgs,
        context:      ToolContext,
        can_use_tool: Any = None,
        on_progress:  Any = None,
    ) -> ToolResult:
        try:
            refs = await self._client.find_references(
                args.file_path, args.line, args.symbol,
            )
        except Exception as e:
            return ToolResult(tool_use_id="", content=f"LSP error: {e}", is_error=True)

        if not refs:
            return ToolResult(
                tool_use_id="",
                content=f"No references found for '{args.symbol}'",
            )

        lines = [f"{r['file']}:{r['line']}:{r['col']}" for r in refs]
        return ToolResult(tool_use_id="", content="\n".join(lines))


class LspRenameTool(BaseTool[LspRenameArgs]):
    name             = "lsp_rename"
    description_text = (
        "Rename a symbol across the workspace using the language server. "
        "Returns changed file paths."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string"},
            "line":      {"type": "integer", "description": "1-based line number"},
            "symbol":    {"type": "string",  "description": "Current symbol name"},
            "new_name":  {"type": "string",  "description": "New symbol name"},
        },
        "required": ["file_path", "line", "symbol", "new_name"],
    }
    is_concurrency_safe = False
    is_read_only        = False
    args_class          = LspRenameArgs

    def __init__(self, client: LspClient):
        super().__init__()
        self._client = client

    async def call(
        self,
        args:         LspRenameArgs,
        context:      ToolContext,
        can_use_tool: Any = None,
        on_progress:  Any = None,
    ) -> ToolResult:
        try:
            result = await self._client.rename(
                args.file_path, args.line, args.symbol, args.new_name,
            )
        except Exception as e:
            return ToolResult(tool_use_id="", content=f"LSP error: {e}", is_error=True)

        changed = result.get("changed_files", [])
        if not changed:
            return ToolResult(tool_use_id="", content="No files changed.")

        summary = (
            f"Renamed '{args.symbol}' → '{args.new_name}' in {len(changed)} file(s):\n"
        )
        summary += "\n".join(f"  {f}" for f in changed)
        return ToolResult(tool_use_id="", content=summary)
