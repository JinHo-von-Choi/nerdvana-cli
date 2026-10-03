"""What a public repository must not carry: development-process documents and names of internal systems.

The repository is public. Retrospectives, decision records, plans, evaluations and review notes describe how
the software was built, not how it is used, and the names of internal tools say how the author's systems are
wired. None of it belongs in a tracked file, in a comment or in text a user can see. Such notes live outside
the repository.

Author: 최진호
Date:   2026-10-04
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

# Tracked paths that may not exist (directory prefixes and file name patterns).
FORBIDDEN_PREFIXES = ("docs/retrospectives/", "docs/adr/", "docs/plans/", "docs/superpowers/", ".nerdvana/", ".claude/")
FORBIDDEN_NAME = re.compile(r"(^|[/\-_.])(retro(spective)?s?|postmortems?|handoff|scratch|brainstorm)([\-_./]|$)", re.IGNORECASE)

# Names and addresses of the author's own systems, and phrases that narrate the development process.
INTERNAL_TERMS = re.compile(
    r"anchormind|memento[-_ ]?mcp|\bpmcp\b|nerdvana-synthetic|\.claude/projects|/home/nirna|~/jobs/|개발\s*플랜|작업\s*분담|retrospective|docs/plans|parent roadmap|analysis report",
    re.IGNORECASE,
)
# Plan phase and task codes (case sensitive: lower case "phase 1" is ordinary prose).
PLAN_CODES = re.compile("Pha" + r"se [0-9A-Z]|T-" + r"0A|Task " + "E1")

# This file names the terms it forbids; the preflight script and the changelog may mention the policy.
EXEMPT = {"tests/contracts/test_public_hygiene.py", "tests/contracts/test_source_hygiene.py", ".gitignore"}


def _tracked() -> list[str]:
    try:
        out = subprocess.run(["git", "ls-files", "-z"], cwd=REPO, capture_output=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("git is not available")
    return [name for name in out.decode().split("\0") if name]


def test_no_development_process_document_is_tracked() -> None:
    bad = [
        name for name in _tracked()
        if name.startswith(FORBIDDEN_PREFIXES) or FORBIDDEN_NAME.search(name)
    ]
    assert bad == [], f"development-process documents must stay out of the repository: {bad}"


def test_no_tracked_text_names_an_internal_system_or_narrates_the_development() -> None:
    offenders: list[str] = []
    for name in _tracked():
        if name in EXEMPT or name == "uv.lock" or name.endswith((".png", ".ico", ".jpg", ".gif")):
            continue
        path = REPO / name
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        match = INTERNAL_TERMS.search(text) or PLAN_CODES.search(text)
        if match:
            line = text.count("\n", 0, match.start()) + 1
            offenders.append(f"{name}:{line}: {match.group(0)}")
    assert offenders == [], "internal names or process narration in tracked files:\n" + "\n".join(offenders[:30])


def test_the_patterns_recognise_what_they_look_for() -> None:
    assert FORBIDDEN_NAME.search("docs/2026-04-18-retrospective-phase.md")
    assert not FORBIDDEN_NAME.search("docs/skills.md")
    for sample in ("calls mcp__anchormind__remember", "see /home/nirna/jobs", "개발 플랜 요약"):
        assert INTERNAL_TERMS.search(sample), sample
    assert PLAN_CODES.search("Pha" + "se G2 notes") and PLAN_CODES.search("see T-" + "0A-05")
    assert not (INTERNAL_TERMS.search("a phase of the run and a plan for the user") or PLAN_CODES.search("phase 1 of the project"))
