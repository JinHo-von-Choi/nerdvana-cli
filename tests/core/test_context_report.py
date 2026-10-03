"""The context breakdown: parts of the system prompt, tool declarations, messages, tool results, advice.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from nerdvana_cli.core.context_report import (
    LARGE_SHARE,
    ContextReport,
    advice,
    build_report,
    masking_effect,
    render,
    role_items,
    system_prompt_items,
)
from nerdvana_cli.core.observation_mask import placeholder_for
from nerdvana_cli.core.settings import SessionConfig
from nerdvana_cli.core.token_estimator import approx_tokens
from nerdvana_cli.types import Message, Role

PROMPT = "\n\n".join([
    "You are an assistant.\n" + "base " * 40,
    "# System\n- rule",
    "# Skills\n\nSkills hold instructions.\n- a: one\n- b: two",
    "# Environment\n- Platform: linux\n- Is a git repository: true\n- Git branch: main\n- Recent commits:\n  abc one\n  def two\n"
    "\n## NIRNA.md pointer\nRead them with FileRead.",
    "# User & Project Instructions (NIRNA.md)\n\nContents of NIRNA.md:\n\n" + "rule " * 100 + "\n\n# Skills\n\nnot a section of the prompt",
])


def tool(name: str, size: int = 10) -> Any:
    return SimpleNamespace(name=name, description_text="d " * size, input_schema={"type": "object", "properties": {}})


def call(tool_id: str, name: str) -> Message:
    return Message(role=Role.ASSISTANT, content="", tool_uses=[{"id": tool_id, "name": name, "input": {}}])


def result(tool_id: str, text: str, error: bool = False) -> Message:
    return Message(role=Role.TOOL, content=text, tool_use_id=tool_id, is_error=error)


def conversation(reads: int, size: int = 4000) -> list[Message]:
    messages = [Message(role=Role.USER, content="read the files")]
    for i in range(reads):
        messages += [call(f"r{i}", "FileRead"), result(f"r{i}", "line\n" * size)]
    return messages + [Message(role=Role.ASSISTANT, content="done")]


def labels(items: Any) -> dict[str, int]:
    return {i.label: i.tokens for i in items}


class TestSystemPromptParts:
    def test_sections_with_their_own_heading_are_split_out(self) -> None:
        parts = labels(system_prompt_items(PROMPT))
        assert set(parts) == {
            "base instructions", "skills catalog", "environment", "git summary", "project documents (NIRNA.md, AGENTS.md, CLAUDE.md)",
        }

    def test_git_lines_are_counted_apart_from_the_environment(self) -> None:
        parts = labels(system_prompt_items(PROMPT))
        assert parts["git summary"] == approx_tokens("- Is a git repository: true\n- Git branch: main\n- Recent commits:\n  abc one\n  def two")
        assert parts["environment"] > 0

    def test_a_heading_inside_the_project_documents_does_not_start_a_new_part(self) -> None:
        parts = labels(system_prompt_items(PROMPT))
        assert parts["skills catalog"] == approx_tokens("# Skills\n\nSkills hold instructions.\n- a: one\n- b: two")

    def test_the_parts_cover_the_whole_prompt(self) -> None:
        total = sum(labels(system_prompt_items(PROMPT)).values())
        assert abs(total - approx_tokens(PROMPT)) <= 12

    def test_extras_are_added_and_empty_ones_left_out(self) -> None:
        parts = labels(system_prompt_items("plain", {"agent role": "review things", "active skill": ""}))
        assert set(parts) == {"base instructions", "agent role"}

    def test_a_prompt_without_markers_is_all_base(self) -> None:
        assert list(labels(system_prompt_items("# System\n- only"))) == ["base instructions"]


class TestMessages:
    def test_tokens_are_counted_per_role_and_tool_results_per_tool(self) -> None:
        messages = [
            Message(role=Role.USER, content="hello"),
            call("a", "FileRead"), result("a", "x" * 400),
            call("b", "Bash"), result("b", "y" * 40),
            call("c", "FileRead"), result("c", "z" * 400),
        ]
        roles, results = role_items(messages)
        assert {r.label: r.count for r in roles} == {"user": 1, "assistant": 3, "tool": 3}
        assert {r.label: r.count for r in results} == {"FileRead": 2, "Bash": 1}
        assert results[0].label == "FileRead" and results[0].tokens == 2 * approx_tokens("x" * 400)

    def test_a_result_whose_call_is_unknown_is_reported_as_such(self) -> None:
        _, results = role_items([result("ghost", "text")])
        assert results[0].label == "(unknown tool)"

    def test_block_content_is_counted(self) -> None:
        roles, _ = role_items([Message(role=Role.USER, content=[{"type": "text", "text": "a" * 400}])])
        assert roles[0].tokens > 90


class TestMaskingEffect:
    def test_old_read_results_would_be_cleared_and_the_newest_kept(self) -> None:
        session = SessionConfig(mask_keep_last=2, mask_trigger_tokens=10)
        saved, due = masking_effect(conversation(5), session)
        one = approx_tokens("line\n" * 4000) - approx_tokens(placeholder_for("FileRead", 20000))
        assert saved == 3 * one
        assert due is True

    def test_nothing_changes_in_the_messages_passed_in(self) -> None:
        messages = conversation(4)
        before   = [m.content for m in messages]
        masking_effect(messages, SessionConfig(mask_keep_last=1))
        assert [m.content for m in messages] == before

    def test_not_due_below_the_trigger(self) -> None:
        saved, due = masking_effect(conversation(3, size=100), SessionConfig(mask_keep_last=1, mask_trigger_tokens=50_000))
        assert saved > 0
        assert due is False

    def test_errors_and_edit_results_are_not_counted(self) -> None:
        messages = [call("a", "FileEdit"), result("a", "ok " * 5000), call("b", "FileRead"), result("b", "bad " * 5000, error=True)]
        assert masking_effect(messages, SessionConfig(mask_keep_last=0))[0] == 0


def report(messages: list[Message], **session: Any) -> ContextReport:
    config = SessionConfig(max_context_tokens=200_000, **session)
    return build_report(PROMPT, [tool("FileEdit", 400), tool("Bash", 40)], messages, config, deferred=3, extras={"agent role": "x " * 10})


class TestBuildReport:
    def test_totals_add_up_and_the_window_is_the_session_setting(self) -> None:
        built = report(conversation(2))
        assert built.total == built.system_tokens + built.tool_tokens + built.conversation_tokens
        assert built.window == 200_000
        assert built.threshold == int(200_000 * SessionConfig().compact_threshold)
        assert built.deferred == 3

    def test_the_contributors_add_up_to_the_total_and_are_sorted(self) -> None:
        built = report(conversation(2))
        sizes = [c.tokens for c in built.contributors()]
        assert sum(sizes) == built.total
        assert sizes == sorted(sizes, reverse=True)

    def test_tools_are_listed_largest_first(self) -> None:
        assert [t.label for t in report([]).tools] == ["FileEdit", "Bash"]

    def test_an_empty_conversation_is_valid(self) -> None:
        built = report([])
        assert built.conversation_tokens == 0 and built.results == ()


class TestAdvice:
    def test_the_largest_contributors_are_called_out(self) -> None:
        notes = advice(report(conversation(3)))
        assert any(n.startswith("Largest: tool results FileRead") for n in notes)
        assert all("%" in n for n in notes if n.startswith("Largest"))

    def test_nothing_is_called_out_below_the_share(self) -> None:
        built = report([])
        small = [c for c in built.contributors() if c.tokens / built.total < LARGE_SHARE]
        assert small  # the check below is meaningful
        assert not any(c.label in n for n in advice(built) for c in small)

    def test_masking_is_suggested_when_it_is_off_and_would_clear_a_lot(self) -> None:
        notes = advice(report(conversation(8), observation_masking=False))
        assert any("session.observation_masking" in n and "would clear" in n for n in notes)

    def test_masking_is_not_suggested_for_a_short_conversation(self) -> None:
        assert not any("masking" in n for n in advice(report(conversation(1, size=50))))

    def test_masking_that_is_on_but_not_due_says_when_it_will_run(self) -> None:
        built = report(conversation(4, size=200), observation_masking=True, mask_keep_last=1, mask_trigger_tokens=100_000)
        assert any("is on and will clear" in n for n in advice(built))

    def test_masking_that_is_on_and_due_adds_no_note(self) -> None:
        built = report(conversation(8), observation_masking=True, mask_keep_last=1, mask_trigger_tokens=100)
        assert not any("masking" in n.lower() for n in advice(built))

    def test_the_compaction_threshold_is_reported_when_passed(self) -> None:
        config = SessionConfig(max_context_tokens=3000, compact_threshold=0.5)
        built  = build_report(PROMPT, [], conversation(2), config)
        assert any("past the compaction threshold" in n for n in advice(built))


class TestRender:
    def test_the_text_shows_every_section_and_the_window(self) -> None:
        text = render(report(conversation(3, size=800)))
        for fragment in ("Context window", "of 200,000 tokens", "System prompt", "Tool declarations", "(2 declared, 3 deferred)",
                         "Conversation", "Tool results by tool", "FileRead", "skills catalog"):
            assert fragment in text

    def test_long_tool_lists_are_folded(self) -> None:
        config = SessionConfig()
        tools  = [tool(f"t{i}") for i in range(12)]
        text   = render(build_report(PROMPT, tools, [], config), top=3)
        assert "9 more tools" in text

    def test_a_zero_window_does_not_divide_by_zero(self) -> None:
        text = render(build_report(PROMPT, [], [], SessionConfig(max_context_tokens=0)))
        assert "n/a" in text
