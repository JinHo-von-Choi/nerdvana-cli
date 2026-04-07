"""Tool base interface and factory — inspired by Claude Code's Tool.ts."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from nerdvana_cli.types import PermissionBehavior, PermissionResult, ToolResult

T = TypeVar("T")


class ToolContext:
    """Runtime context passed to every tool call."""

    def __init__(self, cwd: str = ".", max_result_size: int = 500_000):
        self.cwd = cwd
        self.max_result_size = max_result_size
        self.file_state: dict[str, str] = {}
        self.state: dict[str, Any] = {}


class BaseTool(ABC, Generic[T]):
    """Abstract base for all tools."""

    name: str = ""
    description_text: str = ""
    input_schema: dict[str, Any] = {}
    is_concurrency_safe: bool = False
    is_read_only: bool = False
    is_destructive: bool = False
    max_result_size: int = 500_000
    args_class: type | None = None

    def parse_args(self, raw: dict[str, Any]) -> Any:
        """Convert raw dict from API response to typed Args object."""
        if self.args_class is None:
            return raw
        import inspect
        sig = inspect.signature(self.args_class.__init__)
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
        if len(content) > self.max_result_size:
            half = self.max_result_size // 2
            return (
                content[:half]
                + f"\n\n... [truncated, {len(content) - self.max_result_size} chars omitted] ...\n\n"
                + content[-half:]
            )
        return content


@dataclass
class ToolDef(Generic[T]):
    """Partial tool definition — filled by build_tool() factory."""

    name: str
    description_text: str
    input_schema: dict[str, Any]
    call_fn: Any
    is_concurrency_safe: bool = False
    is_read_only: bool = False
    is_destructive: bool = False
    max_result_size: int = 500_000
    check_permissions_fn: Any = None
    validate_input_fn: Any = None
    prompt_fn: Any = None


def build_tool(defn: ToolDef[T]) -> BaseTool[T]:
    """Factory that creates a concrete tool from a definition."""

    class _Tool(BaseTool[T]):
        name = defn.name
        description_text = defn.description_text
        input_schema = defn.input_schema
        is_concurrency_safe = defn.is_concurrency_safe
        is_read_only = defn.is_read_only
        is_destructive = defn.is_destructive
        max_result_size = defn.max_result_size

        async def call(
            self,
            args: T,
            context: ToolContext,
            can_use_tool: Any,
            on_progress: Any = None,
        ) -> ToolResult:
            return await defn.call_fn(args, context, on_progress)

        if defn.check_permissions_fn is not None:

            def check_permissions(self, args, context):
                return defn.check_permissions_fn(args, context)

        if defn.validate_input_fn is not None:

            def validate_input(self, args, context):
                return defn.validate_input_fn(args, context)

        if defn.prompt_fn is not None:

            def prompt(self):
                return defn.prompt_fn()

    return _Tool()


class ToolRegistry:
    """Central registry of all available tools."""

    def __init__(self):
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> BaseTool | None:
        return self._tools.get(name)

    def all_tools(self) -> list[BaseTool]:
        return list(self._tools.values())

    def concurrency_safe_tools(self) -> list[BaseTool]:
        return [t for t in self._tools.values() if t.is_concurrency_safe]

    def serial_tools(self) -> list[BaseTool]:
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
