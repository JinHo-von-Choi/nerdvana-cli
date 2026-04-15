"""One-shot migration from legacy paths to ~/.nerdvana."""
from __future__ import annotations

from pathlib import Path

import pytest

from nerdvana_cli.core import migrate, paths


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("NERDVANA_DATA_HOME", raising=False)
    return tmp_path


def test_migrate_moves_legacy_sessions(fake_home: Path) -> None:
    legacy = fake_home / ".nerdvana-cli" / "sessions"
    legacy.mkdir(parents=True)
    (legacy / "abc.jsonl").write_text("{}\n")

    moved = migrate.run_if_needed()
    assert moved is True
    assert (fake_home / ".nerdvana" / "sessions" / "abc.jsonl").exists()
    # Legacy is removed after successful move
    assert not (legacy / "abc.jsonl").exists()


def test_migrate_moves_legacy_config(fake_home: Path) -> None:
    legacy = fake_home / ".config" / "nerdvana-cli"
    legacy.mkdir(parents=True)
    (legacy / "config.yml").write_text("model: {}")
    (legacy / "NIRNA.md").write_text("# notes")
    (legacy / "mcp.json").write_text("{}")
    (legacy / "skills").mkdir()
    (legacy / "skills" / "s.md").write_text("---\nname: s\n---\nbody")

    migrate.run_if_needed()

    new_root = fake_home / ".nerdvana"
    assert (new_root / "config.yml").exists()
    assert (new_root / "NIRNA.md").exists()
    assert (new_root / "mcp.json").exists()
    assert (new_root / "skills" / "s.md").exists()


def test_migrate_is_idempotent(fake_home: Path) -> None:
    legacy = fake_home / ".nerdvana-cli" / "sessions"
    legacy.mkdir(parents=True)
    (legacy / "abc.jsonl").write_text("{}\n")

    first  = migrate.run_if_needed()
    second = migrate.run_if_needed()
    assert first  is True
    assert second is False


def test_migrate_does_not_overwrite_existing_new_files(fake_home: Path) -> None:
    legacy_cfg = fake_home / ".config" / "nerdvana-cli" / "config.yml"
    legacy_cfg.parent.mkdir(parents=True)
    legacy_cfg.write_text("legacy: true")

    new_cfg = fake_home / ".nerdvana" / "config.yml"
    new_cfg.parent.mkdir(parents=True)
    new_cfg.write_text("new: true")

    migrate.run_if_needed()
    assert new_cfg.read_text() == "new: true"


def test_run_if_needed_is_safe_to_call_twice_no_legacy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("NERDVANA_DATA_HOME", raising=False)
    # No legacy data at all — should not raise, should return False
    assert migrate.run_if_needed() is False
    assert migrate.run_if_needed() is False
