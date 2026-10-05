"""Campaign: several repositories, one task each, one worktree at a time.

작성자: 최진호
날짜: 2026-10-05

The package keeps a manifest of the repositories a campaign owes work to,
checkpoints every transition so an interrupted campaign can resume, and works
on each repository in a worktree of its own so the repositories themselves are
never written to.
"""
