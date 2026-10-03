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


class TestFirstMessageOfRecordedTranscripts:
    """The transcript writer marks an entry's kind with ``type``; older transcripts used ``role``."""

    def _recorded(self, tmp_path: Path) -> Path:
        from nerdvana_cli.core.state.session import SessionStorage
        storage = SessionStorage(session_id="rec1", storage_dir=str(tmp_path))
        storage.record_user_message("fix the parser")
        storage.record_assistant_message("looking at it")
        storage.record_user_message("and add a test")
        return Path(storage.file_path)

    def test_current_format_previews_the_first_user_message(self, tmp_path: Path) -> None:
        from nerdvana_cli.commands.session_command import _first_message
        path = self._recorded(tmp_path)
        assert json.loads(path.read_text(encoding="utf-8").splitlines()[0])["subtype"] == "session_start"
        assert _first_message(path) == "fix the parser"

    def test_older_format_with_role_still_previews(self, tmp_path: Path) -> None:
        from nerdvana_cli.commands.session_command import _first_message
        path = _make_session(tmp_path, "old", [
            {"ts": "2026-01-01T00:00:00", "role": "assistant", "content": "hi"},
            {"ts": "2026-01-01T00:00:01", "role": "user", "content": "older request"},
        ])
        assert _first_message(path) == "older request"

    def test_entries_that_are_not_user_messages_are_skipped(self, tmp_path: Path) -> None:
        from nerdvana_cli.commands.session_command import _first_message
        path = _make_session(tmp_path, "mixed", [
            {"type": "system", "subtype": "session_start", "cwd": "/work"},
            {"type": "assistant", "content": "not this", "tool_uses": []},
            {"type": "tool_result", "tool_name": "Bash", "tool_use_id": "t1", "content": "nor this"},
            {"type": "user", "content": "this one"},
        ])
        assert _first_message(path) == "this one"

    def test_blank_user_messages_and_lines_that_are_not_objects_are_skipped(self, tmp_path: Path) -> None:
        from nerdvana_cli.commands.session_command import _first_message
        path = tmp_path / "odd.jsonl"
        path.write_text(
            'not json\n[1, 2]\n"text"\n' + json.dumps({"type": "user", "content": "  \n "}) + "\n"
            + json.dumps({"type": "user", "content": [{"type": "text", "text": "from a block"}]}) + "\n",
            encoding="utf-8",
        )
        assert _first_message(path) == "from a block"

    def test_a_multi_line_message_is_one_line_and_cut_at_80_characters(self, tmp_path: Path) -> None:
        from nerdvana_cli.commands.session_command import _first_message
        path = _make_session(tmp_path, "long", [{"type": "user", "content": "first line\n" + "word " * 40}])
        preview = _first_message(path)
        assert "\n" not in preview and preview.startswith("first line word") and len(preview) == 80

    def test_a_transcript_without_a_user_message_has_no_preview(self, tmp_path: Path) -> None:
        from nerdvana_cli.commands.session_command import _first_message
        path = _make_session(tmp_path, "none", [{"type": "system", "subtype": "session_start", "cwd": "/work"}])
        assert _first_message(path) == "(no preview)"


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

    def test_list_shows_the_preview_of_a_recorded_session(self, tmp_path: Path) -> None:
        from nerdvana_cli.core.state.session import SessionStorage
        storage = SessionStorage(session_id="rec42", storage_dir=str(tmp_path / "sessions"))
        storage.record_user_message("summarize the build log")
        result = self._run(["session", "list"], str(tmp_path))
        assert result.exit_code == 0
        assert "summarize the build log" in result.output
        assert "(no preview)" not in result.output

    def test_a_preview_with_brackets_is_printed_as_typed(self, tmp_path: Path) -> None:
        from nerdvana_cli.core.state.session import SessionStorage
        storage = SessionStorage(session_id="rec43", storage_dir=str(tmp_path / "sessions"))
        storage.record_user_message("why does [/bold] break the list")
        result = self._run(["session", "list"], str(tmp_path))
        assert result.exit_code == 0
        assert "why does [/bold] break the list" in result.output

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


# ---------------------------------------------------------------------------
# CLI integration: session resume
# ---------------------------------------------------------------------------

class TestSessionResume:
    def test_resume_hands_the_id_to_the_repl_without_the_environment(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import os

        from nerdvana_cli.main import app
        _make_session(tmp_path / "sessions", "abc123", [{"role": "user", "content": "x"}])
        monkeypatch.delenv("NERDVANA_RESUME", raising=False)
        calls: list[dict[str, object]] = []

        async def fake_repl(**kwargs: object) -> None:
            calls.append(kwargs)

        monkeypatch.setattr("nerdvana_cli.cli.runtime.repl_loop", fake_repl)
        result = CliRunner().invoke(app, ["session", "resume", "abc123"], env={"NERDVANA_DATA_HOME": str(tmp_path)})
        assert result.exit_code == 0, result.output
        assert calls == [{"resume_id": "abc123"}]
        assert "NERDVANA_RESUME" not in os.environ

    def test_the_tui_keeps_the_id_it_is_given(self) -> None:
        from nerdvana_cli.core.config.settings import NerdvanaSettings
        from nerdvana_cli.ui.app import NerdvanaApp
        assert NerdvanaApp(settings=NerdvanaSettings(), resume_id="abc123")._resume_id == "abc123"

    def test_unknown_session_exits_with_an_error(self, tmp_path: Path) -> None:
        from nerdvana_cli.main import app
        result = CliRunner().invoke(app, ["session", "resume", "nope"], env={"NERDVANA_DATA_HOME": str(tmp_path)})
        assert result.exit_code == 1
