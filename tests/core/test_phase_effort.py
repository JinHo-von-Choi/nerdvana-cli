"""Reasoning effort per phase: planning, implementation and verification, recorded per request.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from nerdvana_cli.core.agent_loop import AgentLoop
from nerdvana_cli.core.config.settings import ModelConfig, NerdvanaSettings
from nerdvana_cli.core.goal import Goal
from nerdvana_cli.core.phase_effort import IMPLEMENTATION, PLANNING, VERIFICATION, phase_level, phases_configured
from nerdvana_cli.core.plan_gate import draft_plan
from nerdvana_cli.core.session import SessionStorage
from nerdvana_cli.core.subagent_config import LoopFactories
from nerdvana_cli.core.tool import ToolRegistry
from nerdvana_cli.providers.anthropic_provider import AnthropicProvider
from nerdvana_cli.providers.base import ProviderEvent

END = [ProviderEvent(type="content_delta", content="done"), ProviderEvent(type="done", stop_reason="end_turn")]


class _ConfigProvider:
    """Reads ``config.reasoning_effort`` on every request, as the OpenAI and Gemini adapters do."""

    def __init__(self, base: str = "") -> None:
        self.config  = SimpleNamespace(reasoning_effort=base)
        self.efforts: list[str] = []

    async def stream(self, system_prompt: str, messages: Any, tools: Any) -> AsyncIterator[ProviderEvent]:
        self.efforts.append(self.config.reasoning_effort)
        for event in END:
            yield event


class _TurnProvider:
    """Takes its level through ``set_turn_effort`` and records the level each request runs at."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error   = error
        self.levels: list[str] = []
        self.current = ""
        self.efforts: list[str] = []

    def set_turn_effort(self, level: str) -> bool:
        if self.error is not None:
            raise self.error
        self.levels.append(level)
        self.current = level
        return True

    async def stream(self, system_prompt: str, messages: Any, tools: Any) -> AsyncIterator[ProviderEvent]:
        self.efforts.append(self.current)
        for event in END:
            yield event


def _settings(tmp_path: Path, base: str = "", planning: str = "", implementation: str = "", verification: str = "") -> NerdvanaSettings:
    settings = NerdvanaSettings()
    settings.cwd                         = str(tmp_path)
    settings.model.reasoning_effort      = base
    settings.model.effort_planning       = planning
    settings.model.effort_implementation = implementation
    settings.model.effort_verification   = verification
    return settings


def _loop(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, provider: Any, settings: NerdvanaSettings) -> AgentLoop:
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(AgentLoop, "create_provider_from_settings", lambda self: provider)
    monkeypatch.setattr(AgentLoop, "build_system_prompt", lambda self: "system")
    return AgentLoop(settings=settings, registry=ToolRegistry(), session=SessionStorage(session_id="pe", storage_dir=str(tmp_path / "s")))


async def _drain(loop: AgentLoop, prompt: str = "go") -> None:
    async for _ in loop.run(prompt):
        pass


def _failing_goal(attempts: int = 3) -> Goal:
    return Goal(objective="never", verify="false", max_attempts=attempts)


# ---------------------------------------------------------------------------
# The settings
# ---------------------------------------------------------------------------


def test_a_phase_without_a_level_of_its_own_runs_at_the_configured_effort() -> None:
    model = ModelConfig(reasoning_effort="high", effort_verification="max")
    assert phase_level(model, PLANNING) == "high"
    assert phase_level(model, IMPLEMENTATION) == "high"
    assert phase_level(model, VERIFICATION) == "max"


def test_every_phase_defaults_to_empty_and_nothing_is_configured() -> None:
    model = ModelConfig()
    assert (model.effort_planning, model.effort_implementation, model.effort_verification) == ("", "", "")
    assert phases_configured(model) is False
    assert phases_configured(ModelConfig(effort_planning="low")) is True


# ---------------------------------------------------------------------------
# In the loop: providers that read their config
# ---------------------------------------------------------------------------


async def test_without_phase_settings_the_provider_is_left_alone(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    provider = _ConfigProvider(base="high")
    turn     = _TurnProvider()
    loop     = _loop(monkeypatch, tmp_path, provider, _settings(tmp_path, base="high"))
    loop.set_goal(_failing_goal(2))
    await _drain(loop)
    assert provider.efforts == ["high", "high"]
    loop2 = _loop(monkeypatch, tmp_path, turn, _settings(tmp_path, base="high"))
    await _drain(loop2)
    assert turn.levels == []


async def test_the_main_run_works_at_the_implementation_effort_and_the_retry_turns_at_the_verification_effort(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    provider = _ConfigProvider(base="high")
    loop     = _loop(monkeypatch, tmp_path, provider, _settings(tmp_path, base="high", implementation="medium", verification="max"))
    loop.set_goal(_failing_goal(3))
    await _drain(loop)
    assert provider.efforts == ["medium", "max", "max"]
    assert provider.config.reasoning_effort == "high"          # restored when the run ended


async def test_a_phase_left_empty_runs_at_the_configured_effort(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    provider = _ConfigProvider(base="high")
    loop     = _loop(monkeypatch, tmp_path, provider, _settings(tmp_path, base="high", verification="max"))
    loop.set_goal(_failing_goal(2))
    await _drain(loop)
    assert provider.efforts == ["high", "max"]


async def test_the_next_prompt_starts_at_the_implementation_effort_again(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    provider = _ConfigProvider(base="high")
    loop     = _loop(monkeypatch, tmp_path, provider, _settings(tmp_path, base="high", implementation="medium", verification="max"))
    loop.set_goal(_failing_goal(2))
    await _drain(loop)
    loop.set_goal(None)
    await _drain(loop)
    assert provider.efforts == ["medium", "max", "medium"]


async def test_an_empty_configured_effort_is_put_back_empty(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    provider = _ConfigProvider(base="")
    loop     = _loop(monkeypatch, tmp_path, provider, _settings(tmp_path, implementation="low"))
    await _drain(loop)
    assert provider.efforts == ["low"] and provider.config.reasoning_effort == ""


# ---------------------------------------------------------------------------
# In the loop: providers that change effort between turns
# ---------------------------------------------------------------------------


async def test_a_provider_with_turn_effort_is_told_the_level_of_each_phase(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    provider = _TurnProvider()
    loop     = _loop(monkeypatch, tmp_path, provider, _settings(tmp_path, base="high", implementation="medium", verification="max"))
    loop.set_goal(_failing_goal(3))
    await _drain(loop)
    assert provider.levels == ["medium", "max"]
    assert provider.efforts == ["medium", "max", "max"]


async def test_a_level_the_model_does_not_accept_is_left_out_with_one_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    provider = _TurnProvider(error=ValueError("reasoning_effort 'max' is not an effort level of m"))
    loop     = _loop(monkeypatch, tmp_path, provider, _settings(tmp_path, implementation="max", verification="max"))
    loop.set_goal(_failing_goal(3))
    with caplog.at_level(logging.WARNING, logger="nerdvana_cli.core.phase_effort"):
        await _drain(loop)
    assert provider.efforts == ["", "", ""]
    assert [r.getMessage().startswith("phase effort not applied") for r in caplog.records].count(True) == 1


async def test_a_new_provider_after_a_model_switch_gets_the_phase_the_run_is_in(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    first, second = _TurnProvider(), _TurnProvider()
    providers     = iter([first, second])
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(AgentLoop, "create_provider_from_settings", lambda self: next(providers))
    monkeypatch.setattr(AgentLoop, "build_system_prompt", lambda self: "system")
    loop = AgentLoop(
        settings=_settings(tmp_path, implementation="medium", verification="max"), registry=ToolRegistry(),
        session=SessionStorage(session_id="pe", storage_dir=str(tmp_path / "s")),
    )
    loop.phase_effort.enter(VERIFICATION)
    loop.failover.switch_model(None, "other-model")
    assert loop.provider is second and second.levels == ["max"]


async def test_a_config_provider_after_a_model_switch_gets_the_phase_too(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    first, second = _ConfigProvider("high"), _ConfigProvider("high")
    providers     = iter([first, second])
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(AgentLoop, "create_provider_from_settings", lambda self: next(providers))
    loop = AgentLoop(
        settings=_settings(tmp_path, base="high", verification="max"), registry=ToolRegistry(),
        session=SessionStorage(session_id="pe", storage_dir=str(tmp_path / "s")),
    )
    loop.phase_effort.enter(VERIFICATION)
    loop.failover.switch_model(None, "other-model")
    assert second.config.reasoning_effort == "max"


async def test_the_provider_is_kept_between_prompts_and_rebuilt_only_after_a_model_change(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    built: list[_TurnProvider] = []

    def _make(self: AgentLoop) -> _TurnProvider:
        built.append(_TurnProvider())
        return built[-1]

    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(AgentLoop, "create_provider_from_settings", _make)
    monkeypatch.setattr(AgentLoop, "build_system_prompt", lambda self: "system")
    loop = AgentLoop(settings=_settings(tmp_path), registry=ToolRegistry(), session=SessionStorage(session_id="pe", storage_dir=str(tmp_path / "s")))
    await _drain(loop)
    await _drain(loop)
    assert len(built) == 1
    saved = loop.failover.begin_run()
    loop.failover.switch_model(None, "fallback-model")
    loop.failover.restore_model(saved)
    assert len(built) == 3 and loop.settings.model.model == saved[1]


# ---------------------------------------------------------------------------
# The plan agent
# ---------------------------------------------------------------------------


async def test_the_plan_agent_drafts_at_the_planning_effort_and_the_phase_settings_do_not_reach_it(tmp_path: Path) -> None:
    seen: list[Any] = []

    async def _run(config: Any, abort: Any) -> tuple[str, int]:
        seen.append(config)
        return "the plan", 0

    factories = LoopFactories(run_subagent=_run, subagent_registry=lambda **_: ToolRegistry())
    settings  = _settings(tmp_path, base="high", planning="max", implementation="medium", verification="low")
    assert await draft_plan("refactor the module", settings, factories) == "the plan"
    child = seen[0].settings.model
    assert child.reasoning_effort == "max"
    assert (child.effort_planning, child.effort_implementation, child.effort_verification) == ("", "", "")
    assert settings.model.reasoning_effort == "high"           # the session's own settings are untouched


async def test_without_a_planning_effort_the_plan_agent_keeps_the_configured_effort(tmp_path: Path) -> None:
    seen: list[Any] = []

    async def _run(config: Any, abort: Any) -> tuple[str, int]:
        seen.append(config)
        return "p", 0

    factories = LoopFactories(run_subagent=_run, subagent_registry=lambda **_: ToolRegistry())
    await draft_plan("refactor", _settings(tmp_path, base="high"), factories)
    assert seen[0].settings.model.reasoning_effort == "high"


# ---------------------------------------------------------------------------
# With the real Anthropic provider
# ---------------------------------------------------------------------------


class _Events:
    def __init__(self, events: list[Any]) -> None:
        self.events = events

    def __aiter__(self) -> AsyncIterator[Any]:
        async def _iter() -> AsyncIterator[Any]:
            for event in self.events:
                yield event
        return _iter()


def _text_reply() -> _Events:
    ns = SimpleNamespace
    return _Events([
        ns(type="content_block_start", content_block=ns(type="text", text="")),
        ns(type="content_block_delta", delta=ns(type="text_delta", text="done")),
        ns(type="message_delta", usage=ns(output_tokens=3), delta=ns(stop_reason="end_turn")),
    ])


async def test_on_a_model_with_turn_effort_the_phase_change_is_a_system_message_and_the_top_level_value_stays(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(AgentLoop, "build_system_prompt", lambda self: "system")
    settings = _settings(tmp_path, base="high", implementation="medium", verification="max")
    settings.model.provider = "anthropic"
    settings.model.model    = "claude-sonnet-5-5"
    settings.model.api_key  = "k"
    loop = AgentLoop(settings=settings, registry=ToolRegistry(), session=SessionStorage(session_id="pe", storage_dir=str(tmp_path / "s")))
    provider = loop.provider
    assert isinstance(provider, AnthropicProvider)
    create = AsyncMock(side_effect=lambda **_: _text_reply())
    provider._client = SimpleNamespace(messages=SimpleNamespace(create=create))  # type: ignore[assignment]
    loop.set_goal(_failing_goal(2))
    await _drain(loop)
    first, second = (call.kwargs for call in create.await_args_list)
    assert first["extra_body"]["output_config"]["effort"] == "medium"
    assert not [m for m in first["messages"] if m["role"] == "system"]
    assert second["extra_body"]["output_config"]["effort"] == "medium"
    assert [m["output_config"]["effort"] for m in second["messages"] if m["role"] == "system"] == ["max"]
    assert second["messages"][-1]["role"] == "user"
    loop.set_goal(None)
    await _drain(loop)
    third = create.await_args.kwargs
    assert loop.provider is provider                           # not rebuilt between prompts
    assert [m["output_config"]["effort"] for m in third["messages"] if m["role"] == "system"] == ["max", "medium"]
    assert third["extra_body"]["output_config"]["effort"] == "medium"


async def test_on_a_model_without_turn_effort_the_first_level_is_held(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(AgentLoop, "build_system_prompt", lambda self: "system")
    settings = _settings(tmp_path, base="high", implementation="medium", verification="max")
    settings.model.provider = "anthropic"
    settings.model.model    = "claude-opus-4-6"
    settings.model.api_key  = "k"
    loop = AgentLoop(settings=settings, registry=ToolRegistry(), session=SessionStorage(session_id="pe", storage_dir=str(tmp_path / "s")))
    provider = loop.provider
    assert isinstance(provider, AnthropicProvider)
    create = AsyncMock(side_effect=lambda **_: _text_reply())
    provider._client = SimpleNamespace(messages=SimpleNamespace(create=create))  # type: ignore[assignment]
    loop.set_goal(_failing_goal(2))
    await _drain(loop)
    efforts = [call.kwargs["extra_body"]["output_config"]["effort"] for call in create.await_args_list]
    assert efforts == ["medium", "medium"]
    assert all(m["role"] != "system" for call in create.await_args_list for m in call.kwargs["messages"])
