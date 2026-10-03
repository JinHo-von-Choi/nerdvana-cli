"""Memory tools with ``memory.review`` on and off.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from nerdvana_cli.core.context.memories import MemoriesManager, MemoryScope
from nerdvana_cli.core.context.memory_review import MemoryInbox
from nerdvana_cli.core.tool import ToolContext
from nerdvana_cli.tools import memory_tools
from nerdvana_cli.tools.memory_tools import (
    DeleteMemoryTool,
    EditMemoryTool,
    ListMemoriesTool,
    ReadMemoryTool,
    RenameMemoryTool,
    WriteMemoryTool,
)

PK = MemoryScope.PROJECT_KNOWLEDGE


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    root = tmp_path / "project"
    root.mkdir()
    return root


def _ctx(project: Path, review: bool | None) -> ToolContext:
    context = ToolContext(cwd=str(project))
    if review is not None:
        context.state["memory_review"] = review
    return context


async def _call(tool: Any, args: dict[str, Any], context: ToolContext) -> Any:
    return await tool.call(tool.parse_args(args), context, can_use_tool=None)


class TestReviewOn:
    async def test_write_queues_a_proposal_and_stores_nothing(self, project: Path) -> None:
        result = await _call(
            WriteMemoryTool(),
            {"name": "build", "content": "pytest -q", "scope": "project_knowledge"},
            _ctx(project, True),
        )
        assert not result.is_error
        assert "Queued proposal" in result.content
        assert "awaits the user's review" in result.content
        assert MemoriesManager(str(project)).list_memories() == []
        assert len(MemoryInbox(str(project)).pending()) == 1

    async def test_queued_proposal_is_invisible_to_read_and_list(self, project: Path) -> None:
        ctx = _ctx(project, True)
        await _call(WriteMemoryTool(), {"name": "build", "content": "x", "scope": "project_knowledge"}, ctx)
        read = await _call(ReadMemoryTool(), {"name": "build"}, ctx)
        listed = await _call(ListMemoriesTool(), {}, ctx)
        assert read.is_error
        assert listed.content == "No memories found."

    async def test_edit_queues_the_edited_text(self, project: Path) -> None:
        MemoriesManager(str(project)).write("a", "alpha beta", PK)
        result = await _call(EditMemoryTool(), {"name": "a", "needle": "beta", "repl": "gamma"}, _ctx(project, True))
        assert "Queued proposal" in result.content
        assert MemoriesManager(str(project)).peek("a", PK) == "alpha beta"
        assert MemoryInbox(str(project)).pending()[0].content == "alpha gamma"

    async def test_delete_queues_a_removal(self, project: Path) -> None:
        MemoriesManager(str(project)).write("a", "alpha", PK)
        result = await _call(DeleteMemoryTool(), {"name": "a"}, _ctx(project, True))
        assert "deletion of memory 'a'" in result.content
        assert MemoriesManager(str(project)).peek("a", PK) == "alpha"

    async def test_delete_of_a_missing_memory_is_an_error(self, project: Path) -> None:
        result = await _call(DeleteMemoryTool(), {"name": "missing"}, _ctx(project, True))
        assert result.is_error

    async def test_rename_queues_two_proposals(self, project: Path) -> None:
        MemoriesManager(str(project)).write("old", "alpha", PK)
        result = await _call(RenameMemoryTool(), {"old_name": "old", "new_name": "new"}, _ctx(project, True))
        assert not result.is_error
        assert result.content.count("Queued proposal") == 2
        assert [e.name for e in MemoriesManager(str(project)).list_memories()] == ["old"]

    async def test_unsafe_name_is_still_an_error(self, project: Path) -> None:
        result = await _call(
            WriteMemoryTool(),
            {"name": "../x", "content": "x", "scope": "project_knowledge"},
            _ctx(project, True),
        )
        assert result.is_error
        assert MemoryInbox(str(project)).pending() == []

    async def test_secret_is_still_blocked(self, project: Path) -> None:
        result = await _call(
            WriteMemoryTool(),
            {"name": "k", "content": "token ghp_" + "a" * 36, "scope": "project_knowledge"},
            _ctx(project, True),
        )
        assert result.is_error
        assert MemoryInbox(str(project)).pending() == []

    async def test_agent_experience_scope_is_still_refused(self, project: Path) -> None:
        result = await _call(
            WriteMemoryTool(),
            {"name": "k", "content": "x", "scope": "agent_experience"},
            _ctx(project, True),
        )
        assert result.is_error


class TestReviewOff:
    async def test_write_is_stored_at_once_with_agent_source(self, project: Path) -> None:
        result = await _call(
            WriteMemoryTool(),
            {"name": "build", "content": "pytest -q", "scope": "project_knowledge"},
            _ctx(project, False),
        )
        assert not result.is_error
        (entry,) = MemoriesManager(str(project)).list_memories()
        assert (entry.name, entry.source) == ("build", "agent")
        assert MemoryInbox(str(project)).pending() == []

    async def test_edit_and_delete_act_at_once(self, project: Path) -> None:
        ctx = _ctx(project, False)
        MemoriesManager(str(project)).write("a", "alpha", PK)
        await _call(EditMemoryTool(), {"name": "a", "needle": "alpha", "repl": "beta"}, ctx)
        assert MemoriesManager(str(project)).peek("a", PK) == "beta"
        await _call(DeleteMemoryTool(), {"name": "a"}, ctx)
        assert MemoriesManager(str(project)).list_memories() == []


class TestSettingRead:
    async def test_state_value_wins_over_the_settings(self, project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        def explode() -> None:
            raise AssertionError("settings must not be read when the host set the value")

        monkeypatch.setattr(memory_tools.NerdvanaSettings, "load", staticmethod(explode))
        await _call(
            WriteMemoryTool(), {"name": "a", "content": "x", "scope": "project_knowledge"}, _ctx(project, True),
        )
        assert len(MemoryInbox(str(project)).pending()) == 1

    async def test_without_a_host_value_the_settings_decide_once(
        self, project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        config = tmp_path / "config.yml"
        config.write_text("memory:\n  review: true\n", encoding="utf-8")
        monkeypatch.setenv("NERDVANA_CONFIG", str(config))
        monkeypatch.chdir(tmp_path)
        ctx = _ctx(project, None)
        await _call(WriteMemoryTool(), {"name": "a", "content": "x", "scope": "project_knowledge"}, ctx)
        assert ctx.state["memory_review"] is True
        assert len(MemoryInbox(str(project)).pending()) == 1

    async def test_default_is_review_off(
        self, project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("NERDVANA_CONFIG", str(tmp_path / "missing.yml"))
        monkeypatch.chdir(tmp_path)
        ctx = _ctx(project, None)
        await _call(WriteMemoryTool(), {"name": "a", "content": "x", "scope": "project_knowledge"}, ctx)
        assert ctx.state["memory_review"] is False
        assert [e.name for e in MemoriesManager(str(project)).list_memories()] == ["a"]

    async def test_unreadable_settings_block_the_write(
        self, project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        config = tmp_path / "config.yml"
        config.write_text("memory:\n  review: [not, a, bool]\n", encoding="utf-8")
        monkeypatch.setenv("NERDVANA_CONFIG", str(config))
        monkeypatch.chdir(tmp_path)
        result = await _call(
            WriteMemoryTool(), {"name": "a", "content": "x", "scope": "project_knowledge"}, _ctx(project, None),
        )
        assert result.is_error
        assert "memory.review" in result.content
        assert MemoriesManager(str(project)).list_memories() == []


class TestDescriptions:
    @pytest.mark.parametrize(
        "tool", [WriteMemoryTool(), EditMemoryTool(), DeleteMemoryTool(), RenameMemoryTool()],
    )
    def test_changing_tools_say_that_proposals_await_review(self, tool: Any) -> None:
        text = tool.description_text
        assert "memory.review" in text
        assert "only queues a proposal" in text
        assert "until the user approves it" in text

    @pytest.mark.parametrize("tool", [ReadMemoryTool(), ListMemoriesTool()])
    def test_reading_tools_do_not_carry_the_note(self, tool: Any) -> None:
        assert "memory.review" not in tool.description_text

    def test_list_description_names_the_source(self) -> None:
        assert "source" in ListMemoriesTool().description_text

    def test_proposals_are_plain_json_files(self, project: Path) -> None:
        proposal = MemoryInbox(str(project)).propose_write("a", "x", PK)
        raw = json.loads((project / ".nerdvana" / "memory-inbox" / f"{proposal.id}.json").read_text(encoding="utf-8"))
        assert raw["name"] == "a" and raw["scope"] == "project_knowledge" and raw["action"] == "write"
