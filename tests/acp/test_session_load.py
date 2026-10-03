"""Loading a recorded session through the ACP agent.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("acp")

from acp import text_block
from acp.exceptions import RequestError

from tests.acp.support import RecordingEditor, ScriptedProvider, answer, connected, isolate, make_agent, use_provider


@pytest.fixture()
def project(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    isolate(monkeypatch, tmp_path)
    root = tmp_path / "project"
    root.mkdir()
    monkeypatch.chdir(root)
    return root


async def test_load_session_replays_the_conversation_and_continues_it(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    use_provider(monkeypatch, ScriptedProvider([answer("first ", "answer")]))
    async with connected(make_agent(), RecordingEditor()) as conn:
        session = await conn.new_session(cwd=str(project), mcp_servers=[])
        await conn.prompt(prompt=[text_block("question one")], session_id=session.session_id)

    seen: list[list[Any]] = []

    class _Spy(ScriptedProvider):
        async def stream(self, system_prompt: str, messages: Any, tools: Any):  # type: ignore[no-untyped-def,override]
            seen.append([m["content"] for m in messages])
            async for event in super().stream(system_prompt, messages, tools):
                yield event

    use_provider(monkeypatch, _Spy([answer("second answer")]))
    editor = RecordingEditor()
    async with connected(make_agent(), editor) as conn:
        loaded = await conn.load_session(cwd=str(project), session_id=session.session_id, mcp_servers=[])
        assert loaded is not None
        assert [u.content.text for u in editor.of_kind("user_message_chunk")] == ["question one"]
        assert [u.content.text for u in editor.of_kind("agent_message_chunk")] == ["first answer"]
        await conn.prompt(prompt=[text_block("question two")], session_id=session.session_id)
    assert seen[0][0] == "question one"
    assert seen[0][-1] == "question two"


async def test_load_session_of_an_unknown_session_is_not_found(project: Path) -> None:
    async with connected(make_agent(), RecordingEditor()) as conn:
        with pytest.raises(RequestError) as missing:
            await conn.load_session(cwd=str(project), session_id="no-such-session", mcp_servers=[])
        with pytest.raises(RequestError) as unsafe:
            await conn.load_session(cwd=str(project), session_id="../escape", mcp_servers=[])
    assert missing.value.code == -32002
    assert unsafe.value.code == -32002
