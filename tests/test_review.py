"""nerdvana review: reading a diff, finding the changed symbols and their users, and the command around them.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from nerdvana_cli.commands.review_command import parse_findings, render_findings, should_fail
from nerdvana_cli.core.review_context import (
    ChangedSymbol,
    ReviewError,
    build_context,
    changed_symbols,
    find_references,
    parse_changes,
    render_prompt,
)
from nerdvana_cli.main import app

LIB = (
    "def compute(x):\n"           # 1
    "    return x + 1\n"          # 2
    "\n"                          # 3
    "class Box:\n"                # 4
    "    def size(self):\n"       # 5
    "        return 3\n"          # 6
    "    def other(self):\n"      # 7
    "        return 4\n"          # 8
)
USER = "from lib import compute\n\nprint(compute(2))\n"


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t", *args], check=True, capture_output=True)


@pytest.fixture()
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    _git(tmp_path, "init", "-q")
    (tmp_path / "lib.py").write_text(LIB)
    (tmp_path / "use.py").write_text(USER)
    (tmp_path / "test_lib.py").write_text("from lib import compute\n\ndef test_it():\n    assert compute(1) == 2\n")
    (tmp_path / "notes.txt").write_text("one\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "base")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _change(repo: Path) -> None:
    (repo / "lib.py").write_text(LIB.replace("return x + 1", "return x + 2").replace("return 3", "return 30"))
    (repo / "notes.txt").write_text("one\ntwo\n")


# ---------------------------------------------------------------------------
# Reading the change
# ---------------------------------------------------------------------------


def test_the_changed_lines_of_each_file_are_read_from_the_diff(repo: Path) -> None:
    _change(repo)
    changes = {c.path: c.lines for c in parse_changes(str(repo), "HEAD")}
    assert changes["lib.py"] == {2, 6} and changes["notes.txt"] == {2}


def test_a_deleted_file_and_a_clean_tree_give_no_changes(repo: Path) -> None:
    assert parse_changes(str(repo), "HEAD") == []
    (repo / "use.py").unlink()
    assert [c.path for c in parse_changes(str(repo), "HEAD")] == []


def test_an_unknown_base_is_an_error_with_gits_message(repo: Path) -> None:
    with pytest.raises(ReviewError):
        parse_changes(str(repo), "no-such-ref")


def test_the_innermost_function_or_class_around_a_changed_line_is_named() -> None:
    found = {s.qual: s for s in changed_symbols("lib.py", LIB, {2, 6})}
    assert set(found) == {"compute", "Box.size"}
    assert (found["Box.size"].name, found["Box.size"].start, found["Box.size"].end, found["Box.size"].kind) == ("size", 5, 6, "function")


def test_a_change_in_a_class_body_outside_its_methods_names_the_class() -> None:
    assert [s.qual for s in changed_symbols("lib.py", "class A:\n    x = 1\n    def f(self):\n        pass\n", {2})] == ["A"]


def test_other_languages_and_broken_python_give_no_symbols() -> None:
    assert changed_symbols("lib.js", "function f() {}", {1}) == []
    assert changed_symbols("bad.py", "def (:", {1}) == []


def test_references_exclude_the_definition_itself_and_put_tests_last(repo: Path) -> None:
    symbol = ChangedSymbol("lib.py", "compute", "compute", "function", 1, 2)
    refs   = find_references(str(repo), symbol)
    assert [(r.path, r.line) for r in refs] == [("use.py", 1), ("use.py", 3), ("test_lib.py", 1), ("test_lib.py", 4)]
    assert not any(r.path == "lib.py" and 1 <= r.line <= 2 for r in refs)
    assert find_references(str(repo), ChangedSymbol("lib.py", "ab", "ab", "function", 1, 2)) == []


def test_the_context_has_the_diff_the_symbols_and_their_users(repo: Path) -> None:
    _change(repo)
    diff, symbols, references = build_context(str(repo), "HEAD")
    assert "return x + 2" in diff and {s.qual for s in symbols} == {"compute", "Box.size"}
    prompt = render_prompt("HEAD", diff, symbols, references)
    assert "### lib.py::compute (function, lines 1-2)" in prompt and "- use.py:3: print(compute(2))" in prompt
    assert "### lib.py::Box.size" in prompt and "no other file mentions it" in prompt
    assert prompt.rstrip().endswith("Use an empty list when you find nothing.")


def test_a_long_diff_is_cut_with_a_note() -> None:
    assert "[diff cut here" in render_prompt("HEAD", "x" * 500, [], {}, max_diff_chars=100)


# ---------------------------------------------------------------------------
# The reviewer's answer
# ---------------------------------------------------------------------------

ANSWER = 'Looked at it.\n{"findings": [{"file": "use.py", "line": 3, "severity": "high", "problem": "caller assumes +1", "evidence": "compute(2)"}, {"file": "a", "line": 1, "severity": "low", "problem": "style"}]}'


def test_findings_are_read_from_the_last_json_object_of_the_answer() -> None:
    assert [f["file"] for f in parse_findings(ANSWER)] == ["use.py", "a"]
    assert parse_findings("no json here") == [] and parse_findings('{"other": 1}') == []
    assert parse_findings('{"findings": [{"file": "x"}]} trailing {"findings": []}') == []


def test_fail_on_compares_severities() -> None:
    findings = parse_findings(ANSWER)
    assert should_fail(findings, "high") and should_fail(findings, "low") and not should_fail(findings, "never")
    assert not should_fail([{"severity": "low"}], "medium") and not should_fail([], "low")


def test_findings_print_most_severe_first() -> None:
    text = render_findings(parse_findings(ANSWER))
    assert text.index("[high]") < text.index("[low]") and "compute(2)" in text
    assert render_findings([]) == "No findings."


# ---------------------------------------------------------------------------
# The command
# ---------------------------------------------------------------------------

runner = CliRunner()


def test_context_only_prints_what_the_reviewer_would_get_and_calls_no_model(repo: Path) -> None:
    _change(repo)
    result = runner.invoke(app, ["review", "--context-only"])
    assert result.exit_code == 0 and "### lib.py::compute" in result.stdout


def test_a_clean_tree_is_not_reviewed(repo: Path) -> None:
    assert runner.invoke(app, ["review"]).exit_code == 0


def test_the_command_runs_the_reviewer_and_fails_on_severity(repo: Path) -> None:
    async def fake(prompt: str, model: str, provider: str) -> str:
        assert "compute" in prompt
        return ANSWER

    _change(repo)
    with patch("nerdvana_cli.commands.review_command._run_reviewer", new=fake):
        text = runner.invoke(app, ["review"])
        assert text.exit_code == 0 and "[high] use.py:3" in text.stdout
        failed = runner.invoke(app, ["review", "--fail-on", "high", "--output-format", "json"])
    assert failed.exit_code == 1
    assert json.loads(failed.stdout)["failed"] is True


def test_bad_options_and_an_unknown_base_exit_with_two(repo: Path) -> None:
    assert runner.invoke(app, ["review", "--fail-on", "sometimes"]).exit_code == 2
    assert runner.invoke(app, ["review", "--base", "no-such-ref", "--context-only"]).exit_code == 2
