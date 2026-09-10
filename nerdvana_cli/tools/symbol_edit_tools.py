"""Symbol editing tools: ReplaceBody, InsertBefore, InsertAfter, SafeDelete.

All four tools share one two-step preview/apply shape, so the argument objects,
the input schema and the step dispatch in :meth:`SymbolEditTool.call` come from
a common base. Only the per-kind preview construction differs.

``CodeEditor`` is entirely synchronous and reads and writes whole files, so
every call into it, and every direct file read here, is handed to
``asyncio.to_thread``. Calling them straight from these coroutines would stall
the event loop for the duration of the disk access.

작성자: 최진호
작성일: 2026-04-20
수정일: 2026-09-11
"""

from __future__ import annotations

import asyncio
import json
from abc import abstractmethod
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, ClassVar, TypeVar, cast

from nerdvana_cli.core.tool import BaseTool, ToolCategory, ToolContext, ToolSideEffect
from nerdvana_cli.types import ToolResult

if TYPE_CHECKING:
    from nerdvana_cli.core.code_editor import CodeEditor
    from nerdvana_cli.core.symbol import LanguageServerSymbolRetriever


# ---------------------------------------------------------------------------
# Arg classes
# ---------------------------------------------------------------------------


class SymbolEditArgs:
    """Arguments every two-step symbol edit accepts.

    Step 1 supplies ``name_path`` and ``relative_path``; step 2 supplies
    ``preview_id`` together with ``apply=True``.
    """

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


class SymbolBodyEditArgs(SymbolEditArgs):
    """Arguments for the three edits that carry a body payload in step 1."""

    def __init__(
        self,
        name_path:     str        = "",
        relative_path: str        = "",
        body:          str | None = None,
        preview_id:    str | None = None,
        apply:         bool       = False,
    ) -> None:
        super().__init__(name_path, relative_path, preview_id, apply)
        self.body = body


class ReplaceSymbolBodyArgs(SymbolBodyEditArgs):
    """Arguments for ``replace_symbol_body``."""


class InsertBeforeSymbolArgs(SymbolBodyEditArgs):
    """Arguments for ``insert_before_symbol``."""


class InsertAfterSymbolArgs(SymbolBodyEditArgs):
    """Arguments for ``insert_after_symbol``."""


class SafeDeleteSymbolArgs(SymbolEditArgs):
    """Arguments for ``safe_delete_symbol``; the deletion carries no body."""


# ---------------------------------------------------------------------------
# Input schema
# ---------------------------------------------------------------------------


def _edit_schema(body_description: str | None, apply_description: str) -> dict[str, Any]:
    """Build the two-step input schema shared by the symbol edit tools.

    Parameters
    ----------
    body_description:
        Description of the step-1 ``body`` field, or None for the tools that
        take no payload (safe delete).
    apply_description:
        Description of the step-2 ``apply`` flag.
    """
    properties: dict[str, Any] = {
        "name_path": {
            "type":        "string",
            "description": "Symbol path, e.g. 'MyClass/my_method'",
        },
        "relative_path": {
            "type":        "string",
            "description": "File containing the symbol (relative to project root)",
        },
    }
    if body_description is not None:
        properties["body"] = {"type": "string", "description": body_description}
    properties["preview_id"] = {
        "type":        "string",
        "description": "ID returned by step 1 (step 2 only)",
    }
    properties["apply"] = {
        "type":        "boolean",
        "description": apply_description,
        "default":     False,
    }
    return {"type": "object", "properties": properties, "required": []}


_APPLY_EDIT_DESC   = "Set True to commit a previously previewed edit (step 2)"
_APPLY_DELETE_DESC = "Set True to commit a previously previewed deletion (step 2)"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _path_to_uri(abs_path: str) -> str:
    from pathlib import Path  # noqa: PLC0415
    return Path(abs_path).resolve().as_uri()


def _read_lines(abs_path: str) -> list[str]:
    """Read a whole file into a list of lines.

    Blocking; every caller reaches it through ``asyncio.to_thread``.
    """
    with open(abs_path, encoding="utf-8") as fh:
        return fh.readlines()


def _preview_result(preview_id: str, diff_text: str, kind: str) -> ToolResult:
    """Wrap a freshly created preview as the step-1 tool result."""
    return ToolResult(
        tool_use_id="",
        content=json.dumps(
            {"preview_id": preview_id, "diff": diff_text, "kind": kind},
            ensure_ascii=False,
        ),
    )


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
        original_lines = await asyncio.to_thread(_read_lines, abs_path)
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
        result = await asyncio.to_thread(editor.apply, preview_id)
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
# Shared tool base
# ---------------------------------------------------------------------------


ArgsT = TypeVar("ArgsT", bound=SymbolEditArgs)


class SymbolEditTool(BaseTool[ArgsT]):
    """Two-step preview/apply plumbing shared by the four symbol edit tools.

    Subclasses supply the tool identity (name, description, schema, args class)
    and one :meth:`_do_preview` that builds the step-1 preview for their kind.
    """

    is_concurrency_safe                       = False
    category:              ClassVar[ToolCategory]   = ToolCategory.WRITE
    side_effects:          ClassVar[ToolSideEffect] = ToolSideEffect.FILESYSTEM
    tags:                  ClassVar[frozenset[str]] = frozenset({"lsp", "symbol", "edit"})
    requires_confirmation: ClassVar[bool]           = True

    #: Whether step 1 needs a body payload. Safe delete does not.
    requires_body: ClassVar[bool] = True
    #: Step-1 half of the invalid-argument message.
    step1_hint:    ClassVar[str]  = "Step 1: supply name_path + relative_path + body. "

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
        args:         ArgsT,
        context:      ToolContext,
        can_use_tool: Any = None,
        on_progress:  Any = None,
    ) -> ToolResult:
        if args.apply and args.preview_id:
            return await _do_apply(self._editor, args.preview_id)

        body = getattr(args, "body", None)
        if args.name_path and args.relative_path and (body or not self.requires_body):
            return await self._do_preview(args.name_path, args.relative_path, body or "")

        return ToolResult(
            tool_use_id="",
            content=(
                "Invalid arguments. "
                + self.step1_hint
                + "Step 2: supply preview_id + apply=True."
            ),
            is_error=True,
        )

    @abstractmethod
    async def _do_preview(
        self,
        name_path:     str,
        relative_path: str,
        body:          str,
    ) -> ToolResult:
        """Build the step-1 preview for this tool's edit kind."""
        ...

    async def _do_apply(self, preview_id: str) -> ToolResult:
        return await _do_apply(self._editor, preview_id)


# ---------------------------------------------------------------------------
# Tool 1 — ReplaceSymbolBody
# ---------------------------------------------------------------------------


class ReplaceSymbolBodyTool(SymbolEditTool[ReplaceSymbolBodyArgs]):
    """Replace a symbol's body in two steps: preview then apply."""

    name             = "replace_symbol_body"
    description_text = (
        "Replace the body of a symbol (function/method/class) in two steps. "
        "Step 1: supply name_path + relative_path + body -> get preview_id + diff. "
        "Step 2: supply preview_id + apply=True -> commit the change. "
        "Returns STALE if the target file changed between steps."
    )
    input_schema = _edit_schema(
        "New body text for the symbol (step 1)",
        _APPLY_EDIT_DESC,
    )
    args_class   = ReplaceSymbolBodyArgs

    async def _do_preview(
        self,
        name_path:     str,
        relative_path: str,
        body:          str,
    ) -> ToolResult:
        located = await _locate_symbol_lines(self._retriever, name_path, relative_path)
        if isinstance(located, ToolResult):
            return located
        abs_path, start_line, end_line, original_lines = located

        uri       = _path_to_uri(abs_path)
        new_lines = body.splitlines(keepends=True)
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

        preview_id, diff_text = await asyncio.to_thread(
            self._editor.create_preview,
            kind           = "replace_body",
            workspace_edit = workspace_edit,
            new_contents   = {abs_path: new_content},
        )

        return _preview_result(preview_id, diff_text, "replace_body")


# ---------------------------------------------------------------------------
# Tools 2 and 3 — InsertBeforeSymbol / InsertAfterSymbol
# ---------------------------------------------------------------------------


class InsertSymbolTool(SymbolEditTool[ArgsT]):
    """Shared preview construction for the two insertion tools.

    The pair differs only in which line the insertion anchors to and which
    ``CodeEditor`` prepare method builds the edit.
    """

    #: Preview kind reported back to the caller.
    preview_kind: ClassVar[str]  = ""
    #: True to anchor below the symbol body, False to anchor above its definition.
    insert_after: ClassVar[bool] = False

    async def _do_preview(
        self,
        name_path:     str,
        relative_path: str,
        body:          str,
    ) -> ToolResult:
        located = await _locate_symbol_lines(self._retriever, name_path, relative_path)
        if isinstance(located, ToolResult):
            return located
        abs_path, start_line, end_line, original_lines = located

        # The two prepare methods take the same arguments apart from the anchor
        # line, but mypy sees two distinct signatures; cast to the shared shape.
        prepare = cast(
            "Callable[..., tuple[str, str]]",
            self._editor.prepare_insert_after if self.insert_after
            else self._editor.prepare_insert_before,
        )
        anchor: dict[str, int] = (
            {"end_line": end_line} if self.insert_after else {"start_line": start_line}
        )

        try:
            preview_id, diff_text = await asyncio.to_thread(
                prepare,
                name_path      = name_path,
                relative_path  = relative_path,
                body           = body,
                abs_path       = abs_path,
                original_lines = original_lines,
                **anchor,
            )
        except Exception as e:
            return ToolResult(tool_use_id="", content=f"Preview error: {e}", is_error=True)

        return _preview_result(preview_id, diff_text, self.preview_kind)


class InsertBeforeSymbolTool(InsertSymbolTool[InsertBeforeSymbolArgs]):
    """Insert code immediately before a symbol definition."""

    name             = "insert_before_symbol"
    description_text = (
        "Insert code immediately before a symbol definition in two steps. "
        "Typical uses: new import statements, decorators, typedefs above a class. "
        "Step 1: supply name_path + relative_path + body -> get preview_id + diff. "
        "Step 2: supply preview_id + apply=True -> commit the change."
    )
    input_schema = _edit_schema(
        "Code to insert before the symbol (step 1)",
        _APPLY_EDIT_DESC,
    )
    args_class   = InsertBeforeSymbolArgs

    preview_kind: ClassVar[str]  = "insert_before"
    insert_after: ClassVar[bool] = False


class InsertAfterSymbolTool(InsertSymbolTool[InsertAfterSymbolArgs]):
    """Insert code immediately after a symbol body."""

    name             = "insert_after_symbol"
    description_text = (
        "Insert code immediately after a symbol body in two steps. "
        "Typical uses: adding a new method after an existing one, "
        "a sibling function, or a related constant. "
        "Step 1: supply name_path + relative_path + body -> get preview_id + diff. "
        "Step 2: supply preview_id + apply=True -> commit the change."
    )
    input_schema = _edit_schema(
        "Code to insert after the symbol (step 1)",
        _APPLY_EDIT_DESC,
    )
    args_class   = InsertAfterSymbolArgs

    preview_kind: ClassVar[str]  = "insert_after"
    insert_after: ClassVar[bool] = True


# ---------------------------------------------------------------------------
# Tool 4 — SafeDeleteSymbol
# ---------------------------------------------------------------------------


class SafeDeleteSymbolTool(SymbolEditTool[SafeDeleteSymbolArgs]):
    """Delete a symbol only when it has zero references."""

    name             = "safe_delete_symbol"
    description_text = (
        "Delete a symbol (function/method/class) only when it has zero references. "
        "Step 1: supply name_path + relative_path -> check references. "
        "  If references found: returns blocked_by_references with reference list. "
        "  If zero references: returns preview_id + diff. "
        "Step 2: supply preview_id + apply=True -> commit the deletion."
    )
    input_schema = _edit_schema(None, _APPLY_DELETE_DESC)
    args_class   = SafeDeleteSymbolArgs

    category:      ClassVar[ToolCategory]   = ToolCategory.DESTRUCTIVE
    tags:          ClassVar[frozenset[str]] = frozenset({"lsp", "symbol", "delete", "safe"})
    requires_body: ClassVar[bool]           = False
    step1_hint:    ClassVar[str]            = "Step 1: supply name_path + relative_path. "

    async def _do_preview(
        self,
        name_path:     str,
        relative_path: str,
        body:          str = "",
    ) -> ToolResult:
        located = await _locate_symbol_lines(self._retriever, name_path, relative_path)
        if isinstance(located, ToolResult):
            return located
        abs_path, start_line, end_line, original_lines = located

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
            preview_id, diff_text = await asyncio.to_thread(
                self._editor.prepare_safe_delete,
                name_path      = name_path,
                relative_path  = relative_path,
                abs_path       = abs_path,
                start_line     = start_line,
                end_line       = end_line,
                original_lines = original_lines,
            )
        except Exception as e:
            return ToolResult(tool_use_id="", content=f"Preview error: {e}", is_error=True)

        return _preview_result(preview_id, diff_text, "delete")
