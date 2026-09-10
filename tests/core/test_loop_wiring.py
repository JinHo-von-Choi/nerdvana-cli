"""Wiring between the agent loop and the machinery it claims to drive.

Every check here answers one question: does the mechanism run in production, or
only in a test that calls it directly? Hooks that never fire, checkpoints that
capture nothing and analytics that record nothing all look armed from the
outside, which is worse than not having them.

Author: 최진호
Date:   2026-09-11
"""

from __future__ import annotations

import logging
import sqlite3
import subprocess
import threading
from pathlib import Path
from typing import Any

import pytest

from nerdvana_cli.core.activity_state import ActivityState
from nerdvana_cli.core.agent_loop import AgentLoop, compact_messages
from nerdvana_cli.core.analytics import AnalyticsWriter
from nerdvana_cli.core.checkpoint import CheckpointManager
from nerdvana_cli.core.hooks import HookContext, HookEngine, HookEvent, HookResult
from nerdvana_cli.core.loop_hooks import LoopHookEngine
from nerdvana_cli.core.session import SessionStorage
from nerdvana_cli.core.settings import ModelConfig, NerdvanaSettings, SessionConfig
from nerdvana_cli.core.tool import BaseTool, ToolContext, ToolRegistry
from nerdvana_cli.core.tool_executor import ToolExecutor
from nerdvana_cli.types import Message, Role, ToolResult
from nerdvana_cli.ui.app import make_activity_change_callback

# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------

class _EchoTool(BaseTool[Any]):
    """Read-only tool with no arguments of interest."""

    name                = "Echo"
    is_concurrency_safe = False

    async def call(
        self,
        args:         Any,
        context:      ToolContext,
        can_use_tool: Any,
        on_progress:  Any = None,
    ) -> ToolResult:
        return ToolResult(tool_use_id="", content=f"echo:{args.get('text', '')}")


class _WriteArgs:
    def __init__(self, path: str = "", content: str = "") -> None:
        self.path    = path
        self.content = content


class _FakeFileWriteTool(BaseTool[Any]):
    """Stands in for the real FileWrite: same name, same argument shape."""

    name       = "FileWrite"
    args_class = _WriteArgs

    async def call(
        self,
        args:         Any,
        context:      ToolContext,
        can_use_tool: Any,
        on_progress:  Any = None,
    ) -> ToolResult:
        target = Path(context.cwd) / args.path
        target.write_text(args.content)
        return ToolResult(tool_use_id="", content="written")


class _ExplodingArgs:
    def __init__(self, path: str = "") -> None:
        self._path = path

    @property
    def path(self) -> str:
        raise RuntimeError("argument object refuses to expose its path")


class _HostileArgsWriteTool(_FakeFileWriteTool):
    """Edit tool whose parsed arguments cannot be inspected for a path."""

    args_class = _ExplodingArgs

    async def call(
        self,
        args:         Any,
        context:      ToolContext,
        can_use_tool: Any,
        on_progress:  Any = None,
    ) -> ToolResult:
        return ToolResult(tool_use_id="", content="written")


class _PreviewArgs:
    def __init__(self, relative_path: str = "", apply: bool = False) -> None:
        self.relative_path = relative_path
        self.apply         = apply


class _FakeReplaceSymbolBodyTool(_FakeFileWriteTool):
    name       = "ReplaceSymbolBody"
    args_class = _PreviewArgs

    async def call(
        self,
        args:         Any,
        context:      ToolContext,
        can_use_tool: Any,
        on_progress:  Any = None,
    ) -> ToolResult:
        return ToolResult(tool_use_id="", content="preview only")


class _FakeApp:
    """Minimal stand-in for the Textual app used by the activity callback."""

    def __init__(self, widget: Any, raise_on_query: bool = False) -> None:
        self.widget            = widget
        self.raise_on_query    = raise_on_query
        self.from_thread_calls = 0

    def query_one(self, selector: str, expect_type: Any) -> Any:
        if self.raise_on_query:
            raise LookupError("no widget matches #activity-indicator")
        return self.widget

    def call_from_thread(self, callback: Any) -> None:
        self.from_thread_calls += 1
        callback()


class _Widget:
    def __init__(self) -> None:
        self.state: ActivityState | None = None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture()
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Git repository with one committed file and an isolated snapshot store."""
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))

    project = tmp_path / "project"
    project.mkdir()
    subprocess.run(["git", "init", str(project)], check=True, capture_output=True)
    _git(project, "config", "user.email", "test@test.com")
    _git(project, "config", "user.name", "Test")
    (project / "target.py").write_text("original\n")
    _git(project, "add", ".")
    _git(project, "commit", "-m", "init")
    return project


def _settings(cwd: str) -> NerdvanaSettings:
    settings         = NerdvanaSettings()
    settings.model   = ModelConfig(provider="anthropic", model="claude-test-mock", api_key="test-key")
    settings.session = SessionConfig(persist=False, max_turns=5)
    settings.cwd     = cwd
    settings.verbose = False
    return settings


def _executor(
    tools:              list[BaseTool[Any]],
    hooks:              HookEngine | None       = None,
    checkpoint_manager: CheckpointManager | None = None,
    analytics_writer:   AnalyticsWriter | None   = None,
    cwd:                str                      = ".",
) -> ToolExecutor:
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool)
    return ToolExecutor(
        registry           = registry,
        hooks              = hooks or HookEngine(),
        settings           = _settings(cwd),
        checkpoint_manager = checkpoint_manager,
        analytics_writer   = analytics_writer,
    )


def _call(name: str, **inputs: Any) -> dict[str, Any]:
    return {"id": f"call_{name}_0000abcd", "name": name, "input": dict(inputs)}


# ---------------------------------------------------------------------------
# AFTER_TOOL hooks must fire on the real execution path
# ---------------------------------------------------------------------------

async def test_after_tool_hook_fires_on_real_tool_execution() -> None:
    """A handler registered on AFTER_TOOL sees every tool the executor runs."""
    seen: list[HookContext] = []

    hooks = HookEngine()
    hooks.register(HookEvent.AFTER_TOOL, lambda ctx: seen.append(ctx) or HookResult())

    executor = _executor([_EchoTool()], hooks=hooks)
    results  = await executor.run_batch([_call("Echo", text="hi")], ToolContext())

    assert len(seen) == 1, "AFTER_TOOL did not fire for an executed tool"
    assert seen[0].tool_name == "Echo"
    assert seen[0].tool_result is results[0]
    assert seen[0].tool_input == {"text": "hi"}


async def test_after_tool_hook_fires_for_concurrent_tools() -> None:
    """Concurrency-safe tools go down a separate branch; it must fire too."""
    class _ConcurrentEcho(_EchoTool):
        name                = "ConcurrentEcho"
        is_concurrency_safe = True

    seen: list[str] = []
    hooks = HookEngine()
    hooks.register(HookEvent.AFTER_TOOL, lambda ctx: seen.append(ctx.tool_name) or HookResult())

    executor = _executor([_ConcurrentEcho()], hooks=hooks)
    await executor.run_batch(
        [_call("ConcurrentEcho", text="a"), _call("ConcurrentEcho", text="b")],
        ToolContext(),
    )

    assert seen == ["ConcurrentEcho", "ConcurrentEcho"]


def test_dead_after_tool_dispatcher_is_gone() -> None:
    """LoopHookEngine must not keep a second AFTER_TOOL entry point nothing calls."""
    assert not hasattr(LoopHookEngine, "on_tool_result"), (
        "LoopHookEngine.on_tool_result duplicates the AFTER_TOOL firing that now "
        "happens in ToolExecutor; a dispatcher with no production caller is a "
        "decoy extension point"
    )


def test_before_api_call_has_a_production_fire_site(tmp_path: Path) -> None:
    """BEFORE_API_CALL is dispatched from the loop, so the handler runs.

    The handler lives in core/activity_hooks.py and moves the indicator into its
    waiting_api phase. It only means anything if something fires the event on
    the way to the provider, which core/agent_loop.py now does.
    """
    root       = Path(__file__).resolve().parents[2] / "nerdvana_cli"
    definition = root / "core" / "hooks.py"
    handler    = root / "core" / "activity_hooks.py"

    fire_sites = sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*.py")
        if path not in {definition, handler}
        and "HookEvent.BEFORE_API_CALL" in path.read_text(encoding="utf-8")
    )

    assert "core/agent_loop.py" in fire_sites, (
        "no production code dispatches BEFORE_API_CALL; the handler and the "
        "docs advertise an extension point that never runs"
    )

    loop = AgentLoop(
        settings = _settings(str(tmp_path)),
        registry = ToolRegistry(),
        session  = SessionStorage(session_id="wiring-fire", storage_dir=str(tmp_path)),
    )
    loop.activity_state.phase = "idle"

    fired = loop._fire_before_api_call([])

    assert not fired, "no handler injected messages here"
    assert loop.activity_state.phase == "waiting_api", (
        "the built-in handler did not see the event the loop dispatched"
    )


# ---------------------------------------------------------------------------
# Pre-edit checkpoints must capture files, not merely be called
# ---------------------------------------------------------------------------

async def test_edit_tool_execution_captures_a_snapshot(repo: Path) -> None:
    """Running an edit tool must leave a restorable copy of the original file."""
    manager  = CheckpointManager(cwd=str(repo), session_id="wiring-capture")
    executor = _executor([_FakeFileWriteTool()], checkpoint_manager=manager, cwd=str(repo))

    result = await executor.run_batch(
        [_call("FileWrite", path="target.py", content="rewritten\n")],
        ToolContext(cwd=str(repo)),
    )

    assert not result[0].is_error
    assert (repo / "target.py").read_text() == "rewritten\n"

    entries = manager.list_checkpoints()
    assert entries, "no checkpoint was captured for an executed edit tool"
    assert any("target.py" in path for path in entries[-1].paths)

    manager.undo()
    assert (repo / "target.py").read_text() == "original\n", (
        "the snapshot exists but holds nothing restorable"
    )


async def test_checkpoint_survives_unreadable_edit_arguments(
    repo:    Path,
    caplog:  pytest.LogCaptureFixture,
) -> None:
    """A path that cannot be read is reported, and the tool still runs."""
    manager  = CheckpointManager(cwd=str(repo), session_id="wiring-hostile")
    executor = _executor([_HostileArgsWriteTool()], checkpoint_manager=manager, cwd=str(repo))

    with caplog.at_level(logging.WARNING, logger="nerdvana_cli.core.tool_executor"):
        results = await executor.run_batch(
            [_call("FileWrite", path="target.py")],
            ToolContext(cwd=str(repo)),
        )

    assert not results[0].is_error, "path extraction failure must not fail the tool"
    assert "could not read the edit targets" in caplog.text, (
        "extraction failure was swallowed; undo silently covers nothing"
    )
    assert manager.list_checkpoints() == []


async def test_preview_only_symbol_edit_takes_no_checkpoint(repo: Path) -> None:
    """A preview call changes no file, so it must not consume the undo stack."""
    manager  = CheckpointManager(cwd=str(repo), session_id="wiring-preview")
    executor = _executor([_FakeReplaceSymbolBodyTool()], checkpoint_manager=manager, cwd=str(repo))

    await executor.run_batch(
        [_call("ReplaceSymbolBody", relative_path="target.py", apply=False)],
        ToolContext(cwd=str(repo)),
    )

    assert manager.list_checkpoints() == []


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------

async def test_analytics_rows_accumulate_when_a_writer_is_injected(tmp_path: Path) -> None:
    """The executor writes one row per tool call once a writer reaches it."""
    db     = tmp_path / "analytics.sqlite"
    writer = AnalyticsWriter(db_path=db)
    writer.start_session("wiring-analytics")

    executor = _executor([_EchoTool()], analytics_writer=writer)
    await executor.run_batch(
        [_call("Echo", text="a"), _call("Echo", text="b")],
        ToolContext(),
    )

    with sqlite3.connect(db) as conn:
        rows = conn.execute(
            "SELECT tool_name, success FROM tool_calls WHERE session_id = ?",
            ("wiring-analytics",),
        ).fetchall()

    assert rows == [("Echo", 1), ("Echo", 1)]


async def test_agent_loop_wires_the_analytics_writer(
    tmp_path:    Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The production ToolExecutor gets a writer, and its rows reach the DB.

    The dashboard, the session commands and the cost command all read
    analytics.sqlite. They report on what this executor writes, so the check is
    the recorded row rather than the constructor argument.
    """
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))

    registry = ToolRegistry()
    registry.register(_EchoTool())
    loop = AgentLoop(
        settings = _settings(str(tmp_path)),
        registry = registry,
        session  = SessionStorage(session_id="wiring-c12", storage_dir=str(tmp_path)),
    )

    assert loop.tool_executor._analytics_writer is not None, (
        "the production ToolExecutor is built without a writer, so every read "
        "consumer sees an empty database while looking fully wired"
    )

    await loop.tool_executor.run_batch([_call("Echo", text="hi")], ToolContext())

    with sqlite3.connect(tmp_path / "data" / "analytics.sqlite") as conn:
        rows = conn.execute(
            "SELECT tool_name, success FROM tool_calls WHERE session_id = ?",
            ("wiring-c12",),
        ).fetchall()

    assert rows == [("Echo", 1)], "the writer reached the executor but recorded nothing"


# ---------------------------------------------------------------------------
# Naive compaction
# ---------------------------------------------------------------------------

def test_naive_compaction_leaves_an_orphan_tool_result() -> None:
    """Naive truncation can start the history with a tool result and no tool_use.

    The AI compaction path at core/agent_loop.py:299-301 drops leading TOOL
    messages; the naive fallback at core/agent_loop.py:306 does not, so the
    provider receives a tool_result whose tool_use is gone and answers 400. The
    fix belongs next to that assignment, applying the same leading-TOOL trim
    that the AI path already performs.
    """
    filler   = "x" * 400
    messages = [
        Message(role=Role.USER,      content=filler),
        Message(role=Role.ASSISTANT, content=filler),
        Message(role=Role.USER,      content=filler),
        Message(role=Role.ASSISTANT, content=filler),
        Message(role=Role.TOOL,      content=filler, tool_use_id="call_Echo_0000abcd"),
        Message(role=Role.ASSISTANT, content=filler),
        Message(role=Role.USER,      content=filler),
        Message(role=Role.ASSISTANT, content=filler),
    ]

    compacted = compact_messages(messages, 200)

    assert compacted[0].role == Role.TOOL, (
        "naive compaction no longer starts with an orphan tool result; if the "
        "leading-TOOL trim was added at core/agent_loop.py:306, delete this test"
    )


# ---------------------------------------------------------------------------
# Activity indicator
# ---------------------------------------------------------------------------

def test_activity_callback_updates_widget_on_the_ui_thread() -> None:
    """Same-thread delivery writes the widget directly, never call_from_thread."""
    widget   = _Widget()
    app      = _FakeApp(widget)
    callback = make_activity_change_callback(app, threading.get_ident())

    state = ActivityState(phase="tool_running", label="Running Echo")
    callback(state)

    assert widget.state is state
    assert app.from_thread_calls == 0, (
        "call_from_thread raises RuntimeError when the caller already owns the "
        "event loop, which is exactly what froze the indicator"
    )


def test_activity_callback_marshals_from_a_worker_thread() -> None:
    """A caller on another thread still goes through call_from_thread."""
    widget   = _Widget()
    app      = _FakeApp(widget)
    callback = make_activity_change_callback(app, threading.get_ident() + 1)

    state = ActivityState(phase="waiting_api", label="Waiting")
    callback(state)

    assert widget.state is state
    assert app.from_thread_calls == 1


def test_activity_callback_reports_failure_instead_of_raising(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The loop swallows exceptions from this callback, so it logs its own."""
    app      = _FakeApp(_Widget(), raise_on_query=True)
    callback = make_activity_change_callback(app, threading.get_ident())

    with caplog.at_level(logging.WARNING, logger="nerdvana_cli.ui.app"):
        callback(ActivityState(phase="idle", label="Ready"))

    assert "activity indicator update failed" in caplog.text


# ---------------------------------------------------------------------------
# Regression guard for the untouched path
# ---------------------------------------------------------------------------

async def test_ordinary_tool_execution_is_unchanged() -> None:
    """Results keep their order, and an unknown tool still returns inline."""
    executor = _executor([_EchoTool()])

    results = await executor.run_batch(
        [_call("Echo", text="first"), _call("Nope"), _call("Echo", text="second")],
        ToolContext(),
    )

    assert [r.content for r in results] == ["echo:first", "Unknown tool: Nope", "echo:second"]
    assert [r.is_error for r in results] == [False, True, False]


async def test_mixed_batch_keeps_the_order_it_was_given() -> None:
    """Concurrency-safe calls are scheduled apart, and must return in place.

    Result order is the tool_result order the provider receives. A batch that
    interleaves safe and unsafe tools used to come back grouped by schedule,
    which no longer matches the tool_use order the model declared.
    """
    class _ConcurrentEcho(_EchoTool):
        name                = "ConcurrentEcho"
        is_concurrency_safe = True

    executor = _executor([_EchoTool(), _ConcurrentEcho()])

    calls = [
        {**_call("ConcurrentEcho", text="a"), "id": "call_0"},
        {**_call("Echo",           text="b"), "id": "call_1"},
        {**_call("ConcurrentEcho", text="c"), "id": "call_2"},
        {**_call("Nope"),                     "id": "call_3"},
        {**_call("Echo",           text="d"), "id": "call_4"},
    ]
    results = await executor.run_batch(calls, ToolContext())

    assert [r.content for r in results] == [
        "echo:a",
        "echo:b",
        "echo:c",
        "Unknown tool: Nope",
        "echo:d",
    ]
    assert [r.tool_use_id for r in results] == [c["id"] for c in calls]
