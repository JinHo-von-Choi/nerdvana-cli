"""TOCTOU symlink hardening tests for FileRead/Write/Edit tools.

These tests exercise the O_NOFOLLOW chain installed in
``nerdvana_cli/utils/path.py``. They are skipped on platforms that do not
expose ``os.O_NOFOLLOW`` (Windows), where the hardening falls back to
plain ``os.open`` semantics.
"""

from __future__ import annotations

import os

import pytest

from nerdvana_cli.core.tool import ToolContext
from nerdvana_cli.tools.file_tools import (
    FileEditArgs,
    FileEditTool,
    FileReadArgs,
    FileReadTool,
    FileWriteArgs,
    FileWriteTool,
)

pytestmark = pytest.mark.skipif(
    not hasattr(os, "O_NOFOLLOW"),
    reason="O_NOFOLLOW unavailable; symlink hardening is POSIX-only",
)


# Either guard layer is acceptable: validate_path() catches outside-cwd
# symlinks during a static realpath check, and safe_open_fd() catches every
# symlink (including inside-cwd ones) at the openat layer. The tests assert
# the security outcome — the operation is rejected and the target file is
# untouched — without locking in which layer fired.
_BLOCKED_MARKERS = ("Symbolic link blocked", "Path traversal blocked")


def _assert_blocked(content: str) -> None:
    assert any(marker in content for marker in _BLOCKED_MARKERS), (
        f"expected a blocked-path error, got: {content!r}"
    )


def _make_external_target(tmp_path_factory: pytest.TempPathFactory) -> str:
    """Create an outside-cwd victim file the symlink will point at."""
    outside = tmp_path_factory.mktemp("outside")
    victim = outside / "victim.txt"
    victim.write_text("ORIGINAL VICTIM CONTENT")
    return str(victim)


@pytest.fixture
def victim_file(tmp_path_factory: pytest.TempPathFactory) -> str:
    return _make_external_target(tmp_path_factory)


@pytest.mark.asyncio
async def test_write_through_symlink_file_is_blocked(
    tmp_path: str, victim_file: str
) -> None:
    """A file already replaced by a symlink to /tmp/victim must not be followed."""
    cwd = tmp_path
    link_path = cwd / "link"
    os.symlink(victim_file, link_path)

    ctx = ToolContext(cwd=str(cwd))
    tool = FileWriteTool()
    result = await tool.call(
        FileWriteArgs(path="link", content="HACKED"),
        ctx,
        can_use_tool=None,
    )

    assert result.is_error
    _assert_blocked(result.content)
    # Victim file must remain untouched.
    with open(victim_file) as f:
        assert f.read() == "ORIGINAL VICTIM CONTENT"


@pytest.mark.asyncio
async def test_write_with_symlinked_parent_directory_is_blocked(
    tmp_path: str, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """Parent directory swapped for a symlink that points outside cwd → blocked."""
    cwd = tmp_path
    outside_dir = tmp_path_factory.mktemp("outside_parent")
    (outside_dir / "victim.txt").write_text("ORIGINAL")

    # cwd/parent → outside_parent
    os.symlink(str(outside_dir), cwd / "parent")

    ctx = ToolContext(cwd=str(cwd))
    tool = FileWriteTool()
    result = await tool.call(
        FileWriteArgs(path="parent/victim.txt", content="HACKED"),
        ctx,
        can_use_tool=None,
    )

    assert result.is_error
    _assert_blocked(result.content)
    with open(outside_dir / "victim.txt") as f:
        assert f.read() == "ORIGINAL"


@pytest.mark.asyncio
async def test_write_with_symlinked_grandparent_is_blocked(
    tmp_path: str, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """A symlinked component anywhere above the file must reject the write."""
    cwd = tmp_path
    outside_dir = tmp_path_factory.mktemp("outside_grand")
    inner = outside_dir / "inner"
    inner.mkdir()
    (inner / "victim.txt").write_text("ORIGINAL")

    os.symlink(str(outside_dir), cwd / "grand")

    ctx = ToolContext(cwd=str(cwd))
    tool = FileWriteTool()
    result = await tool.call(
        FileWriteArgs(path="grand/inner/victim.txt", content="HACKED"),
        ctx,
        can_use_tool=None,
    )

    assert result.is_error
    _assert_blocked(result.content)
    with open(inner / "victim.txt") as f:
        assert f.read() == "ORIGINAL"


@pytest.mark.asyncio
async def test_read_through_symlink_file_is_blocked(
    tmp_path: str, victim_file: str
) -> None:
    """FileRead must refuse to follow a symlinked file."""
    cwd = tmp_path
    os.symlink(victim_file, cwd / "link")

    ctx = ToolContext(cwd=str(cwd))
    tool = FileReadTool()
    result = await tool.call(
        FileReadArgs(path="link"),
        ctx,
        can_use_tool=None,
    )

    assert result.is_error
    _assert_blocked(result.content)


@pytest.mark.asyncio
async def test_normal_write_read_edit_roundtrip(tmp_path: str) -> None:
    """Regression: normal in-cwd files still flow through write→read→edit."""
    cwd = tmp_path
    ctx = ToolContext(cwd=str(cwd))

    # 1. Write a fresh file in a new subdirectory (exercises safe_makedirs).
    write_tool = FileWriteTool()
    write_result = await write_tool.call(
        FileWriteArgs(path="sub/dir/example.txt", content="hello\nworld\n"),
        ctx,
        can_use_tool=None,
    )
    assert not write_result.is_error
    assert (cwd / "sub" / "dir" / "example.txt").read_text() == "hello\nworld\n"

    # 2. Read it back.
    read_tool = FileReadTool()
    read_result = await read_tool.call(
        FileReadArgs(path="sub/dir/example.txt"),
        ctx,
        can_use_tool=None,
    )
    assert not read_result.is_error
    assert "hello" in read_result.content
    assert "world" in read_result.content

    # 3. Edit it via old_string path.
    edit_tool = FileEditTool()
    edit_result = await edit_tool.call(
        FileEditArgs(
            path="sub/dir/example.txt",
            old_string="world",
            new_string="cosmos",
        ),
        ctx,
        can_use_tool=None,
    )
    assert not edit_result.is_error
    assert (cwd / "sub" / "dir" / "example.txt").read_text() == "hello\ncosmos\n"


@pytest.mark.asyncio
async def test_inside_cwd_symlink_is_still_blocked_by_safe_open(
    tmp_path: str,
) -> None:
    """A symlink whose target lives inside cwd (validate_path allows it)
    must still be rejected at the safe_open_fd layer, otherwise the TOCTOU
    hardening provides nothing beyond the existing realpath check.
    """
    cwd = tmp_path
    real = cwd / "real.txt"
    real.write_text("REAL")
    os.symlink(str(real), cwd / "link")

    ctx = ToolContext(cwd=str(cwd))
    write_tool = FileWriteTool()
    write_result = await write_tool.call(
        FileWriteArgs(path="link", content="HACKED"),
        ctx,
        can_use_tool=None,
    )
    assert write_result.is_error
    assert "Symbolic link blocked" in write_result.content
    assert real.read_text() == "REAL"

    read_tool = FileReadTool()
    read_result = await read_tool.call(
        FileReadArgs(path="link"),
        ctx,
        can_use_tool=None,
    )
    assert read_result.is_error
    assert "Symbolic link blocked" in read_result.content


@pytest.mark.asyncio
async def test_edit_through_symlink_file_is_blocked_at_read(
    tmp_path: str, victim_file: str
) -> None:
    """FileEdit must refuse before opening a symlinked target for read."""
    cwd = tmp_path
    os.symlink(victim_file, cwd / "link")

    ctx = ToolContext(cwd=str(cwd))
    tool = FileEditTool()
    result = await tool.call(
        FileEditArgs(
            path="link",
            old_string="ORIGINAL",
            new_string="HACKED",
        ),
        ctx,
        can_use_tool=None,
    )

    assert result.is_error
    _assert_blocked(result.content)
    with open(victim_file) as f:
        assert f.read() == "ORIGINAL VICTIM CONTENT"
