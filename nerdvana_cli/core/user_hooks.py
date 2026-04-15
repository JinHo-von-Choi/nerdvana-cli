"""User-defined hook module loader.

Scans well-known directories for `*.py` files exposing a module-level
`register(engine, settings)` function and invokes each one. This is the
extension point for end-users who need to customize NerdVana CLI's
session lifecycle without forking the package.

Discovery order (later registrations win for ordering, but all run):
    1. ~/.nerdvana/hooks/*.py               — global user hooks
    2. <cwd>/.nerdvana/hooks/*.py           — project-local hooks

Each module must define:

    def register(engine: HookEngine, settings) -> None:
        engine.register(HookEvent.SESSION_START, my_handler)
        # ... any number of handlers on any events

Failures (import error, missing register, register raising) are logged
and skipped — they never crash the agent loop.
"""

from __future__ import annotations

import importlib.util
import logging
from pathlib import Path
from typing import Any

from nerdvana_cli.core import paths
from nerdvana_cli.core.hooks import HookEngine

logger = logging.getLogger(__name__)


def global_hooks_dir() -> Path:
    """Return the global user hooks directory."""
    return paths.user_hooks_dir()


def _global_hook_dir() -> Path:
    return global_hooks_dir()


def _project_hook_dir(cwd: str) -> Path:
    return Path(cwd) / ".nerdvana" / "hooks"


def _load_module_from_path(path: Path) -> Any:
    """Import a single .py file as an isolated module."""
    spec = importlib.util.spec_from_file_location(
        f"nerdvana_user_hook_{path.stem}", str(path)
    )
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_user_hooks(engine: HookEngine, settings: Any) -> list[str]:
    """Discover and register all user hook modules.

    Returns the list of successfully-registered module file paths
    (for diagnostics / display).
    """
    registered: list[str] = []
    cwd = getattr(settings, "cwd", ".")

    for hook_dir in (_global_hook_dir(), _project_hook_dir(cwd)):
        if not hook_dir.is_dir():
            continue
        for path in sorted(hook_dir.glob("*.py")):
            if path.name.startswith("_"):
                continue
            try:
                module = _load_module_from_path(path)
                if module is None:
                    logger.warning("user hook %s: failed to load spec", path)
                    continue
                register_fn = getattr(module, "register", None)
                if not callable(register_fn):
                    logger.warning(
                        "user hook %s: missing 'register(engine, settings)' function", path
                    )
                    continue
                register_fn(engine, settings)
                registered.append(str(path))
            except Exception as exc:
                logger.warning("user hook %s failed: %s: %s", path, type(exc).__name__, exc)

    return registered
