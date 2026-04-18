"""Tests for hook audit logging — Phase G2.

Verifies that HookBridge writes rows to the hooks table in audit.sqlite.

Coverage:
  - pre-tool-use call creates a hooks row (1)
  - post-tool-use call creates a hooks row (1)
  - duration_ms is recorded (1)

작성자: 최진호
작성일: 2026-04-18
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from nerdvana_cli.server.hook_bridge import HookBridge


def _query_hooks(db_path: Path) -> list[dict]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM hooks ORDER BY id").fetchall()
    conn.close()
    return [dict(r) for r in rows]


class TestHookAudit:
    @pytest.fixture
    def db_path(self, tmp_path: Path) -> Path:
        return tmp_path / "audit.sqlite"

    def test_pre_tool_use_creates_hooks_row(self, db_path: Path) -> None:
        bridge = HookBridge(db_path=db_path)
        bridge.dispatch({"hook_event_name": "PreToolUse", "tool_name": "Bash"})
        rows = _query_hooks(db_path)
        assert len(rows) >= 1
        row = rows[-1]
        assert row["hook_name"] == "pre-tool-use"
        assert row["tool_name"] == "Bash"
        assert row["permission_decision"] == "approve"

    def test_post_tool_use_creates_hooks_row(self, db_path: Path) -> None:
        bridge = HookBridge(db_path=db_path)
        bridge.dispatch({"hook_event_name": "PostToolUse", "tool_name": "Read", "tool_response": {}})
        rows = _query_hooks(db_path)
        assert any(r["hook_name"] == "post-tool-use" for r in rows)

    def test_duration_ms_recorded(self, db_path: Path) -> None:
        bridge = HookBridge(db_path=db_path)
        bridge.dispatch({"hook_event_name": "UserPromptSubmit", "prompt": "hello"})
        rows = _query_hooks(db_path)
        assert len(rows) >= 1
        # duration_ms should be a non-negative integer
        row = rows[-1]
        assert isinstance(row["duration_ms"], int)
        assert row["duration_ms"] >= 0
