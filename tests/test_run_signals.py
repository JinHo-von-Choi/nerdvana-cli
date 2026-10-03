"""What goes wrong in a run is counted by kind: classification, the executor, the loop and the result.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

from collections import Counter
from typing import Any

import pytest

from nerdvana_cli.cli.run_output import RunResult
from nerdvana_cli.core import signals
from nerdvana_cli.core.hooks import HookEngine
from nerdvana_cli.core.settings import NerdvanaSettings
from nerdvana_cli.core.signals import classify_result
from nerdvana_cli.core.tool import BaseTool, ToolContext, ToolRegistry
from nerdvana_cli.core.tool_executor import ToolExecutor
from nerdvana_cli.types import ToolResult

# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("content", "expected"), [
    ("Refused: Bash was called 5 times in a row with identical arguments.", signals.REPEAT_REFUSED),
    ("Invalid tool input: missing 'path'", signals.INVALID_INPUT),
    ("Permission denied by user: edit", signals.PERMISSION_USER),
    ("Permission denied: blocked by policy", signals.PERMISSION_POLICY),
    ("Blocked by hook: no", signals.HOOK_BLOCKED),
    ("Validation error: bad path", signals.VALIDATION_ERROR),
    ("Tool execution error: boom", signals.TOOL_EXCEPTION),
    ("a.py changed since it was last read. Call FileRead again before editing.", signals.CAS_REJECTED),
    ("a.py has not been read in this session. Call FileRead on it before editing.", signals.CAS_REJECTED),
    ("something else went wrong", signals.TOOL_ERROR),
])
def test_error_results_carry_exactly_one_signal(content: str, expected: str) -> None:
    assert classify_result(content, True) == [expected]


def test_successful_results_carry_only_the_notes_they_contain() -> None:
    assert classify_result("fine", False) == []
    assert classify_result("ok\n\n[Note: this exact call has now been made 3 times in a row.]", False) == [signals.REPEAT_WARNED]
    edit = "Replaced line 4\n\nNew errors reported by the language server after this edit:\n- line 4: x"
    assert classify_result(edit, False) == [signals.NEW_DIAGNOSTICS]


def test_permission_denied_in_a_confined_shell_is_a_sandbox_signal() -> None:
    assert classify_result("cat: x: Permission denied", False, shell_confined=True) == [signals.SANDBOX_DENIED]
    assert classify_result("[exit code: 1]\nPermission denied", True, shell_confined=True) == [signals.SANDBOX_DENIED]
    assert classify_result("cat: x: Permission denied", False, shell_confined=False) == []


def test_merge_adds_counters_and_drops_zero_entries() -> None:
    assert signals.merge(Counter({"b": 2, "a": 1}), Counter({"b": 1, "z": 0})) == {"a": 1, "b": 3}


# ---------------------------------------------------------------------------
# The executor counts what it refuses
# ---------------------------------------------------------------------------


class _Fails(BaseTool[Any]):
    name             = "Fails"
    description_text = "always errors"
    input_schema: dict[str, Any] = {"type": "object", "properties": {"n": {"type": "integer"}}}

    async def call(self, args: Any, context: Any, can_use_tool: Any = None, on_progress: Any = None) -> ToolResult:
        return ToolResult(tool_use_id="", content="a.py changed since it was last read. Call FileRead again before editing.", is_error=True)


async def test_the_executor_counts_each_kind_of_result() -> None:
    registry = ToolRegistry()
    registry.register(_Fails())
    executor = ToolExecutor(registry=registry, hooks=HookEngine(), settings=NerdvanaSettings())
    context  = ToolContext(cwd=".")
    await executor.run_batch([{"id": "1", "name": "Fails", "input": {"n": 1}}], context)
    await executor.run_batch([{"id": "2", "name": "Fails", "input": {"n": "not a number"}}], context)
    await executor.run_batch([{"id": "3", "name": "Nope", "input": {}}], context)
    assert executor.signals[signals.CAS_REJECTED] == 1
    assert executor.signals[signals.INVALID_INPUT] == 1


# ---------------------------------------------------------------------------
# The result object
# ---------------------------------------------------------------------------


def test_the_run_result_carries_the_signals() -> None:
    result = RunResult(signals={"cas_rejected": 2})
    assert result.to_dict()["signals"] == {"cas_rejected": 2}
    assert RunResult().to_dict()["signals"] == {}


def test_the_phrases_the_classifier_looks_for_still_exist_in_the_code_that_writes_them() -> None:
    import inspect

    from nerdvana_cli.core import edit_guard, tool_executor, tool_permission
    from nerdvana_cli.tools import file_tools

    written = "".join(inspect.getsource(module) for module in (tool_executor, tool_permission, edit_guard, file_tools))
    for prefix, _signal in signals._ERROR_PREFIXES:
        assert prefix.rstrip(": ") in written, prefix
    for phrase in (*signals._STALE_PHRASES, "[Note: this exact call has now been made", "New errors reported by the language server after this edit"):
        assert phrase in written, phrase
