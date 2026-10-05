"""An interrupted campaign keeps its checkpoint and resumes with only the work still pending.

작성자: 최진호
날짜: 2026-10-05

Every transition of a campaign is written to its checkpoint, so a campaign
that stops halfway starts again at the first repository still pending. The
worktrees a step created are gone by the time the step returns, whether the
verification passed, reported failure or raised.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from nerdvana_cli.core.campaign.campaign_manager import CampaignManager, CampaignManifest, RepoTaskState
from nerdvana_cli.core.campaign.orchestrator import CampaignOrchestrator
from nerdvana_cli.core.campaign.worktree_pool import WorktreePool

REPOS = ("alpha", "beta", "gamma")

OK: dict[str, str] = dict.fromkeys(REPOS, "ok")


class ReceiptStub:
    """A receipt in the shape a verifier returns: the evidence pinned, and how the run went."""

    def __init__(self, repo_name: str, passed: bool = True) -> None:
        self.passed = passed
        self.repo_name = repo_name

    def compute_digest(self) -> str:
        return hashlib.sha256(self.repo_name.encode("utf-8")).hexdigest()


class VerifierStub:
    """The verifier factory of a campaign: one behaviour per repository, remembering the worktrees it was given."""

    def __init__(self, behaviour: dict[str, str]) -> None:
        self.behaviour = behaviour
        self.seen: list[str] = []
        self.paths: dict[str, Path] = {}

    def __call__(self, worktree_path: Path) -> ReceiptStub:
        repo_name = next((name for name in self.behaviour if name in worktree_path.name), "unknown")
        self.seen.append(repo_name)
        self.paths[repo_name] = worktree_path
        action = self.behaviour[repo_name]
        if action == "raise":
            raise RuntimeError(f"{repo_name} verification crashed")
        return ReceiptStub(repo_name, passed=action == "ok")


def _manifest(repos: dict[str, Path]) -> CampaignManifest:
    """A campaign owing one pending task per repository."""
    return CampaignManifest(
        campaign_id="campaign-2026-10-05",
        name="repository migration",
        created_at=datetime.now(UTC).isoformat(),
        tasks=[
            RepoTaskState(
                repo_name=name,
                repo_path=str(path),
                status="pending",
                contract_path=f"contracts/{name}.json",
            )
            for name, path in repos.items()
        ],
    )


def _statuses(manifest: CampaignManifest) -> dict[str, str]:
    """Where every repository of the campaign has got to."""
    return {task.repo_name: task.status for task in manifest.tasks}


def _campaign(
    repos: dict[str, Path],
    checkpoint: Path,
    behaviour: dict[str, str],
    pool: WorktreePool,
) -> tuple[CampaignOrchestrator, VerifierStub]:
    """An orchestrator for a campaign starting from scratch, with a verifier that behaves per repository."""
    verifier = VerifierStub(behaviour)
    orchestrator = CampaignOrchestrator(CampaignManager(_manifest(repos), checkpoint), pool, verifier)
    return orchestrator, verifier


def test_an_interrupted_campaign_resumes_with_only_the_work_still_pending(
    git_repos: dict[str, Path],
    tmp_path: Path,
) -> None:
    checkpoint = tmp_path / "campaign.json"
    pool = WorktreePool(base_dir=tmp_path / "pool")
    interrupted, first_verifier = _campaign(git_repos, checkpoint, dict(OK), pool)

    first_step = interrupted.step()
    assert first_step is not None
    task, receipt = first_step
    assert task.repo_name == "alpha"
    assert task.status == "verified"
    assert receipt is not None
    first_digest = receipt.compute_digest()
    assert first_verifier.seen == ["alpha"]

    resumed_verifier = VerifierStub(dict(OK))
    resumed = CampaignOrchestrator(
        CampaignManager(CampaignManifest.load_checkpoint(checkpoint), checkpoint),
        pool,
        resumed_verifier,
    )

    processed = resumed.run_all()

    assert [done.repo_name for done in processed] == ["beta", "gamma"]
    assert resumed_verifier.seen == ["beta", "gamma"]
    reloaded = CampaignManifest.load_checkpoint(checkpoint)
    assert _statuses(reloaded) == {"alpha": "verified", "beta": "verified", "gamma": "verified"}
    assert next(task for task in reloaded.tasks if task.repo_name == "alpha").receipt_digest == first_digest
    assert resumed.manager.is_completed()
    assert not any((tmp_path / "pool").iterdir())


def test_a_verification_that_raises_rolls_the_worktree_back_and_records_why(
    git_repos: dict[str, Path],
    tmp_path: Path,
    git: Callable[..., str],
) -> None:
    checkpoint = tmp_path / "campaign.json"
    pool = WorktreePool(base_dir=tmp_path / "pool")
    behaviour = {"alpha": "ok", "beta": "raise", "gamma": "ok"}
    orchestrator, verifier = _campaign(git_repos, checkpoint, behaviour, pool)

    processed = orchestrator.run_all()

    assert [done.repo_name for done in processed] == ["alpha", "beta", "gamma"]
    reloaded = CampaignManifest.load_checkpoint(checkpoint)
    assert _statuses(reloaded) == {"alpha": "verified", "beta": "rolled_back", "gamma": "verified"}
    rolled_back = next(task for task in reloaded.tasks if task.repo_name == "beta")
    assert "verification crashed" in (rolled_back.error_message or "")
    assert rolled_back.receipt_digest is None
    assert not verifier.paths["beta"].exists()
    assert git(git_repos["beta"], "status", "--porcelain") == ""
    assert orchestrator.manager.is_completed()


def test_a_receipt_that_reports_failure_marks_the_task_failed(
    git_repos: dict[str, Path],
    tmp_path: Path,
    git: Callable[..., str],
) -> None:
    checkpoint = tmp_path / "campaign.json"
    pool = WorktreePool(base_dir=tmp_path / "pool")
    behaviour = {"alpha": "ok", "beta": "fail", "gamma": "ok"}
    orchestrator, verifier = _campaign(git_repos, checkpoint, behaviour, pool)

    processed = orchestrator.run_all()

    assert [done.repo_name for done in processed] == ["alpha", "beta", "gamma"]
    reloaded = CampaignManifest.load_checkpoint(checkpoint)
    assert _statuses(reloaded) == {"alpha": "verified", "beta": "failed", "gamma": "verified"}
    failed = next(task for task in reloaded.tasks if task.repo_name == "beta")
    assert failed.error_message is not None
    assert failed.receipt_digest is None
    assert not verifier.paths["beta"].exists()
    assert git(git_repos["beta"], "status", "--porcelain") == ""
    assert orchestrator.manager.is_completed()


def test_only_pending_work_is_handed_out_and_every_transition_is_checkpointed(tmp_path: Path) -> None:
    checkpoint = tmp_path / "campaign.json"
    repos = {name: tmp_path / name for name in REPOS}
    manager = CampaignManager(_manifest(repos), checkpoint)

    first = manager.get_next_pending()
    assert first is not None
    assert first.repo_name == "alpha"

    manager.update_status("alpha", "verified", receipt_digest="digest-of-alpha")
    assert _statuses(CampaignManifest.load_checkpoint(checkpoint))["alpha"] == "verified"
    second = manager.get_next_pending()
    assert second is not None
    assert second.repo_name == "beta"

    manager.update_status("beta", "failed", error_message="tests did not pass")
    manager.update_status("gamma", "rolled_back", error_message="worktree missing")

    assert manager.get_next_pending() is None
    assert manager.is_completed()
    assert _statuses(CampaignManifest.load_checkpoint(checkpoint)) == {
        "alpha": "verified",
        "beta": "failed",
        "gamma": "rolled_back",
    }
    with pytest.raises(KeyError, match="missing"):
        manager.update_status("missing", "verified")


def test_the_checkpoint_is_written_in_one_piece_and_reads_back(tmp_path: Path) -> None:
    manifest = _manifest({name: tmp_path / name for name in REPOS})
    checkpoint = tmp_path / "campaign.json"

    manifest.save_checkpoint(checkpoint)

    assert {entry.name for entry in tmp_path.iterdir()} == {"campaign.json"}
    assert CampaignManifest.load_checkpoint(checkpoint).to_dict() == manifest.to_dict()
    data = json.loads(checkpoint.read_text(encoding="utf-8"))
    assert data["campaign_id"] == manifest.campaign_id
    assert [task["repo_name"] for task in data["tasks"]] == list(REPOS)

    manifest.tasks[0].status = "verified"
    manifest.save_checkpoint(checkpoint)
    assert CampaignManifest.load_checkpoint(checkpoint).tasks[0].status == "verified"
    assert {entry.name for entry in tmp_path.iterdir()} == {"campaign.json"}

    nested = tmp_path / "deep" / "nested" / "campaign.json"
    manifest.save_checkpoint(nested)
    assert CampaignManifest.load_checkpoint(nested).campaign_id == manifest.campaign_id
