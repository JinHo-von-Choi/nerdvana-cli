"""Session persistence — JSONL transcript storage."""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nerdvana_cli.core import paths


class SessionStorage:
    """Append-only JSONL session transcript."""

    def __init__(self, session_id: str | None = None, storage_dir: str = ""):
        self.session_id = session_id or str(uuid.uuid4())[:8]
        base_dir = storage_dir or str(paths.user_sessions_dir())
        os.makedirs(base_dir, exist_ok=True)
        self.file_path = os.path.join(base_dir, f"{self.session_id}.jsonl")

    def record(self, event_type: str, data: dict[str, Any]) -> None:
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
                "content":    content[:500],
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
