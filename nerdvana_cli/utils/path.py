"""Path validation utilities."""

from __future__ import annotations

import os


def validate_path(path: str, cwd: str) -> str | None:
    """Validate that resolved path stays within cwd. Returns error message or None."""
    if os.path.isabs(path):
        return f"Absolute paths are not allowed: {path}"
    resolved = os.path.realpath(os.path.join(cwd, path))
    cwd_resolved = os.path.realpath(cwd)
    if not resolved.startswith(cwd_resolved + os.sep) and resolved != cwd_resolved:
        return f"Path traversal blocked: {path} resolves outside working directory"
    return None
