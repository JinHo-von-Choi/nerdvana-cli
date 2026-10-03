"""Stateless helpers of the LSP client: response parking, language ids, diagnostics and file writes.

Author: 최진호
Date:   2026-10-03
"""
from __future__ import annotations

import logging
import os
from typing import Any

from nerdvana_cli.utils.path import safe_open_fd

logger = logging.getLogger(__name__)

# Responses whose id belongs to another (usually timed out) request are parked
# rather than dropped.  The cap bounds the memory a misbehaving server can
# pin; eviction is oldest-first and always logged.
MAX_PARKED_RESPONSES: int = 32

_LANG_IDS: dict[str, str] = {
    ".py": "python", ".ts": "typescript", ".tsx": "typescriptreact",
    ".js": "javascript", ".jsx": "javascriptreact", ".go": "go", ".rs": "rust",
}


def park_response(
    parked: dict[int, dict[str, Any]],
    msg_id: int,
    msg:    dict[str, Any],
) -> None:
    """Store an out-of-order response, evicting the oldest when full."""
    while len(parked) >= MAX_PARKED_RESPONSES:
        oldest = next(iter(parked))
        del parked[oldest]
        logger.warning(
            "LSP response buffer full; discarding parked response id=%s", oldest
        )
    parked[msg_id] = msg


def language_id_for(suffix: str) -> str:
    """The LSP ``languageId`` of a file suffix."""
    return _LANG_IDS.get(suffix, "plaintext")


def simplify_diagnostic(d: dict[str, Any]) -> dict[str, Any]:
    """A language server diagnostic reduced to line, column, severity and message."""
    severity_map = {1: "error", 2: "warning", 3: "information", 4: "hint"}
    return {
        "line":     d["range"]["start"]["line"] + 1,
        "col":      d["range"]["start"]["character"],
        "severity": severity_map.get(d.get("severity", 2), "warning"),
        "message":  d.get("message", ""),
    }


def write_file(path: str, content: bytes, cwd: str | None) -> None:
    """Write *path* through the symlink-hardened opener rooted at *cwd*.

    There is no unhardened fallback: a target that resolves outside the root,
    or whose components cannot be walked with ``O_NOFOLLOW``, is an error and
    nothing is written.

    Raises:
        PermissionError: *path* resolves outside *cwd*.
        OSError: A path component is a symlink, or the open fails.
    """
    root     = os.path.realpath(cwd or os.getcwd())
    abs_path = os.path.realpath(path)
    if abs_path != root and not abs_path.startswith(root + os.sep):
        raise PermissionError(
            f"Refusing to write outside the project root: {path} "
            f"(project root: {root})"
        )

    rel = os.path.relpath(abs_path, root)
    fd  = safe_open_fd(rel, root, os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
    with os.fdopen(fd, "wb") as fh:
        fh.write(content)
