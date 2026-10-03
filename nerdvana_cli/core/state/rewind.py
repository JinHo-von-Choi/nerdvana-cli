"""Going back before earlier prompts: their messages leave the history and their edits are undone.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nerdvana_cli.core.loop.agent_loop import AgentLoop


class Rewinder:
    """Marks where each prompt of *loop* began, and rewinds the session to such a mark."""

    def __init__(self, loop: AgentLoop) -> None:
        self._loop = loop
        self.marks: list[tuple[int, int]] = []   # per prompt: (messages before it, checkpoints before it)

    def mark(self) -> None:
        """Remember where the prompt about to run begins."""
        self.marks.append((len(self._loop.state.messages), self._checkpoint_depth()))

    def _checkpoint_depth(self) -> int:
        """How many file checkpoints this session has (0 where there is no git repository)."""
        with contextlib.suppress(Exception):
            return sum(1 for c in self._loop._checkpoint_manager.list_checkpoints() if c.kind == "snapshot")
        return 0

    def rewind(self, prompts: int = 1) -> str:
        """Go back before the last *prompts* prompts: drop their messages and undo the edits they made.

        Files come back through the checkpoints taken before each edit, so only edits made by the edit
        tools are undone (not what a shell command changed). A compaction since a prompt ends how far back
        this can go. The session transcript records the rewind so a resumed session agrees.
        """
        loop = self._loop
        if not self.marks:
            return "Nothing to rewind: no earlier prompt is available (compaction or a reset ends how far back it goes)."
        prompts = min(max(prompts, 1), len(self.marks))
        index, depth = self.marks[-prompts]
        del self.marks[-prompts:]
        undone = 0
        while self._checkpoint_depth() > depth and "Undone" in loop._checkpoint_manager.undo():
            undone += 1
        removed = len(loop.state.messages) - index
        del loop.state.messages[index:]
        loop.session.record_system("rewind", {"prompts": prompts})
        loop._context_budget.reset()
        return f"Rewound {prompts} prompt(s): {removed} message(s) removed, {undone} edit(s) undone."
