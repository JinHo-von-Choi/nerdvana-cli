"""Deferred tools: when they are deferred, how ToolSearch loads them and what the loop declares.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, ClassVar

import pytest

from nerdvana_cli.core.agent_loop import AgentLoop
from nerdvana_cli.core.session import SessionStorage
from nerdvana_cli.core.settings import NerdvanaSettings
from nerdvana_cli.core.tool import BaseTool, ToolContext, ToolRegistry
from nerdvana_cli.core.tool_index import ToolIndex, declaration_tokens
from nerdvana_cli.providers.base import ProviderEvent
from nerdvana_cli.tools.tool_search import ToolSearchArgs, ToolSearchTool
from nerdvana_cli.types import ToolResult


class _Mcp(BaseTool[Any]):
    tags: ClassVar[frozenset[str]] = frozenset({"mcp"})

    def __init__(self, name: str, description: str, size: int = 0) -> None:
        self.name             = name
        self.description_text = description
        self.input_schema     = {"type": "object", "properties": {f"p{n}": {"type": "string", "description": "x" * 40} for n in range(size)}}
        self.calls            = 0

    async def call(self, args: Any, context: Any, can_use_tool: Any = None, on_progress: Any = None) -> ToolResult:
        self.calls += 1
        return ToolResult(tool_use_id="", content=f"{self.name} ran")


class _Builtin(BaseTool[Any]):
    name             = "Plain"
    description_text = "a built-in tool"

    async def call(self, args: Any, context: Any, can_use_tool: Any = None, on_progress: Any = None) -> ToolResult:
        return ToolResult(tool_use_id="", content="plain")


def _big() -> list[_Mcp]:
    return [
        _Mcp("mcp__github__create_issue", "Create an issue in a GitHub repository", size=150),
        _Mcp("mcp__github__list_pulls", "List open pull requests of a repository", size=150),
        _Mcp("mcp__db__run_query", "Run a read-only SQL query against the database", size=150),
    ]


# ---------------------------------------------------------------------------
# The index
# ---------------------------------------------------------------------------


def test_small_mcp_sets_are_not_deferred_in_auto_mode_and_large_ones_are() -> None:
    small = [_Mcp("mcp__a__t", "tiny")]
    assert ToolIndex.build(small, "auto", 3000).deferred == {}
    large = _big()
    assert sum(declaration_tokens(t) for t in large) > 3000
    assert set(ToolIndex.build(large, "auto", 3000).deferred) == {t.name for t in large}


def test_always_defers_even_a_small_set_and_never_defers_a_large_one() -> None:
    small = [_Mcp("mcp__a__t", "tiny")]
    assert set(ToolIndex.build(small, "always").deferred) == {"mcp__a__t"}
    assert ToolIndex.build(_big(), "never").deferred == {}


def test_built_in_tools_are_never_deferred() -> None:
    assert ToolIndex.build([_Builtin(), *_big()], "always", 0).deferred.keys().isdisjoint({"Plain"})


def test_declared_hides_unloaded_tools_and_keeps_the_order_of_the_rest() -> None:
    tools = [_Builtin(), *_big()]
    index = ToolIndex.build(tools, "always")
    assert [t.name for t in index.declared(tools)] == ["Plain"]
    index.load([tools[2]])
    assert [t.name for t in index.declared(tools)] == ["Plain", "mcp__github__list_pulls"]


def test_select_names_tools_exactly_and_words_are_scored_with_names_counting_more() -> None:
    index = ToolIndex.build(_big(), "always")
    assert [t.name for t in index.search("select:mcp__db__run_query,missing")] == ["mcp__db__run_query"]
    assert index.search("mcp__db__run_query")[0].name == "mcp__db__run_query"
    found = [t.name for t in index.search("pull requests")]
    assert found[0] == "mcp__github__list_pulls"
    assert index.search("quantum") == []


def test_loading_reports_only_the_new_tools_and_the_index_lists_what_is_left() -> None:
    tools = _big()
    index = ToolIndex.build(tools, "always")
    assert len(index.index_lines()) == 3 and index.index_lines()[0].startswith("- mcp__db__run_query: Run a read-only")
    assert index.load(tools[:1]) == ["mcp__github__create_issue"]
    assert index.load(tools[:1]) == []
    assert len(index.index_lines()) == 2


# ---------------------------------------------------------------------------
# ToolSearch
# ---------------------------------------------------------------------------


async def test_tool_search_loads_matches_and_shows_their_parameters() -> None:
    tools  = _big()
    index  = ToolIndex.build(tools, "always")
    result = await ToolSearchTool(index).call(ToolSearchArgs("select:mcp__db__run_query"), ToolContext(cwd="."))
    assert not result.is_error and "mcp__db__run_query" in result.content and "Parameters:" in result.content
    assert index.loaded == {"mcp__db__run_query"}


async def test_tool_search_without_a_match_is_an_error_that_loads_nothing() -> None:
    index  = ToolIndex.build(_big(), "always")
    result = await ToolSearchTool(index).call(ToolSearchArgs("nothing like this"), ToolContext(cwd="."))
    assert result.is_error and index.loaded == set()


# ---------------------------------------------------------------------------
# In the loop
# ---------------------------------------------------------------------------


class _Script:
    def __init__(self, responses: list[list[ProviderEvent]]) -> None:
        self.responses = list(responses)
        self.declared: list[list[str]] = []
        self.systems:  list[str] = []

    async def stream(self, system_prompt: str, messages: Any, tools: Any) -> AsyncIterator[ProviderEvent]:
        self.declared.append([t.name for t in tools])
        self.systems.append(system_prompt)
        for event in self.responses.pop(0) if self.responses else [ProviderEvent(type="done", stop_reason="end_turn")]:
            yield event


def _call(tool_id: str, name: str, **arguments: Any) -> list[ProviderEvent]:
    return [
        ProviderEvent(type="tool_use_complete", tool_use_id=tool_id, tool_name=name, tool_input_complete=arguments),
        ProviderEvent(type="done", stop_reason="tool_use"),
    ]


def _loop(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, provider: _Script, mcp: list[_Mcp], **session: Any) -> AgentLoop:
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(AgentLoop, "create_provider_from_settings", lambda self: provider)
    registry = ToolRegistry()
    registry.register(_Builtin())
    for tool in mcp:
        registry.register(tool)
    settings     = NerdvanaSettings()
    settings.cwd = str(tmp_path)
    for key, value in session.items():
        setattr(settings.session, key, value)
    return AgentLoop(settings=settings, registry=registry, session=SessionStorage(session_id="idx", storage_dir=str(tmp_path / "s")))


async def _drain(loop: AgentLoop, prompt: str = "go") -> None:
    async for _ in loop.run(prompt):
        pass


async def test_large_mcp_sets_are_hidden_until_the_model_loads_them(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    mcp      = _big()
    provider = _Script([
        _call("c1", "ToolSearch", query="select:mcp__db__run_query"),
        _call("c2", "mcp__db__run_query"),
    ])
    loop = _loop(monkeypatch, tmp_path, provider, mcp)
    await _drain(loop)
    assert provider.declared[0] == ["Plain", "ToolSearch"]
    assert provider.declared[1] == ["Plain", "mcp__db__run_query", "ToolSearch"] or "mcp__db__run_query" in provider.declared[1]
    assert mcp[2].calls == 1
    assert "# Deferred tools" in provider.systems[0] and "mcp__db__run_query: Run a read-only" in provider.systems[0]


async def test_calling_an_unloaded_tool_is_refused_and_counted(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    mcp      = _big()
    provider = _Script([_call("c1", "mcp__db__run_query")])
    loop     = _loop(monkeypatch, tmp_path, provider, mcp)
    await _drain(loop)
    assert mcp[2].calls == 0
    assert loop.signal_summary()["tool_not_loaded"] == 1
    tool_messages = [m for m in loop.state.messages if m.role.value == "tool"]
    assert "select:mcp__db__run_query" in str(tool_messages[0].content)


async def test_what_was_loaded_stays_loaded_for_the_next_prompt(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    provider = _Script([_call("c1", "ToolSearch", query="select:mcp__db__run_query")])
    loop     = _loop(monkeypatch, tmp_path, provider, _big())
    await _drain(loop)
    await _drain(loop, "next")
    assert "mcp__db__run_query" in provider.declared[-1]
    assert "mcp__db__run_query: Run a read-only" in provider.systems[0]      # listed while it was not loaded
    assert "mcp__db__run_query: Run a read-only" not in provider.systems[-1]  # a later prompt no longer lists it


async def test_a_small_set_is_declared_as_before_and_no_search_tool_appears(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    provider = _Script([])
    loop     = _loop(monkeypatch, tmp_path, provider, [_Mcp("mcp__a__tiny", "tiny")])
    await _drain(loop)
    assert provider.declared[0] == ["Plain", "mcp__a__tiny"]
    assert "# Deferred tools" not in provider.systems[0]


async def test_never_keeps_everything_declared(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    provider = _Script([])
    loop     = _loop(monkeypatch, tmp_path, provider, _big(), defer_tools="never")
    await _drain(loop)
    assert len(provider.declared[0]) == 4 and "ToolSearch" not in provider.declared[0]


async def test_sub_agents_never_defer(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from nerdvana_cli.core.analytics import CallOrigin

    provider = _Script([])
    loop     = _loop(monkeypatch, tmp_path, provider, _big())
    loop.origin = CallOrigin(agent_id="a", agent_type="Explore")
    await _drain(loop)
    assert len(provider.declared[0]) == 4 and "ToolSearch" not in provider.declared[0]
