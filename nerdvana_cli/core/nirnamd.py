"""NIRNA.md loader — project instructions for NerdVana CLI.

Discovery order (ascending priority):
1. ~/.nerdvana/NIRNA.md              (global user instructions)
2. <cwd>/NIRNA.md                    (project instructions, checked in)
3. <cwd>/NIRNA.local.md              (local instructions, gitignored)
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from nerdvana_cli.core import paths


@dataclass
class NirnaFile:
    path: str
    type: str  # "global", "project", "local"
    content: str

    def to_prompt_section(self) -> str:
        labels = {
            "global": "(user's global instructions for all projects)",
            "project": "(project instructions, checked into the codebase)",
            "local": "(user's private project instructions, not checked in)",
        }
        label = labels.get(self.type, "")
        return f"Contents of {self.path} {label}:\n\n{self.content}"


def global_nirnamd_path() -> str:
    """Return the global NIRNA.md path."""
    return str(paths.user_nirnamd_path())


def load_nirna_files(
    cwd: str = ".",
    global_path: str | None = None,
) -> list[NirnaFile]:
    if global_path is None:
        global_path = global_nirnamd_path()

    files: list[NirnaFile] = []

    if os.path.isfile(global_path):
        files.append(NirnaFile(
            path=global_path,
            type="global",
            content=_read_file(global_path),
        ))

    project_path = os.path.join(cwd, "NIRNA.md")
    if os.path.isfile(project_path):
        files.append(NirnaFile(
            path=project_path,
            type="project",
            content=_read_file(project_path),
        ))

    local_path = os.path.join(cwd, "NIRNA.local.md")
    if os.path.isfile(local_path):
        files.append(NirnaFile(
            path=local_path,
            type="local",
            content=_read_file(local_path),
        ))

    return files


def format_nirna_for_prompt(files: list[NirnaFile]) -> str | None:
    if not files:
        return None

    header = (
        "# User & Project Instructions (NIRNA.md)\n\n"
        "IMPORTANT: These instructions OVERRIDE default behavior. "
        "Follow them exactly as written.\n"
    )
    sections = [f.to_prompt_section() for f in files]
    return header + "\n\n".join(sections)


def _read_file(path: str, max_bytes: int = 50_000) -> str:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read(max_bytes)
    except Exception:
        return ""
