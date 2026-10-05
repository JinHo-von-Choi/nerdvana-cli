"""Two-pass file migration: a free deterministic transform, then surgical LLM repair.

작성자: 최진호
날짜: 2026-10-05

Pass one runs the injected deterministic transformer (an AST/LAST rewrite supplied by
the caller, so this module knows nothing about how rewrites are found) and then asks the
injected diagnostic runner what still fails. When the diagnostics come back empty the
file is done: the result reports success with zero LLM hunks, and no model is called.

Pass two starts only when diagnostics remain. The residual lines become surgical hunks,
each is rendered into a bare prompt and sent to the injected LLM caller, and the answers
are written back. The diagnostic runner then judges the file again; success is recorded
only when nothing is left to report.

Both collaborators are plain callables: the deterministic pass, the diagnostic source
and the model are decided by whoever wires this class, which keeps the execution layer
free of any code-intelligence dependency.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from nerdvana_cli.core.execution.surgical_llm_pass import (
    Diagnostic,
    SurgicalHunk,
    apply_surgical_hunks,
    extract_surgical_hunks,
    format_surgical_prompt,
)

# (source, rules) -> (rewritten source, number of rewrites applied)
DeterministicTransformer = Callable[[str, list[Any]], tuple[str, int]]
# (file path, source) -> diagnostics still reported for that source
DiagnosticRunner = Callable[[str, str], list[Diagnostic]]
# (prompt) -> replacement code for the hunk the prompt describes
LLMCaller = Callable[[str], str]


def _no_diagnostics(file_path: str, source: str) -> list[Diagnostic]:
    """Stand-in runner for a migrator built without a diagnostic source: nothing reported."""
    return []


@dataclass
class MigrationResult:
    """What one file ended up as, and how much of it each pass contributed."""

    file_path: str
    success: bool
    modified_source: str
    deterministic_count: int
    llm_hunks_applied: int
    remaining_diagnostics: list[Diagnostic]


class HybridMigrator:
    """Run pass one, and pass two only where pass one left diagnostics behind."""

    def __init__(
        self,
        deterministic_transformer: DeterministicTransformer | None = None,
        diagnostic_runner: DiagnosticRunner | None = None,
        llm_caller: LLMCaller | None = None,
        context_lines: int = 3,
    ) -> None:
        self.deterministic_transformer = deterministic_transformer
        self.diagnostic_runner = diagnostic_runner
        self.llm_caller = llm_caller
        self.context_lines = context_lines

    def migrate(
        self,
        file_path: str,
        source: str,
        rules: list[Any],
        instruction: str = "",
    ) -> MigrationResult:
        """Migrate *source* of *file_path*: deterministic pass first, LLM pass second.

        The deterministic pass is skipped when no transformer was supplied, and an empty
        diagnostic list after it ends the migration successfully without touching the
        model. Remaining diagnostics with no LLM caller produce a failed result carrying
        them, so the caller can see what was never attempted.
        """
        current = source
        deterministic_count = 0
        if self.deterministic_transformer is not None:
            current, deterministic_count = self.deterministic_transformer(current, rules)

        runner = self.diagnostic_runner or _no_diagnostics
        residual = runner(file_path, current)
        if not residual:
            return MigrationResult(
                file_path=file_path,
                success=True,
                modified_source=current,
                deterministic_count=deterministic_count,
                llm_hunks_applied=0,
                remaining_diagnostics=[],
            )
        llm_caller = self.llm_caller
        if llm_caller is None:
            return MigrationResult(
                file_path=file_path,
                success=False,
                modified_source=current,
                deterministic_count=deterministic_count,
                llm_hunks_applied=0,
                remaining_diagnostics=residual,
            )

        hunks = extract_surgical_hunks(file_path, current, residual, self.context_lines)
        replacements: list[tuple[SurgicalHunk, str]] = []
        for hunk in hunks:
            prompt = format_surgical_prompt(hunk, instruction)
            replacements.append((hunk, llm_caller(prompt)))
        if replacements:
            current = apply_surgical_hunks(current, replacements)

        remaining = runner(file_path, current)
        return MigrationResult(
            file_path=file_path,
            success=not remaining,
            modified_source=current,
            deterministic_count=deterministic_count,
            llm_hunks_applied=len(replacements),
            remaining_diagnostics=remaining,
        )
