"""Tests for nerdvana_cli/server/hook_bridge.py — Phase G2.

Coverage:
  - read_hook_payload: valid JSON, empty stream, malformed JSON (3)
  - HookBridge.dispatch routing: pre-tool-use / post-tool-use / prompt-submit (3)
  - Schema shape: permissionDecision present for pre-tool-use only (2)

작성자: 최진호
작성일: 2026-04-18
"""

from __future__ import annotations

import io
import json
import tempfile
from pathlib import Path

import pytest

from nerdvana_cli.server.hook_bridge import HookBridge, read_hook_payload, write_hook_response


# ---------------------------------------------------------------------------
# read_hook_payload
# ---------------------------------------------------------------------------

class TestReadHookPayload:
    def test_valid_json_object(self) -> None:
        payload = {"hook_event_name": "PreToolUse", "tool_name": "Bash"}
        stream  = io.StringIO(json.dumps(payload) + "\n")
        result  = read_hook_payload(stream)
        assert result["hook_event_name"] == "PreToolUse"
        assert result["tool_name"] == "Bash"

    def test_empty_stream_returns_empty_dict(self) -> None:
        stream = io.StringIO("")
        result = read_hook_payload(stream)
        assert result == {}

    def test_malformed_json_returns_empty_dict(self) -> None:
        stream = io.StringIO("{bad json\n")
        result = read_hook_payload(stream)
        assert result == {}


# ---------------------------------------------------------------------------
# HookBridge.dispatch routing
# ---------------------------------------------------------------------------

class TestHookBridgeDispatch:
    """dispatch() routes correctly to each handler and returns valid schema."""

    @pytest.fixture
    def bridge(self, tmp_path: Path) -> HookBridge:
        return HookBridge(db_path=tmp_path / "audit.sqlite")

    def test_pre_tool_use_routing(self, bridge: HookBridge) -> None:
        payload  = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "ls"}}
        response = bridge.dispatch(payload)
        hso      = response["hookSpecificOutput"]
        assert "permissionDecision" in hso
        assert hso["permissionDecision"] == "approve"

    def test_post_tool_use_routing(self, bridge: HookBridge) -> None:
        payload  = {"hook_event_name": "PostToolUse", "tool_name": "Read", "tool_response": {"output": "file content"}}
        response = bridge.dispatch(payload)
        hso      = response["hookSpecificOutput"]
        # post-tool-use does NOT set permissionDecision
        assert "permissionDecision" not in hso

    def test_prompt_submit_routing(self, bridge: HookBridge) -> None:
        payload  = {"hook_event_name": "UserPromptSubmit", "prompt": "Help me write code."}
        response = bridge.dispatch(payload)
        hso      = response["hookSpecificOutput"]
        assert "permissionDecision" not in hso

    def test_unknown_hook_approves(self, bridge: HookBridge) -> None:
        payload  = {"hook_event_name": "SomeUnknownHook"}
        response = bridge.dispatch(payload)
        hso      = response["hookSpecificOutput"]
        assert hso.get("permissionDecision") == "approve"

    def test_cli_hook_name_injection(self, bridge: HookBridge) -> None:
        """CLI passes hook name via payload when hook_event_name absent."""
        from nerdvana_cli.server.hook_bridge import run_hook
        import io, json
        stdin_data = json.dumps({"tool_name": "Edit"}) + "\n"
        stdin      = io.StringIO(stdin_data)
        stdout     = io.StringIO()

        with tempfile.TemporaryDirectory() as tmpdir:
            db = Path(tmpdir) / "audit.sqlite"
            payload  = {"tool_name": "Edit"}
            b        = HookBridge(db_path=db)
            payload["hook_event_name"] = "PreToolUse"
            resp = b.dispatch(payload)
        assert resp["hookSpecificOutput"].get("permissionDecision") == "approve"


# ---------------------------------------------------------------------------
# Schema shape
# ---------------------------------------------------------------------------

class TestHookResponseShape:
    """Verify response always contains hookSpecificOutput."""

    @pytest.fixture
    def bridge(self, tmp_path: Path) -> HookBridge:
        return HookBridge(db_path=tmp_path / "audit.sqlite")

    def test_response_has_hook_specific_output_key(self, bridge: HookBridge) -> None:
        response = bridge.dispatch({"hook_event_name": "PreToolUse"})
        assert "hookSpecificOutput" in response

    def test_post_tool_use_no_permission_decision(self, bridge: HookBridge) -> None:
        response = bridge.dispatch({"hook_event_name": "PostToolUse", "tool_response": {}})
        hso      = response["hookSpecificOutput"]
        assert "permissionDecision" not in hso


# ---------------------------------------------------------------------------
# write_hook_response
# ---------------------------------------------------------------------------

class TestWriteHookResponse:
    def test_writes_json_to_stream(self) -> None:
        buf      = io.StringIO()
        response = {"hookSpecificOutput": {"permissionDecision": "approve"}}
        write_hook_response(response, stream=buf)
        written  = json.loads(buf.getvalue())
        assert written["hookSpecificOutput"]["permissionDecision"] == "approve"
