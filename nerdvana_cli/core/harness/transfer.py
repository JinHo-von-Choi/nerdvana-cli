"""How a harness tree reaches a run, and how what a run learned comes back.

Task 4.2 of the Harness2/DSH enhancement plan.

The domain library holds harness trees. A run has to receive the tree of its
domain and, when it is done, hand back what it learned. Two channels carry it,
and both of them are ones a run reads without asking: the helper scripts, which
the agent runs with its shell, and the local project instructions, which the
prompt always carries. A skill of the tree travels as its text — what the next
task starts from is the knowledge, not the mechanism that produced it.

What comes back is what the run changed: the tree before the run and the tree
after it, compared, so the files the task repository already held are not
mistaken for something the agent learned. A run that changed nothing teaches
nothing, and there is nothing to keep.

Author: 최진호
Date:   2026-10-21
"""

from __future__ import annotations

import re
from pathlib import Path

from nerdvana_cli.core.harness.library import LibraryEntry
from nerdvana_cli.core.harness.surface import HarnessTree, normalize_rel

# The directories of the surface a run adds to, as relative prefixes.
ADDITIVE_PREFIXES: tuple[str, ...] = (".nerdvana/skills/", ".nerdvana/agents/", "scripts/")

# The file the transferred instructions are written to. It is the private one,
# so the project instructions of the repository the run works in are left alone.
LOCAL_INSTRUCTIONS = "NIRNA.local.md"

# The most the transferred instructions may hold. The always-on context has a
# budget of its own, and a library that fills it helps nobody.
MAX_INSTRUCTION_CHARS = 24_000

# The front matter of a skill file: what says the name and the description.
_FRONT_MATTER = "---"

# The fields of a front matter that say what a skill is about.
_FRONT_FIELD_RX = re.compile(r"^(name|description):\s*(.+)$", re.MULTILINE)


def _iter_additive(root: Path) -> list[Path]:
    """The files of the surface a run adds to, under *root*."""
    found: list[Path] = []
    for prefix in ADDITIVE_PREFIXES:
        base = root / prefix.rstrip("/")
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file():
                continue
            if "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            found.append(path)
    return found


def read_tree(root: Path) -> HarnessTree:
    """The harness tree a directory holds: the files of the surface it adds."""
    tree: HarnessTree = {}
    for path in _iter_additive(root):
        rel = path.relative_to(root).as_posix()
        try:
            rel = normalize_rel(rel)
        except ValueError:
            continue
        try:
            tree[rel] = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
    return tree


def changed_tree(before: HarnessTree, after: HarnessTree) -> HarnessTree:
    """What a run changed: the paths whose content is not what it was."""
    return {path: content for path, content in after.items() if before.get(path) != content}


def _skill_name(path: str) -> str:
    """The name of the skill a path holds."""
    parts = path.split("/")
    if len(parts) >= 3 and parts[-1] == "SKILL.md":
        return parts[-2]
    return parts[-1].removesuffix(".md")


def _without_front_matter(content: str) -> str:
    """The body of a skill file: what it says, without the front matter."""
    text = content.strip()
    if not text.startswith(_FRONT_MATTER):
        return text
    _, _, rest = text.partition(_FRONT_MATTER)
    _, marker, body = rest.partition(_FRONT_MATTER)
    return body.strip() if marker else text


def front_matter_fields(content: str) -> dict[str, str]:
    """The fields of the front matter of a skill file.

    The description of a skill is the situation it applies to -- the model
    writes it as "use when ...", and it is the one part of the skill that says
    whether the skill is the one a task needs.
    """
    text = content.strip()
    if not text.startswith(_FRONT_MATTER):
        return {}
    _, _, rest = text.partition(_FRONT_MATTER)
    head, marker, _ = rest.partition(_FRONT_MATTER)
    if not marker:
        return {}
    return {
        match.group(1): match.group(2).strip().strip("\"'")
        for match in _FRONT_FIELD_RX.finditer(head)
    }


def tree_of(entries: list[LibraryEntry]) -> HarnessTree:
    """The tree of the entries given, merged in the order they were given."""
    tree: HarnessTree = {}
    for entry in entries:
        tree.update(entry.harness)
    return tree


def _skill_file(path: str) -> str | None:
    """The name of the skill a path holds, when the path is a skill file.

    A skill is either a file of its own (`.nerdvana/skills/<name>.md`) or a
    directory with a `SKILL.md` in it. The files bundled beside a `SKILL.md`
    are part of that skill and are not lessons of their own.
    """
    parts = path.split("/")
    if len(parts) == 3 and parts[2].endswith(".md"):
        return parts[2][: -len(".md")]
    if len(parts) == 4 and parts[3] == "SKILL.md":
        return parts[2]
    return None


def _lessons(entries: list[LibraryEntry]) -> list[tuple[str, str]]:
    """The lessons of the entries, as the name and the situation of each.

    The name says what the lesson is, the description says the situation it
    applies to -- the model writes it as "use when ...", and it is the one part
    of a skill that says whether the skill is the one a task needs. A lesson
    two attempts wrote is one lesson, and is named once.
    """
    lessons: list[tuple[str, str]] = []
    seen: set[str] = set()
    for entry in entries:
        for path, content in sorted(entry.harness.items()):
            if not path.startswith(".nerdvana/skills/"):
                continue
            name = _skill_file(path)
            if name is None or name in seen:
                continue
            seen.add(name)
            fields = front_matter_fields(content)
            lessons.append((name, fields.get("description", "")))
    return lessons


def render_menu(entries: list[LibraryEntry]) -> str:
    """The lessons of a domain as a menu a task may choose from.

    One line per lesson: what it is, and the situation it applies to. The
    lesson itself stays on disk, and the task reads the one it wants. The
    always-on context carries the menu and not the lessons, so a task that
    needs none of them pays for a few lines and not for a shelf.
    """
    lessons = _lessons(entries)
    if not lessons:
        return ""
    lines = [
        "# What earlier tasks of this domain learned",
        "",
        "Each of these is a note an earlier task of this domain left behind. Read the ones that "
        "apply to the task at hand -- the file of a note is under `.nerdvana/skills/` -- and "
        "ignore the rest.",
        "",
    ]
    for name, description in lessons:
        lines.append(f"- `{name}` -- {description}" if description else f"- `{name}`")
    return "\n".join(lines).strip()


def give_menu(entries: list[LibraryEntry], root: Path) -> list[str]:
    """Put the lessons of a domain where the task may choose from them.

    The lessons are written as the files they are, so the task reads the one it
    wants, and the menu is added to the local project instructions, so the task
    knows they are there. The helper scripts are not handed over: a script
    written for another task's check is a trap, not a tool.
    """
    written: list[str] = []
    for entry in entries:
        for path, content in sorted(entry.harness.items()):
            if not path.startswith(".nerdvana/skills/"):
                continue
            rel = normalize_rel(path)
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            written.append(rel)
    menu = render_menu(entries)
    if menu:
        local = root / LOCAL_INSTRUCTIONS
        existing = local.read_text(encoding="utf-8") if local.is_file() else ""
        local.write_text((existing + "\n\n" + menu).strip() + "\n", encoding="utf-8")
        written.append(LOCAL_INSTRUCTIONS)
    return sorted(written)


def render_instructions(tree: HarnessTree) -> str:
    """The tree as the instructions a run reads.

    The scripts are named rather than inlined: the agent runs them. The skills
    are inlined, because their text is the knowledge. Nothing is said about the
    task the tree came from: a lesson that names one task is not a lesson the
    next task of the domain can use.
    """
    skills: list[tuple[str, str]] = []
    scripts: list[str] = []
    for path in sorted(tree):
        content = tree[path]
        if path.startswith(".nerdvana/skills/"):
            skills.append((_skill_name(path), _without_front_matter(content)))
        elif path.startswith("scripts/"):
            scripts.append(path)

    if not skills and not scripts:
        return ""

    lines = [
        "# What earlier tasks of this domain learned",
        "",
        "These are the notes earlier tasks of this domain left behind. They are the harness this "
        "task starts from: use them, and do not treat them as a description of the task at hand.",
        "",
    ]
    for name, body in skills:
        lines.append(f"## {name}")
        lines.append("")
        lines.append(body)
        lines.append("")
    if scripts:
        lines.append("## Helper scripts")
        lines.append("")
        for path in scripts:
            lines.append(f"- `{path}` — run it with `python3 {path}`.")
        lines.append("")
    text = "\n".join(lines).strip()
    if len(text) > MAX_INSTRUCTION_CHARS:
        text = text[:MAX_INSTRUCTION_CHARS].rstrip() + "\n\n[the rest of the notes was left out]"
    return text


def give_tree(tree: HarnessTree, root: Path) -> list[str]:
    """Write the tree into *root*, so the run receives it; the paths written.

    The tree is written as it is, and the instructions that carry its knowledge
    are added to the local project instructions of the run.
    """
    written: list[str] = []
    for raw_path, content in tree.items():
        rel = normalize_rel(raw_path)
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        written.append(rel)

    instructions = render_instructions(tree)
    if instructions:
        local = root / LOCAL_INSTRUCTIONS
        existing = local.read_text(encoding="utf-8") if local.is_file() else ""
        local.write_text((existing + "\n\n" + instructions).strip() + "\n", encoding="utf-8")
        written.append(LOCAL_INSTRUCTIONS)
    return sorted(written)
