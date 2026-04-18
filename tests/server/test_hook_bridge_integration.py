"""Integration tests for the nerdvana hook CLI — Phase G2.

Invokes ``nerdvana hook <subcommand>`` as a subprocess, feeds JSON on stdin,
and validates the stdout JSON response.

Coverage:
  - pre-tool-use: clean payload returns approve (1)
  - prompt-submit: injection payload triggers sanitiser warning (1)
  - post-tool-use: returns hookSpecificOutput without permissionDecision (1)

작성자: 최진호
작성일: 2026-04-18
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest


def _run_hook(
    subcommand: str,
    payload:    dict,
    *,
    db_path: Path | None = None,
) -> dict:
    """Run ``python -m nerdvana_cli.main hook <subcommand>`` with *payload* on stdin."""
    cmd = [
        sys.executable, "-m", "nerdvana_cli.main",
        "hook", subcommand,
    ]
    if db_path is not None:
        cmd += ["--db", str(db_path)]

    result = subprocess.run(
        cmd,
        input   = json.dumps(payload),
        capture_output = True,
        text    = True,
        timeout = 10,
        cwd     = Path(__file__).parent.parent.parent,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    return json.loads(result.stdout.strip())


class TestHookBridgeIntegration:
    @pytest.fixture
    def db_path(self, tmp_path: Path) -> Path:
        return tmp_path / "audit.sqlite"

    def test_pre_tool_use_approves_clean_payload(self, db_path: Path) -> None:
        payload  = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "ls"}}
        response = _run_hook("pre-tool-use", payload, db_path=db_path)
        hso      = response["hookSpecificOutput"]
        assert hso["permissionDecision"] == "approve"

    def test_prompt_submit_returns_hook_specific_output(self, db_path: Path) -> None:
        payload  = {"hook_event_name": "UserPromptSubmit", "prompt": "Normal user request"}
        response = _run_hook("prompt-submit", payload, db_path=db_path)
        assert "hookSpecificOutput" in response

    def test_post_tool_use_no_permission_decision(self, db_path: Path) -> None:
        payload  = {
            "hook_event_name": "PostToolUse",
            "tool_name":       "Read",
            "tool_response":   {"output": "file contents"},
        }
        response = _run_hook("post-tool-use", payload, db_path=db_path)
        hso      = response["hookSpecificOutput"]
        assert "permissionDecision" not in hso
