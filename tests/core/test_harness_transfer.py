"""core/harness/transfer.py: how a harness tree reaches a run and what comes back.

Author: 최진호
Date:   2026-10-21
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nerdvana_cli.core.harness.transfer import (
    LOCAL_INSTRUCTIONS,
    MAX_INSTRUCTION_CHARS,
    changed_tree,
    give_tree,
    read_tree,
    render_instructions,
)

# A tree as the library holds it: a skill and a helper script.
TREE = {
    ".nerdvana/skills/py-slug/SKILL.md": (
        "---\nname: py-slug\ndescription: how a slug is made\n---\n\nSplit on the hyphens, then join with a hyphen.\n"
    ),
    "scripts/check_slug.py": "print('check')\n",
}


# ---------------------------------------------------------------------------
# The tree of a directory
# ---------------------------------------------------------------------------


def test_the_tree_of_a_directory_holds_its_skills_and_its_scripts(tmp_path: Path) -> None:
    give_tree(TREE, tmp_path)

    assert read_tree(tmp_path) == TREE


def test_the_local_instructions_are_not_part_of_the_tree(tmp_path: Path) -> None:
    give_tree(TREE, tmp_path)

    assert LOCAL_INSTRUCTIONS not in read_tree(tmp_path)


def test_the_python_cache_is_not_part_of_the_tree(tmp_path: Path) -> None:
    cache = tmp_path / "scripts" / "__pycache__"
    cache.mkdir(parents=True)
    (cache / "a.cpython-311.pyc").write_bytes(b"\x00")

    assert read_tree(tmp_path) == {}


# ---------------------------------------------------------------------------
# What a run changed
# ---------------------------------------------------------------------------


def test_what_a_run_changed_is_what_is_not_what_it_was() -> None:
    before = {"scripts/a.py": "one\n"}
    after  = {"scripts/a.py": "two\n", "scripts/b.py": "new\n"}

    assert changed_tree(before, after) == {"scripts/a.py": "two\n", "scripts/b.py": "new\n"}


def test_a_run_that_changed_nothing_teaches_nothing() -> None:
    assert changed_tree({"scripts/a.py": "one\n"}, {"scripts/a.py": "one\n"}) == {}


def test_a_file_the_run_left_alone_is_not_taught() -> None:
    before = {"scripts/a.py": "one\n"}
    after  = {"scripts/a.py": "one\n", "scripts/b.py": "new\n"}

    assert changed_tree(before, after) == {"scripts/b.py": "new\n"}


# ---------------------------------------------------------------------------
# The instructions
# ---------------------------------------------------------------------------


def test_the_instructions_carry_the_skill_and_name_the_script() -> None:
    text = render_instructions(TREE)

    assert "Split on the hyphens" in text
    assert "scripts/check_slug.py" in text


def test_the_front_matter_is_not_part_of_the_lesson() -> None:
    text = render_instructions(TREE)

    assert "name: py-slug" not in text
    assert "description: how a slug is made" not in text


def test_a_tree_that_holds_nothing_teaches_nothing() -> None:
    assert render_instructions({}) == ""


def test_the_instructions_are_cut_when_they_grow_past_the_budget() -> None:
    big = {".nerdvana/skills/x/SKILL.md": "word " * 40_000}

    text = render_instructions(big)

    assert len(text) <= MAX_INSTRUCTION_CHARS + 200
    assert "left out" in text


def test_the_instructions_go_into_the_local_ones_and_leave_the_rest_alone(tmp_path: Path) -> None:
    local = tmp_path / LOCAL_INSTRUCTIONS
    local.write_text("# the repository's own instructions\n", encoding="utf-8")

    give_tree(TREE, tmp_path)

    text = local.read_text(encoding="utf-8")
    assert "the repository's own instructions" in text
    assert "Split on the hyphens" in text


def test_a_path_that_escapes_the_tree_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        give_tree({"../evil.md": "x\n"}, tmp_path)


def test_the_paths_that_were_written_are_named(tmp_path: Path) -> None:
    written = give_tree(TREE, tmp_path)

    assert ".nerdvana/skills/py-slug/SKILL.md" in written
    assert "scripts/check_slug.py" in written
