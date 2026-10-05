"""What the verifier does: which differences it reports and what each run leaves behind."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pytest

from nerdvana_cli.core.contract.schemas import TargetRepo, VerificationCommand
from nerdvana_cli.core.contract.task_contract import TaskContract
from nerdvana_cli.core.contract.verifier import ContractVerifier, TamperedTestError

REJECTED = "TAMPERED_TEST_REJECTED"


def _suite(tmp_path: Path) -> Path:
    """A working tree with the two test files the contract below pins."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_alpha.py").write_text("def test_alpha():\n    assert True\n", encoding="utf-8")
    (tmp_path / "tests" / "test_beta.py").write_text("def test_beta():\n    assert True\n", encoding="utf-8")
    return tmp_path


def _contract(root: Path, *commands: str, timeout: int = 60, env: dict[str, str] | None = None) -> TaskContract:
    """The suite of ``root`` pinned, with ``commands`` to run against it."""
    return TaskContract(
        contract_id="contract-001",
        title="Evidence binding for the contract package",
        target_repos=[TargetRepo(path=str(root), base_commit_sha="0123456789abcdef")],
        verification_commands=[
            VerificationCommand(command=command, timeout=timeout, env=env or {}) for command in commands
        ],
        test_digest_manifest=TaskContract.build_test_manifest(root, ["tests"]),
    )


def _python(script: str) -> str:
    """A one-liner written the way a contract states a command: typed at a prompt."""
    return f"{sys.executable} -c \"{script}\""


def test_a_suite_that_still_matches_the_manifest_passes(tmp_path: Path) -> None:
    root = _suite(tmp_path)
    contract = _contract(root)
    assert ContractVerifier().verify_test_integrity(root, contract.test_digest_manifest) is None


def test_a_manifest_that_pins_nothing_checks_nothing(tmp_path: Path) -> None:
    root = _suite(tmp_path)
    (root / "tests" / "test_alpha.py").unlink()
    assert ContractVerifier().verify_test_integrity(root, {}) is None


def test_a_pinned_test_that_was_edited_is_rejected(tmp_path: Path) -> None:
    root = _suite(tmp_path)
    contract = _contract(root)
    (root / "tests" / "test_alpha.py").write_text("def test_alpha():\n    assert False\n", encoding="utf-8")
    with pytest.raises(TamperedTestError) as raised:
        ContractVerifier().verify_test_integrity(root, contract.test_digest_manifest)
    message = str(raised.value)
    assert REJECTED in message
    assert "modified tests/test_alpha.py" in message


def test_a_pinned_test_that_was_removed_is_rejected(tmp_path: Path) -> None:
    root = _suite(tmp_path)
    contract = _contract(root)
    (root / "tests" / "test_beta.py").unlink()
    with pytest.raises(TamperedTestError, match="missing tests/test_beta.py") as raised:
        ContractVerifier().verify_test_integrity(root, contract.test_digest_manifest)
    assert REJECTED in str(raised.value)


def test_a_test_added_to_a_directory_the_manifest_spans_is_rejected(tmp_path: Path) -> None:
    root = _suite(tmp_path)
    contract = _contract(root)
    (root / "tests" / "test_new.py").write_text("assert True\n", encoding="utf-8")
    with pytest.raises(TamperedTestError, match="added tests/test_new.py"):
        ContractVerifier().verify_test_integrity(root, contract.test_digest_manifest)


def test_an_entry_pointing_out_of_the_repository_is_rejected(tmp_path: Path) -> None:
    root = _suite(tmp_path)
    with pytest.raises(TamperedTestError, match="outside") as raised:
        ContractVerifier().verify_test_integrity(root, {"../escape.py": "0" * 64})
    assert REJECTED in str(raised.value)


def test_a_run_checks_the_suite_before_the_first_command_and_writes_nothing(tmp_path: Path) -> None:
    root = _suite(tmp_path)
    marker = root / "ran.txt"
    contract = _contract(root, _python("open('ran.txt', 'w').write('yes')"))
    (root / "tests" / "test_alpha.py").write_text("changed\n", encoding="utf-8")
    with pytest.raises(TamperedTestError):
        ContractVerifier().run_verification(root, contract)
    assert not marker.exists()


def test_a_run_that_edits_the_tests_it_is_judged_by_is_caught_after_the_commands(tmp_path: Path) -> None:
    root = _suite(tmp_path)
    edit = _python("from pathlib import Path; Path('tests/test_alpha.py').write_text('# rewritten')")
    with pytest.raises(TamperedTestError, match="modified tests/test_alpha.py"):
        ContractVerifier().run_verification(root, _contract(root, edit))


def test_a_contract_with_no_command_returns_no_receipt_but_still_checks_the_suite(tmp_path: Path) -> None:
    root = _suite(tmp_path)
    contract = _contract(root)
    assert ContractVerifier().run_verification(root, contract) == []
    (root / "tests" / "test_alpha.py").unlink()
    with pytest.raises(TamperedTestError, match="missing tests/test_alpha.py"):
        ContractVerifier().run_verification(root, contract)


def test_every_command_leaves_a_receipt_bound_to_the_contract(tmp_path: Path) -> None:
    root = _suite(tmp_path)
    contract = _contract(root, _python("print('alpha passed')"), _python("print('beta passed')"))
    receipts = ContractVerifier().run_verification(root, contract)
    assert [receipt.command for receipt in receipts] == [
        specification.command for specification in contract.verification_commands
    ]
    assert all(receipt.passed and receipt.returncode == 0 for receipt in receipts)
    assert all(receipt.contract_digest == contract.compute_digest() for receipt in receipts)
    assert all(receipt.test_digest_manifest == contract.test_digest_manifest for receipt in receipts)
    assert receipts[0].output_tail.endswith("alpha passed\n")
    assert receipts[0].duration_seconds >= 0.0
    assert datetime.fromisoformat(receipts[0].timestamp).tzinfo is not None


def test_a_command_that_fails_is_recorded_in_its_receipt_rather_than_raised(tmp_path: Path) -> None:
    root = _suite(tmp_path)
    contract = _contract(root, _python("raise SystemExit(3)"))
    [receipt] = ContractVerifier().run_verification(root, contract)
    assert receipt.passed is False
    assert receipt.returncode == 3


def test_a_command_runs_with_the_environment_the_contract_declared_over_the_callers(tmp_path: Path) -> None:
    root = _suite(tmp_path)
    probe = _python("import os, sys; sys.exit(0 if os.environ.get('NERDVANA_CONTRACT_ENV') == 'set' else 7)")
    accepted = _contract(root, probe, env={"NERDVANA_CONTRACT_ENV": "set"})
    assert all(receipt.passed for receipt in ContractVerifier().run_verification(root, accepted))
    refused = _contract(root, probe, env={"NERDVANA_CONTRACT_ENV": "other"})
    [receipt] = ContractVerifier().run_verification(root, refused)
    assert receipt.returncode == 7


def test_a_command_that_outlives_its_timeout_is_recorded_as_a_failure(tmp_path: Path) -> None:
    root = _suite(tmp_path)
    contract = _contract(root, _python("import time; time.sleep(30)"), timeout=1)
    [receipt] = ContractVerifier().run_verification(root, contract)
    assert receipt.passed is False
    assert receipt.returncode == -1
    assert "timed out after 1s" in receipt.output_tail
