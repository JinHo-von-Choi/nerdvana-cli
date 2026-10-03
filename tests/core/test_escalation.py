"""Escalation: a stronger model takes over, once, when the run's signals reach their thresholds.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from nerdvana_cli.core import signals
from nerdvana_cli.core.agent_loop import AgentLoop
from nerdvana_cli.core.session import SessionStorage
from nerdvana_cli.core.settings import NerdvanaSettings
from nerdvana_cli.core.tool import ToolRegistry
from nerdvana_cli.providers.base import ProviderEvent
from nerdvana_cli.types import Message, Role

# ---------------------------------------------------------------------------
# The decision
# ---------------------------------------------------------------------------

THRESHOLDS = {"verify_failed": 1, "cas_rejected": 3}


def test_the_first_signal_over_its_limit_is_the_reason() -> None:
    assert signals.escalation_reason({"cas_rejected": 3, "verify_failed": 1}, THRESHOLDS) == "verify_failed x1"
    assert signals.escalation_reason({"cas_rejected": 3}, THRESHOLDS) == "cas_rejected x3"


def test_below_the_limits_or_without_limits_nothing_happens() -> None:
    assert signals.escalation_reason({"cas_rejected": 2}, THRESHOLDS) == ""
    assert signals.escalation_reason({"cas_rejected": 99}, {}) == ""
    assert signals.escalation_reason({"cas_rejected": 99}, {"cas_rejected": 0}) == ""


# ---------------------------------------------------------------------------
# In the loop
# ---------------------------------------------------------------------------


class _Quiet:
    def __init__(self) -> None:
        self.payloads: list[list[dict[str, Any]]] = []

    async def stream(self, system_prompt: str, messages: Any, tools: Any) -> AsyncIterator[ProviderEvent]:
        self.payloads.append([dict(m) for m in messages])
        yield ProviderEvent(type="content_delta", content="ok")
        yield ProviderEvent(type="done", stop_reason="end_turn")


def _loop(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, **session: Any) -> tuple[AgentLoop, list[tuple[str | None, str]]]:
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(AgentLoop, "create_provider_from_settings", lambda self: _Quiet())
    monkeypatch.setattr(AgentLoop, "build_system_prompt", lambda self: "system")
    settings     = NerdvanaSettings()
    settings.cwd = str(tmp_path)
    settings.model.provider = "anthropic"
    settings.model.model    = "claude-haiku-4-5-20251001"
    for key, value in session.items():
        setattr(settings.session, key, value)
    loop     = AgentLoop(settings=settings, registry=ToolRegistry(), session=SessionStorage(session_id="esc", storage_dir=str(tmp_path / "s")))
    switched: list[tuple[str | None, str]] = []

    def record(provider: str | None, model: str) -> None:
        switched.append((provider, model))
        settings.model.model = model

    monkeypatch.setattr(loop.failover, "switch_model", record)
    return loop, switched


async def _drain(loop: AgentLoop) -> str:
    return "".join([chunk async for chunk in loop.run("go")])


async def test_nothing_changes_without_an_escalation_model(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    loop, switched = _loop(monkeypatch, tmp_path)
    loop._signals[signals.VERIFY_FAILED] += 5
    await _drain(loop)
    assert switched == []


async def test_the_model_is_switched_once_when_a_signal_reaches_its_limit(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    loop, switched = _loop(monkeypatch, tmp_path, escalation_model="claude-opus-5-5")
    loop._signals[signals.VERIFY_FAILED] += 1
    output = await _drain(loop)
    assert switched == [(None, "claude-opus-5-5")]
    assert "Escalating to anthropic:claude-opus-5-5: verify_failed x1" in output
    assert loop.signal_summary()["escalated"] == 1
    loop._signals[signals.VERIFY_FAILED] += 1
    await _drain(loop)
    assert len(switched) == 1  # once per session


async def test_the_escalated_model_stays_for_the_next_prompt(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    loop, _ = _loop(monkeypatch, tmp_path, escalation_model="claude-opus-5-5")
    loop._signals[signals.VERIFY_FAILED] += 1
    await _drain(loop)
    assert loop.settings.model.model == "claude-opus-5-5"
    await _drain(loop)
    assert loop.settings.model.model == "claude-opus-5-5"


async def test_without_an_escalation_the_model_is_restored_after_the_prompt(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    loop, _ = _loop(monkeypatch, tmp_path, escalation_model="claude-opus-5-5")
    await _drain(loop)
    assert loop.settings.model.model == "claude-haiku-4-5-20251001"


async def test_below_the_limits_the_model_stays(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    loop, switched = _loop(monkeypatch, tmp_path, escalation_model="claude-opus-5-5")
    loop.tool_executor.signals[signals.CAS_REJECTED] += 2
    await _drain(loop)
    assert switched == []


async def test_a_provider_without_a_credential_is_skipped_and_not_retried(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    loop, switched = _loop(monkeypatch, tmp_path, escalation_model="openai:gpt-4.1")
    loop._signals[signals.VERIFY_FAILED] += 1
    output = await _drain(loop)
    assert switched == [] and "Escalating" not in output
    await _drain(loop)
    assert switched == []


async def test_another_provider_with_a_credential_is_used(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    loop, switched = _loop(monkeypatch, tmp_path, escalation_model="openai:gpt-4.1")
    loop._signals[signals.VERIFY_FAILED] += 1
    await _drain(loop)
    assert switched == [("openai", "gpt-4.1")]


async def test_thinking_blocks_of_the_old_model_are_not_carried_to_the_new_one(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    loop, _ = _loop(monkeypatch, tmp_path, escalation_model="claude-opus-5-5")
    loop.state.messages.append(Message(role=Role.ASSISTANT, content="x", provider_blocks=[{"type": "thinking", "thinking": "t", "signature": "s"}]))
    loop._signals[signals.VERIFY_FAILED] += 1
    await _drain(loop)
    assert all(not m.provider_blocks for m in loop.state.messages)


def test_the_default_thresholds_cover_the_main_signs_of_trouble() -> None:
    assert set(NerdvanaSettings().session.escalation_signals) == {"verify_failed", "repeat_refused", "cas_rejected", "new_diagnostics"}
