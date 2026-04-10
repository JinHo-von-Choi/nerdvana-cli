from nerdvana_cli.core.builtin_hooks import session_start_context_injection
from nerdvana_cli.core.hooks import HookContext, HookEvent
from unittest.mock import MagicMock


def test_injects_tool_list():
    tool1, tool2 = MagicMock(), MagicMock()
    tool1.name, tool2.name = "Bash", "FileRead"
    ctx = HookContext(
        event=HookEvent.SESSION_START, tools=[tool1, tool2],
        settings=MagicMock(model=MagicMock(provider="anthropic", model="test"), session=MagicMock(max_context_tokens=200000, max_turns=200)),
    )
    result = session_start_context_injection(ctx)
    assert "Bash" in result.system_prompt_append
    assert "FileRead" in result.system_prompt_append
    assert result.inject_messages == []


def test_no_third_party_guidance_for_arbitrary_mcp_tools():
    """builtin must not append guidance text targeted at any specific
    MCP tool, regardless of which tool happens to be registered. The
    only thing the builtin knows about a tool is its name in the list.
    """
    memory_tool = MagicMock()
    memory_tool.name = "mcp__example_provider__context"
    ctx = HookContext(
        event=HookEvent.SESSION_START, tools=[memory_tool],
        settings=MagicMock(model=MagicMock(provider="anthropic", model="test"), session=MagicMock(max_context_tokens=200000, max_turns=200)),
    )
    result = session_start_context_injection(ctx)
    assert "MEMORY SYSTEM RULES" not in result.system_prompt_append
    # The tool name appears in the tool list, but nothing more is injected
    # about it — no usage instructions, no priority defaults, no rules.
    append = result.system_prompt_append
    assert memory_tool.name in append
    assert "IMMEDIATELY call" not in append
    assert "importance" not in append.lower()


def test_empty_no_inject():
    ctx = HookContext(event=HookEvent.SESSION_START, tools=[], settings=None)
    result = session_start_context_injection(ctx)
    assert result.system_prompt_append == ""
    assert len(result.inject_messages) == 0
