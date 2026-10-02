"""Doctor checks for config load warnings, model resolution, fallback models, MCP config.

작성자: 최진호
작성일: 2026-10-03
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nerdvana_cli.commands import doctor_command as dc


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point HOME, data home, XDG and cwd at tmp_path so no real config is read."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("NERDVANA_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)


def _config(tmp_path: Path, text: str) -> None:
    (tmp_path / "nerdvana.yml").write_text(text, encoding="utf-8")


def _mcp(tmp_path: Path, servers: object) -> None:
    (tmp_path / ".mcp.json").write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")


class TestConfigWarnings:
    def test_ok_without_config(self) -> None:
        assert dc._check_config_warnings().status == "ok"

    def test_warn_on_recovered_field(self, tmp_path: Path) -> None:
        _config(tmp_path, "model:\n  max_tokens: lots\n")
        r = dc._check_config_warnings()
        assert r.status == "warn"
        assert "model.max_tokens" in r.detail

    def test_warn_on_unknown_key(self, tmp_path: Path) -> None:
        _config(tmp_path, "session:\n  hyperdrive: 1\n")
        r = dc._check_config_warnings()
        assert r.status == "warn"
        assert "session.hyperdrive" in r.detail

    def test_fail_on_invalid_permissions(self, tmp_path: Path) -> None:
        _config(tmp_path, "permissions:\n  always_allow: Bash\n")
        r = dc._check_config_warnings()
        assert r.status == "fail"
        assert "permissions.always_allow" in r.detail


class TestModelResolution:
    def test_ok_for_known_model(self, tmp_path: Path) -> None:
        _config(tmp_path, "model:\n  model: claude-sonnet-4-20250514\n")
        r = dc._check_model_resolution()
        assert r.status == "ok"
        assert "AnthropicProvider" in r.detail

    def test_fail_for_unknown_provider(self, tmp_path: Path) -> None:
        _config(tmp_path, "model:\n  provider: nonesuch\n  model: x\n")
        r = dc._check_model_resolution()
        assert r.status == "fail"
        assert "nonesuch" in r.detail

    def test_fail_for_empty_model(self, tmp_path: Path) -> None:
        _config(tmp_path, "model:\n  model: ''\n")
        assert dc._check_model_resolution().status == "fail"

    def test_fail_when_config_rejected(self, tmp_path: Path) -> None:
        _config(tmp_path, "permissions: 3\n")
        assert dc._check_model_resolution().status == "fail"


class TestFallbackModels:
    def test_skip_when_none(self) -> None:
        assert dc._check_fallback_models().status == "skip"

    def test_ok_when_all_resolve(self, tmp_path: Path) -> None:
        _config(tmp_path, "model:\n  provider: anthropic\n  fallback_models: [claude-haiku-4-5]\n")
        assert dc._check_fallback_models().status == "ok"

    def test_fail_on_blank_entry(self, tmp_path: Path) -> None:
        _config(tmp_path, "model:\n  provider: anthropic\n  fallback_models: ['  ']\n")
        assert dc._check_fallback_models().status == "fail"

    def test_fail_on_unknown_provider(self, tmp_path: Path) -> None:
        _config(tmp_path, "model:\n  provider: nonesuch\n  fallback_models: [m]\n")
        assert dc._check_fallback_models().status == "fail"

    def test_warn_when_entry_belongs_to_another_provider(self, tmp_path: Path) -> None:
        _config(tmp_path, "model:\n  provider: anthropic\n  fallback_models: [gpt-4.1]\n")
        r = dc._check_fallback_models()
        assert r.status == "warn"
        assert "gpt-4.1" in r.detail


class TestMcpConfig:
    def test_skip_without_files(self) -> None:
        assert dc._check_mcp_config().status == "skip"

    def test_ok_for_existing_stdio_command(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(dc.shutil, "which", lambda cmd: f"/bin/{cmd}")
        _mcp(tmp_path, {"a": {"command": "npx", "args": ["-y", "pkg"]}})
        assert dc._check_mcp_config().status == "ok"

    def test_warn_for_missing_command(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(dc.shutil, "which", lambda cmd: None)
        _mcp(tmp_path, {"a": {"command": "ghost-bin"}})
        r = dc._check_mcp_config()
        assert r.status == "warn"
        assert "ghost-bin" in r.detail

    def test_http_server_needs_no_command_and_no_network(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def _boom(*_a: object, **_k: object) -> int:
            raise AssertionError("network used")

        monkeypatch.setattr(dc, "_ping_http", _boom)
        _mcp(tmp_path, {"web": {"type": "http", "url": "https://example.invalid/mcp"}})
        assert dc._check_mcp_config().status == "ok"

    def test_fail_on_invalid_json(self, tmp_path: Path) -> None:
        (tmp_path / ".mcp.json").write_text("{not json", encoding="utf-8")
        r = dc._check_mcp_config()
        assert r.status == "fail"
        assert ".mcp.json" in r.detail

    def test_fail_on_malformed_entries(self, tmp_path: Path) -> None:
        _mcp(
            tmp_path,
            {"a": "oops", "b": {"type": "carrier-pigeon"}, "c": {"type": "sse"}, "d": {"command": "x", "args": "y"}},
        )
        r = dc._check_mcp_config()
        assert r.status == "fail"
        for name in ("a:", "b:", "c:", "d:"):
            assert name in r.detail

    def test_global_file_is_checked(self, tmp_path: Path) -> None:
        data = tmp_path / "data"
        data.mkdir()
        (data / "mcp.json").write_text(json.dumps({"mcpServers": []}), encoding="utf-8")
        assert dc._check_mcp_config().status == "fail"


class TestStrictSemantics:
    def test_new_checks_registered(self) -> None:
        for fn in (
            dc._check_config_warnings,
            dc._check_model_resolution,
            dc._check_fallback_models,
            dc._check_mcp_config,
        ):
            assert fn in dc._ALL_CHECKS

    def test_warn_fails_only_under_strict(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import typer

        _config(tmp_path, "model:\n  max_tokens: lots\n")
        monkeypatch.setattr(dc, "run_all_checks", lambda: [dc._check_config_warnings()])
        with pytest.raises(typer.Exit) as lax:
            dc.doctor_command(strict=False, json_output=True)
        with pytest.raises(typer.Exit) as strict:
            dc.doctor_command(strict=True, json_output=True)
        assert lax.value.exit_code == 0
        assert strict.value.exit_code == 1
