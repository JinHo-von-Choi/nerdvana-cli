"""Phase D semantic symbol tools: Overview, Find, References (read-only).

3 read-only query tools backed by LspClient + LanguageServerSymbolRetriever.
Edit tools (ReplaceBody, InsertBefore, InsertAfter, SafeDelete) live in
symbol_edit_tools.py; the LSP lifecycle tool (RestartLanguageServer) lives in
lsp_tools.py.

This module re-exports all 8 Args + 8 Tool classes for backward compatibility.

작성자: 최진호
작성일: 2026-04-18
수정일: 2026-04-20 (split into symbol_tools / symbol_edit_tools / lsp_tools)
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, ClassVar

from nerdvana_cli.core.tool import BaseTool, ToolCategory, ToolContext, ToolSideEffect

# Re-export from sibling modules for backward compatibility
from nerdvana_cli.tools.lsp_tools import (  # noqa: F401
    RestartLanguageServerArgs,
    RestartLanguageServerTool,
)
from nerdvana_cli.tools.symbol_edit_tools import (  # noqa: F401
    InsertAfterSymbolArgs,
    InsertAfterSymbolTool,
    InsertBeforeSymbolArgs,
    InsertBeforeSymbolTool,
    ReplaceSymbolBodyArgs,
    ReplaceSymbolBodyTool,
    SafeDeleteSymbolArgs,
    SafeDeleteSymbolTool,
)
from nerdvana_cli.types import ToolResult

if TYPE_CHECKING:
    from nerdvana_cli.core.code_editor import CodeEditor
    from nerdvana_cli.core.lsp_client import LspClient
    from nerdvana_cli.core.symbol import LanguageServerSymbolRetriever


# ---------------------------------------------------------------------------
# Arg classes (read-only tools)
# ---------------------------------------------------------------------------


class SymbolOverviewArgs:
    def __init__(
        self,
        relative_path: str,
        depth:         int  = 0,
        with_graph:    bool = False,
    ) -> None:
        self.relative_path = relative_path
        self.depth         = depth
        self.with_graph    = with_graph


class FindSymbolArgs:
    def __init__(
        self,
        name_path:             str,
        substring_matching:    bool        = False,
        include_body:          bool        = False,
        within_relative_path:  str | None  = None,
    ) -> None:
        self.name_path            = name_path
        self.substring_matching   = substring_matching
        self.include_body         = include_body
        self.within_relative_path = within_relative_path


class FindReferencingSymbolsArgs:
    def __init__(self, name_path: str, relative_path: str) -> None:
        self.name_path     = name_path
        self.relative_path = relative_path


# ---------------------------------------------------------------------------
# Tool 1 — SymbolOverview
# ---------------------------------------------------------------------------


class SymbolOverviewTool(BaseTool[SymbolOverviewArgs]):
    """Return a structured outline of all symbols in a source file."""

    name             = "symbol_overview"
    description_text = (
        "Return a structured outline of all top-level symbols in a source file. "
        "depth=0 gives top-level symbols only; depth=1 includes one level of children. "
        "with_graph=True appends a compact Repo Map JSON with call-graph edges."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "relative_path": {
                "type":        "string",
                "description": "Path to the source file (relative to project root or absolute)",
            },
            "depth": {
                "type":        "integer",
                "description": "How many levels of children to include (0 = top-level only)",
                "default":     0,
            },
            "with_graph": {
                "type":        "boolean",
                "description": "Include compact Repo Map JSON (call-graph edges)",
                "default":     False,
            },
        },
        "required": ["relative_path"],
    }
    is_concurrency_safe                       = True
    args_class                                = SymbolOverviewArgs
    category:              ClassVar[ToolCategory]    = ToolCategory.SYMBOLIC
    side_effects:          ClassVar[ToolSideEffect]  = ToolSideEffect.EXTERNAL
    tags:                  ClassVar[frozenset[str]]  = frozenset({"lsp", "symbol"})
    requires_confirmation: ClassVar[bool]            = False

    def __init__(
        self,
        retriever: LanguageServerSymbolRetriever,
        client:    LspClient,
    ) -> None:
        super().__init__()
        self._retriever = retriever
        self._client    = client

    async def call(
        self,
        args:         SymbolOverviewArgs,
        context:      ToolContext,
        can_use_tool: Any = None,
        on_progress:  Any = None,
    ) -> ToolResult:
        try:
            symbols = await self._retriever.get_overview(
                args.relative_path, depth=args.depth,
            )
        except Exception as e:
            return ToolResult(tool_use_id="", content=f"LSP error: {e}", is_error=True)

        if not symbols:
            return ToolResult(
                tool_use_id="",
                content=f"No symbols found in {args.relative_path}",
            )

        sym_list = [s.to_dict(include_children=args.depth > 0) for s in symbols]
        output: dict[str, Any] = {"symbols": sym_list}

        if args.with_graph:
            from nerdvana_cli.core.symbol_graph import SymbolGraph  # noqa: PLC0415
            graph = SymbolGraph()
            for sym in symbols:
                graph.add_symbol(sym)
            output["repo_map"] = json.loads(graph.to_compact_json())

        return ToolResult(
            tool_use_id="",
            content=json.dumps(output, ensure_ascii=False, indent=2),
        )


# ---------------------------------------------------------------------------
# Tool 2 — FindSymbol
# ---------------------------------------------------------------------------


class FindSymbolTool(BaseTool[FindSymbolArgs]):
    """Find symbols by name-path within a file."""

    name             = "find_symbol"
    description_text = (
        "Find symbols matching a name-path expression within a file. "
        "name_path format: 'Parent/child' or 'Class/method'. "
        "within_relative_path restricts search to that file. "
        "substring_matching enables partial name match."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "name_path": {
                "type":        "string",
                "description": "Symbol path, e.g. 'MyClass/method' or 'function_name'",
            },
            "substring_matching": {
                "type":        "boolean",
                "description": "Match leaf segment as case-insensitive substring",
                "default":     False,
            },
            "include_body": {
                "type":        "boolean",
                "description": "Not yet implemented; reserved for 0.5.1",
                "default":     False,
            },
            "within_relative_path": {
                "type":        "string",
                "description": "Restrict search to this file (required for current implementation)",
            },
        },
        "required": ["name_path"],
    }
    is_concurrency_safe                       = True
    args_class                                = FindSymbolArgs
    category:              ClassVar[ToolCategory]    = ToolCategory.SYMBOLIC
    side_effects:          ClassVar[ToolSideEffect]  = ToolSideEffect.EXTERNAL
    tags:                  ClassVar[frozenset[str]]  = frozenset({"lsp", "symbol"})
    requires_confirmation: ClassVar[bool]            = False

    def __init__(self, retriever: LanguageServerSymbolRetriever) -> None:
        super().__init__()
        self._retriever = retriever

    async def call(
        self,
        args:         FindSymbolArgs,
        context:      ToolContext,
        can_use_tool: Any = None,
        on_progress:  Any = None,
    ) -> ToolResult:
        try:
            symbols = await self._retriever.find(
                name_path  = args.name_path,
                substring  = args.substring_matching,
                within     = args.within_relative_path,
            )
        except Exception as e:
            return ToolResult(tool_use_id="", content=f"LSP error: {e}", is_error=True)

        if not symbols:
            return ToolResult(
                tool_use_id="",
                content=f"No symbols found matching {args.name_path!r}",
            )

        results = [s.to_dict(include_children=False) for s in symbols]
        return ToolResult(
            tool_use_id="",
            content=json.dumps({"matches": results}, ensure_ascii=False, indent=2),
        )


# ---------------------------------------------------------------------------
# Tool 3 — FindReferencingSymbols
# ---------------------------------------------------------------------------


class FindReferencingSymbolsTool(BaseTool[FindReferencingSymbolsArgs]):
    """Find all locations that reference a given symbol."""

    name             = "find_referencing_symbols"
    description_text = (
        "Find all locations in the codebase that reference the given symbol. "
        "Returns file path, line, and column for each reference."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "name_path": {
                "type":        "string",
                "description": "Symbol name-path to find references for",
            },
            "relative_path": {
                "type":        "string",
                "description": "File containing the symbol definition",
            },
        },
        "required": ["name_path", "relative_path"],
    }
    is_concurrency_safe                       = True
    args_class                                = FindReferencingSymbolsArgs
    category:              ClassVar[ToolCategory]    = ToolCategory.SYMBOLIC
    side_effects:          ClassVar[ToolSideEffect]  = ToolSideEffect.EXTERNAL
    tags:                  ClassVar[frozenset[str]]  = frozenset({"lsp", "symbol"})
    requires_confirmation: ClassVar[bool]            = False

    def __init__(self, retriever: LanguageServerSymbolRetriever) -> None:
        super().__init__()
        self._retriever = retriever

    async def call(
        self,
        args:         FindReferencingSymbolsArgs,
        context:      ToolContext,
        can_use_tool: Any = None,
        on_progress:  Any = None,
    ) -> ToolResult:
        try:
            symbols = await self._retriever.find(
                name_path = args.name_path,
                within    = args.relative_path,
            )
        except Exception as e:
            return ToolResult(tool_use_id="", content=f"LSP error: {e}", is_error=True)

        if not symbols:
            return ToolResult(
                tool_use_id="",
                content=f"Symbol {args.name_path!r} not found in {args.relative_path}",
                is_error=True,
            )

        target = symbols[0]
        try:
            refs = await self._retriever.find_references(target)
        except Exception as e:
            return ToolResult(tool_use_id="", content=f"LSP error: {e}", is_error=True)

        if not refs:
            return ToolResult(
                tool_use_id="",
                content=f"No references found for {args.name_path!r}",
            )

        ref_list = [
            {"file": r.file_path, "line": r.line, "character": r.character}
            for r in refs
        ]
        return ToolResult(
            tool_use_id="",
            content=json.dumps({"references": ref_list}, ensure_ascii=False, indent=2),
        )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_symbol_tools(
    client:    LspClient,
    retriever: LanguageServerSymbolRetriever,
    editor:    CodeEditor,
) -> list[BaseTool[Any]]:
    """Return all 8 symbol tools ready for registry registration."""
    return [
        SymbolOverviewTool(retriever=retriever, client=client),
        FindSymbolTool(retriever=retriever),
        FindReferencingSymbolsTool(retriever=retriever),
        RestartLanguageServerTool(client=client),
        ReplaceSymbolBodyTool(retriever=retriever, editor=editor),
        InsertBeforeSymbolTool(retriever=retriever, editor=editor),
        InsertAfterSymbolTool(retriever=retriever, editor=editor),
        SafeDeleteSymbolTool(retriever=retriever, editor=editor),
    ]
