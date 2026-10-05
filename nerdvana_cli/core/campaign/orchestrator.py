"""Campaign orchestration: step one repository at a time, verify it, roll back what fails.

작성자: 최진호
날짜: 2026-10-05

Each step takes the first pending repository, checks it out in a worktree of
its own, hands the worktree to the verifier and writes the outcome to the
checkpoint. The worktree is taken down whatever the verifier does: a receipt
that passes marks the task verified, a receipt that does not passes the task as
failed, and a verifier that raises rolls the task back with the reason kept.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from nerdvana_cli.core.campaign.campaign_manager import CampaignManager, RepoTaskState
from nerdvana_cli.core.campaign.worktree_pool import WorktreePool, WorktreeSession


class Receipt(Protocol):
    """What a verifier hands back: a digest that pins the evidence, and a pass flag when it has one."""

    def compute_digest(self) -> str:
        """The digest of the evidence produced for this worktree."""
        ...


class CampaignOrchestrator:
    """Runs a campaign one step at a time, keeping the checkpoint ahead of any crash."""

    def __init__(
        self,
        manager: CampaignManager,
        worktree_pool: WorktreePool,
        verifier_factory: Callable[[Path], Receipt] | None = None,
    ) -> None:
        """The verifier is built per worktree; without one, a step fails and the task rolls back."""
        self.manager = manager
        self.worktree_pool = worktree_pool
        self.verifier_factory = verifier_factory

    def step(self) -> tuple[RepoTaskState, Receipt | None] | None:
        """Work on the first pending repository and record the outcome.

        Returns the task and the receipt the verifier returned, or None when
        nothing is pending. The worktree is gone by the time this returns.
        """
        task = self.manager.get_next_pending()
        if task is None:
            return None
        self.manager.update_status(task.repo_name, "in_progress")
        try:
            session = self.worktree_pool.create_worktree(Path(task.repo_path), self._branch_name(task))
        except Exception as error:
            self.manager.update_status(task.repo_name, "rolled_back", error_message=str(error))
            return task, None
        try:
            receipt = self._verify(session)
        except Exception as error:
            self.worktree_pool.remove_worktree(session, force=True)
            self.manager.update_status(task.repo_name, "rolled_back", error_message=str(error))
            return task, None
        self.worktree_pool.remove_worktree(session, force=True)
        if not bool(getattr(receipt, "passed", True)):
            self.manager.update_status(task.repo_name, "failed", error_message="verification did not pass")
            return task, receipt
        self.manager.update_status(task.repo_name, "verified", receipt_digest=receipt.compute_digest())
        return task, receipt

    def run_all(self) -> list[RepoTaskState]:
        """Step through the campaign until nothing is pending or left mid-flight, returning what was worked on."""
        processed: list[RepoTaskState] = []
        while not self.manager.is_completed():
            outcome = self.step()
            if outcome is None:
                break
            processed.append(outcome[0])
        return processed

    def _verify(self, session: WorktreeSession) -> Receipt:
        """Hand the worktree to the verifier, or fail when no verifier was configured."""
        if self.verifier_factory is None:
            raise RuntimeError(f"campaign {self.manager.manifest.campaign_id} has no verifier")
        return self.verifier_factory(session.worktree_path)

    def _branch_name(self, task: RepoTaskState) -> str:
        """A fresh branch per step, so a retry never collides with the branch an earlier run used."""
        safe = re.sub(r"[^A-Za-z0-9._-]+", "-", task.repo_name).strip("-") or "repo"
        return f"campaign/{safe}-{uuid.uuid4().hex[:8]}"
