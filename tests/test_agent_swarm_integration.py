"""Integration test: all agent/swarm tools appear in the registry."""

from __future__ import annotations

from nerdvana_cli.core.settings  import NerdvanaSettings
from nerdvana_cli.tools.registry import create_tool_registry


def test_create_tool_registry_with_settings_includes_agent_swarm_tools() -> None:
    settings   = NerdvanaSettings()
    registry   = create_tool_registry(settings=settings)
    tool_names = {t.name for t in registry.all_tools()}

    assert "Agent"       in tool_names
    assert "Swarm"       in tool_names
    assert "TeamCreate"  in tool_names
    assert "SendMessage" in tool_names
    assert "TaskGet"     in tool_names
    assert "TaskStop"    in tool_names
