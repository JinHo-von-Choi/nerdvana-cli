"""Tests for the memory and checkpoint slash-command handlers.

작성자: 최진호
작성일: 2026-09-11
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from nerdvana_cli.commands import memory_commands as mc
from nerdvana_cli.core.checkpoint import CheckpointEntry
from nerdvana_cli.core.memories import MemoriesManager, MemoryScope

# ---------------------------------------------------------------------------
# Doubles
# ---------------------------------------------------------------------------


class _Settings:
    def __init__(self, cwd: str) -> None:
        self.cwd = cwd


class _App:
    """Minimal stand-in for NerdvanaApp: records what reached the chat pane."""

    def __init__(self, cwd: str = ".", checkpoint_manager: Any = None) -> None:
        self.settings            = _Settings(cwd)
        self.messages: list[str] = []
        if checkpoint_manager is not None:
            self._checkpoint_manager = checkpoint_manager

    def _add_chat_message(self, message: str, **kwargs: Any) -> None:
        self.messages.append(message)

    @property
    def last(self) -> str:
        return self.messages[-1]


class _Checkpoints:
    def __init__(
        self,
        entries:  list[CheckpointEntry] | None = None,
        undo_msg: str                          = "Undone checkpoint 0001 (edit #1): 2 file(s) restored.",
        redo_msg: str                          = "Redone edit as checkpoint 0002: 2 file(s) re-applied.",
    ) -> None:
        self._entries = entries or []
        self._undo    = undo_msg
        self._redo    = redo_msg

    def undo(self) -> str:
        return self._undo

    def redo(self) -> str:
        return self._redo

    def list_checkpoints(self) -> list[CheckpointEntry]:
        return self._entries


def _entry(cid: str, edit_id: int, kind: str = "snapshot") -> CheckpointEntry:
    return CheckpointEntry(
        checkpoint_id = cid,
        session       = "sess",
        edit_id       = edit_id,
        paths         = (),
        kind          = kind,
    )


# ---------------------------------------------------------------------------
# /undo and /redo
# ---------------------------------------------------------------------------


class TestUndoRedo:
    async def test_undo_without_manager(self) -> None:
        app = _App()
        await mc.handle_undo(app, "")
        assert "Checkpoint manager not available" in app.last

    async def test_redo_without_manager(self) -> None:
        app = _App()
        await mc.handle_redo(app, "")
        assert "Checkpoint manager not available" in app.last

    async def test_undo_success_is_green(self) -> None:
        app = _App(checkpoint_manager=_Checkpoints())
        await mc.handle_undo(app, "")
        assert app.last.startswith("[green]")
        assert "Undone checkpoint" in app.last

    async def test_undo_failure_is_yellow(self) -> None:
        app = _App(checkpoint_manager=_Checkpoints(undo_msg="Nothing to undo."))
        await mc.handle_undo(app, "")
        assert app.last.startswith("[yellow]")

    async def test_redo_success_is_green(self) -> None:
        app = _App(checkpoint_manager=_Checkpoints())
        await mc.handle_redo(app, "")
        assert app.last.startswith("[green]")

    async def test_redo_failure_is_yellow(self) -> None:
        app = _App(checkpoint_manager=_Checkpoints(redo_msg="Nothing to redo."))
        await mc.handle_redo(app, "")
        assert app.last.startswith("[yellow]")


# ---------------------------------------------------------------------------
# /checkpoints
# ---------------------------------------------------------------------------


class TestCheckpointsListing:
    async def test_without_manager(self) -> None:
        app = _App()
        await mc.handle_checkpoints(app, "")
        assert "Checkpoint manager not available" in app.last

    async def test_empty(self) -> None:
        app = _App(checkpoint_manager=_Checkpoints([]))
        await mc.handle_checkpoints(app, "")
        assert "No checkpoints in this session" in app.last

    async def test_lists_newest_first_by_checkpoint_id(self) -> None:
        entries = [_entry("0001-aaa", 1), _entry("0002-bbb", 2)]
        app     = _App(checkpoint_manager=_Checkpoints(entries))
        await mc.handle_checkpoints(app, "")

        lines = app.last.splitlines()
        assert "Session checkpoints (2)" in lines[0]
        assert "0002-bbb" in lines[1]
        assert "0001-aaa" in lines[2]

    async def test_legacy_stash_rows_are_marked(self) -> None:
        entries = [_entry("stash@{0}", 7, kind="legacy-stash"), _entry("0003-ccc", 8)]
        app     = _App(checkpoint_manager=_Checkpoints(entries))
        await mc.handle_checkpoints(app, "")

        rows = {line.split()[0]: line for line in app.last.splitlines()[1:]}
        assert "earlier build" in rows["stash@{0}"]
        assert "/undo skips it" in rows["stash@{0}"]
        assert "earlier build" not in rows["0003-ccc"]


# ---------------------------------------------------------------------------
# /memories
# ---------------------------------------------------------------------------


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A project root with an isolated global memory directory."""
    root = tmp_path / "proj"
    root.mkdir()
    monkeypatch.setattr(
        "nerdvana_cli.core.memories.core_paths.global_memories_dir",
        lambda: tmp_path / "global",
    )
    return root


class TestMemoriesListing:
    async def test_empty_project(self, project: Path) -> None:
        app = _App(cwd=str(project))
        await mc.handle_memories(app, "")
        assert "Project memories — 0 found" in app.last
        assert "(none)" in app.last

    async def test_lists_entries(self, project: Path) -> None:
        mgr = MemoriesManager(str(project))
        mgr.write("build/commands", "pytest -q", MemoryScope.PROJECT_KNOWLEDGE)

        app = _App(cwd=str(project))
        await mc.handle_memories(app, "")
        assert "Project memories — 1 found" in app.last
        assert "build/commands" in app.last
        assert "project_knowledge" in app.last

    async def test_stale_flag_reports_nothing_when_fresh(self, project: Path) -> None:
        MemoriesManager(str(project)).write("fresh", "now", MemoryScope.PROJECT_KNOWLEDGE)
        app = _App(cwd=str(project))
        await mc.handle_memories(app, "--stale")
        assert "Stale memories (>= 30 days old) — 0 found" in app.last

    async def test_stale_flag_with_custom_days(self, project: Path) -> None:
        mgr = MemoriesManager(str(project))
        mgr.write("old", "ancient", MemoryScope.PROJECT_KNOWLEDGE)

        aged = project / ".nerdvana" / "memories" / "old.md"
        past = time.time() - (10 * 86400)
        import os
        os.utime(aged, (past, past))

        app = _App(cwd=str(project))
        await mc.handle_memories(app, "--stale --days 5")
        assert "Stale memories (>= 5 days old) — 1 found" in app.last
        assert "old" in app.last


# ---------------------------------------------------------------------------
# Scope classification
# ---------------------------------------------------------------------------


class TestClassifyScope:
    @pytest.mark.parametrize(
        ("content", "expected"),
        [
            ("You must never commit secrets", "project_rule"),
            ("The build imports the config module", "project_knowledge"),
            ("I prefer this style, I like it", "user_global"),
            ("traceback exception bug fix resolved", "agent_experience"),
        ],
    )
    def test_dominant_signal_wins(self, content: str, expected: str) -> None:
        assert mc._classify_scope(content) == expected

    def test_no_signal_falls_back_to_project_knowledge(self) -> None:
        assert mc._classify_scope("qqq zzz") == "project_knowledge"

    def test_score_scopes_covers_every_scope(self) -> None:
        scores = mc._score_scopes("error")
        assert set(scores) == {
            "project_rule", "project_knowledge", "user_global", "agent_experience",
        }
        assert scores["agent_experience"] == 1


# ---------------------------------------------------------------------------
# /route-knowledge
# ---------------------------------------------------------------------------


class TestRouteKnowledge:
    async def test_usage_help_when_empty(self) -> None:
        app = _App()
        await mc.handle_route_knowledge(app, "   ")
        assert "Usage: /route-knowledge" in app.last
        assert "agent_experience" in app.last

    async def test_help_does_not_promise_writememory_for_experience(self) -> None:
        app = _App()
        await mc.handle_route_knowledge(app, "")
        experience_line = next(
            line for line in app.last.splitlines() if "agent_experience" in line
        )
        assert "AnchorMind" in app.last
        assert "WriteMemory" not in experience_line

    async def test_suggests_writememory_for_file_backed_scope(self) -> None:
        app = _App()
        await mc.handle_route_knowledge(app, "The build imports the config module")
        assert "Suggested scope:" in app.last
        assert "project_knowledge" in app.last
        assert "WriteMemory(name='<name>'" in app.last
        assert "scope='project_knowledge'" in app.last

    async def test_scores_are_shown_for_every_scope(self) -> None:
        app = _App()
        await mc.handle_route_knowledge(app, "must never do this")
        for scope in ("project_rule", "project_knowledge", "user_global", "agent_experience"):
            assert scope in app.last
        assert "score=" in app.last

    async def test_experience_content_routes_to_anchormind_not_writememory(self) -> None:
        app = _App()
        await mc.handle_route_knowledge(app, "traceback exception bug fix resolved")
        assert "agent_experience" in app.last
        assert "AnchorMind" in app.last
        assert "mcp__anchormind__remember" in app.last
        assert "WriteMemory(name=" not in app.last
