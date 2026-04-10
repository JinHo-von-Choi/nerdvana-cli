# tests/test_file_tools_hashline.py
import hashlib
import os
import tempfile
from unittest.mock import MagicMock

import pytest

from nerdvana_cli.core.tool import ToolContext
from nerdvana_cli.tools.file_tools import FileReadArgs, FileReadTool


def _make_context(cwd: str) -> ToolContext:
    ctx = MagicMock(spec=ToolContext)
    ctx.cwd = cwd
    ctx.file_state = {}
    return ctx


def _hash4(line: str) -> str:
    return hashlib.sha256(line.encode()).hexdigest()[:4]


@pytest.mark.asyncio
async def test_hashline_output_format():
    """FileRead should prefix each line with N:xxxx format."""
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "test.py")
        with open(p, "w") as f:
            f.write("def foo():\n    return bar\n")
        tool = FileReadTool()
        ctx = _make_context(d)
        result = await tool.call(FileReadArgs("test.py"), ctx)
        lines = result.content.split("\n")
        # skip header line
        data_lines = [ln for ln in lines if ":" in ln and not ln.startswith("[")]
        assert data_lines[0].startswith("1:")
        h = _hash4("def foo():\n")
        assert data_lines[0].startswith(f"1:{h}")


@pytest.mark.asyncio
async def test_hashline_duplicate_lines():
    """Duplicate lines get disambiguated with #N suffix."""
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "dup.py")
        with open(p, "w") as f:
            f.write("pass\npass\npass\n")
        tool = FileReadTool()
        ctx = _make_context(d)
        result = await tool.call(FileReadArgs("dup.py"), ctx)
        content = result.content
        # second and third "pass" lines should have #2 and #3 suffixes
        assert "#2" in content
        assert "#3" in content


@pytest.mark.asyncio
async def test_hashline_file_state_preserves_raw():
    """file_state stores raw content (without hash prefix) for downstream use."""
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "raw.py")
        with open(p, "w") as f:
            f.write("x = 1\n")
        tool = FileReadTool()
        ctx = _make_context(d)
        await tool.call(FileReadArgs("raw.py"), ctx)
        # raw content stored, not hash-prefixed
        assert "1:" not in ctx.file_state.get("raw.py", "")


@pytest.mark.asyncio
async def test_anchor_hash_edit_success():
    """anchor_hash identifies the correct line; it gets replaced by new_string."""
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "edit.py")
        with open(p, "w") as f:
            f.write("def foo():\n    return 1\n")
        tool_read = FileReadTool()
        ctx = _make_context(d)
        # read to get hashes
        await tool_read.call(FileReadArgs("edit.py"), ctx)
        # extract anchor for line 2 ("    return 1\n")
        line2_content = "    return 1\n"
        anchor = _hash4(line2_content)

        from nerdvana_cli.tools.file_tools import FileEditArgs, FileEditTool
        tool_edit = FileEditTool()
        result = await tool_edit.call(
            FileEditArgs("edit.py", old_string=None, new_string="    return 42\n",
                         anchor_hash=anchor),
            ctx,
        )
        assert not result.is_error
        with open(p) as f:
            updated = f.read()
        assert "return 42" in updated
        assert "return 1" not in updated


@pytest.mark.asyncio
async def test_anchor_hash_stale_error():
    """anchor_hash that doesn't match any line returns a ToolError."""
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "stale.py")
        with open(p, "w") as f:
            f.write("x = 1\n")
        from nerdvana_cli.tools.file_tools import FileEditArgs, FileEditTool
        tool = FileEditTool()
        ctx = _make_context(d)
        result = await tool.call(
            FileEditArgs("stale.py", old_string=None, new_string="x = 2\n",
                         anchor_hash="dead"),
            ctx,
        )
        assert result.is_error
        assert "Re-read" in result.content


@pytest.mark.asyncio
async def test_anchor_hash_duplicate_disambiguate():
    """anchor_hash with #N suffix targets the Nth occurrence of a duplicate line."""
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "dup2.py")
        with open(p, "w") as f:
            f.write("pass\npass\npass\n")
        from nerdvana_cli.tools.file_tools import FileEditArgs, FileEditTool
        tool = FileEditTool()
        ctx = _make_context(d)
        h = _hash4("pass\n")
        # target the 2nd "pass" line
        result = await tool.call(
            FileEditArgs("dup2.py", old_string=None, new_string="break\n",
                         anchor_hash=f"{h}#2"),
            ctx,
        )
        assert not result.is_error
        with open(p) as f:
            content = f.read()
        assert content == "pass\nbreak\npass\n"


@pytest.mark.asyncio
async def test_validation_error_when_no_anchor_and_no_old_string():
    """FileEdit with neither anchor_hash nor old_string returns a validation error."""
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "x.py")
        with open(p, "w") as f:
            f.write("x = 1\n")
        from nerdvana_cli.tools.file_tools import FileEditArgs, FileEditTool
        tool = FileEditTool()
        ctx = _make_context(d)
        result = await tool.call(
            FileEditArgs("x.py", old_string=None, new_string="x = 2\n",
                         anchor_hash=None),
            ctx,
        )
        assert result.is_error
        assert "old_string" in result.content or "anchor_hash" in result.content
