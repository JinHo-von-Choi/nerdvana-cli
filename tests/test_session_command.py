"""Tests for nerdvana_cli.commands.session_command.

Author: 최진호
Date:   2026-04-29
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_session(sessions_dir: Path, sid: str, messages: list[dict]) -> Path:
    """Write a fake *.jsonl session file."""
    sessions_dir.mkdir(parents=True, exist_ok=True)
    path = sessions_dir / f"{sid}.jsonl"
    lines = [json.dumps(m) for m in messages]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Unit tests — _parse_duration
# ---------------------------------------------------------------------------

class TestParseDuration:
    def test_days(self) -> None:
        from nerdvana_cli.commands.session_command import _parse_duration
        assert _parse_duration("7d") == 7 * 86400

    def test_hours(self) -> None:
        from nerdvana_cli.commands.session_command import _parse_duration
        assert _parse_duration("24h") == 86400

    def test_all_returns_none(self) -> None:
        from nerdvana_cli.commands.session_command import _parse_duration
        assert _parse_duration("all") is None

    def test_invalid_raises(self) -> None:
        import typer

        from nerdvana_cli.commands.session_command import _parse_duration
        with pytest.raises(typer.BadParameter):
            _parse_duration("2w")


# ---------------------------------------------------------------------------
# Unit tests — _first_message / _message_count
# ---------------------------------------------------------------------------

class TestFirstMessage:
    def test_returns_first_user_text(self, tmp_path: Path) -> None:
        from nerdvana_cli.commands.session_command import _first_message
        path = _make_session(tmp_path, "s1", [
            {"role": "assistant", "content": "hello"},
            {"role": "user", "content": "world request"},
        ])
        assert _first_message(path) == "world request"

    def test_missing_file_returns_no_preview(self, tmp_path: Path) -> None:
        from nerdvana_cli.commands.session_command import _first_message
        assert _first_message(tmp_path / "ghost.jsonl") == "(no preview)"

    def test_block_content(self, tmp_path: Path) -> None:
        from nerdvana_cli.commands.session_command import _first_message
        path = _make_session(tmp_path, "s2", [
            {"role": "user", "content": [{"type": "text", "text": "block message"}]},
        ])
        assert _first_message(path) == "block message"


class TestMessageCount:
    def test_counts_lines(self, tmp_path: Path) -> None:
        from nerdvana_cli.commands.session_command import _message_count
        path = _make_session(tmp_path, "s3", [
            {"role": "user", "content": "a"},
            {"role": "assistant", "content": "b"},
            {"role": "user", "content": "c"},
        ])
        assert _message_count(path) == 3

    def test_missing_file_returns_zero(self, tmp_path: Path) -> None:
        from nerdvana_cli.commands.session_command import _message_count
        assert _message_count(tmp_path / "nope.jsonl") == 0


# ---------------------------------------------------------------------------
# CLI integration — session list
# ---------------------------------------------------------------------------

class TestSessionList:
    def _run(self, args: list[str], data_home: str) -> object:
        from nerdvana_cli.main import app
        runner = CliRunner()
        return runner.invoke(app, args, env={"NERDVANA_DATA_HOME": data_home})

    def test_empty_sessions_dir(self, tmp_path: Path) -> None:
        result = self._run(["session", "list"], str(tmp_path))
        assert result.exit_code == 0
        assert "No sessions" in result.output

    def test_lists_session_files(self, tmp_path: Path) -> None:
        _make_session(tmp_path / "sessions", "abc123", [
            {"role": "user", "content": "test prompt"},
        ])
        result = self._run(["session", "list"], str(tmp_path))
        assert result.exit_code == 0
        assert "abc123" in result.output

    def test_json_output(self, tmp_path: Path) -> None:
        _make_session(tmp_path / "sessions", "xyz789", [
            {"role": "user", "content": "json prompt"},
        ])
        result = self._run(["session", "list", "--json"], str(tmp_path))
        assert result.exit_code == 0
        records = json.loads(result.output)
        assert isinstance(records, list)
        assert any(r["id"] == "xyz789" for r in records)

    def test_limit_respected(self, tmp_path: Path) -> None:
        sessions_dir = tmp_path / "sessions"
        for i in range(5):
            _make_session(sessions_dir, f"session-{i:02d}", [{"role": "user", "content": str(i)}])
            time.sleep(0.01)
        result = self._run(["session", "list", "--limit", "2"], str(tmp_path))
        assert result.exit_code == 0
        # Count lines containing 'session-'
        lines = [ln for ln in result.output.splitlines() if "session-" in ln]
        assert len(lines) <= 2


# ---------------------------------------------------------------------------
# CLI integration — session purge
# ---------------------------------------------------------------------------

class TestSessionPurge:
    def _run(self, args: list[str], data_home: str) -> object:
        from nerdvana_cli.main import app
        runner = CliRunner()
        return runner.invoke(app, args, env={"NERDVANA_DATA_HOME": data_home})

    def test_dry_run_does_not_delete(self, tmp_path: Path) -> None:
        path = _make_session(tmp_path / "sessions", "old-session", [{"role": "user", "content": "x"}])
        # Make file appear old
        old_ts = time.time() - (60 * 86400)
        import os
        os.utime(path, (old_ts, old_ts))

        result = self._run(["session", "purge", "--older-than", "30d", "--dry-run"], str(tmp_path))
        assert result.exit_code == 0
        assert path.exists(), "dry-run must not delete files"
        assert "Dry run" in result.output

    def test_purge_old_deletes(self, tmp_path: Path) -> None:
        import os
        path = _make_session(tmp_path / "sessions", "to-delete", [{"role": "user", "content": "y"}])
        old_ts = time.time() - (60 * 86400)
        os.utime(path, (old_ts, old_ts))

        result = self._run(["session", "purge", "--older-than", "30d"], str(tmp_path))
        assert result.exit_code == 0
        assert not path.exists()

    def test_purge_all(self, tmp_path: Path) -> None:
        sessions_dir = tmp_path / "sessions"
        for i in range(3):
            _make_session(sessions_dir, f"s{i}", [{"role": "user", "content": str(i)}])

        result = self._run(["session", "purge", "--older-than", "all"], str(tmp_path))
        assert result.exit_code == 0
        assert list(sessions_dir.glob("*.jsonl")) == []

    def test_no_sessions_no_crash(self, tmp_path: Path) -> None:
        result = self._run(["session", "purge"], str(tmp_path))
        assert result.exit_code == 0
