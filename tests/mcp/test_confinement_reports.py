"""Where the confinement of stdio MCP servers is reported: `nerdvana doctor` and the `/mcp` command.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from nerdvana_cli.cli.commands import doctor_command as dc
from nerdvana_cli.core.safety import sandbox
from nerdvana_cli.ui.slash.session_commands import handle_mcp


def _mcp(tmp_path: Path, servers: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    (tmp_path / ".mcp.json").write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")


class TestDoctorMcpSandbox:
    def test_skip_without_stdio_servers(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _mcp(tmp_path, {"web": {"type": "http", "url": "https://example.invalid/mcp"}}, monkeypatch)

        assert dc._check_mcp_sandbox().status == "skip"

    def test_names_the_confined_and_the_unconfined_servers(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sandbox, "landlock_abi", lambda: 4)
        _mcp(tmp_path, {"locked": {"command": "node", "sandbox": "auto"}, "open": {"command": "node"}}, monkeypatch)

        result = dc._check_mcp_sandbox()

        assert result.status == "ok"
        assert result.detail == "confined: locked; unconfined: open"

    def test_warns_when_confinement_was_asked_for_but_the_system_cannot_give_it(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(sandbox, "landlock_abi", lambda: 0)
        _mcp(tmp_path, {"locked": {"command": "node", "sandbox": "auto"}}, monkeypatch)

        result = dc._check_mcp_sandbox()

        assert result.status == "warn"
        assert "locked ask for confinement" in result.detail

    def test_fails_when_confinement_is_required_and_unavailable(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sandbox, "landlock_abi", lambda: 0)
        _mcp(tmp_path, {"locked": {"command": "node", "sandbox": "require"}}, monkeypatch)

        result = dc._check_mcp_sandbox()

        assert result.status == "fail"
        assert "required but unavailable" in result.detail


class TestDoctorMcpSandboxSettings:
    @pytest.mark.parametrize("entry", [
        {"command": "node", "sandbox": "strict"},
        {"command": "node", "sandbox": True},
        {"command": "node", "network": "no"},
    ])
    def test_an_unusable_setting_fails_the_check(
        self, entry: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _mcp(tmp_path, {"a": entry}, monkeypatch)

        result = dc._check_mcp_sandbox()

        assert result.status == "fail"
        assert "MCP server 'a'" in result.detail

    def test_valid_settings_pass(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sandbox, "landlock_abi", lambda: 4)
        _mcp(tmp_path, {"a": {"command": "node", "sandbox": "require", "network": False, "write_paths": ["/srv"]}}, monkeypatch)

        assert dc._check_mcp_sandbox().status == "ok"


class _Manager:
    def get_status(self) -> dict[str, bool]:
        return {"locked": True, "open": True, "remote": False}

    def get_confinement(self) -> dict[str, str]:
        return {"locked": "confined (writes limited, network allowed)", "open": "unconfined (sandbox off)"}


class _App:
    def __init__(self) -> None:
        self.mcp_manager = _Manager()
        self.lines: list[str] = []

    def _add_chat_message(self, text: str, raw_text: str | None = None) -> None:
        self.lines.append(text)


@pytest.mark.asyncio
async def test_mcp_command_shows_how_each_connected_server_is_confined() -> None:
    app = _App()

    await handle_mcp(app, "")  # type: ignore[arg-type]

    assert any("locked" in line and "confined (writes limited" in line for line in app.lines)
    assert any("open" in line and "unconfined (sandbox off)" in line for line in app.lines)
    assert [line for line in app.lines if "remote" in line][0].rstrip().endswith("remote")
