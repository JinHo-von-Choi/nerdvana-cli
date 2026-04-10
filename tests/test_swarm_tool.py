"""Tests for SwarmTool (model-callable swarm entry point)."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch

from nerdvana_cli.core.settings    import NerdvanaSettings
from nerdvana_cli.core.task_state  import TaskRegistry
from nerdvana_cli.core.tool        import ToolContext
from nerdvana_cli.tools.swarm_tool import SwarmTool


@pytest.mark.asyncio
async def test_swarm_tool_dispatches_tasks_and_returns_summary() -> None:
    settings      = NerdvanaSettings()
    task_registry = TaskRegistry()
    tool          = SwarmTool(settings=settings, task_registry=task_registry)
    ctx           = ToolContext(cwd=".", task_registry=task_registry)

    with patch(
        "nerdvana_cli.tools.swarm_tool.run_swarm",
        new_callable=AsyncMock,
        return_value={"worker-1@test": "result A", "worker-2@test": "result B"},
    ):
        result = await tool.call(
            tool.args_class(  # type: ignore[call-arg]
                team_name = "test",
                tasks     = [
                    {"name": "worker-1", "prompt": "task A"},
                    {"name": "worker-2", "prompt": "task B"},
                ],
            ),
            ctx,
            can_use_tool=None,
        )

    assert not result.is_error
    assert "worker-1@test" in result.content
    assert "result A" in result.content
