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

import hashlib
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_USER_SUBDIRS = ("sessions", "skills", "hooks", "agents", "teams", "cache", "logs")

# One-shot deprecation flag — emits at most once per process.
_nerdvana_home_warned: bool = False

# Server store files already reported by server_store_path(), so each warns once.
_legacy_store_warned: set[Path] = set()


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


def user_workflows_dir() -> Path:
    """Directory for global workflow definitions."""
    return user_data_home() / "workflows"


def workflow_runs_dir() -> Path:
    """Directory with one sub-directory of stored step results per workflow run."""
    return user_workflows_dir() / "runs"


def user_teams_dir() -> Path:
    """Directory for team state."""
    return user_data_home() / "teams"


def user_cache_dir() -> Path:
    """Directory for runtime caches (updater, model lists, etc.)."""
    return user_data_home() / "cache"


def analytics_db_path() -> Path:
    """Analytics database path."""
    return user_data_home() / "analytics.sqlite"


def server_store_path(filename: str) -> Path:
    """Path of a server store file (``mcp_keys.yml``, ``mcp_acl.yml``, ``audit.sqlite``).

    These files used to be created under ``~/.nerdvana`` whatever
    $NERDVANA_DATA_HOME said. When the file is absent from the data root but
    present in ``~/.nerdvana``, the existing file is returned and one warning
    names both locations. Nothing is copied or deleted. Without
    $NERDVANA_DATA_HOME both locations are the same file, so the data root path
    is always returned.
    """
    current = user_data_home() / filename
    legacy  = Path.home() / ".nerdvana" / filename
    if current.exists() or not legacy.exists():
        return current
    if legacy not in _legacy_store_warned:
        _legacy_store_warned.add(legacy)
        logger.warning(
            "%s not found in the data root %s; using the existing %s. "
            "Move it into the data root to stop this warning.",
            filename,
            current.parent,
            legacy,
        )
    return legacy


def system_managed_dir() -> Path:
    """Administrator-owned drop-in directory for managed settings."""
    return Path("/etc/nerdvana/managed-settings.d")


def managed_settings_dirs() -> list[Path]:
    """Directories searched for managed settings files, in the order they are read.

    The system directory comes first. $NERDVANA_MANAGED_DIR adds one more directory;
    managed files only ever restrict, so an extra directory cannot loosen the system one.
    """
    dirs = [system_managed_dir()]
    env  = os.environ.get("NERDVANA_MANAGED_DIR", "").strip()
    if env:
        dirs.append(Path(env).expanduser())
    return dirs


def managed_audit_path() -> Path:
    """Append-only record of the managed policy applied at each startup."""
    return user_data_home() / "logs" / "managed-policy.jsonl"


def schedule_dir() -> Path:
    """Root of the scheduler's files (job definitions, run records, lock files)."""
    return user_data_home() / "schedule"


def schedule_jobs_path() -> Path:
    """YAML file holding the scheduled job definitions."""
    return schedule_dir() / "jobs.yml"


def schedule_runs_dir() -> Path:
    """Directory with one sub-directory of run records per scheduled job."""
    return schedule_dir() / "runs"


def schedule_lock_path(job_name: str) -> Path:
    """Lock file that keeps two runs of the same scheduled job from overlapping."""
    return schedule_dir() / "locks" / f"{job_name}.lock"


def runs_dir() -> Path:
    """Root of the run store: one directory per background run (record, log, result, and the key claims)."""
    return user_data_home() / "runs"


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
    This is the source path for the one-shot migration.
    Writing here corrupts git pull --ff-only.
    """
    return Path.home() / ".nerdvana-cli" / "sessions"


# ---------------------------------------------------------------------------
# Project-local helpers — always take an explicit cwd argument
# ---------------------------------------------------------------------------


def project_skills_dir(cwd: str) -> Path:
    """Project-local skills directory."""
    return Path(cwd) / ".nerdvana" / "skills"


def project_nirnamd_path(cwd: str) -> Path:
    """Project-local NIRNA.md instructions file."""
    return Path(cwd) / "NIRNA.md"


# ---------------------------------------------------------------------------
# Memory helpers
# ---------------------------------------------------------------------------

def project_memories_dir(cwd: str) -> Path:
    """Project-local memories directory (<cwd>/.nerdvana/memories/)."""
    return Path(cwd) / ".nerdvana" / "memories"


def project_onboarding_dir(cwd: str) -> Path:
    """Project-local onboarding stamp directory."""
    return Path(cwd) / ".nerdvana" / "memories" / "onboarding"


def project_memory_tool_dir(cwd: str) -> Path:
    """Directory behind Anthropic's memory tool for the project at *cwd* (data root, one directory per project)."""
    root   = Path(cwd).expanduser().resolve()
    digest = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:12]
    return user_data_home() / "memory-tool" / f"{root.name or 'root'}-{digest}"


def global_memories_dir() -> Path:
    """User-global memories directory (~/.nerdvana/memories/global/)."""
    return user_data_home() / "memories" / "global"


def project_memory_inbox_dir(cwd: str) -> Path:
    """Directory of memory proposals awaiting review (<cwd>/.nerdvana/memory-inbox/)."""
    return Path(cwd) / ".nerdvana" / "memory-inbox"


def memory_audit_log() -> Path:
    """Append-only log of memory approvals, rejections and forgets (~/.nerdvana/logs/memory-audit.jsonl)."""
    return user_data_home() / "logs" / "memory-audit.jsonl"


# ---------------------------------------------------------------------------
# Runtime profile paths
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
