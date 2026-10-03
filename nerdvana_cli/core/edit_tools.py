"""Which tools change a file, and where the file named by their arguments is.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

from typing import Any

# Edit-tool names that trigger a checkpoint before execution.
EDIT_TOOL_NAMES: frozenset[str] = frozenset({
    "FileEdit",
    "FileWrite",
    "replace_symbol_body",
    "insert_before_symbol",
    "insert_after_symbol",
    "safe_delete_symbol",
})

# Argument attributes that carry the file an edit tool is about to change, in resolution order.
# File tools expose ``path``; symbol tools expose ``relative_path``.
EDIT_PATH_ATTRS: tuple[str, ...] = ("path", "relative_path", "file_path")


def is_applied_edit(tool_name: str, tool_input: dict[str, Any]) -> bool:
    """True when the call is an edit tool that is not a dry run."""
    return tool_name in EDIT_TOOL_NAMES and bool(tool_input.get("apply", True))


def edited_path(tool_input: dict[str, Any]) -> str:
    """The file an edit call is about, or an empty string."""
    return next((value for attr in EDIT_PATH_ATTRS if isinstance(value := tool_input.get(attr), str) and value.strip()), "")
