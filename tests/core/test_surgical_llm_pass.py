"""Unit tests for nerdvana_cli.core.execution.surgical_llm_pass.

Covers diagnostic-to-hunk extraction with overlapping and adjacent ranges, filtering
by file, prompt contents, and bottom-up application of several replacements.

작성자: 최진호
날짜: 2026-10-05
"""

from __future__ import annotations

from nerdvana_cli.core.execution.surgical_llm_pass import (
    Diagnostic,
    SurgicalHunk,
    apply_surgical_hunks,
    extract_surgical_hunks,
    format_surgical_prompt,
)


def _numbered_source(line_count: int) -> str:
    """Lines ``line1``..``lineN`` joined by newlines."""
    return "\n".join(f"line{number}" for number in range(1, line_count + 1))


def test_adjacent_diagnostics_merge_into_single_hunk() -> None:
    source = _numbered_source(40)
    diagnostics = [
        Diagnostic(file_path="sample.py", line=10, message="first hit"),
        Diagnostic(file_path="sample.py", line=17, message="second hit"),
        Diagnostic(file_path="other.py", line=10, message="different file"),
    ]

    hunks = extract_surgical_hunks("sample.py", source, diagnostics, context_lines=3)

    assert len(hunks) == 1
    hunk = hunks[0]
    assert (hunk.start_line, hunk.end_line) == (7, 20)
    assert hunk.diagnostics == diagnostics[:2]
    expected_lines = [f"line{number}" for number in range(7, 21)]
    assert hunk.original_code == "\n".join(expected_lines)


def test_overlapping_diagnostics_merge_and_distant_stay_separate() -> None:
    source = _numbered_source(40)
    diagnostics = [
        Diagnostic(file_path="sample.py", line=10, message="near"),
        Diagnostic(file_path="sample.py", line=12, message="overlapping"),
        Diagnostic(file_path="sample.py", line=35, message="far away"),
    ]

    hunks = extract_surgical_hunks("sample.py", source, diagnostics, context_lines=3)

    assert len(hunks) == 2
    assert (hunks[0].start_line, hunks[0].end_line) == (7, 15)
    assert [d.message for d in hunks[0].diagnostics] == ["near", "overlapping"]
    assert (hunks[1].start_line, hunks[1].end_line) == (32, 38)


def test_format_surgical_prompt_carries_range_code_and_instruction() -> None:
    hunk = SurgicalHunk(
        file_path="sample.py",
        start_line=7,
        end_line=9,
        original_code="a = legacy()\nb = legacy()\nc = legacy()",
        diagnostics=[
            Diagnostic(file_path="sample.py", line=8, message="legacy() is gone", rule_id="migration/legacy"),
        ],
    )

    prompt = format_surgical_prompt(hunk, "Rewrite the calls to the new API.")

    assert "File: sample.py" in prompt
    assert "Lines: 7-9" in prompt
    assert "legacy() is gone" in prompt
    assert "migration/legacy" in prompt
    assert "a = legacy()\nb = legacy()\nc = legacy()" in prompt
    assert "Rewrite the calls to the new API." in prompt
    assert "No explanation, no markdown fences" in prompt


def test_apply_surgical_hunks_replaces_each_range_bottom_up() -> None:
    source = "\n".join(f"line{number}" for number in range(1, 31)) + "\n"
    first = SurgicalHunk("sample.py", 2, 4, "line2\nline3\nline4", [])
    second = SurgicalHunk("sample.py", 20, 22, "line20\nline21\nline22", [])

    result = apply_surgical_hunks(source, [(first, "A1\nA2"), (second, "B1\nB2\nB3")])

    lines = result.split("\n")
    expected = ["line1", "A1", "A2"]
    expected += [f"line{number}" for number in range(5, 20)]
    expected += ["B1", "B2", "B3"]
    expected += [f"line{number}" for number in range(23, 31)]
    expected += [""]
    assert lines == expected
    assert result.endswith("\n")
