"""Smaller sections of the configuration: skills, goal, workflow, telemetry, memory, tools and agents."""

from __future__ import annotations

import fnmatch

from pydantic import BaseModel, Field, field_validator

# The smallest result cap tools.max_result_chars accepts, in characters.
MIN_RESULT_CHARS = 1000


class SkillsConfig(BaseModel):
    # Also scan ~/.claude/skills and <cwd>/.claude/skills, one tier below the
    # matching .agents and nerdvana skill directories. Skills under <cwd> load
    # only for a trusted project. See core.skills.
    include_claude_skills: bool = False


class GoalConfig(BaseModel):
    # How a verified goal (a command that decides whether the objective was reached) is run.
    verify_timeout:    int = 300   # seconds before the verification command is stopped
    max_attempts:      int = 5     # failed verifications before the goal is given up
    output_tail_chars: int = 4000  # how much of the end of a failing output is shown to the model
    auto_verify:       bool = False  # without a goal, check a run that changed files with the project's detected test command


class WorkflowConfig(BaseModel):
    # Declarative multi-agent workflows (core/workflow.py); see docs/workflows.md.
    enabled:      bool = False   # offer the Workflow tool to the model (`nerdvana workflow run` always works)
    max_parallel: int  = 4       # agents of one run working at once; never above session.max_parallel_agents
    max_agents:   int  = 50      # most agent runs one `foreach` fan-out may start; a longer list stops the run


class OtelConfig(BaseModel):
    # OpenTelemetry traces of the agent, the model requests and the tool calls. Needs the `otel`
    # extra (pip install 'nerdvana-cli[otel]'). See docs/observability.md.
    enabled:         bool = False
    # OTLP/HTTP collector base URL; empty = the OTEL_EXPORTER_OTLP_ENDPOINT environment variable.
    endpoint:        str  = ""
    service_name:    str  = "nerdvana-cli"
    # Also record the conversation, tool arguments and tool results on the spans (secrets masked).
    capture_content: bool = False


class TelemetryConfig(BaseModel):
    otel: OtelConfig = Field(default_factory=OtelConfig)


class MemoryConfig(BaseModel):
    # true: a memory the agent writes, edits, renames or deletes with the memory tools waits in
    # an inbox until the user approves it (nerdvana memory inbox); nothing is stored or shown
    # to the agent before that. See docs/memory.md.
    review: bool = False


class ToolsConfig(BaseModel):
    # Tool name (or glob such as "mcp__server__*") -> most characters of a result kept in the conversation;
    # a longer result keeps its head and tail and the full text is saved to the tool-output directory.
    # Overrides the tool's own limit; empty = every tool keeps its own limit.
    max_result_chars: dict[str, int] = Field(default_factory=dict)

    @field_validator("max_result_chars")
    @classmethod
    def _check_caps(cls, caps: dict[str, int]) -> dict[str, int]:
        for name, cap in caps.items():
            if cap < MIN_RESULT_CHARS:
                raise ValueError(f"{name}: a result limit below {MIN_RESULT_CHARS} characters leaves nothing to read")
        return caps

    def result_cap(self, tool_name: str) -> int | None:
        """The character limit for *tool_name*: an exact entry first, then the longest matching glob."""
        if tool_name in self.max_result_chars:
            return self.max_result_chars[tool_name]
        matches = [pattern for pattern in self.max_result_chars if fnmatch.fnmatchcase(tool_name, pattern)]
        return self.max_result_chars[max(matches, key=len)] if matches else None


class AgentsConfig(BaseModel):
    # Category name -> model for sub-agents, written "model" or "provider:model".
    # An agent type or an Agent call that names a category runs on the mapped model.
    categories: dict[str, str] = Field(default_factory=dict)
