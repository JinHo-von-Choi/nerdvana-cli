"""Unit tests for nerdvana_cli.core.evidence.receipt_bundle.

Covers creation, canonical JSON round-trip, deterministic checksums, tamper
detection on the covered fields and atomic save/load of a bundle to disk.

작성자: 최진호
날짜: 2026-10-05
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from nerdvana_cli.core.evidence.receipt_bundle import CHECKSUM_FIELDS, ReceiptBundle


def _sample() -> ReceiptBundle:
    """A bundle with every field stated, the shape the assertions below read back."""
    return ReceiptBundle(
        bundle_id="bundle-001",
        contract_digest="a" * 64,
        receipt_digest="b" * 64,
        repo_name="nerdvana-cli",
        created_at="2026-10-05T00:00:00Z",
        diff_text="- client.fetch(user)\n+ client.records.get(user)\n",
        static_analysis_output="Found 1 error in sample.py:12",
        test_console_output="3 passed in 0.21s",
        passed=True,
    )


def test_every_field_a_bundle_is_created_with_comes_back_unchanged() -> None:
    bundle = _sample()
    assert bundle.bundle_id == "bundle-001"
    assert bundle.contract_digest == "a" * 64
    assert bundle.receipt_digest == "b" * 64
    assert bundle.repo_name == "nerdvana-cli"
    assert bundle.created_at == "2026-10-05T00:00:00Z"
    assert bundle.diff_text.startswith("- client.fetch")
    assert bundle.static_analysis_output.startswith("Found 1 error")
    assert bundle.test_console_output.startswith("3 passed")
    assert bundle.passed is True
    assert bundle.checksum == ""


def test_a_bundle_survives_a_canonical_json_round_trip() -> None:
    bundle = _sample()
    raw = json.dumps(bundle.to_dict(), sort_keys=True, ensure_ascii=False)
    restored = ReceiptBundle.from_dict(json.loads(raw))
    assert restored == bundle
    assert restored.to_dict() == bundle.to_dict()


def test_from_dict_ignores_keys_the_record_does_not_carry() -> None:
    data = _sample().to_dict()
    data["stale_field"] = "written by an older release"
    restored = ReceiptBundle.from_dict(data)
    assert restored == _sample()


def test_the_checksum_is_a_sha256_over_the_canonical_covered_fields() -> None:
    bundle = _sample()
    payload = {name: getattr(bundle, name) for name in CHECKSUM_FIELDS}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    assert bundle.compute_checksum() == hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    assert len(bundle.compute_checksum()) == 64


def test_equal_bundles_checksum_alike_and_a_changed_field_does_not() -> None:
    same, other = _sample(), _sample()
    assert same.compute_checksum() == other.compute_checksum()
    other.diff_text += "+ client.records.get(admin)\n"
    assert other.compute_checksum() != same.compute_checksum()


def test_verify_integrity_accepts_a_bundle_whose_checksum_it_still_hashes_to() -> None:
    bundle = _sample()
    bundle.checksum = bundle.compute_checksum()
    assert bundle.verify_integrity() is True


def test_verify_integrity_rejects_a_bundle_that_was_never_checksummed() -> None:
    assert _sample().verify_integrity() is False


def test_editing_the_diff_or_the_verdict_is_detected() -> None:
    bundle = _sample()
    bundle.checksum = bundle.compute_checksum()
    edited = ReceiptBundle.from_dict(bundle.to_dict())
    edited.diff_text = edited.diff_text.replace("client.records.get", "client.records.pop")
    assert edited.verify_integrity() is False
    flipped = ReceiptBundle.from_dict(bundle.to_dict())
    flipped.passed = False
    assert flipped.verify_integrity() is False


def test_editing_any_covered_field_is_detected() -> None:
    bundle = _sample()
    bundle.checksum = bundle.compute_checksum()
    for name in CHECKSUM_FIELDS:
        tampered = ReceiptBundle.from_dict(bundle.to_dict())
        setattr(tampered, name, "edited" if name != "passed" else False)
        assert tampered.verify_integrity() is False, name


def test_save_then_load_returns_the_same_bundle_and_leaves_no_temporary_file(tmp_path: Path) -> None:
    bundle = _sample()
    bundle.checksum = bundle.compute_checksum()
    path = tmp_path / "receipt" / "bundle.json"
    bundle.save(path)
    assert path.is_file()
    assert sorted(p.name for p in path.parent.iterdir() if p.name.endswith(".tmp")) == []
    loaded = ReceiptBundle.load(path)
    assert loaded == bundle
    assert loaded.verify_integrity() is True


def test_save_replaces_what_was_there_before(tmp_path: Path) -> None:
    path = tmp_path / "bundle.json"
    first, second = _sample(), _sample()
    second.bundle_id = "bundle-002"
    second.checksum = second.compute_checksum()
    first.save(path)
    second.save(path)
    assert ReceiptBundle.load(path) == second
    assert sorted(p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")) == []


def test_a_bundle_edited_on_disk_after_it_was_written_fails_verification(tmp_path: Path) -> None:
    bundle = _sample()
    bundle.checksum = bundle.compute_checksum()
    path = tmp_path / "bundle.json"
    bundle.save(path)
    edited = json.loads(path.read_text(encoding="utf-8"))
    edited["test_console_output"] = "0 failed, everything green"
    path.write_text(json.dumps(edited, indent=2, ensure_ascii=False), encoding="utf-8")
    assert ReceiptBundle.load(path).verify_integrity() is False
