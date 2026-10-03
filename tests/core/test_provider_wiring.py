"""What the loop hands the Anthropic provider: its settings, and the tool search that is left to the server.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, ClassVar

import pytest

from nerdvana_cli.cli.bootstrap import loop_factories
from nerdvana_cli.core.agent_loop import AgentLoop
from nerdvana_cli.core.config.settings import NerdvanaSettings
from nerdvana_cli.core.context.loop_context import defer_mode
from nerdvana_cli.core.state.session import SessionStorage
from nerdvana_cli.core.tool import BaseTool, ToolRegistry
from nerdvana_cli.providers.anthropic_provider import AnthropicProvider
from nerdvana_cli.providers.base import ProviderEvent
from nerdvana_cli.types import ToolResult


class _Mcp(BaseTool[Any]):
    tags: ClassVar[frozenset[str]] = frozenset({"mcp"})

    def __init__(self, name: str) -> None:
        self.name             = name
        self.description_text = f"{name} tool"
        self.input_schema     = {"type": "object", "properties": {f"p{n}": {"type": "string", "description": "x" * 40} for n in range(150)}}

    async def call(self, args: Any, context: Any, can_use_tool: Any = None, on_progress: Any = None) -> ToolResult:
        return ToolResult(tool_use_id="", content="ran")


class _Plain(BaseTool[Any]):
    name             = "Plain"
    description_text = "a built-in tool"

    async def call(self, args: Any, context: Any, can_use_tool: Any = None, on_progress: Any = None) -> ToolResult:
        return ToolResult(tool_use_id="", content="plain")


class _Declared:
    def __init__(self) -> None:
        self.declared: list[list[str]] = []

    async def stream(self, system_prompt: str, messages: Any, tools: Any) -> AsyncIterator[ProviderEvent]:
        self.declared.append([t.name for t in tools])
        yield ProviderEvent(type="done", stop_reason="end_turn")


def _settings(tmp_path: Path, provider: str = "anthropic", model: str = "claude-sonnet-5-5", search: str = "off", defer: str = "always") -> NerdvanaSettings:
    settings = NerdvanaSettings()
    settings.cwd                        = str(tmp_path)
    settings.model.provider             = provider
    settings.model.model                = model
    settings.model.anthropic_tool_search = search  # type: ignore[assignment]
    settings.session.defer_tools        = defer  # type: ignore[assignment]
    return settings


def _loop(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, settings: NerdvanaSettings, provider: _Declared) -> AgentLoop:
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(AgentLoop, "create_provider_from_settings", lambda self: provider)
    registry = ToolRegistry()
    registry.register(_Plain())
    registry.register(_Mcp("mcp__db__run_query"))
    return AgentLoop(settings=settings, registry=registry, session=SessionStorage(session_id="w", storage_dir=str(tmp_path / "s")), factories=loop_factories())


async def _declared_names(loop: AgentLoop, provider: _Declared) -> list[str]:
    async for _ in loop.run("go"):
        pass
    return provider.declared[0]


@pytest.mark.parametrize(("search", "provider", "model", "expected"), [
    ("bm25",  "anthropic", "claude-sonnet-5-5", "never"),
    ("regex", "anthropic", "claude-opus-5-5",   "never"),
    ("off",   "anthropic", "claude-sonnet-5-5", "always"),
    ("bm25",  "openai",    "gpt-4.1",           "always"),
    ("bm25",  "anthropic", "MiniMax-M2",        "always"),
])
def test_the_local_defer_mode_yields_to_the_server_side_search(tmp_path: Path, search: str, provider: str, model: str, expected: str) -> None:
    assert defer_mode(_settings(tmp_path, provider, model, search)) == expected


def test_the_mode_follows_the_model_name_when_the_provider_is_not_set(tmp_path: Path) -> None:
    settings = _settings(tmp_path, provider="", model="claude-sonnet-5-5", search="bm25")
    assert defer_mode(settings) == "never"


async def test_with_server_search_on_the_loop_declares_the_mcp_tools_and_adds_no_local_search(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    provider = _Declared()
    loop     = _loop(monkeypatch, tmp_path, _settings(tmp_path, search="bm25"), provider)
    names    = await _declared_names(loop, provider)
    assert "mcp__db__run_query" in names and "ToolSearch" not in names


async def test_without_it_the_local_tool_search_still_defers_them(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    provider = _Declared()
    loop     = _loop(monkeypatch, tmp_path, _settings(tmp_path, search="off"), provider)
    names    = await _declared_names(loop, provider)
    assert "mcp__db__run_query" not in names and "ToolSearch" in names


async def test_on_another_provider_the_local_tool_search_still_defers_them(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    provider = _Declared()
    loop     = _loop(monkeypatch, tmp_path, _settings(tmp_path, "openai", "gpt-4.1", search="bm25"), provider)
    names    = await _declared_names(loop, provider)
    assert "mcp__db__run_query" not in names and "ToolSearch" in names


def test_the_provider_is_built_with_the_server_side_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    settings = _settings(tmp_path, search="regex")
    settings.model.anthropic_compaction = "on"
    settings.model.api_key              = "k"
    loop     = AgentLoop(settings=settings, registry=ToolRegistry(), session=SessionStorage(session_id="w", storage_dir=str(tmp_path / "s")))
    provider = loop.provider
    assert isinstance(provider, AnthropicProvider)
    assert provider.config.anthropic_tool_search == "regex"
    assert provider.config.anthropic_compaction == "on"
    assert provider.supports_server_compaction is True
