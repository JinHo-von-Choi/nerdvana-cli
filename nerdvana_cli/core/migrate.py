"""One-shot migration of user data from legacy locations to ~/.nerdvana/.

Runs at most once per installation — sentinel file .migrated prevents reruns.
Safe to call on every CLI startup: it checks the sentinel first and returns
False immediately if it has already run.

Migration policy:
    Sessions    — MOVED (shutil.move) from ~/.nerdvana-cli/sessions/.
                  Rationale: the install dir is a git repo; sessions there
                  conflict with git pull --ff-only. Moving prevents future
                  accumulation in the git tree.

    Config files — COPIED (shutil.copy2) from ~/.config/nerdvana-cli/.
                  Rationale: non-destructive. Users who have shell aliases or
                  dotfile managers referencing the old XDG location keep
                  working. The legacy dir is left in place.
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path

from nerdvana_cli.core import paths

logger = logging.getLogger(__name__)

_SENTINEL = ".migrated"

# (legacy_name, new_name) — copied from ~/.config/nerdvana-cli/ to user_data_home().
_FILE_MIGRATIONS: list[tuple[str, str]] = [
    ("config.yml", "config.yml"),
    ("NIRNA.md",   "NIRNA.md"),
    ("mcp.json",   "mcp.json"),
]

_DIR_MIGRATIONS: list[tuple[str, str]] = [
    ("skills", "skills"),
    ("hooks",  "hooks"),
    ("agents", "agents"),
]


def run_if_needed() -> bool:
    """Run migration if the sentinel is absent. Returns True if anything moved.

    Safe to call on every startup — returns False immediately after the first
    successful run (sentinel is written even when nothing needed moving).
    """
    new_root = paths.user_data_home()
    sentinel = new_root / _SENTINEL
    if sentinel.exists():
        return False

    moved_anything = False
    new_root.mkdir(parents=True, exist_ok=True)
    paths.ensure_user_dirs()

    # ------------------------------------------------------------------
    # 1. Sessions from install dir (critical — data-loss risk otherwise)
    # ------------------------------------------------------------------
    legacy_sessions = paths.legacy_sessions_dir()
    new_sessions    = paths.user_sessions_dir()
    if legacy_sessions.exists():
        for src in legacy_sessions.iterdir():
            if not src.is_file() or src.suffix != ".jsonl":
                continue
            dst = new_sessions / src.name
            if dst.exists():
                logger.info("migrate: skip existing session %s", dst)
                continue
            shutil.move(str(src), str(dst))
            moved_anything = True
            logger.info("migrate: moved session %s -> %s", src, dst)
        # Remove the now-empty legacy sessions dir only if nothing remains.
        try:
            if not any(legacy_sessions.iterdir()):
                legacy_sessions.rmdir()
        except OSError:
            pass

    # ------------------------------------------------------------------
    # 2. Files from ~/.config/nerdvana-cli/  (copy, non-destructive)
    # ------------------------------------------------------------------
    legacy_cfg_dir = paths.legacy_config_dir()
    if legacy_cfg_dir.exists():
        for src_name, dst_name in _FILE_MIGRATIONS:
            src = legacy_cfg_dir / src_name
            dst = new_root / dst_name
            if src.exists() and not dst.exists():
                shutil.copy2(str(src), str(dst))
                moved_anything = True
                logger.info("migrate: copied %s -> %s", src, dst)

        for src_name, dst_name in _DIR_MIGRATIONS:
            src = legacy_cfg_dir / src_name
            dst = new_root / dst_name
            if not src.exists():
                continue
            dst.mkdir(parents=True, exist_ok=True)
            for item in src.iterdir():
                target = dst / item.name
                if target.exists():
                    continue
                if item.is_dir():
                    shutil.copytree(str(item), str(target))
                else:
                    shutil.copy2(str(item), str(target))
                moved_anything = True
                logger.info("migrate: copied %s -> %s", item, target)

    sentinel.write_text("1\n", encoding="utf-8")
    return moved_anything
