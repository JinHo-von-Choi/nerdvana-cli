"""Medium-severity defects in the response runner, profile loader and prompt builder.

Each test pins one behaviour that regressed or was never guarded:
cancellation cleanup in the streaming runner, profile-name containment and
data-home resolution in the profile loader, and the git snapshot cache that
keeps the prompt builder off the subprocess path on every turn.

Author: 최진호
Date:   2026-09-11
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from nerdvana_cli.core import prompts
from nerdvana_cli.core.profiles import ProfileManager
from nerdvana_cli.core.symbol import LanguageServerSymbol, Location, _flatten
from nerdvana_cli.ui.response_runner import run_response_stream

# ---------------------------------------------------------------------------
# Test doubles for the streaming runner
# ---------------------------------------------------------------------------


class _FakeWidget:
    """Records class toggles and rendered content the way the real widgets do."""

    def __init__(self) -> None:
        self.classes:     set[str]  = set()
        self.rendered:    list[Any] = []
        self.reset_calls: int       = 0

    def add_class(self, name: str) -> None:
        self.classes.add(name)

    def remove_class(self, name: str) -> None:
        self.classes.discard(name)

    def update(self, content: Any = "") -> None:
        self.rendered.append(content)

    def update_content(self, text: str) -> None:
        self.rendered.append(text)

    def update_thinking(self, text: str) -> None:
        self.rendered.append(text)

    def update_status(self, **kwargs: Any) -> None:
        self.rendered.append(kwargs)

    def reset(self) -> None:
        self.reset_calls += 1

    def scroll_end(self, animate: bool = False) -> None:
        return None


class _HangingLoop:
    """Yields the given chunks, then blocks until the caller is cancelled."""

    def __init__(self, chunks: list[str]) -> None:
        self._chunks             = chunks
        self.state               = SimpleNamespace(
            usage=SimpleNamespace(input_tokens=0, output_tokens=0)
        )
        self.registry            = SimpleNamespace(all_tools=lambda: [])
        self.last_thinking       = ""
        self._on_thinking_chunk: Any = None
        self.started             = asyncio.Event()

    async def run(self, prompt: str) -> Any:
        for chunk in self._chunks:
            yield chunk
        self.started.set()
        await asyncio.Event().wait()


class _FakeApp:
    """Minimal stand-in for NerdvanaApp exposing only what the runner touches."""

    def __init__(self, chunks: list[str]) -> None:
        self.streaming      = _FakeWidget()
        self.tool_status    = _FakeWidget()
        self.status_bar     = _FakeWidget()
        self.chat_frame     = _FakeWidget()
        self.messages:      list[str] = []
        self._is_generating = False
        self.parism_client  = None
        self.settings       = SimpleNamespace(
            model=SimpleNamespace(model="test-model", provider="test-provider")
        )
        self._agent_loop    = _HangingLoop(chunks)

    def query_one(self, selector: Any, expect_type: Any = None) -> Any:
        mapping = {
            "#streaming-output": self.streaming,
            "#tool-status":      self.tool_status,
            "#status-bar":       self.status_bar,
            "#chat-frame":       self.chat_frame,
        }
        if isinstance(selector, str):
            return mapping[selector]
        return self.tool_status

    def _add_chat_message(self, text: str, raw_text: str | None = None, thinking: str = "") -> None:
        self.messages.append(text)

    def _update_context_usage(self, pct: int) -> None:
        return None


def _timer_task() -> asyncio.Task[Any]:
    """Return the runner's thinking-timer task, which must exist while streaming."""
    candidates = [
        task for task in asyncio.all_tasks()
        if getattr(task.get_coro(), "__name__", "") == "_update_thinking_timer"
    ]
    assert len(candidates) == 1, f"expected one timer task, found {len(candidates)}"
    return candidates[0]


async def _run_until_hanging(app: _FakeApp) -> asyncio.Task[None]:
    task = asyncio.create_task(run_response_stream(app, "hello"))
    await asyncio.wait_for(app._agent_loop.started.wait(), timeout=2.0)
    await asyncio.sleep(0)
    return task


# ---------------------------------------------------------------------------
# C16: cancellation cleanup
# ---------------------------------------------------------------------------


async def test_cancelled_stream_cancels_the_timer_task() -> None:
    """A cancelled worker must not leave the elapsed-time timer running."""
    app   = _FakeApp(["partial answer"])
    task  = await _run_until_hanging(app)
    timer = _timer_task()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0)

    assert timer.done(), "timer task survived cancellation and keeps updating the status bar"
    assert app._is_generating is False


async def test_cancelled_stream_clears_active_state_and_keeps_the_body() -> None:
    """Cancellation must drop the active markers and commit what was streamed."""
    app  = _FakeApp(["first half. ", "second half."])
    task = await _run_until_hanging(app)

    assert "active" in app.streaming.classes, "precondition: stream marked active"

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert "active" not in app.streaming.classes, "streaming widget stuck in generating state"
    assert "active" not in app.tool_status.classes, "tool status stuck in generating state"
    assert app.streaming.reset_calls >= 1
    assert any("first half. second half." in message for message in app.messages), (
        "streamed body was discarded instead of being committed to the chat"
    )


# ---------------------------------------------------------------------------
# C17: profile name containment and data-home resolution
# ---------------------------------------------------------------------------


@pytest.fixture()
def profile_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """Return (project_root, data_home) with both context directories present."""
    project   = tmp_path / "proj"
    data_home = tmp_path / "data"
    (project / ".nerdvana" / "contexts").mkdir(parents=True)
    (data_home / "contexts").mkdir(parents=True)
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(data_home))
    return project, data_home


@pytest.mark.parametrize("escape", ["../../../evil", "../../.././evil"])
def test_context_name_traversal_is_rejected(
    profile_env: tuple[Path, Path],
    tmp_path:    Path,
    escape:      str,
) -> None:
    """A name that climbs out of the profile directory must never be read."""
    project, _ = profile_env
    outside    = tmp_path / "evil.yml"
    outside.write_text("description: pwned\n", encoding="utf-8")

    resolved = (project / ".nerdvana" / "contexts" / f"{escape}.yml").resolve()
    assert resolved == outside.resolve(), "precondition: the name really points at the outside file"

    manager = ProfileManager(cwd=str(project))
    with pytest.raises(ValueError):
        manager.set_context(escape)

    assert manager.active_context_name == ProfileManager.DEFAULT_CONTEXT
    assert all("pwned" not in profile.description for profile in manager._context_cache.values())


def test_absolute_context_name_is_rejected(profile_env: tuple[Path, Path], tmp_path: Path) -> None:
    """An absolute name is an escape too, even when it contains no dot segments."""
    project, _ = profile_env
    outside    = tmp_path / "evil.yml"
    outside.write_text("description: pwned\n", encoding="utf-8")

    manager = ProfileManager(cwd=str(project))
    with pytest.raises(ValueError):
        manager.set_context(str(tmp_path / "evil"))


def test_valid_context_name_still_loads(profile_env: tuple[Path, Path]) -> None:
    """The containment check must not break ordinary project-local profiles."""
    project, _ = profile_env
    (project / ".nerdvana" / "contexts" / "teamwork.yml").write_text(
        "description: project local context\n", encoding="utf-8"
    )

    manager = ProfileManager(cwd=str(project))
    profile = manager.set_context("teamwork")

    assert profile.name == "teamwork"
    assert profile.description == "project local context"
    assert manager.active_context_name == "teamwork"
    assert "teamwork" in manager.available_contexts()


def test_nerdvana_data_home_is_respected(profile_env: tuple[Path, Path]) -> None:
    """User-global profiles come from NERDVANA_DATA_HOME, not a hardcoded home."""
    project, data_home = profile_env
    (data_home / "contexts" / "shared.yml").write_text(
        "description: from data home\n", encoding="utf-8"
    )

    manager = ProfileManager(cwd=str(project))

    assert "shared" in manager.available_contexts()
    assert manager.load_context("shared").description == "from data home"


# ---------------------------------------------------------------------------
# C19: git snapshot cache and flatten ordering
# ---------------------------------------------------------------------------


@pytest.fixture()
def git_calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, ...]]:
    """Count every git subprocess the prompt builder starts, with a cold cache."""
    calls: list[tuple[str, ...]] = []

    def _fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(tuple(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="true\n", stderr="")

    monkeypatch.setattr(prompts.subprocess, "run", _fake_run)
    prompts.clear_git_info_cache()
    yield calls
    prompts.clear_git_info_cache()


def test_git_info_is_cached_within_a_window(
    git_calls: list[tuple[str, ...]],
    tmp_path:  Path,
) -> None:
    """Repeated lookups in one turn must not multiply the subprocess count."""
    first = prompts._git_info(str(tmp_path))
    after_first = len(git_calls)
    assert after_first > 0, "precondition: the first lookup actually shells out"

    second = prompts._git_info(str(tmp_path))
    third  = prompts._git_info(str(tmp_path))

    assert len(git_calls) == after_first, "cached lookups still spawned git subprocesses"
    assert second == first and third == first

    prompts.clear_git_info_cache()
    prompts._git_info(str(tmp_path))
    assert len(git_calls) > after_first, "clearing the cache must force a fresh collection"


def test_build_system_prompt_reuses_the_cached_git_snapshot(
    git_calls: list[tuple[str, ...]],
    tmp_path:  Path,
) -> None:
    """Two prompt builds for one directory cost one git collection, not two."""
    prompts.build_system_prompt(tools=[], cwd=str(tmp_path))
    after_first = len(git_calls)

    prompts.build_system_prompt(tools=[], cwd=str(tmp_path))

    assert len(git_calls) == after_first


async def test_warm_git_info_fills_the_cache_off_thread(
    git_calls: list[tuple[str, ...]],
    tmp_path:  Path,
) -> None:
    """The async warm-up primes the cache so the next sync read costs nothing."""
    await prompts.warm_git_info(str(tmp_path))
    after_warm = len(git_calls)
    assert after_warm > 0

    prompts._git_info(str(tmp_path))

    assert len(git_calls) == after_warm


def _symbol(name: str, children: list[LanguageServerSymbol] | None = None) -> LanguageServerSymbol:
    return LanguageServerSymbol(
        name      = name,
        name_path = name,
        kind      = "Class",
        kind_int  = 5,
        location  = Location(file_path="a.py", line=1, character=0),
        children  = children or [],
        detail    = None,
    )


def test_flatten_keeps_depth_first_order() -> None:
    """The deque rewrite must not disturb the traversal order callers rely on."""
    tree = [
        _symbol("A", [_symbol("A1", [_symbol("A1a")]), _symbol("A2")]),
        _symbol("B", [_symbol("B1")]),
    ]

    assert [sym.name for sym in _flatten(tree)] == ["A", "A1", "A1a", "A2", "B", "B1"]
