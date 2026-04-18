"""Phase D semantic symbol tools: Overview, Find, References, Restart, ReplaceBody.
Phase D.1 adds: InsertBeforeSymbol, InsertAfterSymbol, SafeDeleteSymbol.

8 tools backed by LspClient + LanguageServerSymbolRetriever + CodeEditor.

작성자: 최진호
작성일: 2026-04-18
수정일: 2026-04-18 (Phase D.1 — insert before/after, safe delete)
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, ClassVar

from nerdvana_cli.core.tool import BaseTool, ToolCategory, ToolContext, ToolSideEffect
from nerdvana_cli.types import ToolResult

if TYPE_CHECKING:
    from nerdvana_cli.core.code_editor import CodeEditor
    from nerdvana_cli.core.lsp_client import LspClient
    from nerdvana_cli.core.symbol import LanguageServerSymbolRetriever


# ---------------------------------------------------------------------------
# Arg classes
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


class RestartLanguageServerArgs:
    def __init__(self, language: str | None = None) -> None:
        self.language = language


class ReplaceSymbolBodyArgs:
    def __init__(
        self,
        name_path:     str        = "",
        relative_path: str        = "",
        body:          str | None = None,
        preview_id:    str | None = None,
        apply:         bool       = False,
    ) -> None:
        self.name_path     = name_path
        self.relative_path = relative_path
        self.body          = body
        self.preview_id    = preview_id
        self.apply         = apply


class InsertBeforeSymbolArgs:
    def __init__(
        self,
        name_path:     str        = "",
        relative_path: str        = "",
        body:          str | None = None,
        preview_id:    str | None = None,
        apply:         bool       = False,
    ) -> None:
        self.name_path     = name_path
        self.relative_path = relative_path
        self.body          = body
        self.preview_id    = preview_id
        self.apply         = apply


class InsertAfterSymbolArgs:
    def __init__(
        self,
        name_path:     str        = "",
        relative_path: str        = "",
        body:          str | None = None,
        preview_id:    str | None = None,
        apply:         bool       = False,
    ) -> None:
        self.name_path     = name_path
        self.relative_path = relative_path
        self.body          = body
        self.preview_id    = preview_id
        self.apply         = apply


class SafeDeleteSymbolArgs:
    def __init__(
        self,
        name_path:     str        = "",
        relative_path: str        = "",
        preview_id:    str | None = None,
        apply:         bool       = False,
    ) -> None:
        self.name_path     = name_path
        self.relative_path = relative_path
        self.preview_id    = preview_id
        self.apply         = apply


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
        retriever:    LanguageServerSymbolRetriever,
        client:       LspClient,
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
# Tool 4 — RestartLanguageServer
# ---------------------------------------------------------------------------


class RestartLanguageServerTool(BaseTool[RestartLanguageServerArgs]):
    """Restart one or all language server processes."""

    name             = "restart_language_server"
    description_text = (
        "Restart the language server process for a specific language (e.g. 'python', "
        "'typescript') or all servers when language is omitted. Use after installing "
        "packages or when the server becomes unresponsive."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "language": {
                "type":        "string",
                "description": "Language identifier: 'python', 'typescript', 'go', 'rust'. "
                               "Omit to restart all servers.",
            },
        },
        "required": [],
    }
    is_concurrency_safe                       = False
    args_class                                = RestartLanguageServerArgs
    category:              ClassVar[ToolCategory]    = ToolCategory.META
    side_effects:          ClassVar[ToolSideEffect]  = ToolSideEffect.EXTERNAL
    tags:                  ClassVar[frozenset[str]]  = frozenset({"lsp", "symbol"})
    requires_confirmation: ClassVar[bool]            = False

    _LANG_TO_EXT: ClassVar[dict[str, str]] = {
        "python":     ".py",
        "typescript": ".ts",
        "javascript": ".js",
        "go":         ".go",
        "rust":       ".rs",
    }

    def __init__(self, client: LspClient) -> None:
        super().__init__()
        self._client = client

    async def call(
        self,
        args:         RestartLanguageServerArgs,
        context:      ToolContext,
        can_use_tool: Any = None,
        on_progress:  Any = None,
    ) -> ToolResult:
        if args.language is not None:
            ext = self._LANG_TO_EXT.get(args.language.lower())
            if ext is None:
                return ToolResult(
                    tool_use_id="",
                    content=f"Unknown language {args.language!r}. "
                            f"Supported: {', '.join(self._LANG_TO_EXT)}",
                    is_error=True,
                )
            try:
                await self._client.shutdown_server(ext)
            except Exception as e:
                return ToolResult(
                    tool_use_id="",
                    content=f"Error restarting {args.language} server: {e}",
                    is_error=True,
                )
            # Clear disabled flag so next request re-launches the server
            self._client._disabled.discard(ext)   # noqa: SLF001
            return ToolResult(
                tool_use_id="",
                content=f"Language server for {args.language!r} restarted successfully.",
            )
        else:
            # Restart all
            restarted: list[str] = []
            for lang, ext in self._LANG_TO_EXT.items():
                try:
                    await self._client.shutdown_server(ext)
                    self._client._disabled.discard(ext)   # noqa: SLF001
                    restarted.append(lang)
                except Exception:
                    pass
            return ToolResult(
                tool_use_id="",
                content=f"Restarted language servers: {', '.join(restarted) or 'none'}",
            )


# ---------------------------------------------------------------------------
# Tool 5 — ReplaceSymbolBody
# ---------------------------------------------------------------------------


class ReplaceSymbolBodyTool(BaseTool[ReplaceSymbolBodyArgs]):
    """Replace a symbol's body in two steps: preview then apply.

    Step 1 — Generate preview:
        Call with ``name_path``, ``relative_path``, and ``body``.
        Returns ``{"preview_id": "...", "diff": "...", "kind": "replace_body"}``.

    Step 2 — Apply:
        Call with ``preview_id`` and ``apply=True``.
        Returns ``{"status": "applied"}`` or ``{"status": "STALE", "reason": "..."}``.
    """

    name             = "replace_symbol_body"
    description_text = (
        "Replace the body of a symbol (function/method/class) in two steps. "
        "Step 1: supply name_path + relative_path + body → get preview_id + diff. "
        "Step 2: supply preview_id + apply=True → commit the change. "
        "Returns STALE if the target file changed between steps."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "name_path": {
                "type":        "string",
                "description": "Symbol path, e.g. 'MyClass/my_method'",
            },
            "relative_path": {
                "type":        "string",
                "description": "File containing the symbol (relative to project root)",
            },
            "body": {
                "type":        "string",
                "description": "New body text for the symbol (step 1)",
            },
            "preview_id": {
                "type":        "string",
                "description": "ID returned by step 1 (step 2 only)",
            },
            "apply": {
                "type":        "boolean",
                "description": "Set True to commit a previously previewed edit (step 2)",
                "default":     False,
            },
        },
        "required": [],
    }
    is_concurrency_safe                       = False
    args_class                                = ReplaceSymbolBodyArgs
    category:              ClassVar[ToolCategory]    = ToolCategory.WRITE
    side_effects:          ClassVar[ToolSideEffect]  = ToolSideEffect.FILESYSTEM
    tags:                  ClassVar[frozenset[str]]  = frozenset({"lsp", "symbol", "edit"})
    requires_confirmation: ClassVar[bool]            = True

    def __init__(
        self,
        retriever: LanguageServerSymbolRetriever,
        editor:    CodeEditor,
    ) -> None:
        super().__init__()
        self._retriever = retriever
        self._editor    = editor

    async def call(
        self,
        args:         ReplaceSymbolBodyArgs,
        context:      ToolContext,
        can_use_tool: Any = None,
        on_progress:  Any = None,
    ) -> ToolResult:
        # --- Step 2: apply ---
        if args.apply and args.preview_id:
            return await self._do_apply(args.preview_id)

        # --- Step 1: preview ---
        if args.body and args.name_path and args.relative_path:
            return await self._do_preview(
                args.name_path, args.relative_path, args.body, context,
            )

        return ToolResult(
            tool_use_id="",
            content=(
                "Invalid arguments. "
                "Step 1: supply name_path + relative_path + body. "
                "Step 2: supply preview_id + apply=True."
            ),
            is_error=True,
        )

    # -- private --

    async def _do_preview(
        self,
        name_path:     str,
        relative_path: str,
        new_body:      str,
        context:       ToolContext,
    ) -> ToolResult:
        """Locate the symbol, build the replacement, and store a preview."""
        try:
            symbols = await self._retriever.find(
                name_path = name_path,
                within    = relative_path,
            )
        except Exception as e:
            return ToolResult(tool_use_id="", content=f"LSP error: {e}", is_error=True)

        if not symbols:
            return ToolResult(
                tool_use_id="",
                content=f"Symbol {name_path!r} not found in {relative_path}",
                is_error=True,
            )

        target  = symbols[0]
        abs_path = self._retriever._resolve(relative_path)   # noqa: SLF001

        try:
            with open(abs_path, encoding="utf-8") as fh:
                original_lines = fh.readlines()
        except OSError as e:
            return ToolResult(
                tool_use_id="",
                content=f"Cannot read {relative_path}: {e}",
                is_error=True,
            )

        # Determine replacement range (start line to end of body)
        start_line = target.location.line - 1   # 0-based

        # Determine the end of the symbol body by scanning for the next
        # same-or-lower-indentation line after the def/class header.
        end_line = _find_symbol_end(original_lines, start_line)

        # Build workspace edit (documentChanges format)
        uri         = _path_to_uri(abs_path)
        new_lines   = new_body.splitlines(keepends=True)
        if new_lines and not new_lines[-1].endswith("\n"):
            new_lines[-1] += "\n"

        workspace_edit: dict[str, Any] = {
            "documentChanges": [
                {
                    "textDocument": {"uri": uri, "version": None},
                    "edits": [
                        {
                            "range": {
                                "start": {"line": start_line, "character": 0},
                                "end":   {"line": end_line,   "character": 0},
                            },
                            "newText": "".join(new_lines),
                        }
                    ],
                }
            ]
        }

        # Build proposed new content for diff generation
        proposed = list(original_lines)
        proposed[start_line:end_line] = new_lines
        new_content = "".join(proposed)

        preview_id, diff_text = self._editor.create_preview(
            kind           = "replace_body",
            workspace_edit = workspace_edit,
            new_contents   = {abs_path: new_content},
        )

        return ToolResult(
            tool_use_id="",
            content=json.dumps(
                {"preview_id": preview_id, "diff": diff_text, "kind": "replace_body"},
                ensure_ascii=False,
            ),
        )

    async def _do_apply(self, preview_id: str) -> ToolResult:
        """Commit a previously created preview."""
        return await _do_apply(self._editor, preview_id)


# ---------------------------------------------------------------------------
# Tool 6 — InsertBeforeSymbol
# ---------------------------------------------------------------------------


class InsertBeforeSymbolTool(BaseTool[InsertBeforeSymbolArgs]):
    """Insert code immediately before a symbol definition.

    Step 1 — Generate preview:
        Call with ``name_path``, ``relative_path``, and ``body``.
        Returns ``{"preview_id": "...", "diff": "...", "kind": "insert_before"}``.

    Step 2 — Apply:
        Call with ``preview_id`` and ``apply=True``.
        Returns ``{"status": "applied"}`` or ``{"status": "STALE", "reason": "..."}``.
    """

    name             = "insert_before_symbol"
    description_text = (
        "Insert code immediately before a symbol definition in two steps. "
        "Typical uses: new import statements, decorators, typedefs above a class. "
        "Step 1: supply name_path + relative_path + body → get preview_id + diff. "
        "Step 2: supply preview_id + apply=True → commit the change."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "name_path": {
                "type":        "string",
                "description": "Symbol path, e.g. 'MyClass/my_method'",
            },
            "relative_path": {
                "type":        "string",
                "description": "File containing the symbol (relative to project root)",
            },
            "body": {
                "type":        "string",
                "description": "Code to insert before the symbol (step 1)",
            },
            "preview_id": {
                "type":        "string",
                "description": "ID returned by step 1 (step 2 only)",
            },
            "apply": {
                "type":        "boolean",
                "description": "Set True to commit a previously previewed edit (step 2)",
                "default":     False,
            },
        },
        "required": [],
    }
    is_concurrency_safe                       = False
    args_class                                = InsertBeforeSymbolArgs
    category:              ClassVar[ToolCategory]    = ToolCategory.WRITE
    side_effects:          ClassVar[ToolSideEffect]  = ToolSideEffect.FILESYSTEM
    tags:                  ClassVar[frozenset[str]]  = frozenset({"lsp", "symbol", "edit"})
    requires_confirmation: ClassVar[bool]            = True

    def __init__(
        self,
        retriever: LanguageServerSymbolRetriever,
        editor:    CodeEditor,
    ) -> None:
        super().__init__()
        self._retriever = retriever
        self._editor    = editor

    async def call(
        self,
        args:         InsertBeforeSymbolArgs,
        context:      ToolContext,
        can_use_tool: Any = None,
        on_progress:  Any = None,
    ) -> ToolResult:
        if args.apply and args.preview_id:
            return await _do_apply(self._editor, args.preview_id)

        if args.body and args.name_path and args.relative_path:
            return await self._do_preview(
                args.name_path, args.relative_path, args.body,
            )

        return ToolResult(
            tool_use_id="",
            content=(
                "Invalid arguments. "
                "Step 1: supply name_path + relative_path + body. "
                "Step 2: supply preview_id + apply=True."
            ),
            is_error=True,
        )

    async def _do_preview(
        self,
        name_path:     str,
        relative_path: str,
        body:          str,
    ) -> ToolResult:
        result = await _locate_symbol_lines(
            self._retriever, name_path, relative_path,
        )
        if isinstance(result, ToolResult):
            return result
        abs_path, start_line, _end_line, original_lines = result

        try:
            preview_id, diff_text = self._editor.prepare_insert_before(
                name_path      = name_path,
                relative_path  = relative_path,
                body           = body,
                abs_path       = abs_path,
                start_line     = start_line,
                original_lines = original_lines,
            )
        except Exception as e:
            return ToolResult(tool_use_id="", content=f"Preview error: {e}", is_error=True)

        return ToolResult(
            tool_use_id="",
            content=json.dumps(
                {"preview_id": preview_id, "diff": diff_text, "kind": "insert_before"},
                ensure_ascii=False,
            ),
        )


# ---------------------------------------------------------------------------
# Tool 7 — InsertAfterSymbol
# ---------------------------------------------------------------------------


class InsertAfterSymbolTool(BaseTool[InsertAfterSymbolArgs]):
    """Insert code immediately after a symbol body.

    Step 1 — Generate preview:
        Call with ``name_path``, ``relative_path``, and ``body``.
        Returns ``{"preview_id": "...", "diff": "...", "kind": "insert_after"}``.

    Step 2 — Apply:
        Call with ``preview_id`` and ``apply=True``.
        Returns ``{"status": "applied"}`` or ``{"status": "STALE", "reason": "..."}``.
    """

    name             = "insert_after_symbol"
    description_text = (
        "Insert code immediately after a symbol body in two steps. "
        "Typical uses: adding a new method after an existing one, "
        "a sibling function, or a related constant. "
        "Step 1: supply name_path + relative_path + body → get preview_id + diff. "
        "Step 2: supply preview_id + apply=True → commit the change."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "name_path": {
                "type":        "string",
                "description": "Symbol path, e.g. 'MyClass/my_method'",
            },
            "relative_path": {
                "type":        "string",
                "description": "File containing the symbol (relative to project root)",
            },
            "body": {
                "type":        "string",
                "description": "Code to insert after the symbol (step 1)",
            },
            "preview_id": {
                "type":        "string",
                "description": "ID returned by step 1 (step 2 only)",
            },
            "apply": {
                "type":        "boolean",
                "description": "Set True to commit a previously previewed edit (step 2)",
                "default":     False,
            },
        },
        "required": [],
    }
    is_concurrency_safe                       = False
    args_class                                = InsertAfterSymbolArgs
    category:              ClassVar[ToolCategory]    = ToolCategory.WRITE
    side_effects:          ClassVar[ToolSideEffect]  = ToolSideEffect.FILESYSTEM
    tags:                  ClassVar[frozenset[str]]  = frozenset({"lsp", "symbol", "edit"})
    requires_confirmation: ClassVar[bool]            = True

    def __init__(
        self,
        retriever: LanguageServerSymbolRetriever,
        editor:    CodeEditor,
    ) -> None:
        super().__init__()
        self._retriever = retriever
        self._editor    = editor

    async def call(
        self,
        args:         InsertAfterSymbolArgs,
        context:      ToolContext,
        can_use_tool: Any = None,
        on_progress:  Any = None,
    ) -> ToolResult:
        if args.apply and args.preview_id:
            return await _do_apply(self._editor, args.preview_id)

        if args.body and args.name_path and args.relative_path:
            return await self._do_preview(
                args.name_path, args.relative_path, args.body,
            )

        return ToolResult(
            tool_use_id="",
            content=(
                "Invalid arguments. "
                "Step 1: supply name_path + relative_path + body. "
                "Step 2: supply preview_id + apply=True."
            ),
            is_error=True,
        )

    async def _do_preview(
        self,
        name_path:     str,
        relative_path: str,
        body:          str,
    ) -> ToolResult:
        result = await _locate_symbol_lines(
            self._retriever, name_path, relative_path,
        )
        if isinstance(result, ToolResult):
            return result
        abs_path, _start_line, end_line, original_lines = result

        try:
            preview_id, diff_text = self._editor.prepare_insert_after(
                name_path      = name_path,
                relative_path  = relative_path,
                body           = body,
                abs_path       = abs_path,
                end_line       = end_line,
                original_lines = original_lines,
            )
        except Exception as e:
            return ToolResult(tool_use_id="", content=f"Preview error: {e}", is_error=True)

        return ToolResult(
            tool_use_id="",
            content=json.dumps(
                {"preview_id": preview_id, "diff": diff_text, "kind": "insert_after"},
                ensure_ascii=False,
            ),
        )


# ---------------------------------------------------------------------------
# Tool 8 — SafeDeleteSymbol
# ---------------------------------------------------------------------------


class SafeDeleteSymbolTool(BaseTool[SafeDeleteSymbolArgs]):
    """Delete a symbol only when it has zero references.

    Step 1 — Check & generate preview:
        Call with ``name_path`` and ``relative_path``.
        - If references exist: returns ``{"status": "blocked_by_references",
          "references": [...]}``. No preview is stored; no filesystem change occurs.
        - If no references: returns ``{"preview_id": "...", "diff": "...",
          "kind": "delete"}``.

    Step 2 — Apply:
        Call with ``preview_id`` and ``apply=True``.
        Returns ``{"status": "applied"}`` or ``{"status": "STALE", ...}``.
    """

    name             = "safe_delete_symbol"
    description_text = (
        "Delete a symbol (function/method/class) only when it has zero references. "
        "Step 1: supply name_path + relative_path → check references. "
        "  If references found: returns blocked_by_references with reference list. "
        "  If zero references: returns preview_id + diff. "
        "Step 2: supply preview_id + apply=True → commit the deletion."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "name_path": {
                "type":        "string",
                "description": "Symbol path, e.g. 'MyClass/my_method'",
            },
            "relative_path": {
                "type":        "string",
                "description": "File containing the symbol (relative to project root)",
            },
            "preview_id": {
                "type":        "string",
                "description": "ID returned by step 1 (step 2 only)",
            },
            "apply": {
                "type":        "boolean",
                "description": "Set True to commit a previously previewed deletion (step 2)",
                "default":     False,
            },
        },
        "required": [],
    }
    is_concurrency_safe                       = False
    args_class                                = SafeDeleteSymbolArgs
    category:              ClassVar[ToolCategory]    = ToolCategory.DESTRUCTIVE
    side_effects:          ClassVar[ToolSideEffect]  = ToolSideEffect.FILESYSTEM
    tags:                  ClassVar[frozenset[str]]  = frozenset({"lsp", "symbol", "delete", "safe"})
    requires_confirmation: ClassVar[bool]            = True

    def __init__(
        self,
        retriever: LanguageServerSymbolRetriever,
        editor:    CodeEditor,
    ) -> None:
        super().__init__()
        self._retriever = retriever
        self._editor    = editor

    async def call(
        self,
        args:         SafeDeleteSymbolArgs,
        context:      ToolContext,
        can_use_tool: Any = None,
        on_progress:  Any = None,
    ) -> ToolResult:
        if args.apply and args.preview_id:
            return await _do_apply(self._editor, args.preview_id)

        if args.name_path and args.relative_path:
            return await self._do_preview(args.name_path, args.relative_path)

        return ToolResult(
            tool_use_id="",
            content=(
                "Invalid arguments. "
                "Step 1: supply name_path + relative_path. "
                "Step 2: supply preview_id + apply=True."
            ),
            is_error=True,
        )

    async def _do_preview(
        self,
        name_path:     str,
        relative_path: str,
    ) -> ToolResult:
        # --- locate symbol ---
        result = await _locate_symbol_lines(
            self._retriever, name_path, relative_path,
        )
        if isinstance(result, ToolResult):
            return result
        abs_path, start_line, end_line, original_lines = result

        # --- reference check (safe-delete gate) ---
        try:
            symbols = await self._retriever.find(
                name_path = name_path,
                within    = relative_path,
            )
        except Exception as e:
            return ToolResult(tool_use_id="", content=f"LSP error: {e}", is_error=True)

        target = symbols[0]
        try:
            refs = await self._retriever.find_references(target)
        except Exception as e:
            return ToolResult(tool_use_id="", content=f"LSP error: {e}", is_error=True)

        if refs:
            ref_list = [
                {"file": r.file_path, "line": r.line, "character": r.character}
                for r in refs
            ]
            return ToolResult(
                tool_use_id="",
                content=json.dumps(
                    {"status": "blocked_by_references", "references": ref_list},
                    ensure_ascii=False,
                ),
            )

        # --- build delete preview ---
        try:
            preview_id, diff_text = self._editor.prepare_safe_delete(
                name_path      = name_path,
                relative_path  = relative_path,
                abs_path       = abs_path,
                start_line     = start_line,
                end_line       = end_line,
                original_lines = original_lines,
            )
        except Exception as e:
            return ToolResult(tool_use_id="", content=f"Preview error: {e}", is_error=True)

        return ToolResult(
            tool_use_id="",
            content=json.dumps(
                {"preview_id": preview_id, "diff": diff_text, "kind": "delete"},
                ensure_ascii=False,
            ),
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _path_to_uri(abs_path: str) -> str:
    from pathlib import Path  # noqa: PLC0415
    return Path(abs_path).resolve().as_uri()


async def _locate_symbol_lines(
    retriever:     LanguageServerSymbolRetriever,
    name_path:     str,
    relative_path: str,
) -> tuple[str, int, int, list[str]] | ToolResult:
    """Locate a symbol and return (abs_path, start_line, end_line, original_lines).

    ``start_line`` and ``end_line`` are 0-based; ``end_line`` is exclusive (the
    first line *after* the symbol body — same convention as ``_find_symbol_end``).

    Returns a ``ToolResult`` (error) if the symbol cannot be found or the file
    cannot be read.
    """
    try:
        symbols = await retriever.find(name_path=name_path, within=relative_path)
    except Exception as e:
        return ToolResult(tool_use_id="", content=f"LSP error: {e}", is_error=True)

    if not symbols:
        return ToolResult(
            tool_use_id="",
            content=f"Symbol {name_path!r} not found in {relative_path}",
            is_error=True,
        )

    target   = symbols[0]
    abs_path = retriever._resolve(relative_path)   # noqa: SLF001

    try:
        with open(abs_path, encoding="utf-8") as fh:
            original_lines = fh.readlines()
    except OSError as e:
        return ToolResult(
            tool_use_id="",
            content=f"Cannot read {relative_path}: {e}",
            is_error=True,
        )

    start_line = target.location.line - 1   # convert to 0-based
    end_line   = _find_symbol_end(original_lines, start_line)

    return abs_path, start_line, end_line, original_lines


async def _do_apply(editor: CodeEditor, preview_id: str) -> ToolResult:
    """Apply a previously created preview (shared by all edit tools)."""
    from nerdvana_cli.core.code_editor import (  # noqa: PLC0415
        StalePreviewError,
        UnknownPreviewError,
    )
    try:
        result = editor.apply(preview_id)
    except UnknownPreviewError:
        return ToolResult(
            tool_use_id="",
            content=json.dumps(
                {"status": "ERROR", "reason": f"No preview with id={preview_id!r}"}
            ),
            is_error=True,
        )
    except StalePreviewError as e:
        return ToolResult(
            tool_use_id="",
            content=json.dumps(
                {"status": "STALE", "reason": str(e), "changed": e.changed_paths}
            ),
            is_error=True,
        )
    return ToolResult(
        tool_use_id="",
        content=json.dumps(result, ensure_ascii=False),
    )


def _find_symbol_end(lines: list[str], start_line: int) -> int:
    """Return the 0-based line index *after* the symbol body ends.

    Strategy: the first line at or after ``start_line + 1`` that has
    indentation ≤ the definition-line indentation (and is non-empty)
    marks the boundary.  If no such line is found, returns ``len(lines)``.
    """
    if start_line >= len(lines):
        return len(lines)

    def_indent = len(lines[start_line]) - len(lines[start_line].lstrip())

    for i in range(start_line + 1, len(lines)):
        stripped = lines[i].lstrip()
        if not stripped:
            continue   # blank line — keep scanning
        indent = len(lines[i]) - len(stripped)
        if indent <= def_indent:
            return i

    return len(lines)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_symbol_tools(
    client:       LspClient,
    retriever:    LanguageServerSymbolRetriever,
    editor:       CodeEditor,
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
