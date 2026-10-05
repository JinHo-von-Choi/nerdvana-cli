"""Campaign manifest, per-repository task state and the checkpoint file.

작성자: 최진호
날짜: 2026-10-05

A campaign is a list of repositories that each owe one task. Every transition
is written to the checkpoint straight away, so a campaign that is interrupted
picks up at the first repository still marked pending. The checkpoint is
written in one step: a temporary file is filled and moved into place, so a
reader never sees a half-written manifest.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

RepoStatus = Literal["pending", "in_progress", "verified", "failed", "rolled_back"]

TERMINAL_STATUSES: frozenset[str] = frozenset({"verified", "failed", "rolled_back"})


def _now() -> str:
    """A UTC timestamp with its offset, for ``updated_at``."""
    return datetime.now(UTC).isoformat()


@dataclass
class RepoTaskState:
    """One repository in a campaign: where it lives, which contract judges it and where it has got to."""

    repo_name: str
    repo_path: str
    status: RepoStatus
    contract_path: str
    receipt_digest: str | None = None
    error_message: str | None = None
    updated_at: str = field(default_factory=_now)


@dataclass
class CampaignManifest:
    """What a campaign knows about itself and about every repository it owes work to."""

    campaign_id: str
    name: str
    created_at: str
    tasks: list[RepoTaskState]

    def to_dict(self) -> dict[str, Any]:
        """A plain mapping of the manifest, ready to be written as json."""
        return {
            "campaign_id": self.campaign_id,
            "name": self.name,
            "created_at": self.created_at,
            "tasks": [asdict(task) for task in self.tasks],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CampaignManifest:
        """The manifest the mapping came from, tasks and all."""
        return cls(
            campaign_id=str(data["campaign_id"]),
            name=str(data["name"]),
            created_at=str(data["created_at"]),
            tasks=[RepoTaskState(**task) for task in data["tasks"]],
        )

    @classmethod
    def load_checkpoint(cls, path: Path) -> CampaignManifest:
        """The manifest saved at ``path``."""
        with path.open(encoding="utf-8") as handle:
            data = json.load(handle)
        return cls.from_dict(data)

    def save_checkpoint(self, path: Path) -> None:
        """Write the manifest to ``path`` in one step, replacing whatever was there.

        The payload goes into a temporary file beside the checkpoint first, so
        a failure leaves the previous checkpoint intact.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        handle, temp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
        temp_path = Path(temp_name)
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                stream.write(json.dumps(self.to_dict(), indent=2, ensure_ascii=False))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_path, path)
        except BaseException:
            temp_path.unlink(missing_ok=True)
            raise


class CampaignManager:
    """The manifest of a campaign, the checkpoint it is written to and the transitions its tasks go through."""

    def __init__(self, manifest: CampaignManifest, checkpoint_path: Path) -> None:
        """Every transition is written to ``checkpoint_path`` as soon as it happens."""
        self.manifest = manifest
        self.checkpoint_path = checkpoint_path

    def get_next_pending(self) -> RepoTaskState | None:
        """The first repository still waiting to be worked on, or None when there is none."""
        for task in self.manifest.tasks:
            if task.status == "pending":
                return task
        return None

    def update_status(
        self,
        repo_name: str,
        status: RepoStatus,
        receipt_digest: str | None = None,
        error_message: str | None = None,
    ) -> None:
        """Move one repository to ``status``, record the evidence or the failure, and checkpoint at once.

        Raises ``KeyError`` when the campaign has no repository by that name.
        """
        task = self._task(repo_name)
        task.status = status
        if receipt_digest is not None:
            task.receipt_digest = receipt_digest
        if error_message is not None:
            task.error_message = error_message
        task.updated_at = _now()
        self.manifest.save_checkpoint(self.checkpoint_path)

    def is_completed(self) -> bool:
        """True when nothing is mid-flight: every task is verified, failed or rolled back."""
        return all(task.status in TERMINAL_STATUSES for task in self.manifest.tasks)

    def _task(self, repo_name: str) -> RepoTaskState:
        """The task of ``repo_name``, or a KeyError naming the campaign it is missing from."""
        for task in self.manifest.tasks:
            if task.repo_name == repo_name:
                return task
        raise KeyError(f"campaign {self.manifest.campaign_id} has no repository named {repo_name}")
