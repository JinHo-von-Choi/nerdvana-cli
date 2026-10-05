"""Diagnostic-driven surgical LLM patching of the smallest possible source ranges.

작성자: 최진호
날짜: 2026-10-05

Diagnostics arrive line by line, but an edit is made against a range of lines, so
:func:`extract_surgical_hunks` widens each diagnostic line by a fixed number of context
lines and merges ranges that overlap or touch, producing one hunk per contiguous region
of a file. Each hunk carries the exact source text it covers and the diagnostics that
put it there.

:func:`format_surgical_prompt` renders a hunk into a bare request: target file, line
range, diagnostic messages, original code, and the instruction to apply. Nothing else
is sent, so the model answers with replacement code and no surrounding prose.

:func:`apply_surgical_hunks` writes the answers back a hunk at a time, from the last
line of the file towards the first, so applying several replacements in one pass never
shifts a range that has not been written yet.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Diagnostic:
    """One reported problem: where it is, what it says, and how bad it is."""

    file_path: str
    line: int
    column: int = 0
    message: str = ""
    rule_id: str = ""
    severity: str = "error"


@dataclass
class SurgicalHunk:
    """A contiguous range of one file that may be replaced as a single block."""

    file_path: str
    start_line: int
    end_line: int
    original_code: str
    diagnostics: list[Diagnostic]


def extract_surgical_hunks(
    file_path: str,
    source: str,
    diagnostics: list[Diagnostic],
    context_lines: int = 3,
) -> list[SurgicalHunk]:
    """Hunks for *file_path* only: one per merged run of diagnostic lines.

    Every diagnostic of *file_path* widens to ``[line - context_lines, line +
    context_lines]`` clipped to the file, ranges that overlap or touch (``next_start
    <= end + 1``) become one hunk carrying all of their diagnostics, and diagnostics of
    other files are ignored. Hunk order follows the file from top to bottom.
    """
    lines = source.split("\n")
    total_lines = len(lines)
    own = sorted(
        (d for d in diagnostics if d.file_path == file_path),
        key=lambda d: (d.line, d.column),
    )
    merged: list[tuple[int, int, list[Diagnostic]]] = []
    for diag in own:
        start = max(1, diag.line - context_lines)
        end = min(total_lines, diag.line + context_lines)
        if start > end:
            continue
        if merged and start <= merged[-1][1] + 1:
            prev_start, prev_end, prev_diagnostics = merged[-1]
            merged[-1] = (prev_start, max(prev_end, end), [*prev_diagnostics, diag])
        else:
            merged.append((start, end, [diag]))
    return [
        SurgicalHunk(
            file_path=file_path,
            start_line=start,
            end_line=end,
            original_code="\n".join(lines[start - 1 : end]),
            diagnostics=hunk_diagnostics,
        )
        for start, end, hunk_diagnostics in merged
    ]


def format_surgical_prompt(hunk: SurgicalHunk, instruction: str) -> str:
    """The whole request for *hunk*: file, range, diagnostics, code, instruction."""
    diagnostics = "\n".join(
        _format_diagnostic(diag) for diag in hunk.diagnostics
    )
    return (
        f"File: {hunk.file_path}\n"
        f"Lines: {hunk.start_line}-{hunk.end_line}\n"
        "\n"
        f"Diagnostics:\n{diagnostics}\n"
        "\n"
        "Original code:\n"
        "<<<ORIGINAL\n"
        f"{hunk.original_code}\n"
        ">>>\n"
        "\n"
        f"Instruction: {instruction}\n"
        "\n"
        f"Return only the replacement code for lines {hunk.start_line}-{hunk.end_line} "
        "of the file. No explanation, no markdown fences, no text around it.\n"
    )


def _format_diagnostic(diagnostic: Diagnostic) -> str:
    """One diagnostic line: position, severity, rule and message."""
    parts = [f"- line {diagnostic.line}"]
    if diagnostic.column:
        parts[0] += f":{diagnostic.column}"
    parts.append(f"[{diagnostic.severity}]")
    if diagnostic.rule_id:
        parts.append(f"({diagnostic.rule_id})")
    if diagnostic.message:
        parts.append(diagnostic.message)
    return " ".join(parts).rstrip()


def apply_surgical_hunks(source: str, replacements: list[tuple[SurgicalHunk, str]]) -> str:
    """*source* with every hunk replaced by its new code, applied bottom-up.

    Ranges are written from the highest ``start_line`` downwards, so an earlier write
    cannot move the lines a later write still points at. A hunk whose range lies outside
    the file is skipped rather than raising.
    """
    lines = source.split("\n")
    ordered = sorted(replacements, key=lambda pair: pair[0].start_line, reverse=True)
    for hunk, new_code in ordered:
        start = max(1, hunk.start_line)
        end = min(len(lines), hunk.end_line)
        if start > end:
            continue
        lines[start - 1 : end] = new_code.split("\n")
    return "\n".join(lines)
