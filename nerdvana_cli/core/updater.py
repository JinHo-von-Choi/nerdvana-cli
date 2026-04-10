"""GitHub release check and self-update."""

from __future__ import annotations

import logging
import re
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

GITHUB_REPO = "JinHo-von-Choi/nerdvana-cli"
GITHUB_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"


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
            tag = data.get("tag_name", "")
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


def run_self_update(install_dir: Path | None = None) -> tuple[bool, str]:
    if install_dir is None:
        install_dir = Path(__file__).resolve().parent.parent.parent

    git_dir = install_dir / ".git"
    if not git_dir.exists():
        return False, "Not a git installation. Reinstall with install.sh."

    try:
        result = subprocess.run(
            ["git", "pull", "--ff-only", "origin", "main"],
            cwd=install_dir,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return False, f"git pull failed: {result.stderr.strip()}"

        if "Already up to date" in result.stdout:
            return True, "Already up to date."

        pip_result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--quiet", "-e", ".[all]"],
            cwd=install_dir,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if pip_result.returncode != 0:
            return False, f"pip install failed: {pip_result.stderr.strip()}"

        return True, "Updated successfully. Restart to apply changes."

    except subprocess.TimeoutExpired:
        return False, "Update timed out."
    except Exception as e:
        return False, f"Update failed: {e}"
