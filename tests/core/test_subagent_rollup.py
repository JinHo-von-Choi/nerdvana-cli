"""A finished sub-agent's tokens and signals are added to its parent's totals.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from nerdvana_cli.core.config.settings import NerdvanaSettings
from nerdvana_cli.core.delegation.subagent import run_subagent
from nerdvana_cli.core.loop.agent_loop import AgentLoop
from nerdvana_cli.core.loop.subagent_config import SubagentConfig
from nerdvana_cli.core.state.session import SessionStorage
from nerdvana_cli.core.tool import ToolRegistry


def _loop(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> AgentLoop:
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(AgentLoop, "create_provider_from_settings", lambda self: None)
    settings     = NerdvanaSettings()
    settings.cwd = str(tmp_path)
    return AgentLoop(settings=settings, registry=ToolRegistry(), session=SessionStorage(session_id="p", storage_dir=str(tmp_path / "s")))


def test_absorbing_adds_tokens_and_signal_counts_to_the_session(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    loop = _loop(monkeypatch, tmp_path)
    loop.limits.input_tokens = 100
    loop._signals["tool_error"] = 1
    loop.absorb_subagent(
        {"input_tokens": 40, "output_tokens": 5, "cache_read_tokens": 30, "cache_write_tokens": 2},
        {"tool_error": 2, "cas_rejected": 1},
    )
    assert loop.usage_summary() == {"input_tokens": 140, "output_tokens": 5, "cache_read_tokens": 30, "cache_write_tokens": 2}
    assert loop.signal_summary()["tool_error"] == 3 and loop.signal_summary()["cas_rejected"] == 1


def test_the_tool_context_hands_the_sub_agent_the_hook(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    loop = _loop(monkeypatch, tmp_path)
    assert loop._new_tool_context().state["absorb"] == loop.absorb_subagent


async def test_run_subagent_reports_even_when_it_is_aborted(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen: list[tuple[dict[str, int], dict[str, int]]] = []

    class _Child:
        last_stop = "completed"

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.wrap_up_at = 0

        async def run(self, prompt: str) -> Any:
            yield "first"
            yield "second"

        def session_cost_usd(self) -> float:
            return 0.25

        def usage_summary(self) -> dict[str, int]:
            return {"input_tokens": 7, "output_tokens": 3, "cache_read_tokens": 0, "cache_write_tokens": 0}

        def signal_summary(self) -> dict[str, int]:
            return {"tool_error": 1}

    monkeypatch.setattr("nerdvana_cli.core.delegation.subagent.AgentLoop", _Child)
    abort = asyncio.Event()
    abort.set()
    config = SubagentConfig(
        agent_id="a", name="Explore", prompt="p", settings=NerdvanaSettings(), registry=ToolRegistry(),
        absorb=lambda usage, counts: seen.append((usage, counts)),
    )
    text, tokens = await run_subagent(config, abort)
    assert text.endswith("[aborted]") and tokens == 0
    assert seen == [({"input_tokens": 7, "output_tokens": 3, "cache_read_tokens": 0, "cache_write_tokens": 0}, {"tool_error": 1})]
    assert config.cost_usd == pytest.approx(0.25)
