"""AgentLoop 핵심 경로 통합 테스트.

Mock provider를 주입하여 run() 메서드의 스트리밍 흐름,
도구 실행 흐름, 턴 제한, 미지 도구 처리를 검증한다.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from nerdvana_cli.core.agent_loop import AgentLoop, CONTEXT_USAGE_PREFIX, TOOL_DONE_PREFIX, TOOL_STATUS_PREFIX
from nerdvana_cli.core.settings import ModelConfig, NerdvanaSettings, SessionConfig
from nerdvana_cli.core.tool import BaseTool, ToolRegistry
from nerdvana_cli.providers.base import ProviderEvent
from nerdvana_cli.types import PermissionBehavior, PermissionResult, Role, ToolResult

_MARKER_PREFIXES = (TOOL_STATUS_PREFIX, TOOL_DONE_PREFIX, CONTEXT_USAGE_PREFIX)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_settings(**overrides) -> NerdvanaSettings:
    """테스트용 최소 설정 생성."""
    session_kw = overrides.pop("session", {})
    settings = MagicMock(spec=NerdvanaSettings)
    settings.model = ModelConfig(
        provider="anthropic",
        model="test-model",
        api_key="test-key",
    )
    settings.session = SessionConfig(**session_kw)
    settings.cwd = "/tmp"
    settings.verbose = False
    return settings


def _make_mock_provider(event_sequences: list[list[ProviderEvent]]):
    """호출 횟수에 따라 다른 이벤트 시퀀스를 반환하는 mock provider."""
    provider = MagicMock()
    call_count = 0

    async def _stream(system_prompt, messages, tools):
        nonlocal call_count
        idx = min(call_count, len(event_sequences) - 1)
        call_count += 1
        for event in event_sequences[idx]:
            yield event

    provider.stream = _stream
    return provider


class _DummyTool(BaseTool):
    """테스트용 동기 도구."""

    name = "Bash"
    description_text = "Run a shell command"
    input_schema = {
        "type": "object",
        "properties": {"command": {"type": "string"}},
        "required": ["command"],
    }
    is_concurrency_safe = False

    def parse_args(self, raw: dict[str, Any]) -> dict[str, Any]:
        return raw

    async def call(self, args, context, can_use_tool=None, on_progress=None) -> ToolResult:
        return ToolResult(tool_use_id="", content=f"output of: {args.get('command', '')}", is_error=False)

    def check_permissions(self, args, context) -> PermissionResult:
        return PermissionResult(behavior=PermissionBehavior.ALLOW)

    def validate_input(self, args, context) -> str | None:
        return None


def _make_registry(tools: list[BaseTool] | None = None) -> ToolRegistry:
    registry = ToolRegistry()
    for tool in (tools or []):
        registry.register(tool)
    return registry


async def _collect(agent_loop: AgentLoop, prompt: str) -> list[str]:
    """run()의 모든 yield 값을 수집. 내부 마커(TOOL_STATUS, TOOL_DONE, CONTEXT_USAGE)는 제외."""
    chunks: list[str] = []
    async for chunk in agent_loop.run(prompt):
        if any(chunk.startswith(p) for p in _MARKER_PREFIXES):
            continue
        chunks.append(chunk)
    return chunks


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_simple_text_response():
    """모델이 텍스트만 반환(end_turn). messages에 user+assistant 2개만 남아야 한다."""
    events = [
        ProviderEvent(type="content_delta", content="Hello "),
        ProviderEvent(type="content_delta", content="world!"),
        ProviderEvent(type="done", stop_reason="end_turn"),
    ]

    provider = _make_mock_provider([events])
    settings = _make_settings()
    registry = _make_registry()

    with (
        patch.object(AgentLoop, "create_provider_from_settings", return_value=provider),
        patch.object(AgentLoop, "build_system_prompt", return_value="system"),
    ):
        loop = AgentLoop(settings=settings, registry=registry)
        chunks = await _collect(loop, "Hi")

    assert "".join(chunks) == "Hello world!"

    msgs = loop.state.messages
    # reminder (USER) + real user message (USER) + assistant = 3
    assert len(msgs) == 3
    assert msgs[0].role == Role.USER
    assert "<system-reminder>" in msgs[0].content
    assert msgs[1].role == Role.USER
    assert msgs[1].content == "Hi"
    assert msgs[2].role == Role.ASSISTANT
    assert msgs[2].content == "Hello world!"


@pytest.mark.asyncio
async def test_max_turns_enforced():
    """max_turns=1 설정 후 모델이 tool_use를 반환하면 'Max turns' 메시지가 나와야 한다."""

    # 첫 번째 턴: tool_use 완료 -> 도구 결과 추가 -> 루프 계속
    first_turn_events = [
        ProviderEvent(type="tool_use_start", tool_name="Bash", tool_use_id="call_0"),
        ProviderEvent(
            type="tool_use_complete",
            tool_use_id="call_0",
            tool_name="Bash",
            tool_input_complete={"command": "ls"},
        ),
        ProviderEvent(type="done", stop_reason="tool_use"),
    ]

    # 두 번째 턴은 도달하지 못해야 한다 (max_turns=1)
    second_turn_events = [
        ProviderEvent(type="content_delta", content="should not reach"),
        ProviderEvent(type="done", stop_reason="end_turn"),
    ]

    provider = _make_mock_provider([first_turn_events, second_turn_events])
    settings = _make_settings(session={"max_turns": 1})
    registry = _make_registry([_DummyTool()])

    with (
        patch.object(AgentLoop, "create_provider_from_settings", return_value=provider),
        patch.object(AgentLoop, "build_system_prompt", return_value="system"),
    ):
        loop = AgentLoop(settings=settings, registry=registry)
        chunks = await _collect(loop, "run ls")

    combined = "".join(chunks)
    assert "Max turns" in combined
    assert "should not reach" not in combined


@pytest.mark.asyncio
async def test_tool_execution_flow():
    """모델이 tool_use -> 도구 결과 -> end_turn. messages 순서 검증."""

    first_turn_events = [
        ProviderEvent(type="content_delta", content="Running command..."),
        ProviderEvent(type="tool_use_start", tool_name="Bash", tool_use_id="call_0"),
        ProviderEvent(type="tool_use_delta", tool_input_delta='{"command":'),
        ProviderEvent(type="tool_use_delta", tool_input_delta=' "echo hi"}'),
        ProviderEvent(
            type="tool_use_complete",
            tool_use_id="call_0",
            tool_name="Bash",
            tool_input_complete={"command": "echo hi"},
        ),
        ProviderEvent(type="done", stop_reason="tool_use"),
    ]

    second_turn_events = [
        ProviderEvent(type="content_delta", content="Done."),
        ProviderEvent(type="done", stop_reason="end_turn"),
    ]

    provider = _make_mock_provider([first_turn_events, second_turn_events])
    settings = _make_settings()
    registry = _make_registry([_DummyTool()])

    with (
        patch.object(AgentLoop, "create_provider_from_settings", return_value=provider),
        patch.object(AgentLoop, "build_system_prompt", return_value="system"),
    ):
        loop = AgentLoop(settings=settings, registry=registry)
        await _collect(loop, "echo hi")

    msgs = loop.state.messages
    # reminder(USER) -> user -> assistant(tool_uses) -> tool -> assistant(end_turn)
    assert len(msgs) == 5
    assert msgs[0].role == Role.USER
    assert "<system-reminder>" in msgs[0].content

    assert msgs[1].role == Role.USER
    assert msgs[1].content == "echo hi"

    assert msgs[2].role == Role.ASSISTANT
    assert len(msgs[2].tool_uses) == 1
    assert msgs[2].tool_uses[0]["name"] == "Bash"

    assert msgs[3].role == Role.TOOL
    assert "output of: echo hi" in msgs[3].content
    assert msgs[3].is_error is False

    assert msgs[4].role == Role.ASSISTANT
    assert msgs[4].content == "Done."


@pytest.mark.asyncio
async def test_unknown_tool_returns_error():
    """존재하지 않는 도구 호출 시 is_error=True인 ToolResult가 messages에 들어가야 한다."""

    first_turn_events = [
        ProviderEvent(type="tool_use_start", tool_name="NonExistent", tool_use_id="call_0"),
        ProviderEvent(
            type="tool_use_complete",
            tool_use_id="call_0",
            tool_name="NonExistent",
            tool_input_complete={"arg": "value"},
        ),
        ProviderEvent(type="done", stop_reason="tool_use"),
    ]

    second_turn_events = [
        ProviderEvent(type="content_delta", content="Sorry."),
        ProviderEvent(type="done", stop_reason="end_turn"),
    ]

    provider = _make_mock_provider([first_turn_events, second_turn_events])
    settings = _make_settings()
    # 빈 레지스트리 — NonExistent 도구 없음
    registry = _make_registry()

    with (
        patch.object(AgentLoop, "create_provider_from_settings", return_value=provider),
        patch.object(AgentLoop, "build_system_prompt", return_value="system"),
    ):
        loop = AgentLoop(settings=settings, registry=registry)
        await _collect(loop, "use NonExistent")

    msgs = loop.state.messages
    # reminder(USER) -> user -> assistant(tool_uses) -> tool(error) -> assistant(end_turn)
    assert len(msgs) == 5
    assert msgs[0].role == Role.USER
    assert "<system-reminder>" in msgs[0].content

    tool_msg = msgs[3]
    assert tool_msg.role == Role.TOOL
    assert tool_msg.is_error is True
    assert "Unknown tool" in tool_msg.content


@pytest.mark.asyncio
async def test_sticky_session_context_includes_project_snapshot(tmp_path):
    """After one run() cycle, _sticky_session_context must contain project snapshot."""
    import subprocess
    from pathlib import Path

    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "myapp"\nversion = "0.1.0"\n'
    )
    (tmp_path / "src").mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)

    events = [
        ProviderEvent(type="content_delta", content="ok"),
        ProviderEvent(type="done", stop_reason="end_turn"),
    ]
    provider = _make_mock_provider([events])
    settings = _make_settings()
    settings.cwd = str(tmp_path)
    registry = _make_registry()

    with (
        patch.object(AgentLoop, "create_provider_from_settings", return_value=provider),
        patch.object(AgentLoop, "build_system_prompt", return_value="system"),
    ):
        loop = AgentLoop(settings=settings, registry=registry)
        await _collect(loop, "hello")

    assert "# Project Snapshot" in loop._sticky_session_context


@pytest.mark.asyncio
async def test_reminder_message_injected_on_each_turn():
    """run() called twice must produce two <system-reminder> USER messages."""
    events = [
        ProviderEvent(type="content_delta", content="resp"),
        ProviderEvent(type="done", stop_reason="end_turn"),
    ]
    # Each run() call consumes one event sequence
    provider = _make_mock_provider([events, events])
    settings = _make_settings()
    registry = _make_registry()

    with (
        patch.object(AgentLoop, "create_provider_from_settings", return_value=provider),
        patch.object(AgentLoop, "build_system_prompt", return_value="system"),
    ):
        loop = AgentLoop(settings=settings, registry=registry)
        await _collect(loop, "first")
        await _collect(loop, "second")

    reminder_msgs = [
        m for m in loop.state.messages
        if m.role == Role.USER and "<system-reminder>" in m.content
    ]
    assert len(reminder_msgs) == 2, (
        f"Expected 2 reminder messages, got {len(reminder_msgs)}"
    )
    # Turn numbers must be sequential
    assert "turn=1" in reminder_msgs[0].content
    assert "turn=2" in reminder_msgs[1].content


@pytest.mark.asyncio
async def test_active_skill_persists_across_turns():
    """activate_skill() body must appear in the system prompt of every subsequent turn."""
    captured_prompts: list[str] = []

    events = [
        ProviderEvent(type="content_delta", content="ok"),
        ProviderEvent(type="done", stop_reason="end_turn"),
    ]
    provider = _make_mock_provider([events, events])

    async def _capturing_stream(system_prompt, messages, tools):
        captured_prompts.append(system_prompt)
        for evt in events:
            yield evt

    provider.stream = _capturing_stream

    settings = _make_settings()
    registry = _make_registry()

    with (
        patch.object(AgentLoop, "create_provider_from_settings", return_value=provider),
        patch.object(AgentLoop, "build_system_prompt", return_value="base_system"),
    ):
        loop = AgentLoop(settings=settings, registry=registry)
        loop.activate_skill("DEBUG MODE INSTRUCTIONS")
        await _collect(loop, "first turn")
        await _collect(loop, "second turn")

    assert len(captured_prompts) == 2
    assert "DEBUG MODE INSTRUCTIONS" in captured_prompts[0]
    assert "DEBUG MODE INSTRUCTIONS" in captured_prompts[1]
