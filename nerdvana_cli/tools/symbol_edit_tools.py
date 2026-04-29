"""Symbol editing tools: ReplaceBody, InsertBefore, InsertAfter, SafeDelete.

All 4 tools use a 2-step preview/apply pattern backed by CodeEditor.
Shared helpers (_path_to_uri, _locate_symbol_lines, _do_apply, _find_symbol_end)
are defined at the bottom of this module.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, ClassVar

from nerdvana_cli.core.tool import BaseTool, ToolCategory, ToolContext, ToolSideEffect
from nerdvana_cli.types import ToolResult

if TYPE_CHECKING:
    from nerdvana_cli.core.code_editor import CodeEditor
    from nerdvana_cli.core.symbol import LanguageServerSymbolRetriever


# ---------------------------------------------------------------------------
# Arg classes
# ---------------------------------------------------------------------------


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
# Shared helpers
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

    ``start_line`` and ``end_line`` are 0-based; ``end_line`` is exclusive.
    Returns a ``ToolResult`` (error) if the symbol cannot be found.
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
    """Return the 0-based line index *after* the symbol body ends."""
    if start_line >= len(lines):
        return len(lines)

    def_indent = len(lines[start_line]) - len(lines[start_line].lstrip())

    for i in range(start_line + 1, len(lines)):
        stripped = lines[i].lstrip()
        if not stripped:
            continue
        indent = len(lines[i]) - len(stripped)
        if indent <= def_indent:
            return i

    return len(lines)


# ---------------------------------------------------------------------------
# Tool 1 — ReplaceSymbolBody
# ---------------------------------------------------------------------------


class ReplaceSymbolBodyTool(BaseTool[ReplaceSymbolBodyArgs]):
    """Replace a symbol's body in two steps: preview then apply."""

    name             = "replace_symbol_body"
    description_text = (
        "Replace the body of a symbol (function/method/class) in two steps. "
        "Step 1: supply name_path + relative_path + body -> get preview_id + diff. "
        "Step 2: supply preview_id + apply=True -> commit the change. "
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
        if args.apply and args.preview_id:
            return await _do_apply(self._editor, args.preview_id)

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

    async def _do_preview(
        self,
        name_path:     str,
        relative_path: str,
        new_body:      str,
        context:       ToolContext,
    ) -> ToolResult:
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

        target   = symbols[0]
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

        start_line = target.location.line - 1
        end_line   = _find_symbol_end(original_lines, start_line)

        uri       = _path_to_uri(abs_path)
        new_lines = new_body.splitlines(keepends=True)
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
        return await _do_apply(self._editor, preview_id)


# ---------------------------------------------------------------------------
# Tool 2 — InsertBeforeSymbol
# ---------------------------------------------------------------------------


class InsertBeforeSymbolTool(BaseTool[InsertBeforeSymbolArgs]):
    """Insert code immediately before a symbol definition."""

    name             = "insert_before_symbol"
    description_text = (
        "Insert code immediately before a symbol definition in two steps. "
        "Typical uses: new import statements, decorators, typedefs above a class. "
        "Step 1: supply name_path + relative_path + body -> get preview_id + diff. "
        "Step 2: supply preview_id + apply=True -> commit the change."
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
# Tool 3 — InsertAfterSymbol
# ---------------------------------------------------------------------------


class InsertAfterSymbolTool(BaseTool[InsertAfterSymbolArgs]):
    """Insert code immediately after a symbol body."""

    name             = "insert_after_symbol"
    description_text = (
        "Insert code immediately after a symbol body in two steps. "
        "Typical uses: adding a new method after an existing one, "
        "a sibling function, or a related constant. "
        "Step 1: supply name_path + relative_path + body -> get preview_id + diff. "
        "Step 2: supply preview_id + apply=True -> commit the change."
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
# Tool 4 — SafeDeleteSymbol
# ---------------------------------------------------------------------------


class SafeDeleteSymbolTool(BaseTool[SafeDeleteSymbolArgs]):
    """Delete a symbol only when it has zero references."""

    name             = "safe_delete_symbol"
    description_text = (
        "Delete a symbol (function/method/class) only when it has zero references. "
        "Step 1: supply name_path + relative_path -> check references. "
        "  If references found: returns blocked_by_references with reference list. "
        "  If zero references: returns preview_id + diff. "
        "Step 2: supply preview_id + apply=True -> commit the deletion."
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
        result = await _locate_symbol_lines(
            self._retriever, name_path, relative_path,
        )
        if isinstance(result, ToolResult):
            return result
        abs_path, start_line, end_line, original_lines = result

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
