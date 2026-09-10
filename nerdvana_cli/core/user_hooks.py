"""User-defined hook module loader.

Scans well-known directories for `*.py` files exposing a module-level
`register(engine, settings)` function and invokes each one. This is the
extension point for end-users who need to customize NerdVana CLI's
session lifecycle without forking the package.

Discovery order (later registrations win for ordering, but all run):
    1. ~/.nerdvana/hooks/*.py       global user hooks, always eligible
    2. <cwd>/.nerdvana/hooks/*.py   project-local hooks, disabled by default

A project-local hook file lives inside a repository, so cloning an
untrusted repository must never be enough to execute the code it
carries. A project hook runs only when both of these hold:

    1. `hooks.allow_project_hooks` is true in the active settings.
    2. The file's SHA-256 digest matches the digest recorded for that
       exact path in the approval record (`project_hook_trust_path`).

Global hooks live under the user's own data directory and are subject
to neither condition.

Each module must define:

    def register(engine: HookEngine, settings) -> None:
        engine.register(HookEvent.SESSION_START, my_handler)
        # ... any number of handlers on any events

Failures (import error, missing register, register raising) are logged
and skipped — they never crash the agent loop.
"""

from __future__ import annotations

import hashlib
import hmac
import importlib.util
import json
import logging
from pathlib import Path
from typing import Any

from nerdvana_cli.core import paths
from nerdvana_cli.core.hooks import HookEngine

logger = logging.getLogger(__name__)

TRUST_RECORD_FILENAME = "trusted_hooks.json"


def project_hook_trust_path() -> Path:
    """Return the file recording approved project-hook digests."""
    return paths.user_data_home() / TRUST_RECORD_FILENAME


def hook_digest(path: Path) -> str:
    """Return the SHA-256 hex digest of a hook file's bytes."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_trust_record() -> dict[str, str]:
    """Return the {absolute hook path: digest} approval map.

    A missing, unreadable, or malformed record approves nothing.
    """
    try:
        raw = json.loads(project_hook_trust_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(k): v for k, v in raw.items() if isinstance(v, str)}


def trust_project_hook(path: Path) -> str:
    """Record the current digest of `path` as approved and return it.

    Approval is bound to the exact bytes present now: any later edit to
    the file invalidates it until this is called again.
    """
    resolved = Path(path).resolve()
    digest   = hook_digest(resolved)
    record   = load_trust_record()
    record[str(resolved)] = digest

    target = project_hook_trust_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
    return digest


def revoke_project_hook(path: Path) -> bool:
    """Drop any approval recorded for `path`. True if one was removed."""
    resolved = str(Path(path).resolve())
    record   = load_trust_record()
    if record.pop(resolved, None) is None:
        return False
    project_hook_trust_path().write_text(
        json.dumps(record, indent=2, sort_keys=True), encoding="utf-8"
    )
    return True


def project_hooks_enabled(settings: Any) -> bool:
    """Whether settings opt in to executing project-local hooks."""
    return bool(getattr(getattr(settings, "hooks", None), "allow_project_hooks", False))


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


def _project_hook_allowed(path: Path, opt_in: bool, trusted: dict[str, str]) -> bool:
    """Whether a project-local hook file may be imported and executed."""
    if not opt_in:
        logger.warning(
            "project hook %s skipped: project-local hooks are off "
            "(set hooks.allow_project_hooks to enable them)",
            path,
        )
        return False

    try:
        digest = hook_digest(path)
    except OSError as exc:
        logger.warning("project hook %s skipped: unreadable (%s)", path, exc)
        return False

    recorded = trusted.get(str(path.resolve()))
    if recorded is None:
        logger.warning("project hook %s skipped: not approved for this machine", path)
        return False
    if not hmac.compare_digest(recorded, digest):
        logger.warning("project hook %s skipped: contents changed since approval", path)
        return False
    return True


def load_user_hooks(engine: HookEngine, settings: Any) -> list[str]:
    """Discover and register all user hook modules.

    Global hooks always load. Project-local hooks load only when the
    settings opt in and the file matches its recorded digest.

    Returns the list of successfully-registered module file paths
    (for diagnostics / display).
    """
    registered: list[str] = []
    cwd = getattr(settings, "cwd", ".")

    opt_in  = project_hooks_enabled(settings)
    trusted = load_trust_record() if opt_in else {}

    for hook_dir, is_project in ((_global_hook_dir(), False), (_project_hook_dir(cwd), True)):
        if not hook_dir.is_dir():
            continue
        for path in sorted(hook_dir.glob("*.py")):
            if path.name.startswith("_"):
                continue
            if is_project and not _project_hook_allowed(path, opt_in, trusted):
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
