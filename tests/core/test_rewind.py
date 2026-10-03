"""/rewind: messages and file edits go back together.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import subprocess
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from nerdvana_cli.core.agent_loop import AgentLoop
from nerdvana_cli.core.session import SessionStorage, messages_from_transcript
from nerdvana_cli.core.settings import NerdvanaSettings
from nerdvana_cli.core.tool import ToolRegistry
from nerdvana_cli.providers.base import ProviderEvent
from nerdvana_cli.tools.file_tools import FileReadTool, FileWriteTool
from nerdvana_cli.types import Role


class _Script:
    """Per prompt: write a file, then answer (the step before an answer is always a tool result)."""

    def __init__(self) -> None:
        self.writes = 0

    async def stream(self, system_prompt: str, messages: Any, tools: Any) -> AsyncIterator[ProviderEvent]:
        if messages[-1]["role"] == "tool":
            yield ProviderEvent(type="content_delta", content="done")
            yield ProviderEvent(type="done", stop_reason="end_turn")
            return
        self.writes += 1
        yield ProviderEvent(type="tool_use_complete", tool_use_id=f"w{self.writes}", tool_name="FileWrite",
                            tool_input_complete={"path": f"f{self.writes}.txt", "content": f"content {self.writes}\n"})
        yield ProviderEvent(type="done", stop_reason="tool_use")


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t", *args], check=True, capture_output=True)


@pytest.fixture()
def loop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AgentLoop:
    project = tmp_path / "project"
    project.mkdir()
    _git(project, "init", "-q")
    (project / "base.txt").write_text("base\n")
    _git(project, "add", "-A")
    _git(project, "commit", "-q", "-m", "base")
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    script = _Script()          # one instance: the loop builds its provider again after every prompt
    monkeypatch.setattr(AgentLoop, "create_provider_from_settings", lambda self: script)
    monkeypatch.setattr(AgentLoop, "build_system_prompt", lambda self: "system")
    registry = ToolRegistry()
    registry.register(FileWriteTool())
    registry.register(FileReadTool())
    settings     = NerdvanaSettings()
    settings.cwd = str(project)
    settings.session.default_mode = "one-shot"
    return AgentLoop(settings=settings, registry=registry, session=SessionStorage(session_id="rw", storage_dir=str(tmp_path / "s")))


async def _prompt(loop: AgentLoop, text: str) -> None:
    async for _ in loop.run(text):
        pass


async def test_rewinding_one_prompt_removes_its_messages_and_its_file(loop: AgentLoop) -> None:
    project = Path(loop.settings.cwd)
    await _prompt(loop, "first")
    after_first = len(loop.state.messages)
    await _prompt(loop, "second")
    assert (project / "f1.txt").exists() and (project / "f2.txt").exists()

    message = loop.rewind(1)
    assert "Rewound 1 prompt(s)" in message and "1 edit(s) undone" in message
    assert len(loop.state.messages) == after_first
    assert (project / "f1.txt").exists() and not (project / "f2.txt").exists()


async def test_rewinding_several_prompts_goes_back_to_the_start(loop: AgentLoop) -> None:
    project = Path(loop.settings.cwd)
    await _prompt(loop, "first")
    await _prompt(loop, "second")
    assert "Rewound 2 prompt(s)" in loop.rewind(5)       # more than there are: all of them
    assert loop.state.messages == [] and not (project / "f1.txt").exists() and not (project / "f2.txt").exists()
    assert (project / "base.txt").exists()


async def test_with_nothing_to_rewind_it_says_so_and_changes_nothing(loop: AgentLoop) -> None:
    assert "Nothing to rewind" in loop.rewind(1)
    await _prompt(loop, "first")
    loop.rewind(1)
    assert "Nothing to rewind" in loop.rewind(1)


async def test_a_compaction_ends_how_far_back_it_can_go(loop: AgentLoop) -> None:
    await _prompt(loop, "first")
    loop.rewinder.marks.clear()           # what a compaction does to the marks
    assert "Nothing to rewind" in loop.rewind(1)


async def test_a_prompt_after_a_rewind_starts_from_the_rewound_state(loop: AgentLoop) -> None:
    await _prompt(loop, "first")
    loop.rewind(1)
    await _prompt(loop, "again")
    assert [m.content for m in loop.state.messages if m.role == Role.USER][-1] == "again"
    assert len(loop.rewinder.marks) == 1


async def test_the_transcript_records_the_rewind_so_a_resumed_session_agrees(loop: AgentLoop) -> None:
    await _prompt(loop, "first")
    await _prompt(loop, "second")
    loop.rewind(1)
    restored = messages_from_transcript(loop.session.replay())
    users    = [m.content for m in restored if m.role == Role.USER]
    assert users == ["first"]
    assert all("f2.txt" not in str(m.tool_uses) for m in restored)


def test_transcript_entries_after_a_rewind_are_kept() -> None:
    entries = [
        {"type": "user", "content": "a"}, {"type": "assistant", "content": "A"},
        {"type": "user", "content": "b"}, {"type": "assistant", "content": "B"},
        {"type": "system", "subtype": "rewind", "prompts": 1},
        {"type": "user", "content": "c"}, {"type": "assistant", "content": "C"},
    ]
    assert [m.content for m in messages_from_transcript(entries)] == ["a", "A", "c", "C"]
    two = entries[:4] + [{"type": "system", "subtype": "rewind", "prompts": 9}]
    assert messages_from_transcript(two) == []


async def test_the_command_reports_and_refuses_bad_input(loop: AgentLoop) -> None:
    from nerdvana_cli.commands.memory_commands import handle_rewind

    class _App:
        def __init__(self, agent: Any, busy: bool = False) -> None:
            self._agent_loop, self._is_generating, self.messages = agent, busy, []  # type: ignore[var-annotated]

        def _add_chat_message(self, markup: str, **_: Any) -> None:
            self.messages.append(markup)

    await _prompt(loop, "first")
    app = _App(loop)
    await handle_rewind(app, "")  # type: ignore[arg-type]
    assert "Rewound 1 prompt(s)" in app.messages[-1]
    await handle_rewind(app, "zero")  # type: ignore[arg-type]
    assert "Usage" in app.messages[-1]
    busy = _App(loop, busy=True)
    await handle_rewind(busy, "1")  # type: ignore[arg-type]
    assert "idle" in busy.messages[-1]
