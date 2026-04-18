"""Updater user-data preservation tests (T-user-data-preservation).

Verifies that ``/update`` (run_self_update) never modifies files under the
user data root, refuses to run when the install dir is dirty, snapshots
user data before pulling, rotates old snapshots, and detects integrity
drift.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from nerdvana_cli.core import updater

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _init_fake_install(install_dir: Path) -> None:
    """Create a clean git repo inside ``install_dir``."""
    install_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=install_dir, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=install_dir, check=True)
    subprocess.run(["git", "config", "user.name",  "test"],             cwd=install_dir, check=True)
    (install_dir / "README.md").write_text("install\n", encoding="utf-8")
    subprocess.run(["git", "add", "."],                      cwd=install_dir, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=install_dir, check=True)


def _seed_user_data(data_home: Path) -> dict[str, str]:
    """Populate the user data root with the files the integrity check tracks."""
    data_home.mkdir(parents=True, exist_ok=True)
    files: dict[str, str] = {
        "config.yml":             "model:\n  provider: anthropic\n",
        "NIRNA.md":               "# user rules\n",
        "mcp.json":               "{\"mcpServers\": {}}\n",
        "external_projects.yml":  "projects: []\n",
        "contexts/standalone.yml": "description: user override\nexcluded_tools: []\n",
        "modes/planning.yml":      "description: user planning\nexcluded_tools: [Bash]\n",
        "memories/proj/rule.md":   "# rule\n",
    }
    for rel, body in files.items():
        p = data_home / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    return files


# ---------------------------------------------------------------------------
# 1. Install/data separation guard
# ---------------------------------------------------------------------------

def test_refuses_when_install_equals_data_home(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    shared.mkdir()
    with pytest.raises(RuntimeError, match="equals user_data_home"):
        updater._assert_install_user_separation(shared, shared)


def test_refuses_when_data_home_nested_in_install(tmp_path: Path) -> None:
    install = tmp_path / "install"
    data    = install / "data"
    data.mkdir(parents=True)
    with pytest.raises(RuntimeError, match="nested under install_dir"):
        updater._assert_install_user_separation(install, data)


def test_refuses_when_install_nested_in_data_home(tmp_path: Path) -> None:
    data    = tmp_path / "data"
    install = data / "install"
    install.mkdir(parents=True)
    with pytest.raises(RuntimeError, match="nested under user_data_home"):
        updater._assert_install_user_separation(install, data)


def test_separation_allows_siblings(tmp_path: Path) -> None:
    install = tmp_path / "install"
    data    = tmp_path / "data"
    install.mkdir()
    data.mkdir()
    # No exception → siblings are fine.
    updater._assert_install_user_separation(install, data)


# ---------------------------------------------------------------------------
# 2. Dirty install dir guard
# ---------------------------------------------------------------------------

def test_clean_install_passes(tmp_path: Path) -> None:
    install = tmp_path / "install"
    _init_fake_install(install)
    clean, msg = updater._check_install_dir_clean(install)
    assert clean, msg
    assert msg == ""


def test_dirty_install_is_refused(tmp_path: Path) -> None:
    install = tmp_path / "install"
    _init_fake_install(install)
    (install / "rogue.txt").write_text("unexpected\n", encoding="utf-8")
    clean, msg = updater._check_install_dir_clean(install)
    assert not clean
    assert "uncommitted" in msg


# ---------------------------------------------------------------------------
# 3. Snapshot + pruning
# ---------------------------------------------------------------------------

def test_snapshot_copies_user_data(tmp_path: Path) -> None:
    data_home = tmp_path / "data"
    _seed_user_data(data_home)

    snap = updater._snapshot_user_data(data_home, "20260418-120000")
    assert snap is not None
    assert snap.is_dir()
    assert (snap / "config.yml").read_text(encoding="utf-8").startswith("model:")
    assert (snap / "modes/planning.yml").is_file()
    # The snapshot dir itself must never recurse into .update-backups.
    assert not (snap / updater._SNAPSHOT_DIRNAME).exists()


def test_snapshot_returns_none_for_missing_data_home(tmp_path: Path) -> None:
    assert updater._snapshot_user_data(tmp_path / "absent", "ts") is None


def test_prune_keeps_latest_three(tmp_path: Path) -> None:
    data_home = tmp_path / "data"
    data_home.mkdir()
    root = data_home / updater._SNAPSHOT_DIRNAME
    root.mkdir()
    for ts in ("20260101", "20260102", "20260103", "20260104", "20260105"):
        (root / f"pre-update-{ts}").mkdir()

    removed = updater._prune_snapshots(data_home, keep=3)
    assert removed == 2

    remaining = sorted(p.name for p in root.iterdir())
    assert remaining == [
        "pre-update-20260103",
        "pre-update-20260104",
        "pre-update-20260105",
    ]


# ---------------------------------------------------------------------------
# 4. Integrity hash
# ---------------------------------------------------------------------------

def test_hash_user_data_covers_tracked_files(tmp_path: Path) -> None:
    data_home = tmp_path / "data"
    _seed_user_data(data_home)

    hashes = updater._hash_user_data(data_home)

    # Tracked files appear in the hash map.
    assert "config.yml"              in hashes
    assert "modes/planning.yml"      in hashes
    assert "memories/proj/rule.md"   in hashes

    # Untracked files (sessions/, audit.sqlite, etc.) do not appear.
    (data_home / "sessions").mkdir()
    (data_home / "sessions/s1.jsonl").write_text("x\n", encoding="utf-8")
    hashes2 = updater._hash_user_data(data_home)
    assert "sessions/s1.jsonl" not in hashes2


def test_hash_changes_when_file_edited(tmp_path: Path) -> None:
    data_home = tmp_path / "data"
    _seed_user_data(data_home)

    before = updater._hash_user_data(data_home)
    (data_home / "config.yml").write_text("model:\n  provider: openai\n", encoding="utf-8")
    after  = updater._hash_user_data(data_home)

    assert before["config.yml"] != after["config.yml"]


# ---------------------------------------------------------------------------
# 5. Full orchestrator — no-op pull
# ---------------------------------------------------------------------------

def test_run_self_update_no_change_preserves_user_data(tmp_path: Path) -> None:
    """Simulate 'Already up to date' → user data untouched."""
    install   = tmp_path / "install"
    data_home = tmp_path / "data"
    _init_fake_install(install)
    _seed_user_data(data_home)

    before = updater._hash_user_data(data_home)

    real_subprocess_run = subprocess.run

    class _FakeCompleted:
        returncode = 0
        stdout     = "Already up to date.\n"
        stderr     = ""

    def _fake_run(cmd, **kwargs):
        # Let the real ``git status --porcelain`` through so the dirty check
        # sees an authentic clean repo; stub only the network-touching calls.
        if cmd[:2] == ["git", "status"]:
            return real_subprocess_run(cmd, capture_output=True, text=True, cwd=kwargs.get("cwd"), timeout=kwargs.get("timeout"))
        return _FakeCompleted()

    with patch.object(updater, "user_data_home", return_value=data_home), \
         patch.object(updater.subprocess, "run", side_effect=_fake_run):
        ok, msg = updater.run_self_update(install_dir=install)

    assert ok, msg
    assert "Already up to date" in msg
    assert updater._hash_user_data(data_home) == before


# ---------------------------------------------------------------------------
# 6. Rejects overlapping install / data home at entrypoint
# ---------------------------------------------------------------------------

def test_run_self_update_rejects_nested_data_home(tmp_path: Path) -> None:
    install   = tmp_path / "install"
    data_home = install  / "inside"
    _init_fake_install(install)
    data_home.mkdir(parents=True)

    with patch.object(updater, "user_data_home", return_value=data_home):
        ok, msg = updater.run_self_update(install_dir=install)

    assert not ok
    assert "nested under install_dir" in msg
