"""GitHub release check and self-update.

Update path hardening (T-user-data-preservation, 2026-04-18):
    - Install root (`~/.nerdvana-cli` or `$NERDVANA_HOME`) is the only thing
      ``git pull`` ever touches. User data under ``user_data_home()``
      (`~/.nerdvana` or `$NERDVANA_DATA_HOME`) is explicitly asserted to be
      outside the pull target.
    - Before pulling, the install dir must be clean (no uncommitted edits).
      Otherwise the update is refused — we never stash or discard user
      changes silently.
    - A rotating snapshot of the user data root is taken before every
      update. On success the snapshot is kept for rollback; the 3 most
      recent snapshots are retained and older ones pruned.
    - A post-update integrity check compares a hash of critical user files
      (config.yml, profile YAMLs, registry YAMLs) before and after the
      pull. Any drift aborts the update with a restore hint.
"""

from __future__ import annotations

import hashlib
import logging
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from nerdvana_cli.core.paths import install_root, user_data_home

logger = logging.getLogger(__name__)

GITHUB_REPO    = "JinHo-von-Choi/nerdvana-cli"
GITHUB_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"

_SNAPSHOT_DIRNAME = ".update-backups"
_SNAPSHOT_KEEP    = 3
_INTEGRITY_FILES  = (
    "config.yml",
    "NIRNA.md",
    "mcp.json",
    "mcp_acl.yml",
    "mcp_keys.yml",
    "external_projects.yml",
)
_INTEGRITY_DIRS   = (
    "contexts",
    "modes",
    "memories",
    "agents",
    "skills",
    "hooks",
)


# ---------------------------------------------------------------------------
# Version handling
# ---------------------------------------------------------------------------

def parse_version(version_str: str) -> tuple[int, int, int]:
    match = re.match(r"v?(\d+)\.(\d+)\.(\d+)", version_str)
    if not match:
        return (0, 0, 0)
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def compare_versions(local: str, remote: str) -> int:
    l_ver = parse_version(local)
    r_ver = parse_version(remote)
    if l_ver < r_ver:
        return -1
    if l_ver > r_ver:
        return 1
    return 0


async def check_for_update(current_version: str) -> dict[str, str] | None:
    import httpx
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                GITHUB_API_URL,
                headers={"Accept": "application/vnd.github.v3+json"},
                follow_redirects=True,
            )
            if resp.status_code != 200:
                return None
            data = resp.json()
            tag  = data.get("tag_name", "")
            if not tag:
                return None
            if compare_versions(current_version, tag) < 0:
                return {
                    "version": tag,
                    "url": data.get("html_url", f"https://github.com/{GITHUB_REPO}/releases"),
                }
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# User-data preservation guards
# ---------------------------------------------------------------------------

def _assert_install_user_separation(install_dir: Path, data_home: Path) -> None:
    """Refuse to proceed if install dir and user data root overlap.

    Overlap would mean ``git pull`` could rewrite files the user owns.
    """
    resolved_install = install_dir.resolve()
    resolved_data    = data_home.resolve()

    if resolved_install == resolved_data:
        raise RuntimeError(
            f"install_dir ({resolved_install}) equals user_data_home ({resolved_data}); "
            "refusing update to avoid overwriting user data. "
            "Set NERDVANA_DATA_HOME to a different path."
        )

    # Also guard against nesting (one inside the other).
    try:
        resolved_data.relative_to(resolved_install)
    except ValueError:
        pass
    else:
        raise RuntimeError(
            f"user_data_home ({resolved_data}) is nested under install_dir "
            f"({resolved_install}); git pull would touch user data. Move your "
            "data root outside the install directory."
        )

    try:
        resolved_install.relative_to(resolved_data)
    except ValueError:
        pass
    else:
        raise RuntimeError(
            f"install_dir ({resolved_install}) is nested under user_data_home "
            f"({resolved_data}); refusing update."
        )


def _check_install_dir_clean(install_dir: Path) -> tuple[bool, str]:
    """Return (clean, message). Refuse update when the repo is dirty."""
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=install_dir,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        return False, f"git status failed: {result.stderr.strip()}"
    if result.stdout.strip():
        return False, (
            "install dir has uncommitted changes; refusing update.\n"
            f"  {install_dir}\n"
            "Commit, stash, or reset those changes before running /update."
        )
    return True, ""


def _iter_integrity_paths(data_home: Path) -> list[Path]:
    """Yield the user-data paths whose contents must survive the update."""
    paths: list[Path] = []
    for name in _INTEGRITY_FILES:
        p = data_home / name
        if p.is_file():
            paths.append(p)
    for name in _INTEGRITY_DIRS:
        d = data_home / name
        if not d.is_dir():
            continue
        for child in sorted(d.rglob("*")):
            if child.is_file():
                paths.append(child)
    return paths


def _hash_user_data(data_home: Path) -> dict[str, str]:
    """Return a {relative_path: sha256} map for integrity comparison."""
    out: dict[str, str] = {}
    for path in _iter_integrity_paths(data_home):
        rel = str(path.relative_to(data_home))
        h   = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        out[rel] = h.hexdigest()
    return out


def _snapshot_user_data(data_home: Path, timestamp: str) -> Path | None:
    """Copy user data to a timestamped backup. Return backup path or None
    when the data root does not yet exist (first-time user)."""
    if not data_home.is_dir():
        return None

    backup_root = data_home / _SNAPSHOT_DIRNAME
    backup_root.mkdir(exist_ok=True)
    backup_dir = backup_root / f"pre-update-{timestamp}"

    def _ignore(src: str, names: list[str]) -> list[str]:
        skipped: list[str] = []
        if Path(src).resolve() == data_home.resolve():
            skipped.append(_SNAPSHOT_DIRNAME)
        # Never back up SQLite WAL sidecars (copy_tree on live WAL is unsafe).
        for n in names:
            if n.endswith("-wal") or n.endswith("-shm"):
                skipped.append(n)
        return skipped

    shutil.copytree(data_home, backup_dir, ignore=_ignore, symlinks=True)
    return backup_dir


def _prune_snapshots(data_home: Path, keep: int = _SNAPSHOT_KEEP) -> int:
    """Remove all but the ``keep`` most recent pre-update backups."""
    backup_root = data_home / _SNAPSHOT_DIRNAME
    if not backup_root.is_dir():
        return 0
    candidates = sorted(
        (p for p in backup_root.iterdir() if p.is_dir() and p.name.startswith("pre-update-")),
        key=lambda p: p.name,
        reverse=True,
    )
    removed = 0
    for old in candidates[keep:]:
        shutil.rmtree(old, ignore_errors=True)
        removed += 1
    return removed


def _run_post_update_migrate() -> None:
    """Re-run user-data migrations in case the new version added any."""
    try:
        from nerdvana_cli.core.migrate import run_if_needed
    except ImportError:
        logger.debug("migrate module not available; skipping post-update migrations")
        return
    try:
        run_if_needed()
    except Exception as exc:  # noqa: BLE001 — migration failures must not crash update
        logger.warning("post-update migrations raised %s; continuing", exc)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def run_self_update(install_dir: Path | None = None) -> tuple[bool, str]:
    """Update the install tree without touching user data.

    Returns (success, message). The message mentions the snapshot path on
    success so the user knows where to roll back from.
    """
    if install_dir is None:
        install_dir = install_root()

    data_home = user_data_home()

    # Guard 1: ensure install_dir and data_home do not overlap.
    try:
        _assert_install_user_separation(install_dir, data_home)
    except RuntimeError as exc:
        return False, str(exc)

    git_dir = install_dir / ".git"
    if not git_dir.exists():
        return False, f"Not a git installation ({install_dir}). Reinstall with install.sh."

    # Guard 2: refuse if install dir is dirty.
    clean, dirty_msg = _check_install_dir_clean(install_dir)
    if not clean:
        return False, dirty_msg

    # Guard 3: snapshot user data BEFORE pulling.
    timestamp    = datetime.now().strftime("%Y%m%d-%H%M%S")
    try:
        snapshot = _snapshot_user_data(data_home, timestamp)
    except OSError as exc:
        return False, f"User-data snapshot failed: {exc}"

    pre_hashes = _hash_user_data(data_home) if data_home.is_dir() else {}

    try:
        pull = subprocess.run(
            ["git", "pull", "--ff-only", "origin", "main"],
            cwd=install_dir,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if pull.returncode != 0:
            return False, f"git pull failed: {pull.stderr.strip()}"

        if "Already up to date" in pull.stdout:
            _prune_snapshots(data_home)
            return True, "Already up to date."

        pip = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--quiet", "-e", ".[all]"],
            cwd=install_dir,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if pip.returncode != 0:
            return False, f"pip install failed: {pip.stderr.strip()}"

    except subprocess.TimeoutExpired:
        return False, "Update timed out."
    except Exception as exc:  # noqa: BLE001
        return False, f"Update failed: {exc}"

    # Guard 4: verify user data is byte-identical post-pull.
    post_hashes = _hash_user_data(data_home) if data_home.is_dir() else {}
    drifted = [
        rel for rel in set(pre_hashes) | set(post_hashes)
        if pre_hashes.get(rel) != post_hashes.get(rel)
    ]
    if drifted:
        hint = (
            f" Restore from snapshot: cp -a {snapshot}/* {data_home}/"
            if snapshot else ""
        )
        return False, (
            f"User data changed during update ({len(drifted)} files): "
            f"{drifted[:5]}{' …' if len(drifted) > 5 else ''}.{hint}"
        )

    # Post-update: run any new migrations the release introduced.
    _run_post_update_migrate()

    removed = _prune_snapshots(data_home)
    msg = "Updated successfully. Restart to apply changes."
    if snapshot:
        msg += f" Backup: {snapshot}"
        if removed:
            msg += f" (pruned {removed} older snapshot{'s' if removed != 1 else ''})"
    return True, msg
