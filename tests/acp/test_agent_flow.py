"""The ACP agent over an in-memory connection: initialization, sessions, prompts, tool calls and permissions.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("acp")

from acp import text_block
from acp.exceptions import RequestError

from nerdvana_cli.providers.base import ProviderEvent
from tests.acp.support import (
    RecordingEditor,
    ScriptedProvider,
    answer,
    connected,
    isolate,
    make_agent,
    tool_turn,
    use_provider,
)


@pytest.fixture()
def project(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    isolate(monkeypatch, tmp_path)
    root = tmp_path / "project"
    root.mkdir()
    monkeypatch.chdir(root)
    return root


async def test_initialize_reports_version_one_and_what_the_agent_supports(project: Path) -> None:
    async with connected(make_agent(), RecordingEditor()) as editor:
        response = await editor.initialize(protocol_version=1)
        newer    = await editor.initialize(protocol_version=2)
    assert response.protocol_version == 1
    assert newer.protocol_version == 1
    caps = response.agent_capabilities
    assert caps.load_session is True
    assert caps.prompt_capabilities.image is True
    assert caps.prompt_capabilities.embedded_context is True
    assert caps.prompt_capabilities.audio is False
    assert caps.mcp_capabilities.http is True
    assert response.agent_info.name == "nerdvana"
    assert response.auth_methods == []


async def test_new_session_publishes_the_project_commands_after_the_answer(project: Path) -> None:
    commands = project / ".nerdvana" / "commands"
    commands.mkdir(parents=True)
    (commands / "review.md").write_text("---\ndescription: Review the diff\n---\nReview $ARGUMENTS\n", encoding="utf-8")
    editor = RecordingEditor()
    async with connected(make_agent(), editor) as conn:
        await conn.initialize(protocol_version=1)
        session = await conn.new_session(cwd=str(project), mcp_servers=[])
        assert session.session_id
        await editor.wait_for("available_commands_update")
    update = editor.of_kind("available_commands_update")[0]
    assert [(c.name, c.description) for c in update.available_commands] == [("review", "Review the diff")]


async def test_a_prompt_streams_the_answer_and_reports_usage_and_cost(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    use_provider(monkeypatch, ScriptedProvider([answer("all ", "done")]))
    editor = RecordingEditor()
    async with connected(make_agent(), editor) as conn:
        await conn.initialize(protocol_version=1)
        session = await conn.new_session(cwd=str(project), mcp_servers=[])
        result  = await conn.prompt(prompt=[text_block("go")], session_id=session.session_id)
    assert result.stop_reason == "end_turn"
    assert editor.message_text() == "all done"
    assert (result.usage.input_tokens, result.usage.output_tokens, result.usage.cached_read_tokens) == (100, 7, 40)
    assert result.usage.total_tokens == 107
    usage = editor.of_kind("usage_update")[-1]
    assert usage.used == 100
    assert usage.size > 0
    assert usage.cost.currency == "USD"


async def test_thoughts_are_sent_as_thought_chunks_and_can_be_turned_off(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    thinking = [
        ProviderEvent(type="thinking_delta", thinking="first "),
        ProviderEvent(type="thinking_delta", thinking="second"),
        *answer("ok"),
    ]
    use_provider(monkeypatch, ScriptedProvider([thinking]))
    shown, hidden = RecordingEditor(), RecordingEditor()
    async with connected(make_agent(), shown) as conn:
        session = await conn.new_session(cwd=str(project), mcp_servers=[])
        await conn.prompt(prompt=[text_block("think")], session_id=session.session_id)
    async with connected(make_agent(set_values=["model.show_thinking=false"]), hidden) as conn:
        session = await conn.new_session(cwd=str(project), mcp_servers=[])
        await conn.prompt(prompt=[text_block("think")], session_id=session.session_id)
    assert "".join(u.content.text for u in shown.of_kind("agent_thought_chunk")) == "first second"
    assert hidden.of_kind("agent_thought_chunk") == []


async def test_a_file_tool_call_reports_kind_location_diff_and_progress(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = project / "notes.txt"
    use_provider(monkeypatch, ScriptedProvider([tool_turn("t1", "FileWrite", {"path": "notes.txt", "content": "hello\n"}), answer("done")]))
    editor = RecordingEditor()
    async with connected(make_agent(), editor) as conn:
        session = await conn.new_session(cwd=str(project), mcp_servers=[])
        result  = await conn.prompt(prompt=[text_block("write it")], session_id=session.session_id)
    assert result.stop_reason == "end_turn"
    assert target.read_text(encoding="utf-8") == "hello\n"
    start = editor.of_kind("tool_call")[0]
    assert (start.tool_call_id, start.kind, start.status) == ("t1", "edit", "pending")
    assert start.title == "Write notes.txt"
    assert [location.path for location in start.locations] == [str(target)]
    assert (start.content[0].type, start.content[0].path, start.content[0].new_text) == ("diff", str(target), "hello\n")
    statuses = [u.status for u in editor.of_kind("tool_call_update") if u.tool_call_id == "t1"]
    assert statuses == ["in_progress", "completed"]


async def test_a_tool_that_needs_approval_asks_the_editor_and_runs_when_allowed(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    use_provider(monkeypatch, ScriptedProvider([tool_turn("t1", "Bash", {"command": "printenv"}), answer("done")]))
    editor = RecordingEditor(["allow_once"])
    async with connected(make_agent(), editor) as conn:
        session = await conn.new_session(cwd=str(project), mcp_servers=[])
        result  = await conn.prompt(prompt=[text_block("env")], session_id=session.session_id)
    assert result.stop_reason == "end_turn"
    request = editor.permissions[0]
    assert request["tool_call"].tool_call_id == "t1"
    assert request["tool_call"].kind == "execute"
    assert [option.kind for option in request["options"]] == ["allow_once", "allow_always", "reject_once"]
    statuses = [u.status for u in editor.of_kind("tool_call_update") if u.tool_call_id == "t1"]
    assert statuses == ["in_progress", "completed"]


async def test_a_rejected_call_does_not_run_and_is_reported_failed(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    use_provider(monkeypatch, ScriptedProvider([tool_turn("t1", "Bash", {"command": "printenv"}), answer("understood")]))
    editor = RecordingEditor(["reject_once"])
    async with connected(make_agent(), editor) as conn:
        session = await conn.new_session(cwd=str(project), mcp_servers=[])
        result  = await conn.prompt(prompt=[text_block("env")], session_id=session.session_id)
    assert result.stop_reason == "end_turn"
    updates = [u for u in editor.of_kind("tool_call_update") if u.tool_call_id == "t1"]
    assert [u.status for u in updates] == ["failed"]
    assert "Permission denied by user" in updates[0].content[0].content.text
    assert editor.message_text() == "understood"


async def test_allow_always_is_remembered_for_the_same_call(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    turns = [
        tool_turn("t1", "Bash", {"command": "printenv"}),
        tool_turn("t2", "Bash", {"command": "printenv"}),
        answer("done"),
    ]
    use_provider(monkeypatch, ScriptedProvider(turns))
    editor = RecordingEditor(["allow_always"])
    async with connected(make_agent(), editor) as conn:
        session = await conn.new_session(cwd=str(project), mcp_servers=[])
        await conn.prompt(prompt=[text_block("env twice")], session_id=session.session_id)
    assert len(editor.permissions) == 1
    statuses = [u.status for u in editor.of_kind("tool_call_update") if u.tool_call_id == "t2"]
    assert statuses == ["in_progress", "completed"]


async def test_cancel_ends_a_running_turn_with_the_cancelled_stop_reason(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hang = [ProviderEvent(type="content_delta", content="partial"), ProviderEvent(type="hang")]
    use_provider(monkeypatch, ScriptedProvider([hang]))
    editor = RecordingEditor()
    async with connected(make_agent(), editor) as conn:
        session = await conn.new_session(cwd=str(project), mcp_servers=[])
        pending = asyncio.ensure_future(conn.prompt(prompt=[text_block("go")], session_id=session.session_id))
        await editor.wait_for("agent_message_chunk")
        await conn.cancel(session_id=session.session_id)
        result = await asyncio.wait_for(pending, timeout=5)
    assert result.stop_reason == "cancelled"
    assert editor.message_text() == "partial"


async def test_cancel_while_a_permission_request_is_open_ends_the_turn(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    use_provider(monkeypatch, ScriptedProvider([tool_turn("t1", "Bash", {"command": "printenv"}), answer("never")]))
    editor = RecordingEditor(hold=asyncio.Event())
    async with connected(make_agent(), editor) as conn:
        session = await conn.new_session(cwd=str(project), mcp_servers=[])
        pending = asyncio.ensure_future(conn.prompt(prompt=[text_block("env")], session_id=session.session_id))
        await editor.wait_for_permission()
        await conn.cancel(session_id=session.session_id)
        result = await asyncio.wait_for(pending, timeout=5)
    assert result.stop_reason == "cancelled"
    assert "never" not in editor.message_text()


async def test_a_second_prompt_while_one_runs_is_refused(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    use_provider(monkeypatch, ScriptedProvider([[ProviderEvent(type="content_delta", content="x"), ProviderEvent(type="hang")]]))
    editor = RecordingEditor()
    async with connected(make_agent(), editor) as conn:
        session = await conn.new_session(cwd=str(project), mcp_servers=[])
        first = asyncio.ensure_future(conn.prompt(prompt=[text_block("one")], session_id=session.session_id))
        await editor.wait_for("agent_message_chunk")
        with pytest.raises(RequestError) as refused:
            await conn.prompt(prompt=[text_block("two")], session_id=session.session_id)
        await conn.cancel(session_id=session.session_id)
        await asyncio.wait_for(first, timeout=5)
    assert refused.value.code == -32600


async def test_a_provider_failure_ends_the_prompt_with_an_error_and_the_session_survives(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    provider = ScriptedProvider([[ProviderEvent(type="error", error="bad request", error_kind="other")]])
    use_provider(monkeypatch, provider)
    editor = RecordingEditor()
    async with connected(make_agent(), editor) as conn:
        session = await conn.new_session(cwd=str(project), mcp_servers=[])
        with pytest.raises(RequestError) as failed:
            await conn.prompt(prompt=[text_block("go")], session_id=session.session_id)
        provider.turns = [answer("recovered")]
        provider.calls = 0
        retry = await conn.prompt(prompt=[text_block("again")], session_id=session.session_id)
    assert failed.value.code == -32603
    assert "bad request" in str(failed.value.data)
    assert retry.stop_reason == "end_turn"
    assert editor.message_text() == "recovered"


async def test_the_turn_limit_is_reported_as_a_stop_reason(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    use_provider(monkeypatch, ScriptedProvider([tool_turn(f"t{i}", "Glob", {"pattern": f"*{i}"}) for i in range(5)]))
    async with connected(make_agent(set_values=["session.max_turns=2"]), RecordingEditor()) as conn:
        session = await conn.new_session(cwd=str(project), mcp_servers=[])
        result  = await conn.prompt(prompt=[text_block("loop")], session_id=session.session_id)
    assert result.stop_reason == "max_turn_requests"


@pytest.mark.parametrize("cwd", ["relative/dir", "/definitely/not/a/directory"])
async def test_new_session_rejects_a_working_directory_that_is_not_usable(project: Path, cwd: str) -> None:
    async with connected(make_agent(), RecordingEditor()) as conn:
        with pytest.raises(RequestError) as refused:
            await conn.new_session(cwd=cwd, mcp_servers=[])
    assert refused.value.code == -32602


async def test_a_prompt_for_an_unknown_session_is_refused(project: Path) -> None:
    async with connected(make_agent(), RecordingEditor()) as conn:
        with pytest.raises(RequestError) as refused:
            await conn.prompt(prompt=[text_block("go")], session_id="nope")
    assert refused.value.code == -32602


async def test_a_missing_api_key_asks_for_authentication(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    async with connected(make_agent(), RecordingEditor()) as conn:
        with pytest.raises(RequestError) as refused:
            await conn.new_session(cwd=str(project), mcp_servers=[])
    assert refused.value.code == -32000
    assert "No API key" in str(refused.value.data)


async def test_an_invalid_override_is_reported_as_an_error(project: Path) -> None:
    async with connected(make_agent(set_values=["session.nonsense=1"]), RecordingEditor()) as conn:
        with pytest.raises(RequestError) as refused:
            await conn.new_session(cwd=str(project), mcp_servers=[])
    assert refused.value.code == -32603
    assert "unknown setting" in str(refused.value.data)


async def test_a_project_command_typed_in_a_prompt_expands_into_its_template(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    commands = project / ".nerdvana" / "commands"
    commands.mkdir(parents=True)
    (commands / "shout.md").write_text("Say $ARGUMENTS loudly", encoding="utf-8")
    seen: list[str] = []

    class _Spy(ScriptedProvider):
        async def stream(self, system_prompt: str, messages: Any, tools: Any):  # type: ignore[no-untyped-def,override]
            seen.append(str(messages[-1]["content"]))
            async for event in super().stream(system_prompt, messages, tools):
                yield event

    use_provider(monkeypatch, _Spy([answer("ok")]))
    async with connected(make_agent(), RecordingEditor()) as conn:
        session = await conn.new_session(cwd=str(project), mcp_servers=[])
        await conn.prompt(prompt=[text_block("/shout hello there")], session_id=session.session_id)
    assert seen == ["Say hello there loudly"]


async def test_tools_are_created_in_the_session_directory_and_the_process_directory_is_left_alone(project: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import os

    from nerdvana_cli.core import lsp_client

    seen: list[str] = []

    class _Spy(lsp_client.LspClient):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            seen.append(os.getcwd())
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(lsp_client, "LspClient", _Spy)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    async with connected(make_agent(), RecordingEditor()) as conn:
        await conn.new_session(cwd=str(project), mcp_servers=[])
        assert os.getcwd() == str(elsewhere)
    assert seen == [str(project)]
