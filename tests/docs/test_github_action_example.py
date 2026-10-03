"""The example workflow keeps the guards its documentation promises.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

from pathlib import Path

import yaml

EXAMPLE = Path(__file__).parents[2] / "docs" / "examples" / "nerdvana-comment.yml"


def _load() -> dict:
    return yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))


def test_it_is_valid_yaml_and_triggers_only_on_new_comments() -> None:
    workflow = _load()
    assert workflow[True] == {"issue_comment": {"types": ["created"]}}   # YAML reads the key `on` as True


def test_the_job_is_limited_to_collaborators_and_the_trigger_phrase() -> None:
    condition = _load()["jobs"]["run"]["if"]
    assert "startsWith(github.event.comment.body, '/nerdvana ')" in condition
    for role in ("OWNER", "MEMBER", "COLLABORATOR"):
        assert role in condition


def test_the_token_cannot_push_and_the_checkout_keeps_no_credentials() -> None:
    workflow = _load()
    assert workflow["permissions"]["contents"] == "read"
    checkout = next(s for s in workflow["jobs"]["run"]["steps"] if str(s.get("uses", "")).startswith("actions/checkout"))
    assert checkout["with"]["persist-credentials"] is False


def test_the_run_is_bounded_and_confined() -> None:
    job = _load()["jobs"]["run"]
    run = next(s["run"] for s in job["steps"] if s.get("name") == "Run nerdvana")
    for flag in ("--max-cost-usd", "--max-turns", "--sandbox require", "--output-format json"):
        assert flag in run
    assert job["timeout-minutes"] <= 30


def test_the_workflow_never_runs_fork_code_with_a_write_token() -> None:
    text = EXAMPLE.read_text(encoding="utf-8")
    assert "pull_request_target" not in text and "secrets.GITHUB_TOKEN" not in text


def test_the_inline_script_that_builds_the_comment_is_valid_python() -> None:
    import ast
    import textwrap

    run  = next(s["run"] for s in _load()["jobs"]["run"]["steps"] if s.get("name") == "Run nerdvana")
    body = run.split("<<'EOF' > comment.md\n", 1)[1].split("\nEOF", 1)[0]
    ast.parse(textwrap.dedent(body))
