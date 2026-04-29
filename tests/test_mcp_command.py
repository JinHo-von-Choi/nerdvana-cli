"""Tests for nerdvana_cli.commands.mcp_command.

Author: 최진호
Date:   2026-04-29
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(args: list[str], data_home: str) -> object:
    from nerdvana_cli.main import app
    runner = CliRunner()
    return runner.invoke(app, args, env={"NERDVANA_DATA_HOME": data_home})


def _read_mcp(data_home: Path) -> dict:
    path = data_home / "mcp.json"
    if not path.exists():
        return {"mcpServers": {}}
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Unit tests — _load_raw / _save_raw
# ---------------------------------------------------------------------------

class TestLoadSaveRaw:
    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        from nerdvana_cli.commands.mcp_command import _load_raw
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("NERDVANA_DATA_HOME", str(tmp_path / "nonexistent"))
            data = _load_raw()
        assert data == {"mcpServers": {}}

    def test_invalid_json_returns_empty(self, tmp_path: Path) -> None:
        (tmp_path / "mcp.json").write_text("NOT JSON", encoding="utf-8")
        from nerdvana_cli.commands.mcp_command import _load_raw
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("NERDVANA_DATA_HOME", str(tmp_path))
            data = _load_raw()
        assert data["mcpServers"] == {}

    def test_roundtrip(self, tmp_path: Path) -> None:
        from nerdvana_cli.commands.mcp_command import _load_raw, _save_raw
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("NERDVANA_DATA_HOME", str(tmp_path))
            data = _load_raw()
            data["mcpServers"]["test"] = {"type": "http", "url": "http://localhost:10000"}
            _save_raw(data)
            loaded = _load_raw()
        assert loaded["mcpServers"]["test"]["url"] == "http://localhost:10000"


# ---------------------------------------------------------------------------
# CLI integration — mcp list
# ---------------------------------------------------------------------------

class TestMcpList:
    def test_empty_config(self, tmp_path: Path) -> None:
        result = _run(["mcp", "list"], str(tmp_path))
        assert result.exit_code == 0
        assert "No MCP servers" in result.output

    def test_shows_servers(self, tmp_path: Path) -> None:
        (tmp_path / "mcp.json").write_text(
            json.dumps({"mcpServers": {"myserver": {"type": "http", "url": "http://localhost:10001"}}}),
            encoding="utf-8",
        )
        result = _run(["mcp", "list"], str(tmp_path))
        assert result.exit_code == 0
        assert "myserver" in result.output

    def test_json_output(self, tmp_path: Path) -> None:
        (tmp_path / "mcp.json").write_text(
            json.dumps({"mcpServers": {"srv": {"type": "http", "url": "http://x:10002"}}}),
            encoding="utf-8",
        )
        result = _run(["mcp", "list", "--json"], str(tmp_path))
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "srv" in data


# ---------------------------------------------------------------------------
# CLI integration — mcp add
# ---------------------------------------------------------------------------

class TestMcpAdd:
    def test_add_http_server(self, tmp_path: Path) -> None:
        result = _run(["mcp", "add", "newserver", "--url", "http://localhost:10003"], str(tmp_path))
        assert result.exit_code == 0
        data = _read_mcp(tmp_path)
        assert "newserver" in data["mcpServers"]
        assert data["mcpServers"]["newserver"]["url"] == "http://localhost:10003"

    def test_add_stdio_server(self, tmp_path: Path) -> None:
        result = _run(
            ["mcp", "add", "stdiosrv", "--url", "python3", "--transport", "stdio"],
            str(tmp_path),
        )
        assert result.exit_code == 0
        data = _read_mcp(tmp_path)
        assert data["mcpServers"]["stdiosrv"]["type"] == "stdio"
        assert data["mcpServers"]["stdiosrv"]["command"] == "python3"

    def test_duplicate_exits_nonzero(self, tmp_path: Path) -> None:
        _run(["mcp", "add", "dup", "--url", "http://a:10004"], str(tmp_path))
        result = _run(["mcp", "add", "dup", "--url", "http://b:10005"], str(tmp_path))
        assert result.exit_code != 0

    def test_invalid_transport_exits_nonzero(self, tmp_path: Path) -> None:
        result = _run(
            ["mcp", "add", "bad", "--url", "http://x:10006", "--transport", "grpc"],
            str(tmp_path),
        )
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# CLI integration — mcp remove
# ---------------------------------------------------------------------------

class TestMcpRemove:
    def test_remove_existing(self, tmp_path: Path) -> None:
        _run(["mcp", "add", "tosrm", "--url", "http://localhost:10007"], str(tmp_path))
        result = _run(["mcp", "remove", "tosrm"], str(tmp_path))
        assert result.exit_code == 0
        data = _read_mcp(tmp_path)
        assert "tosrm" not in data["mcpServers"]

    def test_remove_missing_exits_nonzero(self, tmp_path: Path) -> None:
        result = _run(["mcp", "remove", "doesnotexist"], str(tmp_path))
        assert result.exit_code != 0

    def test_add_remove_add_cycle(self, tmp_path: Path) -> None:
        _run(["mcp", "add", "cycle", "--url", "http://localhost:10008"], str(tmp_path))
        _run(["mcp", "remove", "cycle"], str(tmp_path))
        result = _run(["mcp", "add", "cycle", "--url", "http://localhost:10009"], str(tmp_path))
        assert result.exit_code == 0
        data = _read_mcp(tmp_path)
        assert "cycle" in data["mcpServers"]
