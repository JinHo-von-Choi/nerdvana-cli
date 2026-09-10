"""External project query tools — Phase H.

Provides three MCP-visible tools that allow the agent to:
  1. List all registered queryable external projects.
  2. Register a new external project (validates path safety).
  3. Query an external project via an isolated subprocess.

작성자: 최진호
작성일: 2026-04-18
"""

from __future__ import annotations

import errno
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nerdvana_cli.core.external_projects import ExternalProject, ExternalProjectRegistry
from nerdvana_cli.core.tool import BaseTool, ToolCategory, ToolContext, ToolSideEffect
from nerdvana_cli.server.external_worker import ExternalWorker
from nerdvana_cli.types import PermissionBehavior, PermissionResult, ToolResult
from nerdvana_cli.utils.path import safe_open_fd, validate_path

# Optional hard containment root. When set, a registered project path must
# resolve to a directory inside it; when unset only the structural rules in
# RegisterExternalProjectTool._safe_resolve apply.
BOUNDARY_ROOT_ENV_VAR = "NERDVANA_EXTERNAL_PROJECTS_ROOT"

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
        "The path must resolve to an existing real directory: the filesystem root is "
        "rejected, and a symlinked target directory is rejected instead of followed. "
        f"When ${BOUNDARY_ROOT_ENV_VAR} is set the path must also resolve inside that "
        "root, and every path component is opened without following symlinks."
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

    def __init__(
        self,
        registry:     ExternalProjectRegistry | None = None,
        allowed_root: str | Path | None              = None,
    ) -> None:
        self._registry     = registry if registry is not None else ExternalProjectRegistry()
        self._allowed_root = allowed_root

    async def call(
        self,
        args: RegisterExternalProjectArgs,
        context: ToolContext,
        can_use_tool: Any,
        on_progress: Any = None,
    ) -> ToolResult:
        # Resolve and validate path.
        try:
            resolved = self._safe_resolve(args.path, self._allowed_root)
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
    def _boundary_root(allowed_root: str | Path | None = None) -> Path | None:
        """Return the configured containment root, or *None* when unset.

        The explicit constructor argument wins; otherwise the
        ``NERDVANA_EXTERNAL_PROJECTS_ROOT`` environment variable is consulted.
        """
        raw = allowed_root if allowed_root is not None else os.environ.get(BOUNDARY_ROOT_ENV_VAR, "")
        text = str(raw).strip()
        if not text:
            return None
        return Path(text).expanduser()

    @classmethod
    def _safe_resolve(cls, raw_path: str, allowed_root: str | Path | None = None) -> Path:
        """Resolve *raw_path* to a directory that is safe to hand a query subprocess.

        A registered path becomes the working directory of a read-capable
        subprocess, so resolution enforces an actual boundary:

        - The filesystem root is never registrable; a project always has a
          parent directory that acts as the walk base.
        - The final component is opened with ``O_NOFOLLOW`` from that base, so
          a symlinked target directory is rejected rather than followed, and
          the directory check runs on the opened descriptor rather than on a
          separate stat that a race could invalidate.
        - When a containment root is configured, the canonical path must stay
          inside it (:func:`validate_path`) and every component below the root
          is walked with ``O_NOFOLLOW`` (:func:`safe_open_fd`).

        Returns the canonical ``Path`` on success.
        Raises ``ValueError`` when the path is invalid or outside the boundary.
        """
        candidate = Path(raw_path).expanduser()
        candidate = Path(os.path.abspath(candidate))

        if not candidate.name:
            raise ValueError(f"The filesystem root cannot be registered: {raw_path}")

        if not candidate.exists():
            raise ValueError(f"Path does not exist: {raw_path}")
        if not candidate.is_dir():
            raise ValueError(f"Path is not a directory: {raw_path}")

        root = cls._boundary_root(allowed_root)
        if root is None:
            base     = os.path.realpath(candidate.parent)
            relative = candidate.name
        else:
            base = os.path.realpath(root)
            if not os.path.isdir(base):
                raise ValueError(f"Configured external project root is not a directory: {root}")

            relative = os.path.relpath(os.path.realpath(candidate), base)
            if relative == os.curdir:
                raise ValueError(
                    f"The external project root itself cannot be registered: {raw_path}"
                )
            error = validate_path(relative, base)
            if error is not None:
                raise ValueError(
                    f"Path resolves outside the allowed external project root {base}: {raw_path}"
                )

        try:
            fd = safe_open_fd(relative, base, os.O_RDONLY)
        except PermissionError as exc:
            raise ValueError(f"Path is not registrable: {raw_path}: {exc}") from exc
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise ValueError(
                    f"Symlinked path component is not allowed: {raw_path}"
                ) from exc
            raise ValueError(f"Cannot open path {raw_path}: {exc}") from exc

        try:
            if not stat.S_ISDIR(os.fstat(fd).st_mode):
                raise ValueError(f"Path is not a directory: {raw_path}")
        finally:
            os.close(fd)

        return Path(os.path.join(base, relative))


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
