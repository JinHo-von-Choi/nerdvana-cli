"""Behaviour tests for the shared symbol-edit surface.

These pin the observable contract of ``nerdvana_cli.tools.symbol_edit_tools``
before and after the shared base extraction: argument objects, the module-level
locate/apply helpers, and the two-step preview/apply flow of all four tools.

작성자: 최진호
작성일: 2026-09-11
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from nerdvana_cli.core.code_editor import CodeEditor
from nerdvana_cli.core.symbol import LanguageServerSymbol, Location
from nerdvana_cli.core.tool import ToolCategory, ToolContext, ToolSideEffect
from nerdvana_cli.tools import symbol_edit_tools as se

# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


def _ctx() -> ToolContext:
    return ToolContext(cwd=".")


def _symbol(path: Path, line: int = 1, name: str = "foo") -> LanguageServerSymbol:
    return LanguageServerSymbol(
        name      = name,
        name_path = name,
        kind      = "Function",
        kind_int  = 12,
        location  = Location(str(path), line, 0),
    )


def _retriever(
    root:    Path,
    symbols: list[LanguageServerSymbol] | None = None,
    refs:    list[Any] | None                  = None,
) -> AsyncMock:
    r                    = AsyncMock()
    r.find               = AsyncMock(return_value=symbols if symbols is not None else [])
    r.find_references    = AsyncMock(return_value=refs or [])
    r._resolve           = lambda p: str(root / p)
    return r


def _build(tool_cls: type, root: Path, **kwargs: Any) -> Any:
    return tool_cls(retriever=_retriever(root, **kwargs), editor=CodeEditor(project_root=str(root)))


class _Ref:
    """Minimal stand-in for an LSP reference record."""

    def __init__(self, file_path: str, line: int, character: int) -> None:
        self.file_path = file_path
        self.line      = line
        self.character = character


# ---------------------------------------------------------------------------
# Argument objects
# ---------------------------------------------------------------------------


class TestArgObjects:
    @pytest.mark.parametrize(
        "cls",
        [
            se.ReplaceSymbolBodyArgs,
            se.InsertBeforeSymbolArgs,
            se.InsertAfterSymbolArgs,
        ],
    )
    def test_body_args_defaults(self, cls: type) -> None:
        args = cls()
        assert args.name_path     == ""
        assert args.relative_path == ""
        assert args.body          is None
        assert args.preview_id    is None
        assert args.apply         is False

    @pytest.mark.parametrize(
        "cls",
        [
            se.ReplaceSymbolBodyArgs,
            se.InsertBeforeSymbolArgs,
            se.InsertAfterSymbolArgs,
        ],
    )
    def test_body_args_assignment(self, cls: type) -> None:
        args = cls(
            name_path     = "A/b",
            relative_path = "x.py",
            body          = "pass\n",
            preview_id    = "deadbeef",
            apply         = True,
        )
        assert args.name_path     == "A/b"
        assert args.relative_path == "x.py"
        assert args.body          == "pass\n"
        assert args.preview_id    == "deadbeef"
        assert args.apply         is True

    def test_safe_delete_args_have_no_body(self) -> None:
        args = se.SafeDeleteSymbolArgs(name_path="A", relative_path="x.py")
        assert args.name_path     == "A"
        assert args.relative_path == "x.py"
        assert args.preview_id    is None
        assert args.apply         is False
        assert not hasattr(args, "body")

    def test_parse_args_ignores_unknown_keys(self, tmp_path: Path) -> None:
        tool = _build(se.ReplaceSymbolBodyTool, tmp_path)
        args = tool.parse_args({"name_path": "foo", "nonsense": 1})
        assert args.name_path == "foo"

    def test_parse_args_drops_body_for_safe_delete(self, tmp_path: Path) -> None:
        tool = _build(se.SafeDeleteSymbolTool, tmp_path)
        args = tool.parse_args({"name_path": "foo", "body": "x"})
        assert not hasattr(args, "body")


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


class TestPathToUri:
    def test_returns_file_uri(self, tmp_path: Path) -> None:
        uri = se._path_to_uri(str(tmp_path / "a.py"))
        assert uri.startswith("file://")
        assert uri.endswith("a.py")


class TestFindSymbolEnd:
    def test_start_beyond_end_returns_length(self) -> None:
        assert se._find_symbol_end(["a\n"], 5) == 1

    def test_stops_at_dedent(self) -> None:
        lines = ["def a():\n", "    pass\n", "def b():\n", "    pass\n"]
        assert se._find_symbol_end(lines, 0) == 2

    def test_blank_lines_do_not_terminate(self) -> None:
        lines = ["def a():\n", "    x = 1\n", "\n", "    y = 2\n", "def b():\n"]
        assert se._find_symbol_end(lines, 0) == 4

    def test_runs_to_eof_when_never_dedents(self) -> None:
        lines = ["def a():\n", "    pass\n", "    pass\n"]
        assert se._find_symbol_end(lines, 0) == 3


class TestLocateSymbolLines:
    async def test_success(self, tmp_path: Path) -> None:
        f = tmp_path / "src.py"
        f.write_text("def foo():\n    pass\n", encoding="utf-8")
        result = await se._locate_symbol_lines(
            _retriever(tmp_path, [_symbol(f)]), "foo", "src.py",
        )
        assert not isinstance(result, type(None))
        abs_path, start, end, lines = result   # type: ignore[misc]
        assert abs_path == str(f)
        assert (start, end) == (0, 2)
        assert lines == ["def foo():\n", "    pass\n"]

    async def test_lsp_failure_is_reported(self, tmp_path: Path) -> None:
        r      = _retriever(tmp_path)
        r.find = AsyncMock(side_effect=RuntimeError("boom"))
        result = await se._locate_symbol_lines(r, "foo", "src.py")
        assert result.is_error                      # type: ignore[union-attr]
        assert "LSP error: boom" in result.content  # type: ignore[union-attr]

    async def test_symbol_not_found(self, tmp_path: Path) -> None:
        result = await se._locate_symbol_lines(_retriever(tmp_path, []), "foo", "src.py")
        assert result.is_error                        # type: ignore[union-attr]
        assert "not found in src.py" in result.content  # type: ignore[union-attr]

    async def test_unreadable_file(self, tmp_path: Path) -> None:
        missing = tmp_path / "gone.py"
        result  = await se._locate_symbol_lines(
            _retriever(tmp_path, [_symbol(missing)]), "foo", "gone.py",
        )
        assert result.is_error                          # type: ignore[union-attr]
        assert "Cannot read gone.py" in result.content  # type: ignore[union-attr]

    async def test_does_not_block_the_event_loop(self, tmp_path: Path) -> None:
        """The locate helper must yield control while it touches the disk."""
        f = tmp_path / "src.py"
        f.write_text("def foo():\n    pass\n", encoding="utf-8")

        ticks = 0

        async def _tick() -> None:
            nonlocal ticks
            for _ in range(3):
                await asyncio.sleep(0)
                ticks += 1

        ticker = asyncio.create_task(_tick())
        await se._locate_symbol_lines(_retriever(tmp_path, [_symbol(f)]), "foo", "src.py")
        observed = ticks
        await ticker
        assert observed > 0, "locate helper never yielded: file read ran on the event loop"


class TestDoApply:
    async def _preview(self, tmp_path: Path, target: Path) -> tuple[CodeEditor, str]:
        editor = CodeEditor(project_root=str(tmp_path))
        pid, _ = editor.prepare_insert_before(
            name_path      = "foo",
            relative_path  = target.name,
            body           = "X = 1\n",
            abs_path       = str(target),
            start_line     = 0,
            original_lines = target.read_text(encoding="utf-8").splitlines(keepends=True),
        )
        return editor, pid

    async def test_applies_preview(self, tmp_path: Path) -> None:
        f = tmp_path / "src.py"
        f.write_text("def foo():\n    pass\n", encoding="utf-8")
        editor, pid = await self._preview(tmp_path, f)

        result = await se._do_apply(editor, pid)
        assert not result.is_error
        assert json.loads(result.content)["status"] == "applied"
        assert "X = 1" in f.read_text(encoding="utf-8")

    async def test_unknown_preview_id(self, tmp_path: Path) -> None:
        editor = CodeEditor(project_root=str(tmp_path))
        result = await se._do_apply(editor, "nope")
        assert result.is_error
        payload = json.loads(result.content)
        assert payload["status"] == "ERROR"
        assert "nope" in payload["reason"]

    async def test_stale_preview(self, tmp_path: Path) -> None:
        f = tmp_path / "src.py"
        f.write_text("def foo():\n    pass\n", encoding="utf-8")
        editor, pid = await self._preview(tmp_path, f)

        f.write_text("def foo():\n    return 1\n", encoding="utf-8")

        result = await se._do_apply(editor, pid)
        assert result.is_error
        payload = json.loads(result.content)
        assert payload["status"] == "STALE"
        assert payload["changed"] == [str(f)]


# ---------------------------------------------------------------------------
# Tool metadata — the shared surface all four expose
# ---------------------------------------------------------------------------


class TestToolMetadata:
    @pytest.mark.parametrize(
        ("cls", "expected_name", "expected_category"),
        [
            (se.ReplaceSymbolBodyTool, "replace_symbol_body", ToolCategory.WRITE),
            (se.InsertBeforeSymbolTool, "insert_before_symbol", ToolCategory.WRITE),
            (se.InsertAfterSymbolTool, "insert_after_symbol", ToolCategory.WRITE),
            (se.SafeDeleteSymbolTool, "safe_delete_symbol", ToolCategory.DESTRUCTIVE),
        ],
    )
    def test_metadata(
        self,
        tmp_path: Path,
        cls: type,
        expected_name: str,
        expected_category: ToolCategory,
    ) -> None:
        tool = _build(cls, tmp_path)
        assert tool.name                  == expected_name
        assert tool.category              == expected_category
        assert tool.side_effects          == ToolSideEffect.FILESYSTEM
        assert tool.requires_confirmation is True
        assert tool.is_concurrency_safe   is False
        assert {"lsp", "symbol"} <= set(tool.tags)

    @pytest.mark.parametrize(
        ("cls", "wants_body"),
        [
            (se.ReplaceSymbolBodyTool, True),
            (se.InsertBeforeSymbolTool, True),
            (se.InsertAfterSymbolTool, True),
            (se.SafeDeleteSymbolTool, False),
        ],
    )
    def test_input_schema_shape(self, tmp_path: Path, cls: type, wants_body: bool) -> None:
        schema = _build(cls, tmp_path).input_schema
        props  = schema["properties"]
        assert schema["type"]     == "object"
        assert schema["required"] == []
        assert {"name_path", "relative_path", "preview_id", "apply"} <= set(props)
        assert ("body" in props) is wants_body
        assert props["apply"]["type"]    == "boolean"
        assert props["apply"]["default"] is False


# ---------------------------------------------------------------------------
# Invalid-argument branch — identical for all four tools
# ---------------------------------------------------------------------------


class TestInvalidArguments:
    @pytest.mark.parametrize(
        ("tool_cls", "args_cls"),
        [
            (se.ReplaceSymbolBodyTool, se.ReplaceSymbolBodyArgs),
            (se.InsertBeforeSymbolTool, se.InsertBeforeSymbolArgs),
            (se.InsertAfterSymbolTool, se.InsertAfterSymbolArgs),
            (se.SafeDeleteSymbolTool, se.SafeDeleteSymbolArgs),
        ],
    )
    async def test_empty_args_rejected(
        self, tmp_path: Path, tool_cls: type, args_cls: type,
    ) -> None:
        result = await _build(tool_cls, tmp_path).call(args_cls(), _ctx())
        assert result.is_error
        assert "Invalid arguments" in result.content
        assert "preview_id + apply=True" in result.content

    @pytest.mark.parametrize(
        ("tool_cls", "args_cls"),
        [
            (se.ReplaceSymbolBodyTool, se.ReplaceSymbolBodyArgs),
            (se.InsertBeforeSymbolTool, se.InsertBeforeSymbolArgs),
            (se.InsertAfterSymbolTool, se.InsertAfterSymbolArgs),
        ],
    )
    async def test_body_tools_reject_missing_body(
        self, tmp_path: Path, tool_cls: type, args_cls: type,
    ) -> None:
        args   = args_cls(name_path="foo", relative_path="src.py")
        result = await _build(tool_cls, tmp_path).call(args, _ctx())
        assert result.is_error
        assert "Invalid arguments" in result.content

    async def test_apply_without_preview_id_is_rejected(self, tmp_path: Path) -> None:
        args   = se.InsertAfterSymbolArgs(apply=True)
        result = await _build(se.InsertAfterSymbolTool, tmp_path).call(args, _ctx())
        assert result.is_error
        assert "Invalid arguments" in result.content


# ---------------------------------------------------------------------------
# ReplaceSymbolBody
# ---------------------------------------------------------------------------


class TestReplaceSymbolBody:
    async def test_preview_then_apply(self, tmp_path: Path) -> None:
        f = tmp_path / "src.py"
        f.write_text("def foo():\n    pass\n", encoding="utf-8")
        tool = _build(se.ReplaceSymbolBodyTool, tmp_path, symbols=[_symbol(f)])

        r1 = await tool.call(
            se.ReplaceSymbolBodyArgs(
                name_path="foo", relative_path="src.py", body="def foo():\n    return 42\n",
            ),
            _ctx(),
        )
        assert not r1.is_error
        step1 = json.loads(r1.content)
        assert step1["kind"] == "replace_body"
        assert "return 42" in step1["diff"]

        r2 = await tool.call(
            se.ReplaceSymbolBodyArgs(preview_id=step1["preview_id"], apply=True), _ctx(),
        )
        assert not r2.is_error
        assert json.loads(r2.content)["status"] == "applied"
        assert "return 42" in f.read_text(encoding="utf-8")

    async def test_body_without_trailing_newline_is_terminated(self, tmp_path: Path) -> None:
        f = tmp_path / "src.py"
        f.write_text("def foo():\n    pass\n", encoding="utf-8")
        tool = _build(se.ReplaceSymbolBodyTool, tmp_path, symbols=[_symbol(f)])

        r1  = await tool.call(
            se.ReplaceSymbolBodyArgs(
                name_path="foo", relative_path="src.py", body="def foo():\n    return 7",
            ),
            _ctx(),
        )
        pid = json.loads(r1.content)["preview_id"]
        await tool.call(se.ReplaceSymbolBodyArgs(preview_id=pid, apply=True), _ctx())
        assert f.read_text(encoding="utf-8").endswith("\n")

    async def test_lsp_failure(self, tmp_path: Path) -> None:
        tool             = _build(se.ReplaceSymbolBodyTool, tmp_path)
        tool._retriever.find = AsyncMock(side_effect=RuntimeError("down"))
        result = await tool.call(
            se.ReplaceSymbolBodyArgs(name_path="foo", relative_path="src.py", body="x\n"),
            _ctx(),
        )
        assert result.is_error
        assert "LSP error: down" in result.content

    async def test_symbol_not_found(self, tmp_path: Path) -> None:
        tool   = _build(se.ReplaceSymbolBodyTool, tmp_path, symbols=[])
        result = await tool.call(
            se.ReplaceSymbolBodyArgs(name_path="foo", relative_path="src.py", body="x\n"),
            _ctx(),
        )
        assert result.is_error
        assert "not found in src.py" in result.content

    async def test_unreadable_file(self, tmp_path: Path) -> None:
        missing = tmp_path / "gone.py"
        tool    = _build(se.ReplaceSymbolBodyTool, tmp_path, symbols=[_symbol(missing)])
        result  = await tool.call(
            se.ReplaceSymbolBodyArgs(name_path="foo", relative_path="gone.py", body="x\n"),
            _ctx(),
        )
        assert result.is_error
        assert "Cannot read gone.py" in result.content

    async def test_stale_apply(self, tmp_path: Path) -> None:
        f = tmp_path / "src.py"
        f.write_text("def foo():\n    pass\n", encoding="utf-8")
        tool = _build(se.ReplaceSymbolBodyTool, tmp_path, symbols=[_symbol(f)])

        r1  = await tool.call(
            se.ReplaceSymbolBodyArgs(
                name_path="foo", relative_path="src.py", body="def foo():\n    return 1\n",
            ),
            _ctx(),
        )
        pid = json.loads(r1.content)["preview_id"]
        f.write_text("def foo():\n    return 999\n", encoding="utf-8")

        r2 = await tool.call(se.ReplaceSymbolBodyArgs(preview_id=pid, apply=True), _ctx())
        assert r2.is_error
        assert json.loads(r2.content)["status"] == "STALE"


# ---------------------------------------------------------------------------
# InsertBefore / InsertAfter — the two that shared a body
# ---------------------------------------------------------------------------


class TestInsertTools:
    async def test_insert_before_places_text_above_the_symbol(self, tmp_path: Path) -> None:
        f = tmp_path / "src.py"
        f.write_text("# head\n\ndef foo():\n    pass\n", encoding="utf-8")
        tool = _build(se.InsertBeforeSymbolTool, tmp_path, symbols=[_symbol(f, line=3)])

        r1 = await tool.call(
            se.InsertBeforeSymbolArgs(
                name_path="foo", relative_path="src.py", body="BEFORE = True\n",
            ),
            _ctx(),
        )
        step1 = json.loads(r1.content)
        assert step1["kind"] == "insert_before"

        await tool.call(
            se.InsertBeforeSymbolArgs(preview_id=step1["preview_id"], apply=True), _ctx(),
        )
        text = f.read_text(encoding="utf-8")
        assert text.index("BEFORE = True") < text.index("def foo")

    async def test_insert_after_places_text_below_the_symbol(self, tmp_path: Path) -> None:
        f = tmp_path / "src.py"
        f.write_text("def foo():\n    pass\n", encoding="utf-8")
        tool = _build(se.InsertAfterSymbolTool, tmp_path, symbols=[_symbol(f, line=1)])

        r1 = await tool.call(
            se.InsertAfterSymbolArgs(
                name_path="foo", relative_path="src.py", body="AFTER = True\n",
            ),
            _ctx(),
        )
        step1 = json.loads(r1.content)
        assert step1["kind"] == "insert_after"

        await tool.call(
            se.InsertAfterSymbolArgs(preview_id=step1["preview_id"], apply=True), _ctx(),
        )
        text = f.read_text(encoding="utf-8")
        assert text.index("def foo") < text.index("AFTER = True")

    @pytest.mark.parametrize(
        ("tool_cls", "args_cls"),
        [
            (se.InsertBeforeSymbolTool, se.InsertBeforeSymbolArgs),
            (se.InsertAfterSymbolTool, se.InsertAfterSymbolArgs),
        ],
    )
    async def test_locate_failure_propagates(
        self, tmp_path: Path, tool_cls: type, args_cls: type,
    ) -> None:
        tool   = _build(tool_cls, tmp_path, symbols=[])
        result = await tool.call(
            args_cls(name_path="foo", relative_path="src.py", body="x\n"), _ctx(),
        )
        assert result.is_error
        assert "not found in src.py" in result.content

    @pytest.mark.parametrize(
        ("tool_cls", "args_cls", "prepare_name"),
        [
            (se.InsertBeforeSymbolTool, se.InsertBeforeSymbolArgs, "prepare_insert_before"),
            (se.InsertAfterSymbolTool, se.InsertAfterSymbolArgs, "prepare_insert_after"),
        ],
    )
    async def test_prepare_failure_is_wrapped(
        self, tmp_path: Path, tool_cls: type, args_cls: type, prepare_name: str,
    ) -> None:
        f = tmp_path / "src.py"
        f.write_text("def foo():\n    pass\n", encoding="utf-8")
        tool = _build(tool_cls, tmp_path, symbols=[_symbol(f)])

        def _explode(*_a: Any, **_k: Any) -> tuple[str, str]:
            raise RuntimeError("prepare failed")

        setattr(tool._editor, prepare_name, _explode)

        result = await tool.call(
            args_cls(name_path="foo", relative_path="src.py", body="x\n"), _ctx(),
        )
        assert result.is_error
        assert "Preview error: prepare failed" in result.content

    async def test_insert_after_at_end_of_file(self, tmp_path: Path) -> None:
        f = tmp_path / "src.py"
        f.write_text("def foo():\n    pass", encoding="utf-8")
        tool = _build(se.InsertAfterSymbolTool, tmp_path, symbols=[_symbol(f, line=1)])

        r1  = await tool.call(
            se.InsertAfterSymbolArgs(
                name_path="foo", relative_path="src.py", body="TAIL = 1\n",
            ),
            _ctx(),
        )
        assert not r1.is_error
        pid = json.loads(r1.content)["preview_id"]
        r2  = await tool.call(se.InsertAfterSymbolArgs(preview_id=pid, apply=True), _ctx())
        assert not r2.is_error
        assert "TAIL = 1" in f.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# SafeDelete
# ---------------------------------------------------------------------------


class TestSafeDelete:
    async def test_deletes_when_unreferenced(self, tmp_path: Path) -> None:
        f = tmp_path / "src.py"
        f.write_text("def dead():\n    pass\ndef live():\n    pass\n", encoding="utf-8")
        tool = _build(
            se.SafeDeleteSymbolTool, tmp_path, symbols=[_symbol(f, name="dead")], refs=[],
        )

        r1 = await tool.call(
            se.SafeDeleteSymbolArgs(name_path="dead", relative_path="src.py"), _ctx(),
        )
        step1 = json.loads(r1.content)
        assert step1["kind"] == "delete"

        r2 = await tool.call(
            se.SafeDeleteSymbolArgs(preview_id=step1["preview_id"], apply=True), _ctx(),
        )
        assert json.loads(r2.content)["status"] == "applied"
        assert "def dead" not in f.read_text(encoding="utf-8")

    async def test_blocked_by_references(self, tmp_path: Path) -> None:
        f = tmp_path / "src.py"
        f.write_text("def used():\n    pass\n", encoding="utf-8")
        tool = _build(
            se.SafeDeleteSymbolTool,
            tmp_path,
            symbols = [_symbol(f, name="used")],
            refs    = [_Ref("other.py", 12, 4)],
        )

        result = await tool.call(
            se.SafeDeleteSymbolArgs(name_path="used", relative_path="src.py"), _ctx(),
        )
        assert not result.is_error
        payload = json.loads(result.content)
        assert payload["status"]        == "blocked_by_references"
        assert payload["references"]    == [{"file": "other.py", "line": 12, "character": 4}]
        assert "def used" in f.read_text(encoding="utf-8")

    async def test_reference_lookup_failure(self, tmp_path: Path) -> None:
        f = tmp_path / "src.py"
        f.write_text("def used():\n    pass\n", encoding="utf-8")
        tool = _build(se.SafeDeleteSymbolTool, tmp_path, symbols=[_symbol(f, name="used")])
        tool._retriever.find_references = AsyncMock(side_effect=RuntimeError("refs down"))

        result = await tool.call(
            se.SafeDeleteSymbolArgs(name_path="used", relative_path="src.py"), _ctx(),
        )
        assert result.is_error
        assert "LSP error: refs down" in result.content

    async def test_symbol_not_found(self, tmp_path: Path) -> None:
        tool   = _build(se.SafeDeleteSymbolTool, tmp_path, symbols=[])
        result = await tool.call(
            se.SafeDeleteSymbolArgs(name_path="gone", relative_path="src.py"), _ctx(),
        )
        assert result.is_error
        assert "not found in src.py" in result.content

    async def test_prepare_failure_is_wrapped(self, tmp_path: Path) -> None:
        f = tmp_path / "src.py"
        f.write_text("def dead():\n    pass\n", encoding="utf-8")
        tool = _build(se.SafeDeleteSymbolTool, tmp_path, symbols=[_symbol(f, name="dead")])

        def _explode(*_a: Any, **_k: Any) -> tuple[str, str]:
            raise RuntimeError("prepare failed")

        tool._editor.prepare_safe_delete = _explode

        result = await tool.call(
            se.SafeDeleteSymbolArgs(name_path="dead", relative_path="src.py"), _ctx(),
        )
        assert result.is_error
        assert "Preview error: prepare failed" in result.content
