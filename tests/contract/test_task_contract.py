"""What a contract states: its fields, its digest, its serialisations and the manifest it builds."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from nerdvana_cli.core.contract.schemas import PolicyEnvelope, TargetRepo, VerificationCommand
from nerdvana_cli.core.contract.task_contract import TaskContract, files_under, is_test_file


def _sample() -> TaskContract:
    """A contract with every field stated, the shape the assertions below read back."""
    return TaskContract(
        contract_id="contract-001",
        title="Evidence binding for the contract package",
        description="What a receipt has to prove before anyone believes it.",
        target_repos=[TargetRepo(path="nerdvana-cli", base_commit_sha="0123456789abcdef")],
        allowed_paths=["nerdvana_cli/core/contract"],
        forbidden_paths=["pyproject.toml"],
        verification_commands=[VerificationCommand(command="pytest -q")],
        test_digest_manifest={"tests/test_contract.py": "a" * 64},
        policy_envelope=PolicyEnvelope(allowed_models=["mimo"], max_total_tokens=100_000, max_cost_usd=1.5),
    )


def _suite(tmp_path: Path) -> Path:
    """A repository shape: two tests, a helper, a subdirectory and two trees that must be skipped."""
    (tmp_path / "tests" / "unit").mkdir(parents=True)
    (tmp_path / "tests" / "__pycache__").mkdir()
    (tmp_path / "tests" / "node_modules").mkdir()
    (tmp_path / "tests" / "test_alpha.py").write_text("assert True\n", encoding="utf-8")
    (tmp_path / "tests" / "test_beta.py").write_text("assert True\n", encoding="utf-8")
    (tmp_path / "tests" / "conftest.py").write_text("raise SystemExit\n", encoding="utf-8")
    (tmp_path / "tests" / "unit" / "test_gamma.py").write_text("assert True\n", encoding="utf-8")
    (tmp_path / "tests" / "__pycache__" / "test_stale.py").write_text("stale\n", encoding="utf-8")
    (tmp_path / "tests" / "node_modules" / "test_dep.py").write_text("stale\n", encoding="utf-8")
    return tmp_path


def test_every_field_a_contract_states_comes_back_unchanged() -> None:
    contract = _sample()
    assert contract.contract_id == "contract-001"
    assert contract.version == 1
    assert contract.title == "Evidence binding for the contract package"
    assert contract.description.startswith("What a receipt")
    assert contract.target_repos[0].path == "nerdvana-cli"
    assert contract.target_repos[0].base_commit_sha == "0123456789abcdef"
    assert contract.allowed_paths == ["nerdvana_cli/core/contract"]
    assert contract.forbidden_paths == ["pyproject.toml"]
    assert contract.verification_commands[0].command == "pytest -q"
    assert contract.test_digest_manifest == {"tests/test_contract.py": "a" * 64}
    assert contract.policy_envelope.max_total_tokens == 100_000


def test_a_contract_left_blank_states_the_documented_defaults() -> None:
    contract = TaskContract(contract_id="blank", title="Blank", target_repos=[])
    command = VerificationCommand(command="pytest -q")
    assert contract.version == 1
    assert contract.description == ""
    assert contract.allowed_paths == [] and contract.forbidden_paths == []
    assert contract.verification_commands == [] and contract.test_digest_manifest == {}
    assert contract.policy_envelope == PolicyEnvelope()
    assert command.timeout == 300 and command.env == {}


def test_the_digest_is_stable_for_the_same_contract_and_moves_with_every_field() -> None:
    contract = _sample()
    digest = contract.compute_digest()
    assert digest == _sample().compute_digest()
    assert len(digest) == 64 and digest != contract.contract_id
    changes = (
        {"title": "A different title"},
        {"version": 2},
        {"verification_commands": [VerificationCommand(command="ruff check .")]},
        {"test_digest_manifest": {"tests/test_contract.py": "b" * 64}},
        {"policy_envelope": PolicyEnvelope(max_cost_usd=2.5)},
    )
    for change in changes:
        assert contract.model_copy(update=change).compute_digest() != digest, change


def test_the_json_a_contract_writes_reads_back_with_the_same_digest() -> None:
    contract = _sample()
    raw = contract.to_json()
    restored = TaskContract.from_json(raw)
    assert raw == contract.to_json()
    assert restored == contract
    assert restored.compute_digest() == contract.compute_digest()
    assert list(json.loads(raw)) == sorted(json.loads(raw))


def test_the_yaml_a_contract_writes_reads_back_with_the_same_digest() -> None:
    contract = _sample()
    restored = TaskContract.from_yaml(contract.to_yaml())
    assert restored == contract
    assert restored.compute_digest() == contract.compute_digest()


def test_a_serialised_contract_missing_a_required_field_is_rejected() -> None:
    with pytest.raises(ValidationError):
        TaskContract.from_json('{"contract_id": "x", "target_repos": []}')


def test_a_directory_pattern_takes_every_file_it_holds_but_neither_caches_nor_dependencies(tmp_path: Path) -> None:
    root = _suite(tmp_path)
    manifest = TaskContract.build_test_manifest(root, ["tests"])
    assert set(manifest) == {
        "tests/conftest.py",
        "tests/test_alpha.py",
        "tests/test_beta.py",
        "tests/unit/test_gamma.py",
    }
    pinned = (root / "tests" / "test_alpha.py").read_bytes()
    assert manifest["tests/test_alpha.py"] == hashlib.sha256(pinned).hexdigest()


def test_a_file_pattern_takes_only_what_it_names(tmp_path: Path) -> None:
    root = _suite(tmp_path)
    globbed = TaskContract.build_test_manifest(root, ["tests/test_*.py"])
    assert set(globbed) == {"tests/test_alpha.py", "tests/test_beta.py"}
    assert set(TaskContract.build_test_manifest(root, ["tests/test_alpha.py"])) == {"tests/test_alpha.py"}
    assert set(TaskContract.build_test_manifest(root, ["tests/unit"])) == {"tests/unit/test_gamma.py"}
    assert TaskContract.build_test_manifest(root, ["nothing/*"]) == {}


def test_the_manifest_does_not_depend_on_the_order_the_patterns_were_written_in(tmp_path: Path) -> None:
    root = _suite(tmp_path)
    forward = TaskContract.build_test_manifest(root, ["tests/test_alpha.py", "tests/test_beta.py"])
    backward = TaskContract.build_test_manifest(root, ["tests/test_beta.py", "tests/test_alpha.py"])
    assert forward == backward


def test_the_file_helpers_recognise_a_test_and_walk_past_the_ignored_trees(tmp_path: Path) -> None:
    root = _suite(tmp_path)
    assert is_test_file(Path("tests/test_alpha.py"))
    assert is_test_file(Path("tests/alpha_test.py"))
    assert not is_test_file(Path("tests/conftest.py"))
    assert not is_test_file(Path("tests/README.md"))
    walked = {path.name for path in files_under(root / "tests")}
    assert walked == {"test_alpha.py", "test_beta.py", "conftest.py", "test_gamma.py"}
    assert {path.name for path in files_under(root / "tests", test_only=True)} == {
        "test_alpha.py",
        "test_beta.py",
        "test_gamma.py",
    }
