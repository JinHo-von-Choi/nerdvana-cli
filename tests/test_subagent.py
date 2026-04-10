import asyncio
import pytest
from unittest.mock import MagicMock, patch

from nerdvana_cli.core.subagent import SubagentConfig, run_subagent
from nerdvana_cli.core.settings import NerdvanaSettings
from nerdvana_cli.core.tool import ToolRegistry


@pytest.mark.asyncio
async def test_run_subagent_returns_output() -> None:
    settings = NerdvanaSettings()
    registry = ToolRegistry()

    async def _fake_run(prompt: str):
        yield "hello"
        yield " world"

    config = SubagentConfig(
        agent_id="test-001",
        name="general-purpose",
        prompt="say hello",
        settings=settings,
        registry=registry,
    )
    abort = asyncio.Event()

    with patch(
        "nerdvana_cli.core.subagent.AgentLoop",
        return_value=MagicMock(run=_fake_run),
    ):
        result = await run_subagent(config, abort)

    assert result == "hello world"


@pytest.mark.asyncio
async def test_run_subagent_filters_protocol_markers() -> None:
    settings = NerdvanaSettings()
    registry = ToolRegistry()

    async def _fake_run(prompt: str):
        yield "\x00TOOL:Bash ls"
        yield "output line"
        yield "\x00TOOL_DONE:Bash [done]"

    config = SubagentConfig(
        agent_id="test-002",
        name="general-purpose",
        prompt="list files",
        settings=settings,
        registry=registry,
    )
    abort = asyncio.Event()

    with patch(
        "nerdvana_cli.core.subagent.AgentLoop",
        return_value=MagicMock(run=_fake_run),
    ):
        result = await run_subagent(config, abort)

    assert "\x00TOOL" not in result
    assert "output line" in result


@pytest.mark.asyncio
async def test_run_subagent_respects_abort() -> None:
    settings = NerdvanaSettings()
    registry = ToolRegistry()

    abort = asyncio.Event()

    async def _fake_run(prompt: str):
        abort.set()
        yield "chunk1"
        yield "chunk2"

    config = SubagentConfig(
        agent_id="test-003",
        name="general-purpose",
        prompt="long task",
        settings=settings,
        registry=registry,
    )

    with patch(
        "nerdvana_cli.core.subagent.AgentLoop",
        return_value=MagicMock(run=_fake_run),
    ):
        result = await run_subagent(config, abort)

    assert "[aborted]" in result
