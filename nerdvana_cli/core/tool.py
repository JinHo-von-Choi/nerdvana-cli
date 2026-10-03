"""Tool base interface and factory — inspired by Claude Code's Tool.ts."""

from __future__ import annotations

import os
import uuid
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, ClassVar, Generic, TypeVar, cast

from nerdvana_cli.core.context.token_estimator import approx_tokens
from nerdvana_cli.types import PermissionBehavior, PermissionResult, ToolResult

T = TypeVar("T")

# Interactive question hook: receives the question and the suggested options and
# resolves to the user's answer, or None when the user dismissed the prompt.
AskUserCallback = Callable[[str, list[str]], Awaitable[str | None]]

# Permission confirmation hook: receives the tool name and the reason it needs
# approval, and resolves to True only when the user allowed the call.
ConfirmCallback = Callable[[str, str], Awaitable[bool]]

# Directory where full outputs are kept when a result is truncated. Set by the
# tool executor for the duration of one call; unset means no copy is kept.
TOOL_OUTPUT_DIR: ContextVar[str | None] = ContextVar("tool_output_dir", default=None)

# Most characters of a result to keep (``tools.max_result_chars``). Set by the tool executor for the duration
# of one call; unset means the tool's own ``max_result_tokens`` applies.
TOOL_RESULT_CAP: ContextVar[int | None] = ContextVar("tool_result_cap", default=None)

# Share of a truncated result kept from the start; the rest comes from the end.
_HEAD_SHARE = 0.6
# Characters a character-limited result leaves for the note that says it was cut (it also names the saved file).
_NOTE_RESERVE = 400


def _save_full_output(tool_name: str, content: str) -> str | None:
    """Write *content* under ``TOOL_OUTPUT_DIR``; return the path, or None."""
    directory = TOOL_OUTPUT_DIR.get()
    if not directory:
        return None
    try:
        os.makedirs(directory, mode=0o700, exist_ok=True)
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in tool_name) or "tool"
        path = Path(directory) / f"{safe}-{uuid.uuid4().hex[:8]}.txt"
        path.write_text(content, encoding="utf-8")
        return str(path)
    except OSError:
        return None


class ToolCategory(StrEnum):
    """Functional classification of a tool's primary intent."""

    READ        = "read"
    WRITE       = "write"
    DESTRUCTIVE = "destructive"
    SYMBOLIC    = "symbolic"   # symbol-analysis tools
    META        = "meta"       # Agent/Swarm/Team/TaskGet/TaskStop etc.


class ToolSideEffect(StrEnum):
    """Observable side-effects a tool may produce beyond its return value."""

    NONE       = "none"
    FILESYSTEM = "filesystem"
    PROCESS    = "process"
    NETWORK    = "network"
    EXTERNAL   = "external"


class ToolContext:
    """Runtime context passed to every tool call."""

    def __init__(
        self,
        cwd:             str  = ".",
        max_result_size: int  = 500_000,
        task_registry:   Any  = None,
        ask_user:        AskUserCallback | None = None,
        confirm:         ConfirmCallback | None = None,
    ) -> None:
        self.cwd             = cwd
        self.max_result_size = max_result_size
        self.file_state:     dict[str, str] = {}
        self.state:          dict[str, Any] = {}
        self.task_registry                  = task_registry
        self.ask_user                       = ask_user
        self.confirm                        = confirm


class BaseTool(ABC, Generic[T]):
    """Abstract base for all tools."""

    name:                str            = ""
    description_text:    str            = ""
    input_schema:        dict[str, Any] = {}
    is_concurrency_safe: bool           = False
    is_destructive:      bool           = False
    max_result_tokens:   int            = 30_000
    args_class:          type | None    = None

    # Metadata
    category:              ClassVar[ToolCategory]    = ToolCategory.READ
    side_effects:          ClassVar[ToolSideEffect]  = ToolSideEffect.NONE
    tags:                  ClassVar[frozenset[str]]  = frozenset()
    requires_confirmation: ClassVar[bool]            = False
    # Arguments outside ``input_schema`` are rejected unless the schema says otherwise.
    reject_unknown_args:   ClassVar[bool]            = True

    @property
    def is_read_only(self) -> bool:
        """Backward-compatible: True when category is READ or SYMBOLIC."""
        return self.category in {ToolCategory.READ, ToolCategory.SYMBOLIC}

    def parse_args(self, raw: dict[str, Any]) -> Any:
        """Convert raw dict from API response to typed Args object."""
        if self.args_class is None:
            return raw
        import inspect
        sig = inspect.signature(self.args_class.__init__)  # type: ignore[misc]
        valid_keys = {p for p in sig.parameters if p != "self"}
        filtered = {k: v for k, v in raw.items() if k in valid_keys}
        return self.args_class(**filtered)

    @abstractmethod
    async def call(
        self,
        args: T,
        context: ToolContext,
        can_use_tool: Any,
        on_progress: Any = None,
    ) -> ToolResult:
        """Execute the tool and return a ToolResult."""
        ...

    def prompt(self) -> str:
        """Generate the tool description shown to the model."""
        return f"## {self.name}\n\n{self.description_text}\n\nInput schema: {self.input_schema}"

    def check_permissions(self, args: T, context: ToolContext) -> PermissionResult:
        """Tool-specific permission check. Default: allow."""
        return PermissionResult(behavior=PermissionBehavior.ALLOW)

    def validate_input(self, args: T, context: ToolContext) -> str | None:
        """Validate input before execution. Return error message or None."""
        return None

    def truncate_result(self, content: str) -> str:
        """Bound *content* to ``max_result_tokens``, or to the character limit of ``TOOL_RESULT_CAP``, keeping its head and tail.

        When the executor has set ``TOOL_OUTPUT_DIR`` the full output is saved
        there first and the note names the file.
        """
        cap = TOOL_RESULT_CAP.get()
        if cap is not None:
            return self._truncate_to_chars(content, cap)
        total = approx_tokens(content)
        if total <= self.max_result_tokens:
            return content
        keep = int(len(content) * self.max_result_tokens / total * 0.9)
        return self._cut(content, keep, f"about {total - self.max_result_tokens} of {total} estimated tokens omitted.")

    def _truncate_to_chars(self, content: str, cap: int) -> str:
        """*content* cut to about *cap* characters; the note is part of the budget, so a cut result is not cut again."""
        if len(content) <= cap:
            return content
        keep = max(cap - _NOTE_RESERVE, cap // 2)
        return self._cut(content, keep, f"{len(content) - keep} of {len(content)} characters omitted.")

    def _cut(self, content: str, keep: int, omitted: str) -> str:
        """Keep *keep* characters of *content*, head and tail, and say what was left out and where the whole is."""
        saved = _save_full_output(self.name, content)
        head  = int(keep * _HEAD_SHARE)
        tail  = keep - head
        where = (
            f" Full output saved to {saved}; read parts of it with Bash (for example sed -n '1,200p')."
            if saved else ""
        )
        note = (
            f"\n\n... [truncated: {omitted}"
            f"{where} Prefer narrowing the request (offset/limit, a more specific pattern).] ...\n\n"
        )
        return content[:head] + note + (content[-tail:] if tail > 0 else "")


@dataclass
class ToolDef(Generic[T]):
    """Partial tool definition — filled by build_tool() factory."""

    name:                  str
    description_text:      str
    input_schema:          dict[str, Any]
    call_fn:               Any
    is_concurrency_safe:   bool             = False
    # is_read_only is derived from category; kept for back-compat at call sites
    is_read_only:          bool             = False
    is_destructive:        bool             = False
    max_result_tokens:     int              = 30_000
    check_permissions_fn:  Any              = None
    validate_input_fn:     Any              = None
    prompt_fn:             Any              = None
    # Metadata
    category:              ToolCategory     = ToolCategory.READ
    side_effects:          ToolSideEffect   = ToolSideEffect.NONE
    tags:                  frozenset[str]   = frozenset()
    requires_confirmation: bool             = False


def build_tool(defn: ToolDef[T]) -> BaseTool[T]:
    """Factory that creates a concrete tool from a definition.

    is_read_only in ToolDef is honoured for backward compatibility: when True,
    the generated tool's category is set to READ (unless caller supplies an
    explicit category != the default READ).
    """
    # Resolve effective category: explicit category takes precedence;
    # fall back to READ when is_read_only=True, WRITE otherwise.
    effective_category: ToolCategory
    if defn.category != ToolCategory.READ:
        effective_category = defn.category
    elif defn.is_read_only:
        effective_category = ToolCategory.READ
    else:
        effective_category = ToolCategory.WRITE

    _cat  = effective_category
    _se   = defn.side_effects
    _tags = defn.tags
    _rc   = defn.requires_confirmation

    class _Tool(BaseTool[T]):
        name             = defn.name
        description_text = defn.description_text
        input_schema     = defn.input_schema
        is_concurrency_safe = defn.is_concurrency_safe
        is_destructive   = defn.is_destructive
        max_result_tokens = defn.max_result_tokens
        category: ClassVar[ToolCategory]   = _cat
        side_effects: ClassVar[ToolSideEffect] = _se
        tags: ClassVar[frozenset[str]]     = _tags
        requires_confirmation: ClassVar[bool] = _rc

        async def call(
            self,
            args: T,
            context: ToolContext,
            can_use_tool: Any,
            on_progress: Any = None,
        ) -> ToolResult:
            return cast(ToolResult, await defn.call_fn(args, context, on_progress))

        if defn.check_permissions_fn is not None:

            def check_permissions(self: Any, args: T, context: ToolContext) -> PermissionResult:
                return cast(PermissionResult, defn.check_permissions_fn(args, context))

        if defn.validate_input_fn is not None:

            def validate_input(self: Any, args: T, context: ToolContext) -> str | None:
                return cast("str | None", defn.validate_input_fn(args, context))

        if defn.prompt_fn is not None:

            def prompt(self: Any) -> str:
                return cast(str, defn.prompt_fn())

    return _Tool()


class ToolRegistry:
    """Central registry of all available tools."""

    def __init__(self) -> None:
        self._tools: dict[str, BaseTool[Any]] = {}

    def register(self, tool: BaseTool[Any]) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> BaseTool[Any] | None:
        return self._tools.get(name)

    def all_tools(self) -> list[BaseTool[Any]]:
        return list(self._tools.values())

    def concurrency_safe_tools(self) -> list[BaseTool[Any]]:
        return [t for t in self._tools.values() if t.is_concurrency_safe]

    def serial_tools(self) -> list[BaseTool[Any]]:
        return [t for t in self._tools.values() if not t.is_concurrency_safe]

    def tool_schemas(self) -> list[dict[str, Any]]:
        return [
            {
                "name": t.name,
                "description": t.description_text,
                "input_schema": t.input_schema,
            }
            for t in self._tools.values()
        ]

    def filter(
        self,
        *,
        category:     ToolCategory | set[ToolCategory] | None  = None,
        side_effects: ToolSideEffect | set[ToolSideEffect] | None = None,
        tags_any:     set[str] | None                          = None,
        tags_all:     set[str] | None                          = None,
        read_only:    bool | None                              = None,
        requires_confirmation: bool | None                     = None,
    ) -> list[BaseTool[Any]]:
        """Return tools matching all supplied predicates (AND semantics).

        Parameters
        ----------
        category:
            Single category or set of categories to include.
        side_effects:
            Single side-effect or set of side-effects to include.
        tags_any:
            At least one of these tags must be present.
        tags_all:
            All of these tags must be present.
        read_only:
            If True, include only read-only tools; if False, exclude them.
        requires_confirmation:
            Filter by requires_confirmation flag.
        """
        cat_set  = ({category}  if isinstance(category,    ToolCategory)    else category)
        side_set = ({side_effects} if isinstance(side_effects, ToolSideEffect) else side_effects)

        result: list[BaseTool[Any]] = []
        for tool in self._tools.values():
            if cat_set is not None and tool.category not in cat_set:
                continue
            if side_set is not None and tool.side_effects not in side_set:
                continue
            if tags_any is not None and not (tool.tags & tags_any):
                continue
            if tags_all is not None and not tags_all.issubset(tool.tags):
                continue
            if read_only is not None and tool.is_read_only != read_only:
                continue
            if requires_confirmation is not None and tool.requires_confirmation != requires_confirmation:
                continue
            result.append(tool)
        return result
