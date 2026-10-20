"""Tests for the spill storage seam (Task 1.1)."""

from pathlib import Path
from typing import Any, Never

import pytest

from nerdvana_cli.core.config.settings import NerdvanaSettings
from nerdvana_cli.core.execution.spill_store import LocalSpillStore, SaveTextSpill, SpillPolicy
from nerdvana_cli.core.execution.tool_executor import ToolExecutor
from nerdvana_cli.core.hooks.hooks import HookEngine
from nerdvana_cli.core.tool import BaseTool, ToolContext, ToolRegistry
from nerdvana_cli.types import ToolResult


def _request(
    session_id: str = "sess-1",
    call_id: str = "call-1",
    content: str = "hello",
    suggested_name: str = "Bash",
) -> SaveTextSpill:
    return SaveTextSpill(session_id=session_id, tool_name="Bash", call_id=call_id, suggested_name=suggested_name, content=content)


def test_within_limit_content_is_preserved(tmp_path: Path) -> None:
    policy = SpillPolicy(store=LocalSpillStore(root_dir=tmp_path / "spills"), max_inline_chars=100)
    content = "x" * 100
    assert policy.apply(session_id="sess-1", tool_name="Bash", call_id="call-1", content=content) == content
    assert not (tmp_path / "spills").exists()


def test_oversized_content_spills_with_permissions(tmp_path: Path) -> None:
    root = tmp_path / "spills"
    policy = SpillPolicy(store=LocalSpillStore(root_dir=root), max_inline_chars=100, head_chars=20, tail_chars=10)
    content = "A" * 500
    result = policy.apply(session_id="sess-1", tool_name="Bash", call_id="call-1", content=content)

    spilled = list(root.rglob("*.txt"))
    assert len(spilled) == 1
    file = spilled[0]
    assert file.read_text(encoding="utf-8") == content
    assert file.parent.name.startswith("session-")
    assert (file.parent.stat().st_mode & 0o777) == 0o700
    assert (file.stat().st_mode & 0o777) == 0o600
    assert result.startswith("A" * 20)
    assert result.endswith("A" * 10)
    assert "Full result spilled to" in result


def test_suggested_name_cannot_traverse(tmp_path: Path) -> None:
    root = tmp_path / "spills"
    store = LocalSpillStore(root_dir=root)
    ref = store.save_text(_request(suggested_name="../../evil/output.txt"))

    assert ref.locator.startswith(str(root / "session-"))
    assert ".." not in ref.locator
    assert Path(ref.locator).parent.parent == root


class FailingStore(LocalSpillStore):
    """Spill store that always fails as a real disk would (full disk, permissions, ...)."""

    def save_text(self, request: SaveTextSpill) -> Never:
        raise OSError("disk full")


def test_disk_failure_falls_back_to_inline_truncation(tmp_path: Path) -> None:
    policy = SpillPolicy(store=FailingStore(root_dir=tmp_path), max_inline_chars=100, head_chars=20, tail_chars=10)
    content = "M" * 500
    result = policy.apply(session_id="sess-1", tool_name="Bash", call_id="call-1", content=content)

    assert result.startswith("M" * 20)
    assert result.endswith("M" * 10)
    assert "spill store unavailable" in result
    assert not any(tmp_path.rglob("*.txt"))


@pytest.mark.parametrize("raw", ["..", "a/b/c", "\x00bad"])
def test_sanitized_names(tmp_path: Path, raw: str) -> None:
    store = LocalSpillStore(root_dir=tmp_path / "spills")
    ref = store.save_text(_request(suggested_name=raw))
    name = Path(ref.locator).name
    assert "/" not in name
    assert name not in ("", ".", "..")


def test_the_preview_stays_within_the_limit_and_is_not_cut_again(tmp_path: Path) -> None:
    policy = SpillPolicy(store=LocalSpillStore(root_dir=tmp_path / "spills"), max_inline_chars=1_000)
    content = "Z" * 20_000

    once = policy.apply(session_id="sess-1", tool_name="Bash", call_id="call-1", content=content)

    assert len(once) <= 1_000
    assert policy.apply(session_id="sess-1", tool_name="Bash", call_id="call-1", content=once) == once


def test_a_limit_stated_by_the_caller_wins_over_the_default(tmp_path: Path) -> None:
    policy = SpillPolicy(store=LocalSpillStore(root_dir=tmp_path / "spills"), max_inline_chars=1_000)
    content = "Z" * 500

    assert policy.apply(session_id="s1", tool_name="Bash", call_id="c1", content=content) == content
    assert len(policy.apply(session_id="s1", tool_name="Bash", call_id="c1", content=content, max_inline_chars=100)) <= 100


def test_the_same_call_spilled_twice_gets_two_files(tmp_path: Path) -> None:
    store = LocalSpillStore(root_dir=tmp_path / "spills")

    first  = store.save_text(_request(content="one"))
    second = store.save_text(_request(content="two"))

    assert first.locator != second.locator
    assert Path(first.locator).read_text(encoding="utf-8") == "one"
    assert Path(second.locator).read_text(encoding="utf-8") == "two"


def test_the_session_directory_is_named_after_a_digest_and_is_private(tmp_path: Path) -> None:
    root  = tmp_path / "spills"
    store = LocalSpillStore(root_dir=root)

    ref = store.save_text(_request(session_id="sess-1"))

    assert "sess-1" not in ref.locator
    assert store.session_dir("sess-1").name == Path(ref.locator).parent.name
    assert (root.stat().st_mode & 0o777) == 0o700


# ---------------------------------------------------------------------------
# Through the executor
# ---------------------------------------------------------------------------


class _Big(BaseTool[Any]):
    """A tool that bounds its own result, the way the search and file tools do."""

    name              = "Big"
    description_text  = "returns a lot"
    max_result_tokens = 30_000

    async def call(self, args: Any, context: Any, can_use_tool: Any = None, on_progress: Any = None) -> ToolResult:
        return ToolResult(tool_use_id="", content=self.truncate_result(self.body))


async def test_a_tool_that_cuts_its_own_result_spills_it_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    registry = ToolRegistry()
    body     = "Q" * 40_000
    tool     = _Big()
    tool.body = body
    registry.register(tool)
    executor = ToolExecutor(registry=registry, hooks=HookEngine(), settings=NerdvanaSettings())
    context  = ToolContext(cwd=str(tmp_path))
    context.state["session_id"] = "spill-1"

    (result,) = await executor.run_batch([{"id": "1", "name": "Big", "input": {}}], context)

    spilled = list((tmp_path / "data" / "tool-output").glob("session-*/*.txt"))
    assert len(spilled) == 1
    assert spilled[0].read_text(encoding="utf-8") == body
    assert str(spilled[0]) in result.content
    assert result.content.startswith("Q" * 50) and result.content.endswith("Q" * 50)
