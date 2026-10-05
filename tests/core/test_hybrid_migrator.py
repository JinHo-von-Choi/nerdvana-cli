"""Unit tests for nerdvana_cli.core.execution.hybrid_migrator.

Covers the zero-cost path where the deterministic pass clears every diagnostic, the
two-pass path where the LLM repairs what remains, and the failed path where the model
returns code the diagnostics still reject.

작성자: 최진호
날짜: 2026-10-05
"""

from __future__ import annotations

from typing import Any

from nerdvana_cli.core.execution.hybrid_migrator import HybridMigrator
from nerdvana_cli.core.execution.surgical_llm_pass import Diagnostic

FILE_PATH = "sample.py"

SOURCE = "\n".join(
    [
        "import api",
        "result = api.old_call()",
        "setup(legacy=True)",
        "return result",
    ]
)

ZERO_COST_SOURCE = "\n".join(
    [
        "import api",
        "result = api.old_call()",
        "return result",
    ]
)


def _deterministic(source: str, rules: list[Any]) -> tuple[str, int]:
    """Rename the call the rules target, counting what it changed."""
    count = 0
    for rule in rules:
        if rule["old"] in source:
            count += source.count(rule["old"])
            source = source.replace(rule["old"], rule["new"])
    return source, count


def _diagnose(file_path: str, source: str) -> list[Diagnostic]:
    """Report every line that still uses a removed or deprecated spelling."""
    found: list[Diagnostic] = []
    for number, line in enumerate(source.split("\n"), start=1):
        if "old_call" in line:
            found.append(Diagnostic(file_path, number, message="old_call() was removed"))
        if "legacy=True" in line:
            found.append(Diagnostic(file_path, number, message="legacy keyword is gone"))
    return found


def _original_block(prompt: str) -> str:
    """The original code a surgical prompt carries between its markers."""
    start = prompt.index("<<<ORIGINAL\n") + len("<<<ORIGINAL\n")
    end = prompt.index("\n>>>", start)
    return prompt[start:end]


def test_zero_cost_pass_never_calls_the_model() -> None:
    calls: list[str] = []

    def llm_caller(prompt: str) -> str:
        calls.append(prompt)
        raise AssertionError("the model must not run when diagnostics are empty")

    migrator = HybridMigrator(
        deterministic_transformer=_deterministic,
        diagnostic_runner=_diagnose,
        llm_caller=llm_caller,
    )

    result = migrator.migrate(FILE_PATH, ZERO_COST_SOURCE, [{"old": "old_call", "new": "new_call"}])

    assert result.success is True
    assert result.deterministic_count == 1
    assert result.llm_hunks_applied == 0
    assert result.remaining_diagnostics == []
    assert calls == []
    assert "api.new_call()" in result.modified_source


def test_remaining_diagnostics_are_repaired_by_the_llm_pass() -> None:
    prompts: list[str] = []

    def llm_caller(prompt: str) -> str:
        prompts.append(prompt)
        return _original_block(prompt).replace("legacy=True", "modern=True")

    migrator = HybridMigrator(
        deterministic_transformer=_deterministic,
        diagnostic_runner=_diagnose,
        llm_caller=llm_caller,
    )

    result = migrator.migrate(
        FILE_PATH,
        SOURCE,
        [{"old": "old_call", "new": "new_call"}],
        instruction="Move the remaining call to the supported keyword.",
    )

    assert result.success is True
    assert result.deterministic_count == 1
    assert result.llm_hunks_applied == 1
    assert result.remaining_diagnostics == []
    assert len(prompts) == 1
    assert f"File: {FILE_PATH}" in prompts[0]
    assert "legacy keyword is gone" in prompts[0]
    assert "Move the remaining call to the supported keyword." in prompts[0]
    assert "api.new_call()" in result.modified_source
    assert "setup(modern=True)" in result.modified_source


def test_llm_output_that_leaves_diagnostics_reports_failure() -> None:
    migrator = HybridMigrator(
        deterministic_transformer=_deterministic,
        diagnostic_runner=_diagnose,
        llm_caller=lambda prompt: _original_block(prompt),
    )

    result = migrator.migrate(FILE_PATH, SOURCE, [{"old": "old_call", "new": "new_call"}])

    assert result.success is False
    assert result.deterministic_count == 1
    assert result.llm_hunks_applied == 1
    assert len(result.remaining_diagnostics) == 1
    assert result.remaining_diagnostics[0].message == "legacy keyword is gone"
    assert "legacy=True" in result.modified_source
