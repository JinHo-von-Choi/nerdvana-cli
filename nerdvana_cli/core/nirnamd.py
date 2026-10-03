"""NIRNA.md loader — project instructions for NerdVana CLI.

Discovery order (ascending priority):
1. ~/.nerdvana/NIRNA.md              (global user instructions)
2. <cwd>/NIRNA.md                    (project instructions, checked in)
3. <cwd>/NIRNA.local.md              (local instructions, gitignored)
4. <cwd>/AGENTS.md, <cwd>/CLAUDE.md  (appended after the NIRNA.md tiers)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace

from nerdvana_cli.core.config import paths
from nerdvana_cli.core.token_estimator import approx_tokens

MAX_INSTRUCTION_BYTES = 50_000
COMPAT_RULE_FILENAMES = ("AGENTS.md", "CLAUDE.md")


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

    for name in COMPAT_RULE_FILENAMES:
        compat_path = os.path.join(cwd, name)
        content     = read_rule_file(compat_path, cwd)
        if content:
            files.append(NirnaFile(
                path=compat_path,
                type="project",
                content=content,
            ))

    return files


def fit_to_budget(files: list[NirnaFile], max_tokens: int) -> list[NirnaFile]:
    """Cut each document longer than *max_tokens* at a paragraph boundary and say what was left out.

    The notice names the file so the model can read the rest when it matters. A budget of 0
    or less keeps every document whole.
    """
    if max_tokens <= 0:
        return files
    return [_truncated(f, max_tokens) if approx_tokens(f.content) > max_tokens else f for f in files]


def _truncated(file: NirnaFile, max_tokens: int) -> NirnaFile:
    kept: list[str] = []
    used = 0
    for paragraph in file.content.split("\n\n"):
        cost = approx_tokens(paragraph)
        if kept and used + cost > max_tokens:
            break
        kept.append(paragraph)
        used += cost
    omitted = approx_tokens(file.content) - used
    notice  = f"[About {omitted:,} tokens of this document are not shown. Read {file.path} for the rest.]"
    return replace(file, content="\n\n".join(kept) + "\n\n" + notice)


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


def is_within_root(path: str, root: str) -> bool:
    """Return True when *path* resolves, after symlinks, to *root* or below it."""
    resolved_root = os.path.realpath(root)
    resolved_path = os.path.realpath(path)
    try:
        return os.path.commonpath([resolved_root, resolved_path]) == resolved_root
    except ValueError:
        return False


def read_rule_file(path: str, root: str) -> str:
    """Read a rule file that must resolve inside *root*.

    Returns an empty string when the file is missing, unreadable, empty, or
    its symlink-resolved path lies outside *root*.
    """
    if not os.path.isfile(path) or not is_within_root(path, root):
        return ""
    return _read_file(path, MAX_INSTRUCTION_BYTES)


def _read_file(path: str, max_bytes: int = MAX_INSTRUCTION_BYTES) -> str:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read(max_bytes)
    except Exception:
        return ""
