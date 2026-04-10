import asyncio
import pytest
from nerdvana_cli.core.task_state import TaskRegistry, TaskState, TaskStatus


def test_task_registry_register_and_get() -> None:
    reg   = TaskRegistry()
    abort = asyncio.Event()
    task  = TaskState(id="t1", description="test task", abort=abort)
    reg.register(task)
    assert reg.get("t1") is task


def test_task_registry_running_filter() -> None:
    reg = TaskRegistry()
    t1  = TaskState(id="t1", description="a", abort=asyncio.Event(), status=TaskStatus.RUNNING)
    t2  = TaskState(id="t2", description="b", abort=asyncio.Event(), status=TaskStatus.COMPLETED)
    reg.register(t1)
    reg.register(t2)
    running = reg.running()
    assert t1 in running
    assert t2 not in running


def test_task_registry_evict() -> None:
    reg  = TaskRegistry()
    task = TaskState(id="t1", description="x", abort=asyncio.Event())
    reg.register(task)
    reg.evict("t1")
    assert reg.get("t1") is None


def test_task_state_has_current_tool() -> None:
    t = TaskState(id="x", description="d")
    assert t.current_tool == ""


def test_task_state_has_tokens_used() -> None:
    t = TaskState(id="x", description="d")
    assert t.tokens_used == 0


def test_task_state_has_output_buffer() -> None:
    t = TaskState(id="x", description="d")
    assert t.output_buffer == []
    t.output_buffer.append("chunk1")
    assert t.output_buffer == ["chunk1"]
