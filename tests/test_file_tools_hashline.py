"""Hash-anchored reads and edit integrity for the file tools.

Covers the ``N#hhhhhh`` line anchors, relocation of shifted anchors, and the
per-session read ledger that rejects edits or overwrites of files that changed
after they were read.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator
from pathlib import Path

import pytest

from nerdvana_cli.core.tool import ToolContext
from nerdvana_cli.tools import read_ledger
from nerdvana_cli.tools.file_tools import (
    FileEditArgs,
    FileEditTool,
    FileReadArgs,
    FileReadTool,
    FileWriteArgs,
    FileWriteTool,
)
from nerdvana_cli.types import ToolResult

_PREFIX = re.compile(r"^(\d+)#([0-9a-f]{6})    ", re.MULTILINE)


@pytest.fixture(autouse=True)
def _fresh_ledger() -> Iterator[None]:
    read_ledger.clear()
    yield
    read_ledger.clear()


def _h(line: str) -> str:
    """Independent 6-char hash of a line without its newline."""
    return hashlib.sha256(line.rstrip("\n").encode()).hexdigest()[:6]


def _ctx(cwd: Path, session_id: str | None = None) -> ToolContext:
    ctx = ToolContext(cwd=str(cwd))
    if session_id is not None:
        ctx.state["session_id"] = session_id
    return ctx


async def _read(ctx: ToolContext, path: str, **kw: int) -> ToolResult:
    return await FileReadTool().call(FileReadArgs(path, **kw), ctx)


async def _anchor_edit(ctx: ToolContext, path: str, anchor: str, new: str) -> ToolResult:
    return await FileEditTool().call(
        FileEditArgs(path, new_string=new, old_string=None, anchor_hash=anchor), ctx
    )


async def _text_edit(ctx: ToolContext, path: str, old: str, new: str) -> ToolResult:
    return await FileEditTool().call(FileEditArgs(path, new_string=new, old_string=old), ctx)


# ---------------------------------------------------------------------------
# FileRead output
# ---------------------------------------------------------------------------

async def test_read_prefixes_each_line_with_number_and_six_char_hash(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("def foo():\n    return bar\n", encoding="utf-8")
    result = await _read(_ctx(tmp_path), "a.py")

    found = _PREFIX.findall(result.content)
    assert [n for n, _ in found] == ["1", "2"]
    assert found[0][1] == _h("def foo():")
    assert found[1][1] == _h("    return bar")


async def test_read_numbers_lines_from_offset(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")
    result = await _read(_ctx(tmp_path), "a.py", offset=2, limit=1)

    found = _PREFIX.findall(result.content)
    assert found == [("3", _h("three"))]


async def test_read_keeps_raw_content_in_file_state(tmp_path: Path) -> None:
    (tmp_path / "raw.py").write_text("x = 1\n", encoding="utf-8")
    ctx = _ctx(tmp_path)
    await _read(ctx, "raw.py")
    assert ctx.file_state["raw.py"] == "x = 1\n"


# ---------------------------------------------------------------------------
# Anchor edits
# ---------------------------------------------------------------------------

async def test_anchor_edit_replaces_the_named_line(tmp_path: Path) -> None:
    target = tmp_path / "edit.py"
    target.write_text("def foo():\n    return 1\n", encoding="utf-8")
    ctx = _ctx(tmp_path)
    await _read(ctx, "edit.py")

    result = await _anchor_edit(ctx, "edit.py", f"2#{_h('    return 1')}", "    return 42\n")

    assert not result.is_error
    assert target.read_text(encoding="utf-8") == "def foo():\n    return 42\n"


async def test_anchor_line_number_disambiguates_duplicate_lines(tmp_path: Path) -> None:
    target = tmp_path / "dup.py"
    target.write_text("pass\npass\npass\n", encoding="utf-8")
    ctx = _ctx(tmp_path)
    await _read(ctx, "dup.py")

    result = await _anchor_edit(ctx, "dup.py", f"2#{_h('pass')}", "break\n")

    assert not result.is_error
    assert target.read_text(encoding="utf-8") == "pass\nbreak\npass\n"


async def test_malformed_anchor_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "m.py"
    target.write_text("x = 1\n", encoding="utf-8")
    ctx = _ctx(tmp_path)
    await _read(ctx, "m.py")

    for bad in ("dead", "1:dead", "1#dead", "x#aaaaaa"):
        result = await _anchor_edit(ctx, "m.py", bad, "x = 2\n")
        assert result.is_error
        assert "anchor" in result.content.lower()
    assert target.read_text(encoding="utf-8") == "x = 1\n"


async def test_edit_without_anchor_or_old_string_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "x.py").write_text("x = 1\n", encoding="utf-8")
    ctx = _ctx(tmp_path)
    await _read(ctx, "x.py")

    result = await FileEditTool().call(
        FileEditArgs("x.py", new_string="x = 2\n", old_string=None, anchor_hash=None), ctx
    )

    assert result.is_error
    assert "old_string" in result.content or "anchor_hash" in result.content


# ---------------------------------------------------------------------------
# Anchor relocation
# ---------------------------------------------------------------------------

async def test_shifted_anchor_relocates_to_the_unique_match(tmp_path: Path) -> None:
    target = tmp_path / "shift.py"
    target.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
    ctx = _ctx(tmp_path)
    await _read(ctx, "shift.py")
    gamma_anchor = f"3#{_h('gamma')}"

    first = await _anchor_edit(ctx, "shift.py", f"1#{_h('alpha')}", "alpha\nalpha2\nalpha3\n")
    assert not first.is_error

    second = await _anchor_edit(ctx, "shift.py", gamma_anchor, "GAMMA\n")

    assert not second.is_error
    assert target.read_text(encoding="utf-8") == "alpha\nalpha2\nalpha3\nbeta\nGAMMA\n"


async def test_ambiguous_relocation_is_rejected_with_current_anchors(tmp_path: Path) -> None:
    target = tmp_path / "amb.py"
    original = "head\ntwin\nmiddle\ntwin\ntail\n"
    target.write_text(original, encoding="utf-8")
    ctx = _ctx(tmp_path)
    await _read(ctx, "amb.py")

    result = await _anchor_edit(ctx, "amb.py", f"1#{_h('twin')}", "X\n")

    assert result.is_error
    assert f"1#{_h('head')}" in result.content
    assert f"3#{_h('middle')}" in result.content
    assert target.read_text(encoding="utf-8") == original


async def test_anchor_with_no_candidate_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "none.py"
    target.write_text("one\ntwo\nthree\n", encoding="utf-8")
    ctx = _ctx(tmp_path)
    await _read(ctx, "none.py")

    result = await _anchor_edit(ctx, "none.py", f"2#{_h('similar to two')}", "X\n")

    assert result.is_error
    assert f"2#{_h('two')}" in result.content
    assert target.read_text(encoding="utf-8") == "one\ntwo\nthree\n"


async def test_relocation_window_is_twenty_lines(tmp_path: Path) -> None:
    lines = [f"line {i}" for i in range(1, 60)]
    target = tmp_path / "win.py"
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    ctx = _ctx(tmp_path)
    await _read(ctx, "win.py")

    near = await _anchor_edit(ctx, "win.py", f"10#{_h(lines[29])}", "NEAR\n")
    far  = await _anchor_edit(ctx, "win.py", f"5#{_h(lines[49])}", "FAR\n")

    assert not near.is_error
    assert far.is_error
    text = target.read_text(encoding="utf-8")
    assert "NEAR" in text
    assert "FAR" not in text


# ---------------------------------------------------------------------------
# Read ledger: FileEdit
# ---------------------------------------------------------------------------

async def test_edit_of_a_file_never_read_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "unread.txt"
    target.write_text("alpha\n", encoding="utf-8")

    result = await _text_edit(_ctx(tmp_path), "unread.txt", "alpha", "beta")

    assert result.is_error
    assert "FileRead" in result.content
    assert target.read_text(encoding="utf-8") == "alpha\n"


async def test_edit_of_a_file_changed_since_read_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "stale.txt"
    target.write_text("alpha\n", encoding="utf-8")
    ctx = _ctx(tmp_path)
    await _read(ctx, "stale.txt")
    target.write_text("alpha changed elsewhere\n", encoding="utf-8")

    by_text   = await _text_edit(ctx, "stale.txt", "alpha", "beta")
    by_anchor = await _anchor_edit(ctx, "stale.txt", f"1#{_h('alpha changed elsewhere')}", "beta\n")

    assert by_text.is_error
    assert by_anchor.is_error
    assert "FileRead" in by_text.content
    assert "FileRead" in by_anchor.content
    assert target.read_text(encoding="utf-8") == "alpha changed elsewhere\n"


async def test_reread_after_external_change_allows_the_edit(tmp_path: Path) -> None:
    target = tmp_path / "again.txt"
    target.write_text("alpha\n", encoding="utf-8")
    ctx = _ctx(tmp_path)
    await _read(ctx, "again.txt")
    target.write_text("alpha two\n", encoding="utf-8")
    await _read(ctx, "again.txt")

    result = await _text_edit(ctx, "again.txt", "alpha two", "beta")

    assert not result.is_error
    assert target.read_text(encoding="utf-8") == "beta\n"


async def test_consecutive_own_edits_succeed_without_rereading(tmp_path: Path) -> None:
    target = tmp_path / "chain.txt"
    target.write_text("one\ntwo\nthree\n", encoding="utf-8")
    ctx = _ctx(tmp_path)
    await _read(ctx, "chain.txt")

    first  = await _text_edit(ctx, "chain.txt", "one", "uno")
    second = await _anchor_edit(ctx, "chain.txt", f"2#{_h('two')}", "dos\n")
    third  = await _text_edit(ctx, "chain.txt", "three", "tres")

    assert not first.is_error
    assert not second.is_error
    assert not third.is_error
    assert target.read_text(encoding="utf-8") == "uno\ndos\ntres\n"


async def test_edit_recognises_a_file_after_a_partial_read(tmp_path: Path) -> None:
    target = tmp_path / "part.txt"
    target.write_text("one\ntwo\nthree\n", encoding="utf-8")
    ctx = _ctx(tmp_path)
    await _read(ctx, "part.txt", offset=1, limit=1)

    result = await _text_edit(ctx, "part.txt", "three", "tres")

    assert not result.is_error


# ---------------------------------------------------------------------------
# Read ledger: FileWrite
# ---------------------------------------------------------------------------

async def test_write_over_an_unread_existing_file_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "keep.txt"
    target.write_text("precious\n", encoding="utf-8")

    result = await FileWriteTool().call(FileWriteArgs("keep.txt", "overwritten"), _ctx(tmp_path))

    assert result.is_error
    assert "FileRead" in result.content
    assert target.read_text(encoding="utf-8") == "precious\n"


async def test_write_creating_a_new_file_needs_no_read(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)

    created = await FileWriteTool().call(FileWriteArgs("sub/new.txt", "fresh\n"), ctx)

    assert not created.is_error
    assert (tmp_path / "sub" / "new.txt").read_text(encoding="utf-8") == "fresh\n"


async def test_write_over_a_file_changed_since_read_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "moved.txt"
    target.write_text("v1\n", encoding="utf-8")
    ctx = _ctx(tmp_path)
    await _read(ctx, "moved.txt")
    target.write_text("v2\n", encoding="utf-8")

    result = await FileWriteTool().call(FileWriteArgs("moved.txt", "v3\n"), ctx)

    assert result.is_error
    assert target.read_text(encoding="utf-8") == "v2\n"


async def test_write_after_read_succeeds_and_keeps_ledger_current(tmp_path: Path) -> None:
    target = tmp_path / "ok.txt"
    target.write_text("v1\n", encoding="utf-8")
    ctx = _ctx(tmp_path)
    await _read(ctx, "ok.txt")

    overwrite = await FileWriteTool().call(FileWriteArgs("ok.txt", "v2\n"), ctx)
    edit      = await _text_edit(ctx, "ok.txt", "v2", "v3")
    again     = await FileWriteTool().call(FileWriteArgs("ok.txt", "v4\n"), ctx)

    assert not overwrite.is_error
    assert not edit.is_error
    assert not again.is_error
    assert target.read_text(encoding="utf-8") == "v4\n"


async def test_created_file_can_be_edited_by_its_creator(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    await FileWriteTool().call(FileWriteArgs("mine.txt", "hello\n"), ctx)

    result = await _text_edit(ctx, "mine.txt", "hello", "bye")

    assert not result.is_error


# ---------------------------------------------------------------------------
# Read ledger: scope
# ---------------------------------------------------------------------------

async def test_ledger_is_isolated_between_session_ids(tmp_path: Path) -> None:
    target = tmp_path / "shared.txt"
    target.write_text("alpha\n", encoding="utf-8")
    reader = _ctx(tmp_path, "session-a")
    other  = _ctx(tmp_path, "session-b")
    await _read(reader, "shared.txt")

    foreign = await _text_edit(other, "shared.txt", "alpha", "beta")
    foreign_write = await FileWriteTool().call(FileWriteArgs("shared.txt", "x\n"), other)
    own = await _text_edit(reader, "shared.txt", "alpha", "beta")

    assert foreign.is_error
    assert foreign_write.is_error
    assert not own.is_error
    assert target.read_text(encoding="utf-8") == "beta\n"


async def test_ledger_survives_a_new_context_for_the_same_session(tmp_path: Path) -> None:
    target = tmp_path / "turns.txt"
    target.write_text("alpha\n", encoding="utf-8")
    await _read(_ctx(tmp_path, "long-session"), "turns.txt")

    next_turn = _ctx(tmp_path, "long-session")
    result    = await _text_edit(next_turn, "turns.txt", "alpha", "beta")

    assert not result.is_error


async def test_context_without_session_id_uses_the_default_session(tmp_path: Path) -> None:
    (tmp_path / "d.txt").write_text("alpha\n", encoding="utf-8")
    await _read(_ctx(tmp_path), "d.txt")

    same_default = await _text_edit(_ctx(tmp_path), "d.txt", "alpha", "beta")
    named        = await _text_edit(_ctx(tmp_path, "named"), "d.txt", "beta", "gamma")

    assert not same_default.is_error
    assert named.is_error
