"""The start of every request (tool declarations, then the system prompt) must not change between turns.

Providers cache a request from its first byte, so one changed byte in the system prompt or in a tool
declaration makes the whole conversation after it a cache miss. These tests build the prefix more than
once, across simulated turns and prompts and across processes, and require the same bytes each time.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from nerdvana_cli.core.agent_loop import AgentLoop
from nerdvana_cli.core.config.settings import NerdvanaSettings
from nerdvana_cli.core.context import prompts
from nerdvana_cli.core.session import SessionStorage
from nerdvana_cli.providers.anthropic_provider import AnthropicProvider, with_cache_breakpoints
from nerdvana_cli.providers.base import ProviderConfig, ProviderName
from nerdvana_cli.providers.openai_provider import OpenAIProvider
from nerdvana_cli.tools.registry import create_tool_registry

_SESSION_ID = "prefix-stability-session"

# What a prefix must never carry: a date or clock time, a uuid, a long random-looking hex id.
_VOLATILE = {
    "date":       re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),
    "clock time": re.compile(r"\b\d{1,2}:\d{2}:\d{2}\b"),
    "uuid":       re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"),
    "hex id":     re.compile(r"\b[0-9a-f]{24,}\b"),
}

# Builds the prefix of a fresh process and prints one digest of it; run under different hash seeds.
_DIGEST_SCRIPT = """
import hashlib, json, sys
from nerdvana_cli.core.agent_loop import AgentLoop
from nerdvana_cli.core.session import SessionStorage
from nerdvana_cli.core.config.settings import NerdvanaSettings
from nerdvana_cli.tools.registry import create_tool_registry

settings     = NerdvanaSettings()
settings.cwd = sys.argv[1]
registry     = create_tool_registry(settings=settings)
loop         = AgentLoop(settings=settings, registry=registry, session=SessionStorage(session_id="digest", storage_dir=sys.argv[2]))
loop._prepare_tools()
prefix = json.dumps(registry.tool_schemas(), ensure_ascii=False) + loop.build_system_prompt()
print(hashlib.sha256(prefix.encode()).hexdigest())
"""


@pytest.fixture()
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty project directory, with the user's home and data home kept out of the prompt."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    prompts.clear_git_info_cache()
    directory = tmp_path / "project"
    directory.mkdir()
    return directory


def _loop(project: Path, tmp_path: Path) -> AgentLoop:
    settings     = NerdvanaSettings()
    settings.cwd = str(project)
    return AgentLoop(
        settings = settings,
        registry = create_tool_registry(settings=settings),
        session  = SessionStorage(session_id=_SESSION_ID, storage_dir=str(tmp_path / "sessions")),
    )


def _declarations(loop: AgentLoop) -> list[dict[str, Any]]:
    """The tool declarations of the next request, in the shape the Anthropic API receives them."""
    provider = AnthropicProvider(ProviderConfig(provider=ProviderName.ANTHROPIC, api_key="k", model="claude-sonnet-5-5"))
    _, api_tools, _ = provider._prepare("system", [], loop._declared(loop._prepare_tools()))
    return api_tools


def test_the_system_prompt_is_identical_across_builds_and_simulated_turns(project: Path, tmp_path: Path) -> None:
    loop      = _loop(project, tmp_path)
    prompt_of = []
    for _ in range(3):
        loop._prepare_tools()
        prompt_of.append(loop.build_system_prompt())
    assert prompt_of[0]
    assert prompt_of[0] == prompt_of[1] == prompt_of[2]


def test_tool_declarations_are_identical_in_content_order_and_key_order(project: Path, tmp_path: Path) -> None:
    loop   = _loop(project, tmp_path)
    first  = _declarations(loop)
    second = _declarations(loop)
    # No sort_keys: the order the keys were inserted in is part of the bytes that are cached.
    assert json.dumps(first, ensure_ascii=False) == json.dumps(second, ensure_ascii=False)
    registered = [tool.name for tool in loop.registry.all_tools() if tool.name != "ToolSearch"]
    assert [tool["name"] for tool in first][: len(registered)] == registered
    assert [list(tool) for tool in first[:-1]] == [["name", "description", "input_schema"]] * (len(first) - 1)


def test_the_openai_declarations_are_identical_across_builds(project: Path, tmp_path: Path) -> None:
    loop     = _loop(project, tmp_path)
    provider = OpenAIProvider(ProviderConfig(provider=ProviderName.OPENAI, api_key="k", model="m"))
    first    = provider._build_tools(loop._declared(loop._prepare_tools()))
    second   = provider._build_tools(loop._declared(loop._prepare_tools()))
    assert first
    assert json.dumps(first, ensure_ascii=False) == json.dumps(second, ensure_ascii=False)


def test_the_cache_breakpoints_land_on_the_same_blocks_every_turn(project: Path, tmp_path: Path) -> None:
    loop  = _loop(project, tmp_path)
    tools = [{"name": tool["name"], "description": tool["description"], "input_schema": tool["input_schema"]} for tool in _declarations(loop)]
    marks = []
    for turn in range(3):
        messages = [{"role": "user", "content": [{"type": "text", "text": f"turn {turn}"}]}]
        system, marked, _ = with_cache_breakpoints(loop.build_system_prompt(), tools, messages)
        marks.append((system[0]["text"], [tool["name"] for tool in marked if "cache_control" in tool]))
    assert marks[0] == marks[1] == marks[2]
    assert marks[0][1] == [tools[-1]["name"]]


@pytest.mark.parametrize("kind", sorted(_VOLATILE))
def test_the_prefix_carries_no_timestamp_or_random_id(project: Path, tmp_path: Path, kind: str) -> None:
    loop   = _loop(project, tmp_path)
    prefix = json.dumps(_declarations(loop), ensure_ascii=False) + loop.build_system_prompt()
    assert _VOLATILE[kind].findall(prefix) == []
    assert _SESSION_ID not in prefix
    assert str(os.getpid()) not in re.findall(r"\b\d+\b", loop.build_system_prompt())


def test_the_prefix_is_the_same_in_processes_with_different_hash_seeds(project: Path, tmp_path: Path) -> None:
    """Iterating a set or a dict built from one would reorder the prefix from process to process."""
    digests = set()
    for seed in ("1", "2"):
        env    = {**os.environ, "PYTHONHASHSEED": seed}
        result = subprocess.run(
            [sys.executable, "-c", _DIGEST_SCRIPT, str(project), str(tmp_path / "digest-sessions")],
            capture_output=True, text=True, env=env, check=True, timeout=120,
        )
        digests.add(result.stdout.strip())
    assert len(digests) == 1


def test_a_change_in_the_working_tree_between_prompts_does_not_change_the_prefix(project: Path, tmp_path: Path) -> None:
    """Each prompt rebuilds the system prompt; the files an earlier prompt changed must not rewrite it."""
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    loop = _loop(project, tmp_path)
    before = loop.build_system_prompt()
    (project / "edited.py").write_text("x = 1\n", encoding="utf-8")
    prompts.clear_git_info_cache()   # what the passing of the cache's time-to-live does
    assert loop.build_system_prompt() == before
