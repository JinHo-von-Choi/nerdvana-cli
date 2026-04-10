"""Built-in agent type definitions."""

from __future__ import annotations

from nerdvana_cli.agents.registry import AgentDefinition

BUILTIN_AGENTS: list[AgentDefinition] = [
    AgentDefinition(
        agent_type="general-purpose",
        description="General-purpose agent for research, code, and multi-step tasks.",
        max_turns=50,
        allowed_tools=["*"],
    ),
    AgentDefinition(
        agent_type="Explore",
        description="Fast agent for codebase exploration — glob, grep, read only.",
        max_turns=20,
        allowed_tools=["Glob", "Grep", "FileRead", "Bash"],
        system_prompt=(
            "You are an exploration agent. Use search and read tools to answer "
            "questions about the codebase. Do not write or edit files. "
            "Return a concise factual report."
        ),
    ),
    AgentDefinition(
        agent_type="Plan",
        description="Software architect agent for designing implementation plans.",
        max_turns=20,
        allowed_tools=["Glob", "Grep", "FileRead", "Bash"],
        system_prompt=(
            "You are an architect agent. Analyze the codebase and produce a "
            "structured implementation plan. Do not write code — only plan."
        ),
    ),
    AgentDefinition(
        agent_type="code-reviewer",
        description="Code review agent — read-only analysis of code quality and correctness.",
        max_turns=15,
        allowed_tools=["FileRead", "Grep", "Glob"],
        system_prompt=(
            "You are a code review agent. Read files, search for patterns, and "
            "identify bugs, security issues, and style problems. "
            "Do not modify files. Return a structured review report."
        ),
    ),
    AgentDefinition(
        agent_type="git-management",
        description="Git operations agent — commit, branch, status, log.",
        max_turns=20,
        allowed_tools=["Bash", "FileRead"],
        system_prompt=(
            "You are a git management agent. Use Bash for git commands only. "
            "Do not modify source files directly. "
            "Perform git operations: status, add, commit, branch, log, diff."
        ),
    ),
    AgentDefinition(
        agent_type="test-writer",
        description="Test writing agent — creates and runs unit and integration tests.",
        max_turns=30,
        allowed_tools=["*"],
        system_prompt=(
            "You are a test-writing agent. Write thorough tests using the project's "
            "existing test framework. Follow TDD: write failing test first, "
            "then implement minimal code to pass. Do not refactor existing code."
        ),
    ),
]
