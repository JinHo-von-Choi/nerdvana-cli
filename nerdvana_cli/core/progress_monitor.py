"""Noticing a run that is busy but getting nowhere.

Author: 최진호
Date:   2026-10-03

The identical-call guard in ``core/concurrency.py`` catches a model that repeats one call. It does not
catch the other common way to stall: different calls that never change anything. Two patterns are
watched here, from the calls the tool executor has run:

- the same file has had several edits in a row that all failed (a stale or wrong anchor that the model
  keeps retrying), and
- many turns in a row used only read-only tools, with no edit and no command run.

``ProgressMonitor`` is a plain state machine: it is fed one record per executed call and one signal per
turn boundary, and it answers with a short note to show the model, at most once per stall. A stall ends
as soon as the model makes progress (an applied edit for the first pattern, any non-read turn for the
second), after which the same pattern can be noted again.
"""

from __future__ import annotations

from typing import Any

from nerdvana_cli.core.safety.edit_tools import edited_path, is_applied_edit

READ  = "read"    # a read-only tool: reading, searching, listing
EDIT  = "edit"    # a tool that changes a file
OTHER = "other"   # anything else (a command, a sub-agent, an external tool): counts as making progress

DEFAULT_FAILED_EDITS = 3
DEFAULT_READ_TURNS   = 12

_EDITS_NOTE = (
    "[Note: the last {count} edits to {path} all failed. Read the file again and check the exact text "
    "before the next edit, or take a different approach.]"
)
_READS_NOTE = (
    "[Note: the last {count} turns only read or searched, with no edit and no command run. If you have "
    "what you need, make the change or give your answer; if not, say what is still missing.]"
)


class ProgressMonitor:
    """Counts failed edits per file and turns spent only reading; ``0`` turns a limit off."""

    def __init__(self, failed_edit_limit: int = DEFAULT_FAILED_EDITS, read_turn_limit: int = DEFAULT_READ_TURNS) -> None:
        self._edit_limit   = failed_edit_limit
        self._read_limit   = read_turn_limit
        self._edit_path    = ""
        self._edit_fails   = 0
        self._edit_noted   = False
        self._read_turns   = 0
        self._read_noted   = False
        self._turn_calls   = 0
        self._turn_reads   = 0

    @classmethod
    def from_settings(cls, settings: Any) -> ProgressMonitor:
        """A monitor with the limits of ``session.no_progress_*`` (the defaults for a settings object without them)."""
        session = getattr(settings, "session", None)
        return cls(
            getattr(session, "no_progress_failed_edits", DEFAULT_FAILED_EDITS),
            getattr(session, "no_progress_read_turns", DEFAULT_READ_TURNS),
        )

    def observe_call(self, tool_name: str, tool_input: dict[str, Any], read_only: bool, is_error: bool) -> None:
        """Record one executed call by what the tool is: an applied edit, a read-only tool, or anything else."""
        if is_applied_edit(tool_name, tool_input):
            self.observe(EDIT, edited_path(tool_input), is_error)
        else:
            self.observe(READ if read_only else OTHER, "", is_error)

    def observe(self, kind: str, path: str, is_error: bool) -> None:
        """Record one executed call of the current turn; *path* is the file an edit is about, if any."""
        self._turn_calls += 1
        if kind == READ:
            self._turn_reads += 1
        elif kind == EDIT:
            self._observe_edit(path, is_error)

    def _observe_edit(self, path: str, is_error: bool) -> None:
        if not is_error:
            self._edit_path  = ""
            self._edit_fails = 0
            self._edit_noted = False
            return
        if path == self._edit_path:
            self._edit_fails += 1
            return
        self._edit_path  = path
        self._edit_fails = 1
        self._edit_noted = False

    def end_turn(self) -> str:
        """Close the current turn; the note to show the model when it has just become stalled, else an empty string."""
        calls, reads     = self._turn_calls, self._turn_reads
        self._turn_calls = 0
        self._turn_reads = 0
        if calls and reads == calls:
            self._read_turns += 1
        elif calls:
            self._read_turns = 0
            self._read_noted = False
        if 0 < self._edit_limit <= self._edit_fails and not self._edit_noted:
            self._edit_noted = True
            return _EDITS_NOTE.format(count=self._edit_fails, path=self._edit_path or "a file")
        if 0 < self._read_limit <= self._read_turns and not self._read_noted:
            self._read_noted = True
            return _READS_NOTE.format(count=self._read_turns)
        return ""
