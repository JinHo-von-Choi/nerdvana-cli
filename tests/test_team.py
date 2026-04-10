"""Tests for team mailbox and team registry."""

from __future__ import annotations

import json
import pytest
from pathlib import Path

from nerdvana_cli.core.team import (
    Mailbox,
    TeammateMessage,
    get_inbox_path,
    read_inbox,
    write_to_inbox,
)


@pytest.mark.asyncio
async def test_write_and_read_inbox(tmp_path: Path) -> None:
    inbox = get_inbox_path("worker-1", "my-team", base_dir=str(tmp_path))
    msg   = TeammateMessage(from_agent="leader", text="hello", summary="greeting")

    await write_to_inbox(inbox, msg)
    messages = await read_inbox(inbox)

    assert len(messages) == 1
    assert messages[0].text == "hello"
    assert messages[0].from_agent == "leader"
    assert messages[0].read is False


@pytest.mark.asyncio
async def test_write_multiple_messages(tmp_path: Path) -> None:
    inbox = get_inbox_path("worker-1", "my-team", base_dir=str(tmp_path))

    await write_to_inbox(inbox, TeammateMessage(from_agent="leader", text="msg1"))
    await write_to_inbox(inbox, TeammateMessage(from_agent="leader", text="msg2"))
    messages = await read_inbox(inbox)

    assert len(messages) == 2
    assert messages[1].text == "msg2"


def test_get_inbox_path_structure(tmp_path: Path) -> None:
    path = get_inbox_path("alpha", "team-x", base_dir=str(tmp_path))
    assert "team-x" in path
    assert "alpha.json" in path


# ---------------------------------------------------------------------------
# Team tool tests
# ---------------------------------------------------------------------------

import asyncio
from nerdvana_cli.core.task_state  import TaskRegistry, TaskState, TaskStatus
from nerdvana_cli.core.team        import TeamRegistry
from nerdvana_cli.core.tool        import ToolContext
from nerdvana_cli.tools.team_tools import (
    SendMessageTool,
    TaskGetTool,
    TaskStopTool,
    TeamCreateTool,
)


@pytest.mark.asyncio
async def test_team_create_tool() -> None:
    team_registry = TeamRegistry()
    tool          = TeamCreateTool(team_registry=team_registry)
    ctx           = ToolContext(cwd=".", team_registry=team_registry)

    result = await tool.call(
        tool.args_class(team_name="alpha-team"),  # type: ignore[call-arg]
        ctx,
        can_use_tool=None,
    )

    assert not result.is_error
    assert "alpha-team" in result.content
    assert team_registry.get("alpha-team") is not None


@pytest.mark.asyncio
async def test_task_get_tool_completed(tmp_path: Path) -> None:
    task_registry = TaskRegistry()
    task = TaskState(
        id="t42",
        description="test",
        status=TaskStatus.COMPLETED,
        output="finished output",
        abort=asyncio.Event(),
    )
    task_registry.register(task)

    tool = TaskGetTool(task_registry=task_registry)
    ctx  = ToolContext(cwd=".", task_registry=task_registry)

    result = await tool.call(
        tool.args_class(task_id="t42"),  # type: ignore[call-arg]
        ctx,
        can_use_tool=None,
    )

    assert "completed" in result.content
    assert "finished output" in result.content


@pytest.mark.asyncio
async def test_task_stop_tool_kills_running_task() -> None:
    task_registry = TaskRegistry()
    abort = asyncio.Event()
    task  = TaskState(
        id="t99",
        description="long task",
        status=TaskStatus.RUNNING,
        abort=abort,
    )
    task_registry.register(task)

    tool   = TaskStopTool(task_registry=task_registry)
    ctx    = ToolContext(cwd=".", task_registry=task_registry)
    result = await tool.call(
        tool.args_class(task_id="t99"),  # type: ignore[call-arg]
        ctx,
        can_use_tool=None,
    )

    assert not result.is_error
    assert abort.is_set()
    assert task.status == TaskStatus.KILLED


@pytest.mark.asyncio
async def test_send_message_tool(tmp_path: Path) -> None:
    from nerdvana_cli.core.team import TeamMember

    team_registry = TeamRegistry()
    team_registry.create("my-team")
    member = TeamMember(agent_id="worker@my-team", name="worker", team_name="my-team")
    team_registry.register_member("my-team", member)

    tool = SendMessageTool(team_registry=team_registry, base_dir=str(tmp_path))
    ctx  = ToolContext(cwd=".", team_registry=team_registry)

    result = await tool.call(
        tool.args_class(to="worker", message="hello worker", team_name="my-team"),  # type: ignore[call-arg]
        ctx,
        can_use_tool=None,
    )

    assert not result.is_error
    assert "delivered" in result.content.lower() or "sent" in result.content.lower()
