"""The domain library: the harness trees and the lessons that earned their place.

Task 2.2 of the Harness2/DSH enhancement plan.

A domain keeps what its tasks learned: the harness tree that worked, and the
experience that goes with it — the cue that called for it, the lesson it
teaches, the evidence it rests on. The next task of the domain starts from
what is kept here instead of from nothing.

The library refuses what would poison it. A tree that carries the words of the
grader — the rubric, the score — is not an experience but a leak of the
benchmark, so it is never stored.

Based on Google Research Harness2 (arXiv:2610.14224).

Author: 최진호
Date:   2026-10-20
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from nerdvana_cli.core.harness.surface import HarnessTree, SurfaceSpec, normalize_rel

# The words of the grader. A tree that carries one of them leaks the benchmark.
_GRADER_LEAK_RX = re.compile(r"\b(grader|rubric|score|scoring|scorer)\b", re.IGNORECASE)

# The file that holds the index of a domain.
_INDEX_NAME = "INDEX.md"


@dataclass(frozen=True)
class ExperienceNote:
    """What a task learned: the cue, the lesson and the evidence it rests on."""

    cue:      str
    lesson:   str
    evidence: str


@dataclass(frozen=True)
class LibraryEntry:
    """One stored experience: the harness tree, the note and whether it was kept."""

    task_id:   str
    harness:   HarnessTree
    experience: ExperienceNote
    retained:  bool = True


class DomainLibrary:
    """The stored experiences of one domain, on disk.

    The tree of a task is kept whole: the files a harness tree holds are the
    files that made it work.
    """

    def __init__(self, root: Path, domain: str, surface: SurfaceSpec) -> None:
        self._root    = Path(root)
        self._domain  = domain
        self._surface = surface

    @property
    def domain_dir(self) -> Path:
        """The directory that holds the experiences of this domain."""
        return self._root / self._domain

    @property
    def domain(self) -> str:
        """The domain the library holds the experiences of."""
        return self._domain

    @property
    def index_path(self) -> Path:
        """The index of this domain."""
        return self.domain_dir / _INDEX_NAME

    def add(
        self,
        task_id:     str,
        harness:     HarnessTree,
        experience:  ExperienceNote,
        retained:    bool = True,
    ) -> Path | None:
        """Store the experience of a task; None when the tree would poison the library."""
        leak = self._leak(harness, experience)
        if leak is not None:
            return None
        entry_dir = self.domain_dir / task_id
        self._write_tree(entry_dir, harness)
        (entry_dir / "experience.json").write_text(
            json.dumps(asdict(experience), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (entry_dir / "retained").write_text("retained\n" if retained else "forgotten\n", encoding="utf-8")
        self.rebuild_index()
        return entry_dir

    def forget(self, task_id: str) -> bool:
        """Drop the experience of a task; True when there was one to drop."""
        entry_dir = self.domain_dir / task_id
        if not entry_dir.is_dir():
            return False
        (entry_dir / "retained").write_text("forgotten\n", encoding="utf-8")
        self.rebuild_index()
        return True

    def retain(self, task_id: str) -> bool:
        """Keep the experience of a task; True when there was one to keep."""
        entry_dir = self.domain_dir / task_id
        marker    = entry_dir / "retained"
        if not marker.is_file():
            return False
        marker.write_text("retained\n", encoding="utf-8")
        self.rebuild_index()
        return True

    def load_tree(self, task_id: str) -> HarnessTree:
        """The harness tree stored for a task."""
        entry_dir = self.domain_dir / task_id
        tree: HarnessTree = {}
        for path in sorted(entry_dir.rglob("*")):
            if not path.is_file() or path.name in {"experience.json", "retained"}:
                continue
            rel = path.relative_to(entry_dir / "harness").as_posix()
            tree[rel] = path.read_text(encoding="utf-8")
        return tree

    def load_note(self, task_id: str) -> ExperienceNote | None:
        """The experience note stored for a task."""
        path = self.domain_dir / task_id / "experience.json"
        if not path.is_file():
            return None
        return ExperienceNote(**json.loads(path.read_text(encoding="utf-8")))

    def retained_ids(self) -> list[str]:
        """The tasks whose experience is kept, in the order they were stored."""
        if not self.domain_dir.is_dir():
            return []
        kept: list[str] = []
        for entry in sorted(p for p in self.domain_dir.iterdir() if p.is_dir()):
            marker = entry / "retained"
            if marker.is_file() and marker.read_text(encoding="utf-8").startswith("retained"):
                kept.append(entry.name)
        return kept

    def notes(self) -> list[tuple[str, ExperienceNote]]:
        """The kept experiences, each with the task it came from."""
        found: list[tuple[str, ExperienceNote]] = []
        for task_id in self.retained_ids():
            note = self.load_note(task_id)
            if note is not None:
                found.append((task_id, note))
        return found

    def entries(self) -> list[LibraryEntry]:
        """The kept experiences of this domain, with their trees."""
        found: list[LibraryEntry] = []
        for task_id in self.retained_ids():
            note = self.load_note(task_id)
            if note is None:
                continue
            found.append(LibraryEntry(
                task_id    = task_id,
                harness    = self.load_tree(task_id),
                experience = note,
                retained   = True,
            ))
        return found

    def inherited_tree(self) -> HarnessTree:
        """The tree the next task of this domain starts from.

        The kept experiences are merged in the order they were stored, so a later
        one may correct an earlier one. A task that kept nothing leaves the
        tree empty, and the next task starts from nothing.
        """
        tree: HarnessTree = {}
        for task_id in self.retained_ids():
            tree.update(self.load_tree(task_id))
        return tree

    def index_text(self) -> str:
        """The index of the domain, rebuilt and read back."""
        self.rebuild_index()
        return self.index_path.read_text(encoding="utf-8") if self.index_path.is_file() else ""

    def rebuild_index(self) -> Path:
        """Write the index of the domain from what it holds."""
        self.domain_dir.mkdir(parents=True, exist_ok=True)
        lines = [f"# {self._domain}", ""]
        for entry_dir in sorted(p for p in self.domain_dir.iterdir() if p.is_dir()):
            note = self.load_note(entry_dir.name)
            marker = entry_dir / "retained"
            kept = marker.is_file() and marker.read_text(encoding="utf-8").startswith("retained")
            lines.append(f"## {entry_dir.name}")
            lines.append(f"- kept: {'yes' if kept else 'no'}")
            if note is not None:
                lines.append(f"- cue: {note.cue}")
                lines.append(f"- lesson: {note.lesson}")
                lines.append(f"- evidence: {note.evidence}")
            lines.append("")
        self.index_path.write_text("\n".join(lines), encoding="utf-8")
        return self.index_path

    def _write_tree(self, entry_dir: Path, harness: HarnessTree) -> None:
        """Write the harness tree of a task, one file per path."""
        root = entry_dir / "harness"
        for raw_path, content in harness.items():
            rel = normalize_rel(raw_path)
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

    def _leak(self, harness: HarnessTree, experience: ExperienceNote) -> str | None:
        """The word of the grader a tree or a note carries, if any."""
        for path, content in harness.items():
            match = _GRADER_LEAK_RX.search(content)
            if match:
                return f"{path}: {match.group(0)}"
        for field, value in asdict(experience).items():
            match = _GRADER_LEAK_RX.search(str(value))
            if match:
                return f"experience.{field}: {match.group(0)}"
        return None
