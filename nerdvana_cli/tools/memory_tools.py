"""Memory CRUD tools and Onboarding tools — Phase E.

9 tools:
  Memory CRUD:  WriteMemory, ReadMemory, ListMemories, DeleteMemory,
                RenameMemory, EditMemory
  Onboarding:   CheckOnboardingPerformed, Onboarding, InitialInstructions

Author: 최진호
Date:   2026-04-18
"""

from __future__ import annotations

import re
from typing import Any, ClassVar

from nerdvana_cli.core.memories import MemoriesManager, MemoryScope
from nerdvana_cli.core.tool import BaseTool, ToolCategory, ToolContext, ToolSideEffect
from nerdvana_cli.types import ToolResult

# ---------------------------------------------------------------------------
# Secrets scanner (v3 §5.4)
# ---------------------------------------------------------------------------

_SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("OpenAI/Anthropic key",   re.compile(r"sk-[A-Za-z0-9]{32,}")),
    ("AWS Access Key ID",      re.compile(r"AKIA[0-9A-Z]{16}")),
    ("GitHub PAT",             re.compile(r"ghp_[0-9a-zA-Z]{36}")),
    ("API key env var",        re.compile(r".*_API_KEY\s*=\s*\S+")),
    ("Authorization Bearer",   re.compile(r"Authorization:\s*Bearer\s+[^\s]+")),
]


def _scan_secrets(content: str) -> list[str]:
    """Return list of human-readable warnings for any matched secrets."""
    warnings: list[str] = []
    for label, pattern in _SECRET_PATTERNS:
        if pattern.search(content):
            warnings.append(label)
    return warnings


# ---------------------------------------------------------------------------
# Shared arg classes
# ---------------------------------------------------------------------------

class WriteMemoryArgs:
    def __init__(self, name: str, content: str, scope: str) -> None:
        self.name    = name
        self.content = content
        self.scope   = scope


class ReadMemoryArgs:
    def __init__(self, name: str) -> None:
        self.name = name


class ListMemoriesArgs:
    def __init__(self, topic: str = "") -> None:
        self.topic = topic


class DeleteMemoryArgs:
    def __init__(self, name: str) -> None:
        self.name = name


class RenameMemoryArgs:
    def __init__(self, old_name: str, new_name: str, new_scope: str = "") -> None:
        self.old_name  = old_name
        self.new_name  = new_name
        self.new_scope = new_scope


class EditMemoryArgs:
    def __init__(self, name: str, needle: str, repl: str, mode: str = "literal") -> None:
        self.name   = name
        self.needle = needle
        self.repl   = repl
        self.mode   = mode


# ---------------------------------------------------------------------------
# WriteMemory
# ---------------------------------------------------------------------------

class WriteMemoryTool(BaseTool[WriteMemoryArgs]):
    """Write or overwrite a memory entry with scope validation and secret scanning."""

    name             = "WriteMemory"
    description_text = (
        "Write content to a named memory entry.\n\n"
        "scope must be one of:\n"
        "  project_rule      — appended to NIRNA.md as a rule section\n"
        "  project_knowledge — saved to <cwd>/.nerdvana/memories/\n"
        "  user_global       — saved to ~/.nerdvana/memories/global/\n"
        "  agent_experience  — delegates to AnchorMind (returns instructions)\n\n"
        "Use slash namespaces in name (e.g. 'auth/login/rules')."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "name":    {"type": "string",  "description": "Memory name (slash namespaces allowed)"},
            "content": {"type": "string",  "description": "Content to store"},
            "scope":   {
                "type": "string",
                "enum": [s.value for s in MemoryScope],
                "description": "Storage scope (required)",
            },
        },
        "required": ["name", "content", "scope"],
    }
    args_class           = WriteMemoryArgs
    is_concurrency_safe  = False
    category: ClassVar[ToolCategory]   = ToolCategory.WRITE
    side_effects: ClassVar[ToolSideEffect] = ToolSideEffect.FILESYSTEM

    async def call(
        self,
        args: WriteMemoryArgs,
        context: ToolContext,
        can_use_tool: Any,
        on_progress: Any = None,
    ) -> ToolResult:
        # Validate scope
        try:
            scope = MemoryScope(args.scope)
        except ValueError:
            valid = ", ".join(s.value for s in MemoryScope)
            return ToolResult(
                tool_use_id="",
                content=f"Invalid scope {args.scope!r}. Valid values: {valid}",
                is_error=True,
            )

        # Secret scan
        warnings = _scan_secrets(args.content)
        if warnings:
            return ToolResult(
                tool_use_id="",
                content=(
                    "WriteMemory blocked: potential secret detected in content.\n"
                    "Matched patterns: " + ", ".join(warnings) + "\n"
                    "Remove sensitive data before storing."
                ),
                is_error=True,
            )

        mgr = MemoriesManager(context.cwd)
        try:
            msg = mgr.write(args.name, args.content, scope)
        except (NotImplementedError, ValueError, OSError) as exc:
            return ToolResult(tool_use_id="", content=str(exc), is_error=True)
        return ToolResult(tool_use_id="", content=msg, is_error=False)


# ---------------------------------------------------------------------------
# ReadMemory
# ---------------------------------------------------------------------------

class ReadMemoryTool(BaseTool[ReadMemoryArgs]):
    """Read a memory entry by name."""

    name             = "ReadMemory"
    description_text = (
        "Read the content of a named memory entry.\n\n"
        "Searches PROJECT_KNOWLEDGE then USER_GLOBAL scope.\n"
        "Use ListMemories first to discover available names."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Memory name to read"},
        },
        "required": ["name"],
    }
    args_class          = ReadMemoryArgs
    is_concurrency_safe = True
    category: ClassVar[ToolCategory]   = ToolCategory.READ
    side_effects: ClassVar[ToolSideEffect] = ToolSideEffect.NONE

    async def call(
        self,
        args: ReadMemoryArgs,
        context: ToolContext,
        can_use_tool: Any,
        on_progress: Any = None,
    ) -> ToolResult:
        mgr = MemoriesManager(context.cwd)
        try:
            content = mgr.read(args.name)
        except FileNotFoundError as exc:
            return ToolResult(tool_use_id="", content=str(exc), is_error=True)
        return ToolResult(tool_use_id="", content=content, is_error=False)


# ---------------------------------------------------------------------------
# ListMemories
# ---------------------------------------------------------------------------

class ListMemoriesTool(BaseTool[ListMemoriesArgs]):
    """List available memory entries, optionally filtered by topic prefix."""

    name             = "ListMemories"
    description_text = (
        "List all memory entries.\n\n"
        "Optional topic filter uses slash-namespace prefix (e.g. 'auth/login').\n"
        "Returns name, scope, size, and last-modified time for each entry."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "topic": {"type": "string", "description": "Slash-namespace prefix filter (optional)"},
        },
        "required": [],
    }
    args_class          = ListMemoriesArgs
    is_concurrency_safe = True
    category: ClassVar[ToolCategory]   = ToolCategory.READ
    side_effects: ClassVar[ToolSideEffect] = ToolSideEffect.NONE

    async def call(
        self,
        args: ListMemoriesArgs,
        context: ToolContext,
        can_use_tool: Any,
        on_progress: Any = None,
    ) -> ToolResult:
        mgr     = MemoriesManager(context.cwd)
        topic   = args.topic if args.topic else None
        entries = mgr.list_memories(topic=topic)
        if not entries:
            msg = "No memories found." if not topic else f"No memories found for topic '{topic}'."
            return ToolResult(tool_use_id="", content=msg, is_error=False)

        import datetime
        lines: list[str] = []
        for e in entries:
            dt  = datetime.datetime.fromtimestamp(e.mtime).strftime("%Y-%m-%d %H:%M")
            lines.append(f"  {e.name:<40}  {e.scope:<20}  {e.size:>6}B  {dt}")
        header = f"{'Name':<40}  {'Scope':<20}  {'Size':>7}  {'Modified'}"
        body   = "\n".join([header, "-" * 80] + lines)
        return ToolResult(tool_use_id="", content=body, is_error=False)


# ---------------------------------------------------------------------------
# DeleteMemory
# ---------------------------------------------------------------------------

class DeleteMemoryTool(BaseTool[DeleteMemoryArgs]):
    """Delete a memory entry by name."""

    name             = "DeleteMemory"
    description_text = (
        "Delete a named memory entry.\n\n"
        "Only use this when explicitly requested. "
        "Searches PROJECT_KNOWLEDGE then USER_GLOBAL scope."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Memory name to delete"},
        },
        "required": ["name"],
    }
    args_class           = DeleteMemoryArgs
    is_concurrency_safe  = False
    category: ClassVar[ToolCategory]   = ToolCategory.DESTRUCTIVE
    side_effects: ClassVar[ToolSideEffect] = ToolSideEffect.FILESYSTEM

    async def call(
        self,
        args: DeleteMemoryArgs,
        context: ToolContext,
        can_use_tool: Any,
        on_progress: Any = None,
    ) -> ToolResult:
        mgr = MemoriesManager(context.cwd)
        try:
            msg = mgr.delete(args.name)
        except FileNotFoundError as exc:
            return ToolResult(tool_use_id="", content=str(exc), is_error=True)
        return ToolResult(tool_use_id="", content=msg, is_error=False)


# ---------------------------------------------------------------------------
# RenameMemory
# ---------------------------------------------------------------------------

class RenameMemoryTool(BaseTool[RenameMemoryArgs]):
    """Rename a memory entry, optionally changing its scope."""

    name             = "RenameMemory"
    description_text = (
        "Rename a memory entry and optionally move it to a different scope.\n\n"
        "new_scope is optional; if omitted the original scope is preserved.\n"
        "Scope values: project_knowledge, user_global."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "old_name":  {"type": "string", "description": "Current memory name"},
            "new_name":  {"type": "string", "description": "New memory name"},
            "new_scope": {
                "type": "string",
                "enum": ["project_knowledge", "user_global", ""],
                "description": "Target scope (optional; keeps original if empty)",
            },
        },
        "required": ["old_name", "new_name"],
    }
    args_class           = RenameMemoryArgs
    is_concurrency_safe  = False
    category: ClassVar[ToolCategory]   = ToolCategory.WRITE
    side_effects: ClassVar[ToolSideEffect] = ToolSideEffect.FILESYSTEM

    async def call(
        self,
        args: RenameMemoryArgs,
        context: ToolContext,
        can_use_tool: Any,
        on_progress: Any = None,
    ) -> ToolResult:
        target_scope: MemoryScope | None = None
        if args.new_scope:
            try:
                target_scope = MemoryScope(args.new_scope)
            except ValueError:
                return ToolResult(
                    tool_use_id="",
                    content=f"Invalid new_scope {args.new_scope!r}.",
                    is_error=True,
                )
        mgr = MemoriesManager(context.cwd)
        try:
            msg = mgr.rename(args.old_name, args.new_name, new_scope=target_scope)
        except (FileNotFoundError, ValueError) as exc:
            return ToolResult(tool_use_id="", content=str(exc), is_error=True)
        return ToolResult(tool_use_id="", content=msg, is_error=False)


# ---------------------------------------------------------------------------
# EditMemory
# ---------------------------------------------------------------------------

class EditMemoryTool(BaseTool[EditMemoryArgs]):
    """In-place search-and-replace within an existing memory entry."""

    name             = "EditMemory"
    description_text = (
        "Apply a search-and-replace edit to an existing memory.\n\n"
        "mode: 'literal' (default) for exact string match, 'regex' for re.sub.\n"
        "All occurrences are replaced."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "name":   {"type": "string", "description": "Memory name to edit"},
            "needle": {"type": "string", "description": "Pattern to search for"},
            "repl":   {"type": "string", "description": "Replacement string"},
            "mode":   {
                "type": "string",
                "enum": ["literal", "regex"],
                "description": "Match mode (default: literal)",
                "default": "literal",
            },
        },
        "required": ["name", "needle", "repl"],
    }
    args_class           = EditMemoryArgs
    is_concurrency_safe  = False
    category: ClassVar[ToolCategory]   = ToolCategory.WRITE
    side_effects: ClassVar[ToolSideEffect] = ToolSideEffect.FILESYSTEM

    async def call(
        self,
        args: EditMemoryArgs,
        context: ToolContext,
        can_use_tool: Any,
        on_progress: Any = None,
    ) -> ToolResult:
        mgr = MemoriesManager(context.cwd)
        try:
            msg = mgr.edit(args.name, args.needle, args.repl, mode=args.mode)
        except (FileNotFoundError, ValueError) as exc:
            return ToolResult(tool_use_id="", content=str(exc), is_error=True)
        return ToolResult(tool_use_id="", content=msg, is_error=False)


# ===========================================================================
# Onboarding tools
# ===========================================================================

class CheckOnboardingPerformedTool(BaseTool[None]):
    """Check whether onboarding has been performed for the current project."""

    name             = "CheckOnboardingPerformed"
    description_text = (
        "Check whether the current project has been onboarded.\n\n"
        "Returns True if <cwd>/.nerdvana/memories/onboarding/ exists, "
        "False otherwise."
    )
    input_schema        = {"type": "object", "properties": {}, "required": []}
    is_concurrency_safe = True
    category: ClassVar[ToolCategory]   = ToolCategory.READ
    side_effects: ClassVar[ToolSideEffect] = ToolSideEffect.NONE

    async def call(
        self,
        args: None,
        context: ToolContext,
        can_use_tool: Any,
        on_progress: Any = None,
    ) -> ToolResult:
        mgr   = MemoriesManager(context.cwd)
        done  = mgr.onboarding_exists()
        state = "completed" if done else "not yet performed"
        return ToolResult(
            tool_use_id="",
            content=f"Onboarding for '{context.cwd}': {state}.",
            is_error=False,
        )

    def parse_args(self, raw: dict[str, Any]) -> None:
        return None


class OnboardingTool(BaseTool[None]):
    """Perform project onboarding — scan the project and store initial memories."""

    name             = "Onboarding"
    description_text = (
        "Perform initial project onboarding.\n\n"
        "The agent should:\n"
        "1. Read NIRNA.md, README.md, pyproject.toml / package.json, "
        "   and the top-level directory listing.\n"
        "2. Identify build commands, test commands, and key architectural facts.\n"
        "3. Write them as project_knowledge memories with descriptive names.\n"
        "4. Mark onboarding as done (the tool does this automatically).\n\n"
        "This prompt is returned as the tool result; the agent should "
        "then execute the described steps."
    )
    input_schema        = {"type": "object", "properties": {}, "required": []}
    is_concurrency_safe = False
    category: ClassVar[ToolCategory]   = ToolCategory.WRITE
    side_effects: ClassVar[ToolSideEffect] = ToolSideEffect.FILESYSTEM

    async def call(
        self,
        args: None,
        context: ToolContext,
        can_use_tool: Any,
        on_progress: Any = None,
    ) -> ToolResult:
        mgr = MemoriesManager(context.cwd)
        mgr.mark_onboarding_done()
        prompt = (
            f"Project onboarding initiated for: {context.cwd}\n\n"
            "Steps to complete:\n"
            "1. Read NIRNA.md (if present) — extract build, test, lint commands.\n"
            "2. Read README.md — extract high-level architecture and setup steps.\n"
            "3. Read pyproject.toml or package.json — extract dependencies and scripts.\n"
            "4. Run ListMemories to check what is already stored.\n"
            "5. Write key facts using WriteMemory with scope=project_knowledge.\n"
            "   Suggested names: 'build-commands', 'test-commands', 'architecture', 'gotchas'.\n"
            "6. Onboarding stamp has been created at "
            f"{context.cwd}/.nerdvana/memories/onboarding/\n"
        )
        return ToolResult(tool_use_id="", content=prompt, is_error=False)

    def parse_args(self, raw: dict[str, Any]) -> None:
        return None


class InitialInstructionsTool(BaseTool[None]):
    """Supply NIRNA.md contents and memory summary to the agent as context."""

    name             = "InitialInstructions"
    description_text = (
        "Return NIRNA.md content combined with a summary of stored memories.\n\n"
        "Use this at session start to prime the agent with project context.\n"
        "Does NOT auto-inject on every turn (call explicitly to avoid token waste)."
    )
    input_schema        = {"type": "object", "properties": {}, "required": []}
    is_concurrency_safe = True
    category: ClassVar[ToolCategory]   = ToolCategory.READ
    side_effects: ClassVar[ToolSideEffect] = ToolSideEffect.NONE

    async def call(
        self,
        args: None,
        context: ToolContext,
        can_use_tool: Any,
        on_progress: Any = None,
    ) -> ToolResult:
        import os

        parts: list[str] = []

        # NIRNA.md
        nirnamd = os.path.join(context.cwd, "NIRNA.md")
        if os.path.isfile(nirnamd):
            with open(nirnamd, encoding="utf-8") as fh:
                parts.append(f"=== NIRNA.md ===\n{fh.read()}")
        else:
            parts.append("=== NIRNA.md ===\n(not found)")

        # Memory summary
        mgr     = MemoriesManager(context.cwd)
        hint    = mgr.session_start_hint()
        entries = mgr.list_memories()
        if entries:
            import datetime
            lines = []
            for e in entries:
                dt = datetime.datetime.fromtimestamp(e.mtime).strftime("%Y-%m-%d")
                lines.append(f"  {e.name} [{e.scope}] {dt}")
            parts.append("=== Project Memories ===\n" + "\n".join(lines))
        else:
            parts.append(f"=== Project Memories ===\n{hint if hint else 'None stored.'}")

        return ToolResult(
            tool_use_id="",
            content="\n\n".join(parts),
            is_error=False,
        )

    def parse_args(self, raw: dict[str, Any]) -> None:
        return None
