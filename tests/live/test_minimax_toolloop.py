"""Live multi-turn tool use against MiniMax: a tool result must reach the next request intact.

Single-reply smoke tests cannot catch a conversion that drops a tool call or its result; this runs the agent
loop with a tool whose answer the model cannot guess, so the final reply proves the result came back.
Skipped without MINIMAX_API_KEY.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

import pytest

from nerdvana_cli.core.agent_loop import AgentLoop
from nerdvana_cli.core.config.settings import NerdvanaSettings
from nerdvana_cli.core.session import SessionStorage
from nerdvana_cli.core.tool import BaseTool, ToolContext, ToolRegistry
from nerdvana_cli.types import ToolResult

SECRET = "violet-4417-harbor"


class _Lookup(BaseTool[Any]):
    name             = "LookupCode"
    description_text = "Returns the access code of a named door. Call it with the door name."
    input_schema: dict[str, Any] = {"type": "object", "properties": {"door": {"type": "string"}}, "required": ["door"]}

    def __init__(self) -> None:
        self.calls: list[str] = []

    def parse_args(self, tool_input: dict[str, Any]) -> Any:
        return tool_input

    async def call(self, args: Any, context: ToolContext, can_use_tool: Any = None, on_progress: Any = None) -> ToolResult:
        self.calls.append(str(args.get("door")))
        return ToolResult(tool_use_id="", content=f"The code of door {args.get('door')} is {SECRET}.")


@pytest.mark.live
def test_smoke_minimax_toolloop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The model calls the tool, and its next reply quotes what the tool returned."""
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    settings = NerdvanaSettings()
    settings.cwd = str(tmp_path)
    settings.model.provider = "minimax"
    settings.model.model    = "MiniMax-M3"
    settings.model.api_key  = os.environ["MINIMAX_API_KEY"]
    settings.session.max_turns = 6
    tool     = _Lookup()
    registry = ToolRegistry()
    registry.register(tool)
    loop = AgentLoop(settings=settings, registry=registry, session=SessionStorage(session_id="live-tools", storage_dir=str(tmp_path / "s")))

    async def _run() -> str:
        return "".join([c async for c in loop.run("Use the LookupCode tool for the door named 'north' and tell me the code it returns.")])

    output = asyncio.run(asyncio.wait_for(_run(), timeout=120))
    assert tool.calls, "the model never called the tool"
    assert SECRET in output or any(SECRET in str(m.content) for m in loop.state.messages if m.role.value == "assistant"), output
    assert loop.last_stop == "completed"
