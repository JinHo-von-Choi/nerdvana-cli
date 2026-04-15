"""Tests for system prompt builder."""

import subprocess

from nerdvana_cli.core.prompts import build_system_prompt


class TestSystemPromptBuilder:
    def test_returns_non_empty_string(self):
        prompt = build_system_prompt(tools=[], parism_active=False)
        assert len(prompt) > 100

    def test_contains_intro_section(self):
        prompt = build_system_prompt(tools=[], parism_active=False)
        assert "\uc5d0\uc2a4\ud154" in prompt

    def test_contains_tool_guidance(self):
        prompt = build_system_prompt(tools=[], parism_active=False)
        assert "tool" in prompt.lower()

    def test_contains_efficiency_section(self):
        prompt = build_system_prompt(tools=[], parism_active=False)
        assert "straight to the point" in prompt.lower()

    def test_parism_guidance_when_active(self):
        prompt = build_system_prompt(tools=[], parism_active=True)
        assert "Parism" in prompt

    def test_no_parism_guidance_when_inactive(self):
        prompt = build_system_prompt(tools=[], parism_active=False)
        assert "Preferred Shell" not in prompt

    def test_tool_descriptions_included(self):
        class MockTool:
            def prompt(self):
                return "## TestTool\nA test tool."
        prompt = build_system_prompt(tools=[MockTool()], parism_active=False)
        assert "TestTool" in prompt

    def test_direct_answer_guidance(self):
        prompt = build_system_prompt(tools=[], parism_active=False)
        assert "When NOT to use tools" in prompt

    def test_tool_call_discipline(self):
        prompt = build_system_prompt(tools=[], parism_active=False)
        assert "5 tool calls" in prompt

    def test_environment_section(self):
        prompt = build_system_prompt(tools=[], parism_active=False, model="deepseek-chat", provider="deepseek", cwd="/tmp")
        assert "deepseek" in prompt
        assert "/tmp" in prompt


def test_environment_section_includes_platform_and_git(tmp_path):
    """Environment block must expose platform info and git state for real repos."""
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)
    (tmp_path / "a.txt").write_text("hello")
    subprocess.run(["git", "-C", str(tmp_path), "add", "a.txt"], check=True)
    subprocess.run(
        [
            "git", "-C", str(tmp_path),
            "-c", "user.email=t@t", "-c", "user.name=t",
            "commit", "-q", "-m", "init",
        ],
        check=True,
    )

    prompt = build_system_prompt(
        tools=[], model="claude-sonnet-4-6", provider="anthropic", cwd=str(tmp_path)
    )

    assert "# Environment" in prompt
    assert "Platform:" in prompt
    assert "Is a git repository: true" in prompt
    assert "Git branch: main" in prompt
    assert "Recent commits:" in prompt
