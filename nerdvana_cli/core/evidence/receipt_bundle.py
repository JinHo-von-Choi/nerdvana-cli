"""Evidence: what an auditor needs from one verification run, in a file of its own.

작성자: 최진호
날짜: 2026-10-05

A :class:`ReceiptBundle` records a run end to end: the contract digest and
receipt digest it belongs to, the repository and time it happened in, the diff
the run produced, what static analysis and the test run printed, and the
verdict. ``compute_checksum`` folds the fields an auditor reads into one
canonical JSON document and hashes it, so ``verify_integrity`` answers whether
the record still says what it said when it was written. ``save`` writes the
bundle through a temporary file and a rename, so a bundle on disk is never
half-written.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

# The fields the checksum covers: everything the record claims, except the
# checksum itself, which would otherwise cancel itself out.
CHECKSUM_FIELDS: tuple[str, ...] = (
    "bundle_id",
    "contract_digest",
    "receipt_digest",
    "repo_name",
    "diff_text",
    "static_analysis_output",
    "test_console_output",
    "passed",
)


@dataclass
class ReceiptBundle:
    """One verification run as a record: digests, repo, outputs, verdict and its checksum."""

    bundle_id:              str
    contract_digest:        str
    receipt_digest:         str
    repo_name:              str
    created_at:             str
    diff_text:              str
    static_analysis_output: str
    test_console_output:    str
    passed:                 bool
    checksum:               str = ""

    def to_dict(self) -> dict[str, Any]:
        """Every field as a plain dict, checksum included, ready to be serialised."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ReceiptBundle:
        """The bundle ``data`` describes; keys the record does not carry are ignored."""
        known = {field.name for field in fields(cls)}
        return cls(**{key: value for key, value in data.items() if key in known})

    def compute_checksum(self) -> str:
        """Sha256 over the covered fields as canonical JSON: sorted keys, compact separators."""
        payload = {name: getattr(self, name) for name in CHECKSUM_FIELDS}
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def verify_integrity(self) -> bool:
        """True while ``checksum`` still matches what the record hashes to now, False once a field was edited."""
        return bool(self.checksum) and self.checksum == self.compute_checksum()

    def save(self, path: Path) -> None:
        """Write the bundle to ``path`` in one step: a temporary file beside it, then a rename."""
        payload = json.dumps(self.to_dict(), indent=2, sort_keys=True, ensure_ascii=False)
        path.parent.mkdir(parents=True, exist_ok=True)
        handle, temp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
        temp_path = Path(temp_name)
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_path, path)
        except BaseException:
            temp_path.unlink(missing_ok=True)
            raise

    @classmethod
    def load(cls, path: Path) -> ReceiptBundle:
        """The bundle ``path`` holds, checksum included, exactly as it was written."""
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))
