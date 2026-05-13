"""Startup update-notice path: cache, formatter, enable/disable, freshness."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from nerdvana_cli.core import updater
from nerdvana_cli.core.updater import (
    cached_or_check,
    format_update_notice,
    is_update_check_enabled,
    read_update_cache,
    write_update_cache,
)


@pytest.fixture
def isolated_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path))
    monkeypatch.delenv("NERDVANA_NO_UPDATE_CHECK", raising=False)
    return tmp_path / "cache" / "update_check.json"


def test_format_update_notice_is_single_line():
    msg = format_update_notice("1.2.0", "v1.2.1", "https://example/releases/v1.2.1")
    assert "\n" not in msg
    assert "v1.2.1" in msg
    assert "v1.2.0" in msg
    assert "/update" in msg


def test_format_update_notice_handles_missing_url():
    msg = format_update_notice("1.0.0", "1.1.0", "")
    assert "v1.1.0" in msg
    assert "—" not in msg or "https" not in msg


def test_format_update_notice_normalizes_v_prefix():
    a = format_update_notice("v1.0.0", "v1.0.1", "")
    b = format_update_notice("1.0.0", "1.0.1", "")
    assert "v1.0.0" in a and "v1.0.1" in a
    assert "v1.0.0" in b and "v1.0.1" in b


def test_is_update_check_enabled_respects_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NERDVANA_NO_UPDATE_CHECK", "1")
    assert is_update_check_enabled(True) is False
    monkeypatch.setenv("NERDVANA_NO_UPDATE_CHECK", "true")
    assert is_update_check_enabled(True) is False
    monkeypatch.delenv("NERDVANA_NO_UPDATE_CHECK", raising=False)
    assert is_update_check_enabled(True) is True


def test_is_update_check_enabled_respects_settings(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("NERDVANA_NO_UPDATE_CHECK", raising=False)
    assert is_update_check_enabled(False) is False


def test_write_and_read_cache_roundtrip(isolated_cache: Path):
    write_update_cache("v1.2.3", "https://example/v1.2.3")
    entry = read_update_cache()
    assert entry is not None
    assert entry["latest"] == "v1.2.3"
    assert entry["url"] == "https://example/v1.2.3"
    assert "checked_at" in entry


def test_read_cache_returns_none_on_corruption(isolated_cache: Path):
    isolated_cache.parent.mkdir(parents=True, exist_ok=True)
    isolated_cache.write_text("not-json{", encoding="utf-8")
    assert read_update_cache() is None


def test_read_cache_returns_none_when_missing(isolated_cache: Path):
    assert read_update_cache() is None


@pytest.mark.asyncio
async def test_cached_or_check_hit_skips_network(isolated_cache: Path):
    write_update_cache("v9.9.9", "https://example/v9.9.9")
    with patch.object(updater, "check_for_update") as spy:
        result = await cached_or_check("1.0.0", ttl_hours=24)
    assert spy.call_count == 0
    assert result == {"version": "v9.9.9", "url": "https://example/v9.9.9"}


@pytest.mark.asyncio
async def test_cached_or_check_hit_returns_none_when_already_latest(isolated_cache: Path):
    write_update_cache("v1.0.0", "https://example/v1.0.0")
    with patch.object(updater, "check_for_update") as spy:
        result = await cached_or_check("v1.0.0", ttl_hours=24)
    assert spy.call_count == 0
    assert result is None


@pytest.mark.asyncio
async def test_cached_or_check_miss_calls_api_and_writes_cache(isolated_cache: Path):
    async def fake(_):
        return {"version": "v2.0.0", "url": "https://example/v2.0.0"}

    with patch.object(updater, "check_for_update", side_effect=fake) as spy:
        result = await cached_or_check("1.0.0", ttl_hours=24)
    assert spy.call_count == 1
    assert result == {"version": "v2.0.0", "url": "https://example/v2.0.0"}
    entry = read_update_cache()
    assert entry is not None
    assert entry["latest"] == "v2.0.0"


@pytest.mark.asyncio
async def test_cached_or_check_stale_triggers_refresh(isolated_cache: Path):
    isolated_cache.parent.mkdir(parents=True, exist_ok=True)
    stale = datetime.now(UTC) - timedelta(hours=48)
    isolated_cache.write_text(
        json.dumps({"checked_at": stale.isoformat(), "latest": "v0.0.1", "url": ""}),
        encoding="utf-8",
    )

    async def fake(_):
        return {"version": "v3.0.0", "url": "https://example/v3.0.0"}

    with patch.object(updater, "check_for_update", side_effect=fake) as spy:
        result = await cached_or_check("1.0.0", ttl_hours=24)
    assert spy.call_count == 1
    assert result is not None and result["version"] == "v3.0.0"


@pytest.mark.asyncio
async def test_cached_or_check_records_negative_result(isolated_cache: Path):
    """When upstream says 'no update', still touch the cache to throttle next call."""
    async def fake(_):
        return None

    with patch.object(updater, "check_for_update", side_effect=fake):
        result = await cached_or_check("1.0.0", ttl_hours=24)
    assert result is None
    entry = read_update_cache()
    assert entry is not None  # negative cache present
    assert entry["latest"] == "1.0.0"
