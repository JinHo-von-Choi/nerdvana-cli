"""``nerdvana acp`` as a real subprocess: protocol frames on stdout only, in the right order, and a clean exit.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("acp")

from acp import spawn_agent_process, text_block
from typer.testing import CliRunner

from nerdvana_cli.main import app
from tests.acp.support import RecordingEditor, isolate

MAIN = Path(__file__).parent / "fake_agent_main.py"

ANSWER = [
    {"type": "content_delta", "content": "hello "},
    {"type": "content_delta", "content": "editor"},
    {"type": "usage", "usage": {"input_tokens": 10, "output_tokens": 2}},
    {"type": "done", "stop_reason": "end_turn"},
]
PRINTENV = [
    {"type": "tool_use_complete", "tool_use_id": "t1", "tool_name": "Bash", "tool_input_complete": {"command": "printenv"}},
    {"type": "done", "stop_reason": "tool_use"},
]


@pytest.fixture()
def world(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    isolate(monkeypatch, tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    commands = project / ".nerdvana" / "commands"
    commands.mkdir(parents=True)
    (commands / "ping.md").write_text("Ping $ARGUMENTS", encoding="utf-8")
    env = {
        "HOME":                     str(tmp_path),
        "NERDVANA_DATA_HOME":       str(tmp_path / "data"),
        "NERDVANA_NO_UPDATE_CHECK": "1",
        "ANTHROPIC_API_KEY":        "test-key",
        "PYTHONPATH":               os.pathsep.join(sys.path),
    }
    return {"project": project, "env": env, "tmp": tmp_path}


def _script(world: dict[str, Any], turns: list[list[dict[str, Any]]]) -> None:
    path = world["tmp"] / "script.json"
    path.write_text(json.dumps(turns), encoding="utf-8")
    world["env"]["ACP_FAKE_SCRIPT"] = str(path)


async def test_the_sdk_client_drives_the_agent_through_a_permission_request_over_stdio(world: dict[str, Any]) -> None:
    _script(world, [PRINTENV, ANSWER])
    editor = RecordingEditor(["allow_once"])
    async with spawn_agent_process(editor, sys.executable, str(MAIN), env=world["env"], cwd=world["project"]) as (conn, process):
        init    = await conn.initialize(protocol_version=1)
        session = await conn.new_session(cwd=str(world["project"]), mcp_servers=[])
        result  = await asyncio.wait_for(conn.prompt(prompt=[text_block("go")], session_id=session.session_id), timeout=30)
    assert init.protocol_version == 1
    assert result.stop_reason == "end_turn"
    assert editor.permissions[0]["tool_call"].tool_call_id == "t1"
    assert editor.message_text() == "hello editor"
    assert [u.status for u in editor.of_kind("tool_call_update")] == ["in_progress", "completed"]
    assert process.returncode == 0


async def test_raw_frames_are_clean_ordered_and_end_with_the_process_exiting(world: dict[str, Any]) -> None:
    _script(world, [ANSWER])
    env = {**os.environ, **world["env"]}
    process = await asyncio.create_subprocess_exec(
        sys.executable, str(MAIN),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        env=env, cwd=str(world["project"]),
    )
    assert process.stdin is not None
    assert process.stdout is not None

    async def send(frame: dict[str, Any]) -> None:
        process.stdin.write((json.dumps({"jsonrpc": "2.0", **frame}) + "\n").encode())  # type: ignore[union-attr]
        await process.stdin.drain()  # type: ignore[union-attr]

    async def read_until(request_id: int) -> list[dict[str, Any]]:
        frames: list[dict[str, Any]] = []
        while True:
            line = await asyncio.wait_for(process.stdout.readline(), timeout=30)  # type: ignore[union-attr]
            assert line, "the agent closed standard output early"
            frame = json.loads(line)  # every line of standard output must be one JSON frame
            frames.append(frame)
            if frame.get("id") == request_id:
                return frames

    await send({"id": 1, "method": "initialize", "params": {"protocolVersion": 1, "clientCapabilities": {}}})
    init = (await read_until(1))[-1]["result"]
    assert init["protocolVersion"] == 1
    assert init["agentCapabilities"]["loadSession"] is True
    assert init["agentInfo"]["name"] == "nerdvana"

    await send({"id": 2, "method": "session/new", "params": {"cwd": str(world["project"]), "mcpServers": []}})
    created   = await read_until(2)
    session   = created[-1]["result"]["sessionId"]
    await send({"id": 3, "method": "session/prompt", "params": {"sessionId": session, "prompt": [{"type": "text", "text": "/ping now"}]}})
    prompted  = await read_until(3)
    commands  = [f for f in prompted if f.get("method") == "session/update" and f["params"]["update"]["sessionUpdate"] == "available_commands_update"]
    assert all("method" not in frame for frame in created)  # no update about a session before the answer that names it
    assert commands[0]["params"]["update"]["availableCommands"][0]["name"] == "ping"
    text = "".join(
        f["params"]["update"]["content"]["text"]
        for f in prompted
        if f.get("method") == "session/update" and f["params"]["update"]["sessionUpdate"] == "agent_message_chunk"
    )
    assert text == "hello editor"
    assert prompted[-1]["result"]["stopReason"] == "end_turn"
    assert prompted[-1]["result"]["usage"]["inputTokens"] == 10

    process.stdin.close()
    assert await asyncio.wait_for(process.wait(), timeout=30) == 0
    assert process.stderr is not None
    diagnostics = (await process.stderr.read()).decode()
    assert "stray line on standard output" in diagnostics  # what the process prints lands on standard error
    assert "stray bytes on descriptor 1" in diagnostics


def test_the_command_checks_its_options_before_serving() -> None:
    result = CliRunner().invoke(app, ["acp", "--approval-mode", "bogus"])
    assert result.exit_code == 2
