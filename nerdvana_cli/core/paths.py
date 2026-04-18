"""Single source of truth for every runtime path.

Rules:
    - The install directory (~/.nerdvana-cli or $NERDVANA_HOME) is READ-ONLY
      at runtime. Nothing in this module returns a writable path inside it.
    - All user data lives under ~/.nerdvana (or $NERDVANA_DATA_HOME).
    - Project-local paths take an explicit `cwd` argument. No implicit os.getcwd().

Legacy locations (for migration and backwards-compat detection only):
    - ~/.nerdvana-cli/sessions/       (old install-dir sessions — CRITICAL data-loss risk)
    - ~/.config/nerdvana-cli/         (old XDG config root)
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_USER_SUBDIRS = ("sessions", "skills", "hooks", "agents", "teams", "cache", "logs")

# One-shot deprecation flag — emits at most once per process.
_nerdvana_home_warned: bool = False


def user_data_home() -> Path:
    """Root for all user data.

    Resolution order:
      1. $NERDVANA_DATA_HOME   (new, canonical)
      2. ~/.nerdvana            (default)

    If $NERDVANA_HOME is set but $NERDVANA_DATA_HOME is not, and the
    $NERDVANA_HOME directory appears to contain user data (has a sessions/
    sub-directory), emit a one-time deprecation warning: the user likely
    set NERDVANA_HOME expecting it to control the data root, but that env
    var controls the install root since this version.
    """
    global _nerdvana_home_warned

    env_data = os.environ.get("NERDVANA_DATA_HOME", "").strip()
    if env_data:
        return Path(env_data).expanduser()

    # Deprecation check: NERDVANA_HOME set without NERDVANA_DATA_HOME.
    env_install = os.environ.get("NERDVANA_HOME", "").strip()
    if env_install and not _nerdvana_home_warned:
        candidate = Path(env_install).expanduser()
        if (candidate / "sessions").is_dir():
            logger.warning(
                "NERDVANA_HOME=%s appears to contain user data (sessions/ found). "
                "As of this version NERDVANA_HOME controls the install root only. "
                "Set NERDVANA_DATA_HOME=%s to keep your data in that location.",
                env_install,
                env_install,
            )
            _nerdvana_home_warned = True

    return Path.home() / ".nerdvana"


def install_root() -> Path:
    """Install directory. $NERDVANA_HOME wins; default ~/.nerdvana-cli.

    This is read-only at runtime — do not write anywhere under this path.
    """
    env = os.environ.get("NERDVANA_HOME", "").strip()
    if env:
        return Path(env).expanduser()
    return Path.home() / ".nerdvana-cli"


def user_config_path() -> Path:
    """Global config file path."""
    return user_data_home() / "config.yml"


def user_nirnamd_path() -> Path:
    """Global NIRNA.md path."""
    return user_data_home() / "NIRNA.md"


def user_mcp_json() -> Path:
    """Global MCP servers config path."""
    return user_data_home() / "mcp.json"


def user_sessions_dir() -> Path:
    """Directory for JSONL session transcripts."""
    return user_data_home() / "sessions"


def user_skills_dir() -> Path:
    """Directory for global user skills."""
    return user_data_home() / "skills"


def user_hooks_dir() -> Path:
    """Directory for global user hooks."""
    return user_data_home() / "hooks"


def user_agents_dir() -> Path:
    """Directory for global user agent definitions."""
    return user_data_home() / "agents"


def user_teams_dir() -> Path:
    """Directory for team state."""
    return user_data_home() / "teams"


def user_cache_dir() -> Path:
    """Directory for runtime caches (updater, model lists, etc.)."""
    return user_data_home() / "cache"


def user_logs_dir() -> Path:
    """Directory for structured logs (reserved for future use)."""
    return user_data_home() / "logs"


def ensure_user_dirs() -> None:
    """Create all user subdirectories if they do not exist. Idempotent."""
    root = user_data_home()
    root.mkdir(parents=True, exist_ok=True)
    for name in _USER_SUBDIRS:
        (root / name).mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Legacy path helpers — used only for backwards-compat detection and migration
# ---------------------------------------------------------------------------

def legacy_config_dir() -> Path:
    """Pre-migration XDG location. Kept for backward-compat detection.

    Legacy: ~/.config/nerdvana-cli/  (or $XDG_CONFIG_HOME/nerdvana-cli/)
    """
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    base = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    return base / "nerdvana-cli"


def legacy_config_path() -> Path:
    """Legacy global config file. Used by migration and settings fallback.

    Legacy: ~/.config/nerdvana-cli/config.yml
    """
    return legacy_config_dir() / "config.yml"


def legacy_sessions_dir() -> Path:
    """Old install-dir-leaking sessions location.

    Legacy: ~/.nerdvana-cli/sessions/
    This is the source path for the one-shot migration (Task E1).
    Writing here corrupts git pull --ff-only.
    """
    return Path.home() / ".nerdvana-cli" / "sessions"


# ---------------------------------------------------------------------------
# Project-local helpers — always take an explicit cwd argument
# ---------------------------------------------------------------------------

def project_config_path(cwd: str) -> Path:
    """Project config override file (commit-friendly)."""
    return Path(cwd) / "nerdvana.yml"


def project_config_path_yaml(cwd: str) -> Path:
    """Alternate project config override (yaml extension)."""
    return Path(cwd) / "nerdvana.yaml"


def project_skills_dir(cwd: str) -> Path:
    """Project-local skills directory."""
    return Path(cwd) / ".nerdvana" / "skills"


def project_hooks_dir(cwd: str) -> Path:
    """Project-local hooks directory."""
    return Path(cwd) / ".nerdvana" / "hooks"


def project_agents_dir(cwd: str) -> Path:
    """Project-local agent definitions directory."""
    return Path(cwd) / ".nerdvana" / "agents"


def project_mcp_json(cwd: str) -> Path:
    """Project-local MCP config (claude-code compatible name)."""
    return Path(cwd) / ".mcp.json"


def project_nirnamd_path(cwd: str) -> Path:
    """Project-local NIRNA.md instructions file."""
    return Path(cwd) / "NIRNA.md"


# ---------------------------------------------------------------------------
# Phase F: runtime profile paths
# ---------------------------------------------------------------------------

def user_contexts_dir() -> Path:
    """User-global context profile directory."""
    return user_data_home() / "contexts"


def user_modes_dir() -> Path:
    """User-global mode profile directory."""
    return user_data_home() / "modes"


def project_contexts_dir(cwd: str) -> Path:
    """Project-local context profile directory."""
    return Path(cwd) / ".nerdvana" / "contexts"


def project_modes_dir(cwd: str) -> Path:
    """Project-local mode profile directory."""
    return Path(cwd) / ".nerdvana" / "modes"
