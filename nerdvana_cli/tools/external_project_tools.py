"""External project query tools — Phase H.

Provides three MCP-visible tools that allow the agent to:
  1. List all registered queryable external projects.
  2. Register a new external project (validates path safety).
  3. Query an external project via an isolated subprocess.

작성자: 최진호
작성일: 2026-04-18
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nerdvana_cli.core.external_projects import ExternalProject, ExternalProjectRegistry
from nerdvana_cli.core.tool import BaseTool, ToolCategory, ToolContext, ToolSideEffect
from nerdvana_cli.server.external_worker import ExternalWorker
from nerdvana_cli.types import PermissionBehavior, PermissionResult, ToolResult

# ---------------------------------------------------------------------------
# ListQueryableProjects
# ---------------------------------------------------------------------------

class ListQueryableProjectsTool(BaseTool[None]):
    """Return the list of registered external projects that can be queried."""

    name             = "ListQueryableProjects"
    description_text = (
        "List all external projects registered for subprocess-isolated querying. "
        "Returns project names, paths, and language tags."
    )
    input_schema: dict[str, Any] = {"type": "object", "properties": {}, "required": []}
    category         = ToolCategory.READ
    side_effects     = ToolSideEffect.NONE
    tags             = frozenset({"external", "project"})
    is_concurrency_safe = True

    def __init__(self, registry: ExternalProjectRegistry | None = None) -> None:
        self._registry = registry if registry is not None else ExternalProjectRegistry()

    async def call(
        self,
        args: None,
        context: ToolContext,
        can_use_tool: Any,
        on_progress: Any = None,
    ) -> ToolResult:
        projects = self._registry.list_all()
        if not projects:
            return ToolResult(tool_use_id="", content="No external projects registered.")

        lines = ["Registered external projects:\n"]
        for p in projects:
            langs = ", ".join(p.languages) if p.languages else "—"
            lines.append(f"  {p.name}: {p.path}  [{langs}]")
        return ToolResult(tool_use_id="", content="\n".join(lines))

    def check_permissions(self, args: Any, context: ToolContext) -> PermissionResult:
        return PermissionResult(behavior=PermissionBehavior.ALLOW)


# ---------------------------------------------------------------------------
# RegisterExternalProject
# ---------------------------------------------------------------------------

@dataclass
class RegisterExternalProjectArgs:
    name:      str
    path:      str
    languages: list[str] | None = None


class RegisterExternalProjectTool(BaseTool[RegisterExternalProjectArgs]):
    """Register an external project directory for subprocess-isolated querying."""

    name             = "RegisterExternalProject"
    description_text = (
        "Register an external project so it can be queried via QueryExternalProject. "
        "The path must be an existing directory. Symlink traversal outside the "
        "target directory is blocked for safety."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "name": {
                "type":        "string",
                "description": "Short identifier for the project (used in QueryExternalProject).",
            },
            "path": {
                "type":        "string",
                "description": "Absolute path to the project root directory.",
            },
            "languages": {
                "type":  "array",
                "items": {"type": "string"},
                "description": "Primary programming languages (e.g. ['python', 'typescript']).",
            },
        },
        "required": ["name", "path"],
    }
    category         = ToolCategory.WRITE
    side_effects     = ToolSideEffect.FILESYSTEM
    tags             = frozenset({"external", "project"})
    is_concurrency_safe = False
    args_class       = RegisterExternalProjectArgs

    def __init__(self, registry: ExternalProjectRegistry | None = None) -> None:
        self._registry = registry if registry is not None else ExternalProjectRegistry()

    async def call(
        self,
        args: RegisterExternalProjectArgs,
        context: ToolContext,
        can_use_tool: Any,
        on_progress: Any = None,
    ) -> ToolResult:
        # Resolve and validate path.
        try:
            resolved = self._safe_resolve(args.path)
        except (ValueError, OSError) as exc:
            return ToolResult(
                tool_use_id="",
                content=f"Error: {exc}",
                is_error=True,
            )

        project = ExternalProject(
            name      = args.name,
            path      = str(resolved),
            languages = list(args.languages or []),
        )
        self._registry.add(project)
        return ToolResult(
            tool_use_id = "",
            content     = (
                f"Registered external project '{project.name}' at {project.path}."
            ),
        )

    def check_permissions(self, args: Any, context: ToolContext) -> PermissionResult:
        return PermissionResult(behavior=PermissionBehavior.ASK)

    @staticmethod
    def _safe_resolve(raw_path: str) -> Path:
        """Resolve *raw_path* and guard against path traversal.

        Rules:
        - The path must exist and be a directory.
        - Symlinks are resolved; the resolved canonical path must point to an
          existing directory (no dangling symlinks accepted).

        Returns the resolved ``Path`` on success.
        Raises ``ValueError`` when the path is invalid or unsafe.
        """
        candidate = Path(raw_path).expanduser()

        if not candidate.exists():
            raise ValueError(f"Path does not exist: {raw_path}")
        if not candidate.is_dir():
            raise ValueError(f"Path is not a directory: {raw_path}")

        # Resolve symlinks to the canonical path.
        try:
            resolved = candidate.resolve(strict=True)
        except OSError as exc:
            raise ValueError(f"Cannot resolve path {raw_path}: {exc}") from exc

        if not resolved.is_dir():
            raise ValueError(f"Resolved path is not a directory: {resolved}")

        return resolved


# ---------------------------------------------------------------------------
# QueryExternalProject
# ---------------------------------------------------------------------------

@dataclass
class QueryExternalProjectArgs:
    name:     str
    question: str


class QueryExternalProjectTool(BaseTool[QueryExternalProjectArgs]):
    """Query a registered external project via a subprocess-isolated MCP channel."""

    name             = "QueryExternalProject"
    description_text = (
        "Ask a natural-language question about a registered external project. "
        "Spawns an isolated subprocess (no write access), forwards the question, "
        "returns the answer, then immediately terminates the subprocess. "
        "Maximum 3 concurrent queries; excess requests return a 'queue full' error."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "name": {
                "type":        "string",
                "description": "Name of the registered external project to query.",
            },
            "question": {
                "type":        "string",
                "description": "Natural-language question about the project.",
            },
        },
        "required": ["name", "question"],
    }
    category         = ToolCategory.READ
    side_effects     = ToolSideEffect.PROCESS
    tags             = frozenset({"external", "project", "query"})
    is_concurrency_safe = True
    args_class       = QueryExternalProjectArgs

    def __init__(
        self,
        registry: ExternalProjectRegistry | None = None,
        worker:   ExternalWorker          | None = None,
    ) -> None:
        self._registry = registry if registry is not None else ExternalProjectRegistry()
        self._worker   = worker   if worker   is not None else ExternalWorker()

    async def call(
        self,
        args: QueryExternalProjectArgs,
        context: ToolContext,
        can_use_tool: Any,
        on_progress: Any = None,
    ) -> ToolResult:
        project = self._registry.get(args.name)
        if project is None:
            return ToolResult(
                tool_use_id = "",
                content     = (
                    f"Error: no external project named '{args.name}' is registered. "
                    "Use ListQueryableProjects to see available projects."
                ),
                is_error    = True,
            )

        try:
            answer = await self._worker.send_query(project, args.question)
        except RuntimeError as exc:
            # Subprocess or capacity errors — main loop is unaffected.
            return ToolResult(
                tool_use_id = "",
                content     = f"Error querying '{args.name}': {exc}",
                is_error    = True,
            )
        except TimeoutError:
            return ToolResult(
                tool_use_id = "",
                content     = (
                    f"Error: query to '{args.name}' timed out. "
                    "The subprocess was terminated."
                ),
                is_error    = True,
            )

        return ToolResult(tool_use_id="", content=answer)

    def check_permissions(self, args: Any, context: ToolContext) -> PermissionResult:
        return PermissionResult(behavior=PermissionBehavior.ASK)
