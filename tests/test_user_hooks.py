"""Regression tests for user hook loader and sticky session context."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from nerdvana_cli.core.hooks import HookContext, HookEngine, HookEvent, HookResult
from nerdvana_cli.core.user_hooks import load_user_hooks


class _StubSettings:
    def __init__(self, cwd: str):
        self.cwd = cwd


def test_load_user_hooks_returns_empty_when_no_dirs(tmp_path: Path):
    settings = _StubSettings(cwd=str(tmp_path))
    engine = HookEngine()
    result = load_user_hooks(engine, settings)
    assert result == []
    assert not engine.has_handlers(HookEvent.SESSION_START)


def test_load_user_hooks_loads_project_hook(tmp_path: Path):
    hook_dir = tmp_path / ".nerdvana" / "hooks"
    hook_dir.mkdir(parents=True)
    (hook_dir / "my_hook.py").write_text(textwrap.dedent('''
        from nerdvana_cli.core.hooks import HookEngine, HookEvent, HookContext, HookResult

        def _h(ctx):
            return HookResult(system_prompt_append="from-user-hook")

        def register(engine, settings):
            engine.register(HookEvent.SESSION_START, _h)
    '''))

    engine = HookEngine()
    settings = _StubSettings(cwd=str(tmp_path))
    loaded = load_user_hooks(engine, settings)

    assert len(loaded) == 1
    assert engine.has_handlers(HookEvent.SESSION_START)

    ctx = HookContext(event=HookEvent.SESSION_START)
    results = engine.fire(ctx)
    assert len(results) == 1
    assert results[0].system_prompt_append == "from-user-hook"


def test_load_user_hooks_skips_underscore_files(tmp_path: Path):
    hook_dir = tmp_path / ".nerdvana" / "hooks"
    hook_dir.mkdir(parents=True)
    (hook_dir / "_skip.py").write_text("raise RuntimeError('should not load')\n")

    engine = HookEngine()
    settings = _StubSettings(cwd=str(tmp_path))
    loaded = load_user_hooks(engine, settings)
    assert loaded == []


def test_load_user_hooks_swallows_register_failure(tmp_path: Path):
    hook_dir = tmp_path / ".nerdvana" / "hooks"
    hook_dir.mkdir(parents=True)
    (hook_dir / "bad.py").write_text(textwrap.dedent('''
        def register(engine, settings):
            raise RuntimeError("intentional")
    '''))

    engine = HookEngine()
    settings = _StubSettings(cwd=str(tmp_path))
    loaded = load_user_hooks(engine, settings)
    # bad hook is skipped, no exception escapes
    assert loaded == []


def test_hook_result_system_prompt_append_default():
    r = HookResult()
    assert r.system_prompt_append == ""


def test_session_start_context_injection_has_no_third_party_guidance():
    """builtin must not embed any third-party tool name or memory-system text.

    The builtin only injects nerdvana-cli's own information (tool list,
    session config). Anything tied to a particular MCP server, memory
    backend, or user convention belongs in a user hook.
    """
    from nerdvana_cli.core.builtin_hooks import session_start_context_injection
    import inspect

    src = inspect.getsource(session_start_context_injection)
    # No hardcoded MCP tool names
    assert "mcp__" not in src
    # No baked-in memory system instructions
    assert "MEMORY SYSTEM RULES" not in src


def test_session_start_context_injection_returns_system_prompt_append():
    """builtin hook must use system_prompt_append, not inject_messages."""
    from nerdvana_cli.core.builtin_hooks import session_start_context_injection

    class _Tool:
        name = "FileRead"

    class _Settings:
        class model:
            provider = "anthropic"
            model = "claude-sonnet-4"
        class session:
            max_context_tokens = 180000
            max_turns = 200

    ctx = HookContext(
        event=HookEvent.SESSION_START,
        settings=_Settings(),
        tools=[_Tool()],
    )
    result = session_start_context_injection(ctx)
    assert result.system_prompt_append != ""
    assert "FileRead" in result.system_prompt_append
    assert "anthropic" in result.system_prompt_append
    assert result.inject_messages == []
