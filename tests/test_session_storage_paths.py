"""Session storage must write under ~/.nerdvana/sessions, never under install dir."""
from __future__ import annotations

from pathlib import Path

import pytest

from nerdvana_cli.core.session import SessionStorage


def test_session_writes_under_nerdvana_not_install_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("NERDVANA_DATA_HOME", raising=False)

    sess = SessionStorage(session_id="test123")
    sess.record_user_message("hello")

    expected  = tmp_path / ".nerdvana" / "sessions" / "test123.jsonl"
    forbidden = tmp_path / ".nerdvana-cli" / "sessions" / "test123.jsonl"
    assert expected.exists(), f"session not written to {expected}"
    assert not forbidden.exists(), f"session leaked into install dir: {forbidden}"


def test_get_last_session_reads_from_nerdvana(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("NERDVANA_DATA_HOME", raising=False)

    sess = SessionStorage(session_id="abc456")
    sess.record_user_message("first")

    assert SessionStorage.get_last_session() == "abc456"


def test_nerdvana_data_home_override_is_honored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    custom = tmp_path / "custom_data"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(custom))

    sess = SessionStorage(session_id="env_test")
    sess.record_user_message("x")

    assert (custom / "sessions" / "env_test.jsonl").exists()


def test_get_last_session_falls_back_to_legacy_install_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("NERDVANA_DATA_HOME", raising=False)

    legacy = tmp_path / ".nerdvana-cli" / "sessions"
    legacy.mkdir(parents=True)
    (legacy / "legacy_sess.jsonl").write_text("{}\n")

    # New location is empty
    assert SessionStorage.get_last_session() == "legacy_sess"
