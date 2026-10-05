"""Evidence binding: check the tests a contract pinned, then run the commands it demands.

A receipt is only evidence if the suite it was produced by is the suite the contract named,
so :class:`ContractVerifier` checks the test manifest twice: once before the first command
and once after the last one. A file the manifest pinned that was edited, removed or replaced
in between, and a test file added to a directory the manifest spans, raise
:class:`TamperedTestError` instead of a receipt.

The commands themselves run through the working directory of the repository the contract
targets, with the environment the contract declared merged over the caller's, and a command
that fails does not raise: it produces a receipt with ``passed`` false. The caller reads the
list of receipts and decides, keeping the evidence of a failed run instead of losing it to an
exception.

Author: 최진호
Date:   2026-10-05
"""

from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

from nerdvana_cli.core.contract.schemas import VerificationCommand, VerificationReceipt
from nerdvana_cli.core.contract.task_contract import TaskContract, file_digest, files_under

TAMPERED = "TAMPERED_TEST_REJECTED"

# What a command that never produced an exit status is recorded as.
NO_EXIT_STATUS = -1
_OUTPUT_TAIL   = 4_000


class TamperedTestError(Exception):
    """The test files on disk are not the ones the contract pinned.

    The message starts with ``TAMPERED_TEST_REJECTED`` and lists every difference found:
    ``outside`` for an entry that points out of the repository, ``missing`` for one that was
    removed, ``modified`` for one whose bytes changed and ``added`` for a test file in a
    directory the manifest spans that the manifest never listed.
    """


class ContractVerifier:
    """Runs a contract against a working tree and keeps what each command proved."""

    def verify_test_integrity(self, repo_root: Path, manifest: Mapping[str, str]) -> None:
        """Raise :class:`TamperedTestError` if the suite on disk is not the pinned one.

        An empty manifest pins nothing, so there is nothing to check.
        """
        if not manifest:
            return
        root = repo_root.resolve()
        problems = _differences(root, manifest)
        if problems:
            raise TamperedTestError(f"{TAMPERED}: " + "; ".join(problems))

    def run_verification(self, repo_root: Path, contract: TaskContract) -> list[VerificationReceipt]:
        """Check the pinned tests, run every command, check the tests again, keep one receipt each.

        The suite is checked before the first command and after the last, so a run that edits
        the tests it is judged by is caught rather than rewarded.
        """
        manifest = contract.test_digest_manifest
        self.verify_test_integrity(repo_root, manifest)
        digest     = contract.compute_digest()
        receipts   = [self._execute(repo_root, digest, manifest, item) for item in contract.verification_commands]
        self.verify_test_integrity(repo_root, manifest)
        return receipts

    def _execute(
        self,
        repo_root:  Path,
        digest:     str,
        manifest:   Mapping[str, str],
        specification: VerificationCommand,
    ) -> VerificationReceipt:
        """Run one command and record what it returned, whether it passed or not."""
        started = time.monotonic()
        returncode, output = _run(specification, repo_root)
        return VerificationReceipt(
            contract_digest      = digest,
            test_digest_manifest = dict(manifest),
            command              = specification.command,
            returncode           = returncode,
            output_tail          = output[-_OUTPUT_TAIL:],
            passed               = returncode == 0,
            duration_seconds     = round(time.monotonic() - started, 6),
            timestamp            = datetime.now(UTC).isoformat(),
        )


def _run(specification: VerificationCommand, repo_root: Path) -> tuple[int, str]:
    """The exit status and the combined output of one command.

    A command runs through the shell because a contract writes it the way it would be typed
    at a prompt, compound commands and all. A command that cannot start or that outlives its
    timeout has no exit status, and is recorded as :data:`NO_EXIT_STATUS`.
    """
    env = {**os.environ, **specification.env}
    try:
        completed = subprocess.run(
            specification.command,
            shell          = True,
            cwd            = repo_root,
            env            = env,
            capture_output = True,
            text           = True,
            timeout        = specification.timeout,
            check          = False,
        )
    except subprocess.TimeoutExpired:
        return NO_EXIT_STATUS, f"timed out after {specification.timeout}s"
    except OSError as error:
        return NO_EXIT_STATUS, f"could not start: {error}"
    return completed.returncode, f"{completed.stdout}{completed.stderr}"


def _differences(root: Path, manifest: Mapping[str, str]) -> list[str]:
    """Every way the suite differs from the manifest, sorted so the message is stable."""
    problems: list[str] = []
    entries:   dict[str, Path] = {}
    for relative, expected in sorted(manifest.items()):
        target = (root / relative).resolve()
        if not target.is_relative_to(root):
            problems.append(f"outside {relative}")
            continue
        entries[relative] = target
        if not target.is_file():
            problems.append(f"missing {relative}")
        elif file_digest(target) != expected:
            problems.append(f"modified {relative}")
    problems.extend(f"added {relative}" for relative in _added_tests(root, entries))
    return sorted(problems)


def _added_tests(root: Path, entries: Mapping[str, Path]) -> list[str]:
    """Test files in the directories the manifest spans that the manifest never listed."""
    listed = set(entries)
    found: set[str] = set()
    for directory in {target.parent for target in entries.values()}:
        if not directory.is_dir():
            continue
        for candidate in files_under(directory, test_only=True):
            relative = candidate.relative_to(root).as_posix()
            if relative not in listed:
                found.add(relative)
    return sorted(found)
