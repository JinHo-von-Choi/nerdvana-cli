"""What an agent definition lets a sub-agent write.

Author: 최진호
Date:   2026-10-03

An agent type can narrow what its sub-agents change, in two places that need two mechanisms. Shell commands
are confined by the operating system (Landlock, see ``core/safety/sandbox.py``); file and symbol edit tools run in
the application and are held to ``sandbox.edit_scope`` by the tool executor. ``write_scope`` sets both:

    ""  or  "project"   no change, the session's policy applies
    "none"              nothing can be written: no project directory, no temporary directories, no edits
    [paths]             only these paths (relative to the project), plus the temporary directories for commands
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from nerdvana_cli.core.config.settings import NerdvanaSettings


def apply_write_scope(settings: NerdvanaSettings, definition: Any, cwd: str = "") -> bool:
    """Narrow *settings* to the definition's ``write_scope`` and ``network``; True when anything changed.

    A scope on a session whose sandbox is off turns it on in ``auto`` mode: confinement where the system
    supports it, and the edit scope enforced either way. A session set to ``require`` keeps it.
    """
    scope   = getattr(definition, "write_scope", "")
    network = getattr(definition, "network", None)
    sandbox = settings.sandbox
    changed = False
    if scope == "none":
        sandbox.project_writable = False
        sandbox.scratch_writable = False
        sandbox.write_paths      = []
        sandbox.edit_scope       = []
        changed = True
    elif isinstance(scope, list) and scope:
        root = Path(cwd or settings.cwd or ".").resolve()
        sandbox.project_writable = False
        sandbox.write_paths      = [str((root / entry).resolve()) for entry in scope]
        sandbox.edit_scope       = [str(entry) for entry in scope]
        changed = True
    if network is not None and not (network and sandbox.network == "allowlist"):
        sandbox.network = bool(network)
        changed = True
    if changed and sandbox.mode == "off":
        sandbox.mode = "auto"
    return changed
