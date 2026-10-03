"""``nerdvana run``: formats, options and exit codes through the real command.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from nerdvana_cli.core.agent_loop import AgentLoop
from nerdvana_cli.main import app
from nerdvana_cli.providers.base import ProviderEvent

runner = CliRunner()


class _Script:
    def __init__(self, events: list[ProviderEvent]) -> None:
        self.events = events

    async def stream(self, system_prompt: str, messages: Any, tools: Any) -> AsyncIterator[ProviderEvent]:
        for event in self.events:
            yield event


ANSWER = [
    ProviderEvent(type="content_delta", content="all "),
    ProviderEvent(type="content_delta", content="done"),
    ProviderEvent(type="usage", usage={"input_tokens": 120, "output_tokens": 9, "cache_read_tokens": 100}),
    ProviderEvent(type="done", stop_reason="end_turn"),
]


@pytest.fixture()
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("NERDVANA_NO_UPDATE_CHECK", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.delenv("NERDVANA_CONFIG", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(AgentLoop, "build_system_prompt", lambda self: "system")
    return tmp_path


def _provider(monkeypatch: pytest.MonkeyPatch, events: list[ProviderEvent]) -> None:
    provider = _Script(events)
    monkeypatch.setattr(AgentLoop, "create_provider_from_settings", lambda self: provider)


def test_json_format_prints_one_result_object_and_exits_zero(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _provider(monkeypatch, ANSWER)
    result = runner.invoke(app, ["run", "go", "--output-format", "json", "--model", "claude-sonnet-5-5"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["type"] == "result"
    assert payload["subtype"] == "success"
    assert payload["result"] == "all done"
    assert payload["num_turns"] == 1
    assert payload["usage"] == {"input_tokens": 120, "output_tokens": 9, "cache_read_tokens": 100, "cache_write_tokens": 0}
    assert payload["session_id"]
    assert payload["provider"] == "anthropic"


def test_stream_json_prints_only_json_lines_ending_with_the_result(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _provider(monkeypatch, ANSWER)
    result = runner.invoke(app, ["run", "go", "--output-format", "stream-json"])
    assert result.exit_code == 0, result.output
    events = [json.loads(line) for line in result.stdout.splitlines()]
    assert events[0]["type"] == "system"
    assert events[-1]["type"] == "result"
    assert "".join(e["text"] for e in events if e["type"] == "text") == "all done"


def test_text_format_is_unchanged_for_a_person(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _provider(monkeypatch, ANSWER)
    result = runner.invoke(app, ["run", "go"])
    assert result.exit_code == 0
    assert "all done" in result.stdout
    assert not result.stdout.lstrip().startswith("{")


def test_a_provider_failure_exits_one_with_an_error_result(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _provider(monkeypatch, [ProviderEvent(type="error", error="bad request", error_kind="other")])
    result = runner.invoke(app, ["run", "go", "--output-format", "json"])
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["subtype"] == "error_provider"
    assert payload["is_error"] is True


def test_a_provider_failure_in_text_mode_now_exits_nonzero(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _provider(monkeypatch, [ProviderEvent(type="error", error="bad request", error_kind="other")])
    assert runner.invoke(app, ["run", "go"]).exit_code == 1


def test_the_turn_limit_exits_three(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    class _Loops:
        async def stream(self, system_prompt: str, messages: Any, tools: Any) -> AsyncIterator[ProviderEvent]:
            calls["n"] += 1
            yield ProviderEvent(type="tool_use_complete", tool_use_id=f"c{calls['n']}", tool_name="Glob",
                                tool_input_complete={"pattern": f"*{calls['n']}"})
            yield ProviderEvent(type="done", stop_reason="tool_use")

    provider = _Loops()
    monkeypatch.setattr(AgentLoop, "create_provider_from_settings", lambda self: provider)
    result = runner.invoke(app, ["run", "go", "--output-format", "json", "--max-turns", "2"])
    assert result.exit_code == 3
    payload = json.loads(result.stdout)
    assert (payload["subtype"], payload["num_turns"]) == ("error_max_turns", 2)


def test_a_missing_api_key_is_a_config_error(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    result = runner.invoke(app, ["run", "go", "--output-format", "json"])
    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    assert payload["subtype"] == "error_config"
    assert "API key" in payload["error"]


def test_a_bad_output_format_is_rejected_before_anything_runs(env: Path) -> None:
    result = runner.invoke(app, ["run", "go", "--output-format", "xml"])
    assert result.exit_code == 2
    assert result.stdout.strip() == ""


def test_a_bad_approval_mode_is_rejected(env: Path) -> None:
    assert runner.invoke(app, ["run", "go", "--approval-mode", "reckless"]).exit_code == 2


def test_the_options_reach_the_settings(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}
    original = AgentLoop.__init__

    def _spy(self: AgentLoop, *args: Any, **kwargs: Any) -> None:
        original(self, *args, **kwargs)
        seen["max_turns"]    = self.settings.session.max_turns
        seen["max_cost_usd"] = self.settings.session.max_cost_usd
        seen["mode"]         = self.policy.mode_name
        seen["tokens"]       = self.settings.session.max_total_tokens
        seen["price"]        = self.settings.session.require_price
        seen["sandbox"]      = self.settings.sandbox.mode

    monkeypatch.setattr(AgentLoop, "__init__", _spy)
    _provider(monkeypatch, ANSWER)
    result = runner.invoke(app, [
        "run", "go", "--max-turns", "7", "--max-cost-usd", "1.5", "--approval-mode", "yolo",
        "--max-total-tokens", "5000", "--require-price", "--sandbox", "auto",
    ])
    assert result.exit_code == 0, result.output
    assert seen == {"max_turns": 7, "max_cost_usd": 1.5, "mode": "one-shot", "tokens": 5000, "price": True, "sandbox": "auto"}


def test_a_bad_sandbox_mode_is_rejected(env: Path) -> None:
    assert runner.invoke(app, ["run", "go", "--sandbox", "sometimes"]).exit_code == 2


def test_verify_passes_when_the_command_exits_zero(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _provider(monkeypatch, ANSWER)
    result = runner.invoke(app, ["run", "go", "--output-format", "json", "--verify", "true"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["subtype"] == "success"
    assert payload["verification"] == {"command": "true", "status": "met", "attempts": 1, "last_exit": 0}


def test_verify_that_never_passes_ends_as_unmet_with_exit_code_three(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _provider(monkeypatch, ANSWER)
    result = runner.invoke(app, ["run", "go", "--output-format", "json", "--verify", "false", "--verify-attempts", "2"])
    assert result.exit_code == 3, result.output
    payload = json.loads(result.stdout)
    assert payload["subtype"] == "error_goal_unmet"
    assert payload["verification"]["attempts"] == 2 and payload["verification"]["status"] == "unmet"
    assert payload["signals"]["verify_failed"] == 2


def test_without_verify_the_result_has_no_verification_object(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _provider(monkeypatch, ANSWER)
    payload = json.loads(runner.invoke(app, ["run", "go", "--output-format", "json"]).stdout)
    assert "verification" not in payload


def test_set_overrides_one_setting_for_the_run_and_validates_it(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}
    original = AgentLoop.__init__

    def _spy(self: AgentLoop, *args: Any, **kwargs: Any) -> None:
        original(self, *args, **kwargs)
        seen["threshold"] = self.settings.session.compact_threshold
        seen["fallbacks"] = self.settings.model.fallback_models

    monkeypatch.setattr(AgentLoop, "__init__", _spy)
    _provider(monkeypatch, ANSWER)
    result = runner.invoke(app, ["run", "go", "--set", "session.compact_threshold=0.5", "--set", "model.fallback_models=[claude-opus-5-5]"])
    assert result.exit_code == 0, result.output
    assert seen == {"threshold": 0.5, "fallbacks": ["claude-opus-5-5"]}


@pytest.mark.parametrize("assignment", [
    "session.compact_threshold",             # no value
    "compact_threshold=0.5",                 # no section
    "session.no_such_field=1",
    "nosuch.field=1",
    "session.max_turns=many",                # not an integer
    "permissions.mode=yolo",                 # not overridable this way
    "sandbox.mode=off",
])
def test_a_bad_set_is_refused_with_the_configuration_exit_code(env: Path, monkeypatch: pytest.MonkeyPatch, assignment: str) -> None:
    _provider(monkeypatch, ANSWER)
    result = runner.invoke(app, ["run", "go", "--set", assignment])
    assert result.exit_code == 2, result.output


def test_the_result_names_the_model_that_finished_the_run(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original = AgentLoop.run

    async def _switching(self: AgentLoop, prompt: str, images: Any = None) -> Any:
        self.settings.model.model = "claude-opus-5-5"
        async for chunk in original(self, prompt, images):
            yield chunk

    monkeypatch.setattr(AgentLoop, "run", _switching)
    _provider(monkeypatch, ANSWER)
    payload = json.loads(runner.invoke(app, ["run", "go", "--output-format", "json", "--model", "claude-sonnet-5-5"]).stdout)
    assert payload["model"] == "claude-opus-5-5"
