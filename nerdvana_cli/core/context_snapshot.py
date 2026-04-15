"""Session-start project snapshot — ran once, cached into sticky context."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

_MAX_TREE_ENTRIES = 40
_MAX_README_LINES = 20
_SKIP_DIRS        = {".git", ".venv", "node_modules", "__pycache__",
                     ".pytest_cache", ".ruff_cache", ".mypy_cache", ".claude"}


def _detect_project_type(cwd: str) -> tuple[str, str]:
    """Return (type, name) or ('unknown', '')."""
    root = Path(cwd)
    if (root / "pyproject.toml").exists():
        try:
            text = (root / "pyproject.toml").read_text(encoding="utf-8")
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith("name ") or stripped.startswith("name="):
                    name = stripped.split("=", 1)[1].strip().strip('"\'')
                    return ("python", name)
        except OSError:
            pass
        return ("python", "")
    if (root / "package.json").exists():
        try:
            data = json.loads((root / "package.json").read_text(encoding="utf-8"))
            return ("node", str(data.get("name", "")))
        except (OSError, ValueError):
            return ("node", "")
    if (root / "Cargo.toml").exists():
        return ("rust", "")
    if (root / "go.mod").exists():
        return ("go", "")
    return ("unknown", "")


def _collect_tree(cwd: str, max_entries: int = _MAX_TREE_ENTRIES) -> str:
    """Return a shallow directory listing (top level only)."""
    root = Path(cwd)
    lines: list[str] = []
    try:
        for entry in sorted(root.iterdir()):
            if entry.name.startswith(".") or entry.name in _SKIP_DIRS:
                continue
            if len(lines) >= max_entries:
                lines.append("  ...")
                break
            suffix = "/" if entry.is_dir() else ""
            lines.append(f"{entry.name}{suffix}")
    except OSError:
        return ""
    return "\n".join(lines)


def _collect_readme_headings(cwd: str) -> str:
    """Extract markdown headings from the first README found."""
    for candidate in ("README.md", "README.rst", "README.txt", "README"):
        path = Path(cwd) / candidate
        if not path.exists():
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return ""
        headings = [ln for ln in lines if ln.startswith("#")][:_MAX_README_LINES]
        return "\n".join(headings)
    return ""


def _collect_entry_points(project_type: str, cwd: str) -> list[str]:
    """Return known entry-point paths that actually exist."""
    if project_type == "python":
        candidates = ["main.py", "src/main.py", "__main__.py"]
    elif project_type == "node":
        candidates = ["index.js", "src/index.js", "index.ts", "src/index.ts"]
    else:
        candidates = []
    return [c for c in candidates if (Path(cwd) / c).exists()]


async def collect_snapshot(cwd: str) -> dict[str, Any]:
    """Run all collectors off the event loop via asyncio.to_thread (file I/O)."""
    def _run() -> dict[str, Any]:
        project_type, project_name = _detect_project_type(cwd)
        return {
            "project_type":    project_type,
            "project_name":    project_name,
            "tree":            _collect_tree(cwd),
            "readme_headings": _collect_readme_headings(cwd),
            "entry_points":    _collect_entry_points(project_type, cwd),
        }
    return await asyncio.to_thread(_run)


def format_snapshot(snap: dict[str, Any]) -> str:
    """Format the snapshot dict as a markdown string for injection into system prompt."""
    parts = ["# Project Snapshot"]
    if snap.get("project_type") and snap["project_type"] != "unknown":
        line = f"- Type: {snap['project_type']}"
        if snap.get("project_name"):
            line += f" — {snap['project_name']}"
        parts.append(line)
    if snap.get("entry_points"):
        parts.append(f"- Entry points: {', '.join(snap['entry_points'])}")
    if snap.get("tree"):
        parts.append("")
        parts.append("## Top-level layout")
        parts.append("```")
        parts.append(snap["tree"])
        parts.append("```")
    if snap.get("readme_headings"):
        parts.append("")
        parts.append("## README outline")
        parts.append("```")
        parts.append(snap["readme_headings"])
        parts.append("```")
    return "\n".join(parts)
