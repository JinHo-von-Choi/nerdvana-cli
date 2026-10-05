"""The task contract: what a task promises, how it is digested and what it demands of the tests.

A :class:`TaskContract` is the statement one task makes about its own work: the repositories
it touches, the paths a change may and may not write, the commands that have to pass and the
policy the work stays inside. ``compute_digest`` folds that statement into one sha256, so a
receipt, a log line or a review note can be checked against exactly the contract that produced
it instead of against a copy that has since moved on.

``build_test_manifest`` pins the suite the contract is judged by: sha256 of every file the
patterns select, keyed by its path relative to the repository root. The verifier compares that
manifest with what is on disk before and after a run, which is what turns a receipt into
evidence rather than a claim.

Author: 최진호
Date:   2026-10-05
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from pathlib import Path

import yaml  # type: ignore[import-untyped,unused-ignore]
from pydantic import BaseModel, Field

from nerdvana_cli.core.contract.schemas import PolicyEnvelope, TargetRepo, VerificationCommand

# Never part of a manifest, never scanned for a test the contract did not ask for.
IGNORED_DIRS = frozenset({"__pycache__", ".git", ".venv", "venv", "node_modules", ".pytest_cache", ".mypy_cache"})


def file_digest(path: Path) -> str:
    """Sha256 of the file's bytes, the value every manifest entry is compared against."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def is_test_file(path: Path) -> bool:
    """True for the names a suite is written in: ``test_*.py`` and ``*_test.py``."""
    return path.suffix == ".py" and (path.name.startswith("test_") or path.name.endswith("_test.py"))


def files_under(directory: Path, test_only: bool = False) -> Iterator[Path]:
    """Regular files below ``directory``, skipping caches and dependency trees.

    ``test_only`` keeps the test files, which is what the addition check scans for; without it
    every file a directory pattern selects is taken, so a manifest over a test directory also
    pins the fixtures, the helpers and ``conftest.py`` that directory holds.
    """
    for path in sorted(directory.rglob("*")):
        if not path.is_file():
            continue
        if IGNORED_DIRS & set(path.relative_to(directory).parts):
            continue
        if test_only and not is_test_file(path):
            continue
        yield path


def _selected(repo_root: Path, patterns: list[str]) -> set[Path]:
    """Every file the patterns pick out; a pattern naming a directory expands to what is under it."""
    files: set[Path] = set()
    for pattern in patterns:
        for matched in sorted(repo_root.glob(pattern)):
            if matched.is_dir():
                files.update(files_under(matched))
            elif matched.is_file():
                files.add(matched)
    return files


class TaskContract(BaseModel):
    """One task's statement about its own work, digested into a single sha256.

    ``contract_id`` and ``title`` say what the task is, ``version`` says which reading of the
    contract applies, ``target_repos`` says where it lands and ``allowed_paths`` and
    ``forbidden_paths`` say which files a change may touch. ``verification_commands`` are the
    commands that prove the work, ``test_digest_manifest`` is the suite they run against and
    ``policy_envelope`` is the budget the work has to stay inside.
    """

    contract_id:           str
    version:               int                       = 1
    title:                 str
    description:           str                       = ""
    target_repos:          list[TargetRepo]
    allowed_paths:         list[str]                 = Field(default_factory=list)
    forbidden_paths:       list[str]                 = Field(default_factory=list)
    verification_commands: list[VerificationCommand] = Field(default_factory=list)
    test_digest_manifest:  dict[str, str]            = Field(default_factory=dict)
    policy_envelope:       PolicyEnvelope            = Field(default_factory=PolicyEnvelope)

    def compute_digest(self) -> str:
        """Sha256 over the contract as canonical JSON, so equal contracts digest equally."""
        canonical = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def build_test_manifest(repo_root: Path, test_patterns: list[str]) -> dict[str, str]:
        """Sha256 of every file the patterns select, keyed by the path relative to ``repo_root``.

        Keys come out sorted, so the same suite at the same paths yields the same manifest
        whichever order the patterns were written in.
        """
        return {
            path.relative_to(repo_root).as_posix(): file_digest(path)
            for path in sorted(_selected(repo_root, test_patterns))
        }

    def to_json(self) -> str:
        """The contract as stable JSON: sorted keys, two-space indent, unicode kept as written."""
        return json.dumps(self.model_dump(mode="json"), indent=2, sort_keys=True, ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str) -> TaskContract:
        """The contract ``raw`` describes, with every field validated."""
        return cls.model_validate(json.loads(raw))

    def to_yaml(self) -> str:
        """The contract as YAML, written from the same data ``to_json`` writes."""
        text: str = yaml.safe_dump(
            self.model_dump(mode="json"),
            sort_keys=True,
            allow_unicode=True,
            default_flow_style=False,
        )
        return text

    @classmethod
    def from_yaml(cls, raw: str) -> TaskContract:
        """The contract ``raw`` describes, with every field validated."""
        return cls.model_validate(yaml.safe_load(raw))
