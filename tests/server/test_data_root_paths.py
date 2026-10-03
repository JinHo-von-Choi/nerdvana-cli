"""Server stores, analytics and the cost report follow NERDVANA_DATA_HOME.

A user who set the variable before it applied to these files has them in
``~/.nerdvana``; they must stay readable from there, with one warning, and
never be copied or deleted.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from nerdvana_cli.cli.commands.cost_command import build_cost_report
from nerdvana_cli.core.config import paths
from nerdvana_cli.core.telemetry.analytics import AnalyticsWriter
from nerdvana_cli.server.acl import ACLManager
from nerdvana_cli.server.audit import AuditLogger
from nerdvana_cli.server.auth import AuthManager
from nerdvana_cli.server.hook_bridge import HookBridge
from nerdvana_cli.server.sanitizer import SanitizerAudit
from tests.test_cost_command import _insert_row, _make_db

STORE_FILES = ("mcp_keys.yml", "mcp_acl.yml", "audit.sqlite")


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("NERDVANA_DATA_HOME", raising=False)
    monkeypatch.delenv("NERDVANA_HOME", raising=False)
    monkeypatch.setattr(paths, "_legacy_store_warned", set())
    return home


@pytest.fixture
def data_root(home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "data"
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(root))
    return root


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x", encoding="utf-8")
    return path


def _consumer_paths() -> dict[str, Path]:
    return {
        "mcp_acl.yml":   ACLManager()._acl_path,
        "mcp_keys.yml":  AuthManager()._keys_path,
        "audit.sqlite":  AuditLogger()._db_path,
    }


def _audit_consumer_paths() -> list[Path]:
    return [AuditLogger()._db_path, HookBridge()._db_path, SanitizerAudit()._db_path]


@pytest.mark.parametrize("name", STORE_FILES)
def test_store_path_is_in_the_data_root_when_the_variable_is_set(name: str, data_root: Path) -> None:
    _touch(data_root / name)
    assert paths.server_store_path(name) == data_root / name


@pytest.mark.parametrize("name", STORE_FILES)
def test_store_path_prefers_the_data_root_over_an_old_copy(name: str, home: Path, data_root: Path) -> None:
    _touch(data_root / name)
    _touch(home / ".nerdvana" / name)
    assert paths.server_store_path(name) == data_root / name


@pytest.mark.parametrize("name", STORE_FILES)
def test_store_path_falls_back_to_the_old_location_and_warns_once(
    name: str, home: Path, data_root: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    old = _touch(home / ".nerdvana" / name)
    with caplog.at_level(logging.WARNING, logger=paths.logger.name):
        first  = paths.server_store_path(name)
        second = paths.server_store_path(name)
    assert first == second == old
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert str(old) in warnings[0]
    assert str(data_root) in warnings[0]


def test_store_path_does_not_copy_or_delete_the_old_file(home: Path, data_root: Path) -> None:
    old = _touch(home / ".nerdvana" / "mcp_keys.yml")
    paths.server_store_path("mcp_keys.yml")
    assert old.read_text(encoding="utf-8") == "x"
    assert not (data_root / "mcp_keys.yml").exists()


@pytest.mark.parametrize("name", STORE_FILES)
def test_store_path_is_in_the_data_root_when_no_file_exists(name: str, data_root: Path) -> None:
    assert paths.server_store_path(name) == data_root / name


@pytest.mark.parametrize("name", STORE_FILES)
def test_store_path_without_the_variable_is_the_default_root(
    name: str, home: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    _touch(home / ".nerdvana" / name)
    with caplog.at_level(logging.WARNING, logger=paths.logger.name):
        assert paths.server_store_path(name) == home / ".nerdvana" / name
    assert caplog.records == []


def test_server_components_use_the_data_root_when_the_variable_is_set(data_root: Path) -> None:
    for name, path in _consumer_paths().items():
        assert path == data_root / name
    assert all(path == data_root / "audit.sqlite" for path in _audit_consumer_paths())


def test_server_components_read_the_old_location_when_only_it_has_the_files(home: Path, data_root: Path) -> None:
    for name in STORE_FILES:
        _touch(home / ".nerdvana" / name)
    for name, path in _consumer_paths().items():
        assert path == home / ".nerdvana" / name
    assert all(path == home / ".nerdvana" / "audit.sqlite" for path in _audit_consumer_paths())


def test_server_components_use_the_default_root_without_the_variable(home: Path) -> None:
    for name, path in _consumer_paths().items():
        assert path == home / ".nerdvana" / name
    assert all(path == home / ".nerdvana" / "audit.sqlite" for path in _audit_consumer_paths())


def test_an_explicit_path_wins_over_the_data_root(tmp_path: Path, data_root: Path) -> None:
    explicit = tmp_path / "keys.yml"
    assert AuthManager(keys_path=explicit)._keys_path == explicit


def test_analytics_database_follows_the_data_root(data_root: Path) -> None:
    assert paths.analytics_db_path() == data_root / "analytics.sqlite"
    assert AnalyticsWriter(enabled=False)._db_path == data_root / "analytics.sqlite"
    assert data_root.is_dir()


def test_analytics_database_defaults_to_the_home_root(home: Path) -> None:
    assert AnalyticsWriter(enabled=False)._db_path == home / ".nerdvana" / "analytics.sqlite"


def test_cost_report_reads_the_database_of_the_data_root(data_root: Path) -> None:
    data_root.mkdir(parents=True)
    db = data_root / "analytics.sqlite"
    _make_db(db)
    _insert_row(db, "anthropic", "claude-sonnet-4-6", 1000, 500, 4.5)
    report = build_cost_report(since="all", by="provider")
    assert report["total_input"] == 1000
