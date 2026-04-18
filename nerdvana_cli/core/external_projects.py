"""External project registry — Phase H.

Manages a persistent list of external project paths that can be queried
via subprocess-isolated MCP channels.

Storage: ``~/.nerdvana/external_projects.yml``

작성자: 최진호
작성일: 2026-04-18
"""

from __future__ import annotations

import os
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:
    import yaml  # type: ignore[import-untyped]  # noqa: F401
except ImportError:  # pragma: no cover
    yaml = None

from nerdvana_cli.core.paths import user_data_home

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

class ExternalProject:
    """Represents a registered external project.

    Parameters
    ----------
    name:
        Short identifier used to reference the project in tool calls.
    path:
        Absolute filesystem path to the project root.
    languages:
        List of primary programming languages (e.g. ``["python", "typescript"]``).
    registered_at:
        ISO 8601 timestamp of initial registration (UTC).
    """

    __slots__ = ("name", "path", "languages", "registered_at")

    def __init__(
        self,
        *,
        name:          str,
        path:          str,
        languages:     list[str] | None = None,
        registered_at: str | None = None,
    ) -> None:
        self.name          = name
        self.path          = path
        self.languages:    list[str] = languages or []
        self.registered_at = registered_at or datetime.now(tz=UTC).isoformat()

    # ------------------------------------------------------------------
    # Serialisation helpers
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise to plain dict for YAML storage."""
        return {
            "name":          self.name,
            "path":          self.path,
            "languages":     self.languages,
            "registered_at": self.registered_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExternalProject:
        """Deserialise from a plain dict loaded from YAML."""
        return cls(
            name          = str(data["name"]),
            path          = str(data["path"]),
            languages     = list(data.get("languages") or []),
            registered_at = data.get("registered_at"),
        )

    def __repr__(self) -> str:  # pragma: no cover
        return f"ExternalProject(name={self.name!r}, path={self.path!r})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ExternalProject):
            return NotImplemented
        return self.name == other.name and self.path == other.path

    def __hash__(self) -> int:
        return hash((self.name, self.path))


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def _default_registry_path() -> Path:
    """Return the canonical path: ``~/.nerdvana/external_projects.yml``."""
    return user_data_home() / "external_projects.yml"


class ExternalProjectRegistry:
    """Thread-safe, file-backed registry of external projects.

    YAML format::

        projects:
          - name: react-dom
            path: /home/user/libs/react
            languages: [typescript, javascript]
            registered_at: "2026-04-18T00:00:00+00:00"

    Parameters
    ----------
    registry_path:
        Override the storage path.  Defaults to
        ``~/.nerdvana/external_projects.yml``.
    """

    def __init__(self, registry_path: Path | None = None) -> None:
        self._path    = registry_path or _default_registry_path()
        self._lock    = threading.Lock()
        self._projects: dict[str, ExternalProject] = {}
        self._load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> None:
        """Load projects from the YAML file.  No-op if the file does not exist."""
        if not self._path.exists():
            return
        if yaml is None:
            raise ImportError("PyYAML is required to load external_projects.yml")  # pragma: no cover
        with open(self._path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        for raw in data.get("projects") or []:
            try:
                proj = ExternalProject.from_dict(raw)
                self._projects[proj.name] = proj
            except (KeyError, TypeError):
                # Silently skip malformed entries — do not crash on startup.
                pass

    def _save(self) -> None:
        """Persist the current in-memory state to disk atomically."""
        if yaml is None:
            raise ImportError("PyYAML is required to save external_projects.yml")  # pragma: no cover
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "projects": [p.to_dict() for p in self._projects.values()],
        }
        # Atomic write via temp file + rename.
        tmp = self._path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            yaml.safe_dump(payload, fh, allow_unicode=True, sort_keys=False)
        os.replace(tmp, self._path)

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def add(self, project: ExternalProject) -> None:
        """Register a project.  Overwrites an existing entry with the same name."""
        with self._lock:
            self._projects[project.name] = project
            self._save()

    def get(self, name: str) -> ExternalProject | None:
        """Return the project with *name*, or *None* if not found."""
        with self._lock:
            return self._projects.get(name)

    def remove(self, name: str) -> bool:
        """Remove a project by name.  Returns *True* if it existed."""
        with self._lock:
            if name not in self._projects:
                return False
            del self._projects[name]
            self._save()
            return True

    def list_all(self) -> list[ExternalProject]:
        """Return a snapshot of all registered projects, ordered by name."""
        with self._lock:
            return sorted(self._projects.values(), key=lambda p: p.name)

    def __len__(self) -> int:
        with self._lock:
            return len(self._projects)

    def __contains__(self, name: object) -> bool:
        with self._lock:
            return name in self._projects
