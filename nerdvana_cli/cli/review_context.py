"""What a reviewer needs to see about a change: the diff and where the changed code is used.

Author: 최진호
Date:   2026-10-03

A review of a diff alone cannot tell whether a changed function still fits the code that calls it. This
module reads a git diff, finds the functions and classes the changed lines fall in (Python files, through
the ``ast`` module) and lists the places that mention each of them, so the reviewer starts with the
changed code and its users instead of a whole repository.

Other languages get the diff only. The references are found by whole-word text search (``git grep``), so
they include same-named things and miss dynamic uses; the reviewer is told that.
"""

from __future__ import annotations

import ast
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(?P<start>\d+)(?:,(?P<count>\d+))? @@")
MAX_REFERENCES_PER_SYMBOL = 12
MAX_DIFF_CHARS            = 60_000


@dataclass
class FileChange:
    """One changed file: its path and the new-file line numbers that were added or modified."""

    path:  str
    lines: set[int] = field(default_factory=set)


@dataclass(frozen=True)
class ChangedSymbol:
    """A function or class that contains changed lines."""

    path:  str
    name:  str      # the bare name, what other code mentions
    qual:  str      # Class.method
    kind:  str
    start: int
    end:   int


@dataclass(frozen=True)
class Reference:
    """One place that mentions a changed symbol."""

    path: str
    line: int
    text: str


class ReviewError(RuntimeError):
    """The change cannot be read (not a repository, unknown base)."""


def run_git(root: str, *args: str) -> str:
    """Output of a git command in *root*; raises ReviewError with git's message when it fails."""
    done = subprocess.run(["git", "-C", root, *args], capture_output=True, text=True, check=False)  # noqa: S603, S607
    if done.returncode not in (0, 1):  # git grep exits 1 for "no match"
        raise ReviewError(done.stderr.strip() or f"git {args[0]} failed")
    return done.stdout


def diff_text(root: str, base: str, paths: list[str] | None = None) -> str:
    """The unified diff of the working tree against *base*, with three lines of context."""
    return run_git(root, "diff", "--no-color", "-U3", base, "--", *(paths or ["."]))


def parse_changes(root: str, base: str, paths: list[str] | None = None) -> list[FileChange]:
    """The files and new-file lines that differ from *base* (zero-context diff, so only real changes)."""
    changes: list[FileChange] = []
    current: FileChange | None = None
    for line in run_git(root, "diff", "--no-color", "-U0", base, "--", *(paths or ["."])).splitlines():
        if line.startswith("+++ "):
            target  = line[4:]
            current = None if target == "/dev/null" else FileChange(target.removeprefix("b/"))
            if current is not None:
                changes.append(current)
        elif current is not None and (hunk := _HUNK.match(line)):
            start, count = int(hunk["start"]), int(hunk["count"]) if hunk["count"] is not None else 1
            current.lines.update(range(start, start + max(count, 1)))
    return changes


def changed_symbols(path: str, source: str, lines: set[int]) -> list[ChangedSymbol]:
    """The innermost function or class around each changed line of a Python file; empty for other files."""
    if not path.endswith(".py"):
        return []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    defs: list[ChangedSymbol] = []

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                qual = f"{prefix}{child.name}"
                kind = "class" if isinstance(child, ast.ClassDef) else "function"
                defs.append(ChangedSymbol(path, child.name, qual, kind, child.lineno, child.end_lineno or child.lineno))
                walk(child, f"{qual}.")
            else:
                walk(child, prefix)

    walk(tree, "")
    found: dict[str, ChangedSymbol] = {}
    for number in sorted(lines):
        inside = [d for d in defs if d.start <= number <= d.end]
        if inside:
            innermost = min(inside, key=lambda d: d.end - d.start)
            found.setdefault(innermost.qual, innermost)
    return list(found.values())


def find_references(root: str, symbol: ChangedSymbol, limit: int = MAX_REFERENCES_PER_SYMBOL) -> list[Reference]:
    """Lines elsewhere that mention the symbol's name as a whole word, outside its own definition."""
    if len(symbol.name) < 3:
        return []
    out = run_git(root, "grep", "-n", "-w", "-I", "--no-color", "-e", symbol.name, "--", ".")
    found: list[Reference] = []
    for raw in out.splitlines():
        path, _, rest = raw.partition(":")
        number, _, text = rest.partition(":")
        if not number.isdigit():
            continue
        line = int(number)
        if path == symbol.path and symbol.start <= line <= symbol.end:
            continue
        found.append(Reference(path, line, text.strip()[:160]))
    found.sort(key=lambda r: (_is_test(r.path), r.path, r.line))   # callers in the code before tests
    return found[:limit]


def _is_test(path: str) -> bool:
    name = Path(path).name
    return "test" in name.lower() or "/tests/" in f"/{path}"


def build_context(root: str, base: str, paths: list[str] | None = None) -> tuple[str, list[ChangedSymbol], dict[str, list[Reference]]]:
    """The diff text, the changed symbols and the references of each, for the working tree against *base*."""
    diff = diff_text(root, base, paths)
    symbols: list[ChangedSymbol] = []
    for change in parse_changes(root, base, paths):
        file = Path(root) / change.path
        if change.path.endswith(".py") and file.is_file():
            symbols.extend(changed_symbols(change.path, file.read_text(encoding="utf-8", errors="replace"), change.lines))
    return diff, symbols, {s.qual + "@" + s.path: find_references(root, s) for s in symbols}


def render_prompt(base: str, diff: str, symbols: list[ChangedSymbol], references: dict[str, list[Reference]], max_diff_chars: int = MAX_DIFF_CHARS) -> str:
    """The text handed to the reviewer."""
    cut  = diff if len(diff) <= max_diff_chars else diff[:max_diff_chars] + "\n[diff cut here; read the files for the rest]"
    parts = [
        f"Review this change (working tree against {base}). Find defects the change introduces: wrong logic, "
        "callers that no longer fit a changed signature or behaviour, missing error handling, security problems. "
        "Report only what the code supports.",
        "",
        "## Diff",
        "```diff",
        cut.rstrip(),
        "```",
    ]
    if symbols:
        parts += ["", "## Changed functions and classes, and the lines that mention them",
                  "The references are whole-word text matches: some are other things with the same name, and dynamic uses are missing."]
        for symbol in symbols:
            refs = references.get(symbol.qual + "@" + symbol.path, [])
            parts.append(f"### {symbol.path}::{symbol.qual} ({symbol.kind}, lines {symbol.start}-{symbol.end})")
            parts += [f"- {r.path}:{r.line}: {r.text}" for r in refs] or ["- no other file mentions it"]
    parts += ["", "## Answer",
              'End with one JSON object and nothing after it: {"findings": [{"file": "...", "line": 0, "severity": "high|medium|low", '
              '"problem": "...", "evidence": "..."}]}. Use an empty list when you find nothing.']
    return "\n".join(parts)
