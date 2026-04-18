"""8-scenario snapshot suite for AgentLoop parity baseline.

Each test captures the normalized message sequence produced by AgentLoop
under a fully deterministic ProviderMock. Snapshots are stored in
tests/parity/snapshots/ and used to verify that later refactoring
(T-0A-03..07) preserves identical behaviour.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def run_loop(loop: Any, prompt: str) -> list[str]:
    """Synchronously run loop.run(prompt) and return all yielded chunks."""
    chunks: list[str] = []

    async def _inner() -> None:
        async for chunk in loop.run(prompt):
            chunks.append(chunk)

    asyncio.get_event_loop().run_until_complete(_inner())
    return chunks


# ---------------------------------------------------------------------------
# Scenario 01 — REPL single prompt, no tools
# ---------------------------------------------------------------------------


def test_scenario_01_repl_single_prompt(
    build_loop: Any,
    normalize_messages: Any,
    snapshot: Any,
) -> None:
    """REPL single prompt with no tool calls — verifies basic end_turn path."""
    loop, _mock = build_loop("provider_mock_scenario_01.json")

    run_loop(loop, "Hello, world!")

    normalized = normalize_messages(loop.state.messages)
    snapshot.assert_match(
        json.dumps(normalized, ensure_ascii=False, indent=2),
        "scenario_01.json",
    )


# ---------------------------------------------------------------------------
# Scenario 02 — Streaming response (5 chunks consumed)
# ---------------------------------------------------------------------------


def test_scenario_02_streaming_response(
    build_loop: Any,
    normalize_messages: Any,
    snapshot: Any,
) -> None:
    """5-chunk streaming response — verifies chunk accumulation into one message."""
    loop, _mock = build_loop("provider_mock_scenario_02.json")

    chunks = run_loop(loop, "Stream me 5 chunks.")

    # The 5 content deltas must all have arrived
    content_chunks = [c for c in chunks if not c.startswith("\x00")]
    assert len(content_chunks) == 5

    normalized = normalize_messages(loop.state.messages)
    snapshot.assert_match(
        json.dumps(normalized, ensure_ascii=False, indent=2),
        "scenario_02.json",
    )


# ---------------------------------------------------------------------------
# Scenario 03 — Tool call: FileRead (1 tool)
# ---------------------------------------------------------------------------


def test_scenario_03_tool_call_file_read(
    build_loop: Any,
    normalize_messages: Any,
    snapshot: Any,
    session_dir: str,
) -> None:
    """Single FileRead tool invocation — verifies tool_use + tool result path."""
    # Create a dummy file for FileRead to succeed
    readme = Path(session_dir) / "README.md"
    readme.write_text("# Test Project\nThis is a test.", encoding="utf-8")

    loop, _mock = build_loop("provider_mock_scenario_03.json")

    run_loop(loop, "Read the README file.")

    messages = loop.state.messages
    roles = [str(m.role) for m in messages]
    assert "tool" in roles, "Expected a tool result message"

    normalized = normalize_messages(messages)
    snapshot.assert_match(
        json.dumps(normalized, ensure_ascii=False, indent=2),
        "scenario_03.json",
    )


# ---------------------------------------------------------------------------
# Scenario 04 — Tool chain: Glob → FileRead → Grep
# ---------------------------------------------------------------------------


def test_scenario_04_tool_chain_glob_read_grep(
    build_loop: Any,
    normalize_messages: Any,
    snapshot: Any,
    session_dir: str,
) -> None:
    """Three-tool sequential chain: Glob, FileRead, Grep."""
    # Create files so tools return non-error results
    readme = Path(session_dir) / "README.md"
    readme.write_text("# Project\ndef my_function(): pass\n", encoding="utf-8")
    src = Path(session_dir) / "main.py"
    src.write_text("def main():\n    pass\n", encoding="utf-8")

    loop, _mock = build_loop("provider_mock_scenario_04.json")

    run_loop(loop, "Glob py files, read README, then grep for functions.")

    messages   = loop.state.messages
    tool_msgs  = [m for m in messages if str(m.role) == "tool"]
    assert len(tool_msgs) == 3, f"Expected 3 tool result messages, got {len(tool_msgs)}"

    normalized = normalize_messages(messages)
    snapshot.assert_match(
        json.dumps(normalized, ensure_ascii=False, indent=2),
        "scenario_04.json",
    )


# ---------------------------------------------------------------------------
# Scenario 05 — Session JSONL save + resume
# ---------------------------------------------------------------------------


def test_scenario_05_session_resume(
    build_loop: Any,
    normalize_messages: Any,
    snapshot: Any,
    session_dir: str,
) -> None:
    """Session persisted to JSONL after first run; second loop loads transcript."""
    from nerdvana_cli.core.agent_loop import AgentLoop
    from nerdvana_cli.core.session import SessionStorage
    from nerdvana_cli.core.settings import ModelConfig, NerdvanaSettings, SessionConfig
    from nerdvana_cli.core.tool import ToolRegistry
    from nerdvana_cli.tools.file_tools import FileReadTool
    from nerdvana_cli.tools.search_tools import GlobTool, GrepTool
    from tests.parity.conftest import ProviderMock

    fixtures = Path(__file__).parent / "fixtures"

    # ── First session ────────────────────────────────────────────────────
    settings_1        = NerdvanaSettings()
    settings_1.model  = ModelConfig(
        provider = "anthropic",
        model    = "claude-test-mock",
        api_key  = "test-key-00000000",
    )
    settings_1.session = SessionConfig(
        persist           = True,
        max_turns         = 50,
        max_context_tokens = 10_000,
        compact_threshold = 0.8,
        planning_gate     = False,
    )
    settings_1.cwd    = session_dir
    settings_1.verbose = False

    registry_1 = ToolRegistry()
    registry_1.register(FileReadTool())
    registry_1.register(GlobTool())
    registry_1.register(GrepTool())

    storage_1 = SessionStorage(session_id="parity-s05", storage_dir=session_dir)
    loop_1    = AgentLoop(settings=settings_1, registry=registry_1, session=storage_1)

    mock_1 = ProviderMock(fixtures / "provider_mock_scenario_05.json")
    loop_1.provider = mock_1  # type: ignore[assignment]

    asyncio.get_event_loop().run_until_complete(
        _run_gen(loop_1, "First session prompt.")
    )

    # Verify JSONL was written
    jsonl_path = Path(session_dir) / "parity-s05.jsonl"
    assert jsonl_path.exists(), "Session JSONL not created"
    events = [json.loads(line) for line in jsonl_path.read_text().splitlines() if line.strip()]
    assert any(e.get("type") == "user" for e in events)

    # ── Second session (resume) ──────────────────────────────────────────
    settings_2         = NerdvanaSettings()
    settings_2.model   = ModelConfig(
        provider = "anthropic",
        model    = "claude-test-mock",
        api_key  = "test-key-00000000",
    )
    settings_2.session = SessionConfig(
        persist           = True,
        max_turns         = 50,
        max_context_tokens = 10_000,
        compact_threshold = 0.8,
        planning_gate     = False,
    )
    settings_2.cwd     = session_dir
    settings_2.verbose = False

    registry_2 = ToolRegistry()
    registry_2.register(FileReadTool())
    registry_2.register(GlobTool())
    registry_2.register(GrepTool())

    storage_2 = SessionStorage(session_id="parity-s05-resumed", storage_dir=session_dir)
    loop_2    = AgentLoop(settings=settings_2, registry=registry_2, session=storage_2)

    # Re-use the same fixture; pop the second response
    mock_2 = ProviderMock(fixtures / "provider_mock_scenario_05.json")
    # Discard the first response (we simulate resuming mid-fixture)
    mock_2._responses.pop(0)  # noqa: SLF001
    loop_2.provider = mock_2  # type: ignore[assignment]

    # Inject prior conversation into resumed loop's state
    loop_2.state.messages.extend(loop_1.state.messages)

    asyncio.get_event_loop().run_until_complete(
        _run_gen(loop_2, "Resume: continue from where we left off.")
    )

    combined_messages = loop_2.state.messages
    normalized = normalize_messages(combined_messages)
    snapshot.assert_match(
        json.dumps(normalized, ensure_ascii=False, indent=2),
        "scenario_05.json",
    )


async def _run_gen(loop: Any, prompt: str) -> None:
    async for _ in loop.run(prompt):
        pass


# ---------------------------------------------------------------------------
# Scenario 06 — Compaction trigger (compact_threshold exceeded)
# ---------------------------------------------------------------------------


def test_scenario_06_compaction_trigger(
    build_loop: Any,
    normalize_messages: Any,
    snapshot: Any,
) -> None:
    """Compact threshold exceeded — verifies compaction path fires."""
    from nerdvana_cli.types import Message, Role

    # Low threshold: anything > 10 tokens triggers compact (max_context=100, threshold=0.1)
    loop, _mock = build_loop(
        "provider_mock_scenario_06.json",
        extra_session={"max_context_tokens": 100, "compact_threshold": 0.1},
    )

    # Pre-populate history so token estimate exceeds threshold
    for i in range(5):
        loop.state.messages.append(Message(role=Role.USER,      content=f"User message {i} " * 10))
        loop.state.messages.append(Message(role=Role.ASSISTANT, content=f"Assistant reply {i} " * 10))

    run_loop(loop, "Trigger compaction now.")

    messages   = loop.state.messages
    normalized = normalize_messages(messages)

    # The snapshot captures whatever shape the messages are in after compaction
    snapshot.assert_match(
        json.dumps(normalized, ensure_ascii=False, indent=2),
        "scenario_06.json",
    )


# ---------------------------------------------------------------------------
# Scenario 07 — Context limit recovery (max_tokens stop → recovery hook)
# ---------------------------------------------------------------------------


def test_scenario_07_context_limit_recovery(
    build_loop: Any,
    normalize_messages: Any,
    snapshot: Any,
) -> None:
    """Provider returns max_tokens stop → context_limit_recovery hook fires."""
    loop, _mock = build_loop("provider_mock_scenario_07.json")

    run_loop(loop, "Give me a very long response that hits max_tokens.")

    messages = loop.state.messages
    # After recovery, there should be a continuation user message injected
    user_contents = [
        str(m.content) for m in messages if str(m.role) == "user"
    ]
    recovery_msgs = [c for c in user_contents if "Context limit reached" in c]
    assert recovery_msgs, "Expected context_limit_recovery hook to inject continuation message"

    normalized = normalize_messages(messages)
    snapshot.assert_match(
        json.dumps(normalized, ensure_ascii=False, indent=2),
        "scenario_07.json",
    )


# ---------------------------------------------------------------------------
# Scenario 08 — ralph_loop_check (end_turn with TODO markers)
# ---------------------------------------------------------------------------


def test_scenario_08_ralph_loop_check(
    build_loop: Any,
    normalize_messages: Any,
    snapshot: Any,
) -> None:
    """ralph_loop_check fires on end_turn and injects a completion prompt.

    AFTER_API_CALL is now fired for end_turn stops (T-bug-ralph-loop fix).
    When the assistant reply contains TODO/FIXME markers, ralph_loop_check
    injects a user message asking for completion.  The second fixture response
    provides the clean follow-up, and the loop terminates normally.
    """
    loop, _mock = build_loop("provider_mock_scenario_08.json")

    run_loop(loop, "Implement the function.")

    messages = loop.state.messages

    # ralph_loop_check must have injected a continuation message
    user_contents = [str(m.content) for m in messages if str(m.role) == "user"]
    ralph_injected = [
        c for c in user_contents
        if "Incomplete items found" in c or "Complete all TODOs" in c
    ]
    assert ralph_injected, (
        "ralph_loop_check must fire on end_turn and inject a completion prompt."
    )

    # The first assistant message must contain the TODO markers
    assistant_contents = [str(m.content) for m in messages if str(m.role) == "assistant"]
    todo_in_reply = any("TODO" in c for c in assistant_contents)
    assert todo_in_reply, "Expected first assistant reply to contain TODO marker"

    normalized = normalize_messages(messages)
    snapshot.assert_match(
        json.dumps(normalized, ensure_ascii=False, indent=2),
        "scenario_08.json",
    )
