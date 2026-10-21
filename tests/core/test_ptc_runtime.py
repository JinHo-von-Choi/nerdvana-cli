"""The PTC runtime: a sandboxed program, the bindings it calls, and the trail it leaves.

Author: 최진호
Date:   2026-10-21
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from nerdvana_cli.core.execution.ptc_runtime import (
    PTC_DISPATCH_EVENT,
    PtcRunResult,
    PtcRuntime,
)
from nerdvana_cli.core.safety.monotonic_guard import GuardVerdict, MonotonicGuardChain
from nerdvana_cli.core.safety.sandbox import landlock_abi

needs_landlock = pytest.mark.skipif(landlock_abi() < 1, reason="the kernel has no Landlock")


def _chain(*denied: str) -> MonotonicGuardChain:
    """A chain whose guards refuse the named tools."""
    chain = MonotonicGuardChain()

    def guard(tool_name: str, tool_args: dict[str, Any]) -> GuardVerdict:
        if tool_name in denied:
            return GuardVerdict.deny(f"{tool_name} is not allowed here", guard_name="test")
        return GuardVerdict.abstain()

    chain.register("test", guard)
    return chain


async def _echo(args: dict[str, Any]) -> str:
    return f"content of {args.get('path', '?')}"


async def _runtime(
    bindings: dict[str, Any] | None = None,
    chain: MonotonicGuardChain | None = None,
    audit: Any = None,
    cwd: str = ".",
    timeout_ms: int = 30_000,
) -> PtcRuntime:
    """A runtime with the given bindings."""
    return PtcRuntime(
        bindings    = bindings if bindings is not None else {"FileRead": _echo},
        guard_chain = chain if chain is not None else _chain(),
        audit       = audit,
        cwd         = cwd,
        timeout_ms = timeout_ms,
    )


@needs_landlock
async def test_the_value_of_the_last_expression_comes_back() -> None:
    runtime = await _runtime()

    result = await runtime.run("sum(range(10))")

    assert result.ok
    assert result.value == 45
    assert result.error == ""


@needs_landlock
async def test_a_binding_call_reaches_the_host_and_its_answer_comes_back() -> None:
    runtime = await _runtime()

    result = await runtime.run("FileRead(path='a.py')")

    assert result.ok
    assert result.value == "content of a.py"
    assert result.dispatches == 1


@needs_landlock
async def test_a_call_the_guard_refuses_never_reaches_the_host() -> None:
    called: list[str] = []

    async def watched(args: dict[str, Any]) -> str:
        called.append("yes")
        return "should not happen"

    runtime = await _runtime(bindings={"FileRead": watched}, chain=_chain("FileRead"))

    result = await runtime.run("FileRead(path='a.py')")

    assert not result.ok
    assert "refused by the test guard" in result.error
    assert called == []


@needs_landlock
async def test_every_call_is_recorded_in_the_audit_trail() -> None:
    events: list[tuple[str, dict[str, Any]]] = []

    def audit(event: str, data: dict[str, Any]) -> None:
        events.append((event, data))

    runtime = await _runtime(audit=audit)

    await runtime.run("[FileRead(path='a.py'), FileRead(path='b.py')]")

    assert [event for event, _data in events] == [PTC_DISPATCH_EVENT, PTC_DISPATCH_EVENT]
    assert events[0][1]["tool"] == "FileRead"
    assert events[0][1]["args"] == {"path": "a.py"}
    assert events[0][1]["denied"] is False


@needs_landlock
async def test_a_refused_call_is_recorded_as_denied() -> None:
    events: list[tuple[str, dict[str, Any]]] = []

    def audit(event: str, data: dict[str, Any]) -> None:
        events.append((event, data))

    runtime = await _runtime(chain=_chain("FileRead"), audit=audit)

    await runtime.run("FileRead(path='a.py')")

    assert events[0][1]["denied"] is True


@needs_landlock
async def test_the_program_cannot_write_a_file(tmp_path: Path) -> None:
    runtime = await _runtime(cwd=str(tmp_path))

    result = await runtime.run(f"open({str(tmp_path / 'out.txt')!r}, 'w').write('x')")

    assert not result.ok
    assert not (tmp_path / "out.txt").exists()


@needs_landlock
async def test_the_program_cannot_read_a_file_of_the_project(tmp_path: Path) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("the key", encoding="utf-8")
    runtime = await _runtime(cwd=str(tmp_path))

    result = await runtime.run(f"open({str(secret)!r}).read()")

    assert not result.ok
    assert "open" in result.error


@needs_landlock
async def test_the_program_cannot_import_the_process_modules() -> None:
    runtime = await _runtime()

    result = await runtime.run("import os")

    assert not result.ok
    assert "does not allow importing 'os'" in result.error


@needs_landlock
async def test_the_program_may_import_the_modules_of_the_standard_library() -> None:
    runtime = await _runtime()

    result = await runtime.run("import json; json.dumps({'a': 1})")

    assert result.ok
    assert result.value == '{"a": 1}'


@needs_landlock
async def test_a_run_that_is_cancelled_stops_at_once() -> None:
    abort = asyncio.Event()
    runtime = await _runtime(timeout_ms=30_000)

    async def cancel_soon() -> None:
        await asyncio.sleep(0.2)
        abort.set()

    canceller = asyncio.create_task(cancel_soon())
    result = await runtime.run("sum(range(100000000))", abort=abort)
    await canceller

    assert not result.ok
    assert result.cancelled


@needs_landlock
async def test_a_run_that_takes_too_long_is_stopped() -> None:
    runtime = await _runtime(timeout_ms=1_000)

    result = await runtime.run("sum(range(100000000))")

    assert not result.ok
    assert result.timed_out


@needs_landlock
async def test_a_program_that_raises_says_what_happened() -> None:
    runtime = await _runtime()

    result = await runtime.run("1 / 0")

    assert not result.ok
    assert "ZeroDivisionError" in result.error


def test_the_result_is_json_the_model_can_read() -> None:
    result = PtcRunResult(ok=True, value={"files": 3}, dispatches=2, duration_ms=12)

    payload = json.loads(result.to_json())

    assert payload["ok"] is True
    assert payload["value"] == {"files": 3}
    assert payload["dispatches"] == 2


@needs_landlock
async def test_the_program_may_not_reach_the_network() -> None:
    runtime = await _runtime()

    result = await runtime.run("import socket")

    assert not result.ok


# ---------------------------------------------------------------------------
# The policy of the operating system, on its own
# ---------------------------------------------------------------------------

# The policy is irreversible, so it is applied in a process of its own.
_POLICY_PROGRAM = """
import sys
sys.path.insert(0, {root!r})
from nerdvana_cli.core.safety.sandbox import confine_read_only
confine_read_only({allowed!r})
try:
    with open({target!r}) as handle:
        sys.stdout.write("READ:" + handle.read())
except OSError as exc:
    sys.stdout.write("DENIED:" + str(exc))
"""


def _under_policy(target: str, allowed: list[str]) -> str:
    """What a process that carries the read-only policy does with *target*."""
    import nerdvana_cli

    root = str(Path(nerdvana_cli.__file__).resolve().parent.parent)
    program = _POLICY_PROGRAM.format(root=root, allowed=allowed, target=target)
    finished = subprocess.run(
        [sys.executable, "-c", program],
        capture_output = True,
        text          = True,
        timeout       = 60,
    )
    return finished.stdout


@needs_landlock
def test_the_policy_refuses_a_read_outside_the_paths_it_names(tmp_path: Path) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("the key", encoding="utf-8")
    allowed = tmp_path / "allowed"
    allowed.mkdir()

    outcome = _under_policy(str(secret), [str(allowed)])

    assert outcome.startswith("DENIED:")
    assert "Permission denied" in outcome


@needs_landlock
def test_the_policy_refuses_a_write_even_below_the_paths_it_names(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    target = allowed / "out.txt"

    outcome = _under_policy(str(target), [str(allowed)])

    assert outcome.startswith("DENIED:")
    assert not target.exists()


# ---------------------------------------------------------------------------
# The tool in front of the runtime
# ---------------------------------------------------------------------------


class _Session:
    """A session that only keeps what it was told."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def record(self, event: str, data: dict[str, Any]) -> None:
        self.events.append((event, data))


def _tool(cwd: Path) -> Any:
    """The run_code tool, with the tools of a real registry around it."""
    from nerdvana_cli.core.config.settings import NerdvanaSettings
    from nerdvana_cli.tools.registry import create_tool_registry

    registry = create_tool_registry(settings=NerdvanaSettings())
    tool = registry.get("run_code")
    assert tool is not None
    return tool


@needs_landlock
async def test_the_tool_reads_the_project_through_its_bindings(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("one\ntwo\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("three\n", encoding="utf-8")
    from nerdvana_cli.core.tool import ToolContext
    from nerdvana_cli.tools.ptc_tool import RunCodeArgs

    tool = _tool(tmp_path)
    context = ToolContext(cwd=str(tmp_path))

    result = await tool.call(RunCodeArgs(code="[FileRead(path='a.py'), FileRead(path='b.py')]"), context, None)

    assert not result.is_error
    assert "one" in result.content
    assert "three" in result.content


@needs_landlock
async def test_the_tool_counts_the_lines_of_many_files_in_one_call(tmp_path: Path) -> None:
    for name in ("a.py", "b.py", "c.py"):
        (tmp_path / name).write_text("x\ny\n", encoding="utf-8")
    from nerdvana_cli.core.tool import ToolContext
    from nerdvana_cli.tools.ptc_tool import RunCodeArgs

    tool = _tool(tmp_path)
    context = ToolContext(cwd=str(tmp_path))

    result = await tool.call(
        RunCodeArgs(code="sum(len(FileRead(path=f).splitlines()) for f in Glob(pattern='*.py'))"),
        context,
        None,
    )

    assert not result.is_error
    assert '"value": 6' in result.content


@needs_landlock
async def test_the_tool_records_every_dispatch_in_the_session(tmp_path: Path) -> None:
    from nerdvana_cli.core.tool import ToolContext
    from nerdvana_cli.tools.ptc_tool import RunCodeArgs

    session = _Session()
    tool = _tool(tmp_path)
    tool.set_session(session)
    context = ToolContext(cwd=str(tmp_path))

    await tool.call(RunCodeArgs(code="FileRead(path='a.py')"), context, None)

    assert [event for event, _data in session.events] == [PTC_DISPATCH_EVENT]
    assert session.events[0][1]["tool"] == "FileRead"


@needs_landlock
async def test_the_tool_says_that_a_program_failed(tmp_path: Path) -> None:
    from nerdvana_cli.core.tool import ToolContext
    from nerdvana_cli.tools.ptc_tool import RunCodeArgs

    tool = _tool(tmp_path)
    context = ToolContext(cwd=str(tmp_path))

    result = await tool.call(RunCodeArgs(code="1 / 0"), context, None)

    assert result.is_error
    assert "ZeroDivisionError" in result.content


@needs_landlock
async def test_a_program_that_prints_a_lot_does_not_stall() -> None:
    runtime = await _runtime()

    result = await runtime.run("print('x' * 200000)\n'printed'")

    assert result.ok
    assert result.value == "printed"
    assert len(result.stdout) <= 8_100


# The description of the tool
# ---------------------------------------------------------------------------


def test_the_description_says_when_the_program_is_the_right_tool_and_when_it_is_not() -> None:
    """The tool is in every request, so what it says decides what the agent plans.

    A program that answers a question the agent already knows how to ask is worth
    one tool call; a program that sweeps a project is worth one turn per file it
    finds, because every turn sends the conversation again.
    """
    from nerdvana_cli.tools.ptc_tool import RunCodeTool

    description = " ".join(RunCodeTool.description_text.split())

    assert "already know which files to read" in description
    assert "Do not use it to explore" in description
    assert "return the answer, not the files" in description
    assert "needs no second read" in description
    assert "needs no second program" in description
