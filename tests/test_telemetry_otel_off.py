"""Tracing is off unless asked for, and then needs the SDK; none of this needs the SDK to run.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
import yaml

from nerdvana_cli.core import telemetry_otel
from nerdvana_cli.core.agent_loop import AgentLoop
from nerdvana_cli.core.otel_semconv import Attr, provider_name
from nerdvana_cli.core.settings import NerdvanaSettings, OtelConfig
from nerdvana_cli.core.tool import ToolRegistry

NOTHING_IMPORTED = textwrap.dedent("""
    import sys
    from unittest.mock import MagicMock

    from nerdvana_cli.core import telemetry_otel
    from nerdvana_cli.core.settings import NerdvanaSettings
    from nerdvana_cli.tools.bash_tool import _build_env

    assert telemetry_otel.setup(NerdvanaSettings()) == ""
    telemetry_otel.observe_loop(MagicMock())
    assert telemetry_otel.trace_environment() == {}
    _build_env(".")
    loaded = sorted(name for name in sys.modules if name == "opentelemetry" or name.startswith("opentelemetry."))
    print("LOADED", loaded)
""")


def _public(table: type) -> dict[str, str]:
    return {key: value for key, value in vars(table).items() if not key.startswith("_")}


def test_with_tracing_off_the_sdk_is_never_imported() -> None:
    done = subprocess.run([sys.executable, "-c", NOTHING_IMPORTED], capture_output=True, text=True, timeout=120, check=False)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip().splitlines()[-1] == "LOADED []"


def test_tracing_is_off_by_default_and_the_endpoint_comes_from_the_environment() -> None:
    config = NerdvanaSettings().telemetry.otel
    assert config == OtelConfig(enabled=False, endpoint="", service_name="nerdvana-cli", capture_content=False)


def test_the_settings_file_turns_the_options_on(tmp_path: Path) -> None:
    path = tmp_path / "nerdvana.yml"
    path.write_text(
        yaml.safe_dump({"telemetry": {"otel": {"enabled": True, "endpoint": "http://c:4318", "service_name": "x", "capture_content": True}}}),
        encoding="utf-8",
    )
    config = NerdvanaSettings.load(str(path)).telemetry.otel
    assert (config.enabled, config.endpoint, config.service_name, config.capture_content) == (True, "http://c:4318", "x", True)


def test_a_bad_value_in_the_settings_file_falls_back_to_off(tmp_path: Path) -> None:
    path = tmp_path / "nerdvana.yml"
    path.write_text(yaml.safe_dump({"telemetry": {"otel": {"enabled": "perhaps"}}}), encoding="utf-8")
    settings = NerdvanaSettings.load(str(path))
    assert settings.telemetry.otel.enabled is False
    assert any("telemetry.otel" in warning.format() for warning in settings.load_warnings)


def test_a_loop_built_while_tracing_is_off_is_left_alone(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path))
    assert not telemetry_otel.is_active()
    settings                = NerdvanaSettings()
    settings.cwd            = str(tmp_path)
    settings.model.provider = "anthropic"
    settings.model.api_key  = "k"
    assert AgentLoop(settings=settings, registry=ToolRegistry()).usage_listener is None


@pytest.mark.parametrize("hidden", ["opentelemetry.sdk.trace", "opentelemetry.exporter.otlp.proto.http.trace_exporter"])
def test_without_the_sdk_setup_says_how_to_install_it(monkeypatch: pytest.MonkeyPatch, hidden: str) -> None:
    monkeypatch.setitem(sys.modules, hidden, None)
    settings = NerdvanaSettings()
    settings.telemetry.otel.enabled = True
    notice = telemetry_otel.setup(settings)
    assert "nerdvana-cli[otel]" in notice and "telemetry.otel.enabled" in notice
    assert not telemetry_otel.is_active()


def test_usage_listeners_are_called_in_order_and_none_is_skipped() -> None:
    heard: list[str] = []

    def first(info: dict[str, object]) -> None:
        heard.append("first")

    def second(info: dict[str, object]) -> None:
        heard.append("second")

    telemetry_otel.chain_usage_listeners(None, second)({})
    telemetry_otel.chain_usage_listeners(first, second)({})
    assert heard == ["second", "first", "second"]


def test_attribute_names_are_written_only_in_the_one_table() -> None:
    names = _public(Attr).values()
    assert "gen_ai." not in Path(telemetry_otel.__file__).read_text(encoding="utf-8")
    assert len(set(names)) == len(list(names))
    assert all(name.startswith(("gen_ai.", "nerdvana.", "error.")) for name in names)


def test_provider_names_follow_the_conventions_where_they_differ() -> None:
    assert (provider_name("gemini"), provider_name("anthropic"), provider_name("ollama")) == ("gcp.gemini", "anthropic", "ollama")


def test_a_project_file_cannot_turn_tracing_on_or_choose_the_endpoint(tmp_path, monkeypatch) -> None:
    from nerdvana_cli.core.settings import NerdvanaSettings

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.delenv("NERDVANA_CONFIG", raising=False)
    (tmp_path / "nerdvana.yml").write_text("telemetry:\n  otel:\n    enabled: true\n    endpoint: http://collector.invalid\n", encoding="utf-8")
    settings = NerdvanaSettings.load()
    assert settings.telemetry.otel.enabled is False
    assert any(w.kind == "user_only_key" for w in settings.load_warnings)

    user = tmp_path / "user.yml"
    user.write_text("telemetry:\n  otel:\n    enabled: true\n", encoding="utf-8")
    assert NerdvanaSettings.load(str(user)).telemetry.otel.enabled is True
