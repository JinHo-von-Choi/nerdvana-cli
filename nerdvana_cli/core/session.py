"""Session persistence — JSONL transcript storage."""

from __future__ import annotations

import json
import os
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nerdvana_cli.core import paths
from nerdvana_cli.types import Message, Role

# Transcript tool results are capped at this many characters when recorded.
_TRANSCRIPT_RESULT_CAP = 500
_SESSION_ID_RE         = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def resume_session_id() -> str | None:
    """Session id requested through ``NERDVANA_RESUME``, or None.

    Values that could escape the sessions directory are ignored.
    """
    raw = os.environ.get("NERDVANA_RESUME", "").strip()
    if not raw or raw in {".", ".."} or not _SESSION_ID_RE.match(raw):
        return None
    return raw


def messages_from_transcript(entries: list[dict[str, Any]]) -> list[Message]:
    """Rebuild conversation messages from transcript *entries*.

    Tool calls and results are kept only in complete pairs: a tool_use whose
    result was never recorded (the process stopped mid-batch) is removed from
    its assistant message, and a result whose tool_use is missing is dropped.
    Providers reject either half on its own.
    """
    answered = {str(e.get("tool_use_id", "")) for e in entries if e.get("type") == "tool_result"}
    known:    set[str]      = set()
    messages: list[Message] = []
    for entry in entries:
        kind = entry.get("type")
        if kind == "user":
            messages.append(Message(role=Role.USER, content=str(entry.get("content", ""))))
        elif kind == "assistant":
            uses = [tu for tu in entry.get("tool_uses") or [] if str(tu.get("id", "")) in answered]
            known.update(str(tu.get("id", "")) for tu in uses)
            content = str(entry.get("content", ""))
            if content or uses:
                messages.append(Message(role=Role.ASSISTANT, content=content, tool_uses=uses))
        elif kind == "tool_result":
            tool_use_id = str(entry.get("tool_use_id", ""))
            if tool_use_id not in known:
                continue
            content = str(entry.get("content", ""))
            if len(content) >= _TRANSCRIPT_RESULT_CAP:
                content += "\n[truncated in the session transcript; re-run the tool if the full output matters]"
            messages.append(Message(
                role        = Role.TOOL,
                content     = content,
                tool_use_id = tool_use_id,
                is_error    = bool(entry.get("is_error", False)),
            ))
    return messages


class SessionStorage:
    """Append-only JSONL session transcript."""

    def __init__(self, session_id: str | None = None, storage_dir: str = "", persist: bool = True):
        self.session_id = session_id or str(uuid.uuid4())[:8]
        self.persist    = persist
        base_dir = storage_dir or str(paths.user_sessions_dir())
        if persist:
            os.makedirs(base_dir, exist_ok=True)
        self.file_path = os.path.join(base_dir, f"{self.session_id}.jsonl")

    def record(self, event_type: str, data: dict[str, Any]) -> None:
        if not self.persist:
            return
        entry = {
            "ts": datetime.now(UTC).isoformat(),
            "type": event_type,
            **data,
        }
        with open(self.file_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def record_user_message(self, content: str) -> None:
        self.record("user", {"content": content})

    def record_assistant_message(self, content: str, tool_uses: list[dict[str, Any]] | None = None) -> None:
        self.record("assistant", {"content": content, "tool_uses": tool_uses or []})

    def record_tool_result(self, tool_name: str, tool_use_id: str, content: str, is_error: bool = False) -> None:
        self.record(
            "tool_result",
            {
                "tool_name":  tool_name,
                "tool_use_id": tool_use_id,
                "content":    content[:_TRANSCRIPT_RESULT_CAP],
                "is_error":   is_error,
            },
        )

    def record_compaction(
        self,
        tokens_before: int,
        messages_before: int,
        strategy: str,  # "ai" | "naive"
    ) -> None:
        self.record(
            "compaction",
            {
                "tokens_before":    tokens_before,
                "messages_before":  messages_before,
                "strategy":         strategy,
            },
        )

    def record_system(self, subtype: str, data: dict[str, Any]) -> None:
        self.record("system", {"subtype": subtype, **data})

    def replay(self) -> list[dict[str, Any]]:
        if not os.path.exists(self.file_path):
            return []
        messages = []
        with open(self.file_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    messages.append(json.loads(line))
        return messages

    def load_messages(self) -> list[Message]:
        """Conversation messages recorded in this session's transcript."""
        return messages_from_transcript(self.replay())

    @classmethod
    def get_last_session(cls, storage_dir: str = "") -> str | None:
        """Return the session ID of the most recently modified session.

        When called without storage_dir, checks both the canonical new location
        (~/.nerdvana/sessions/) and the legacy install-dir location
        (~/.nerdvana-cli/sessions/) so that upgrading users do not lose history.
        """
        bases: list[str] = []
        if storage_dir:
            bases.append(storage_dir)
        else:
            bases.append(str(paths.user_sessions_dir()))
            bases.append(str(paths.legacy_sessions_dir()))

        best: tuple[float, str] | None = None
        for base in bases:
            if not os.path.exists(base):
                continue
            for fname in os.listdir(base):
                if not fname.endswith(".jsonl"):
                    continue
                mtime = os.path.getmtime(os.path.join(base, fname))
                if best is None or mtime > best[0]:
                    best = (mtime, fname.replace(".jsonl", ""))
        return best[1] if best else None

    def save_summary(self, session_id: str, summary: str) -> None:
        """Save session summary for fast restoration."""
        path = self._summary_path(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(summary, encoding="utf-8")

    def get_summary(self, session_id: str) -> str:
        """Get session summary if available."""
        path = self._summary_path(session_id)
        if path.exists():
            return path.read_text(encoding="utf-8")
        return ""

    def _summary_path(self, session_id: str) -> Path:
        base = Path(self.file_path).parent
        return base / f"{session_id}.summary.md"

    def restore_with_summary(
        self,
        max_messages: int = 5,
    ) -> str:
        """Restore session context using summary or recent messages.

        Args:
            max_messages: Max recent messages to include if no summary

        Returns:
            Context string for session restoration
        """
        summary = self.get_summary(self.session_id)
        if summary:
            return f"[Previous Session Summary]: {summary}"

        messages = list(self.replay())
        if not messages:
            return ""

        recent = messages[-max_messages:]
        context_parts = []
        for msg in recent:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")[:200]
            context_parts.append(f"[{role}]: {content}")

        return "\n".join(context_parts)
