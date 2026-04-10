"""Session persistence — JSONL transcript storage."""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime
from typing import Any


class SessionStorage:
    """Append-only JSONL session transcript."""

    def __init__(self, session_id: str | None = None, storage_dir: str = ""):
        self.session_id = session_id or str(uuid.uuid4())[:8]
        base_dir = storage_dir or os.path.join(os.path.expanduser("~/.nerdvana-cli/sessions"))
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
                "tool_name": tool_name,
                "tool_use_id": tool_use_id,
                "content": content[:500],
                "is_error": is_error,
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
                "tokens_before": tokens_before,
                "messages_before": messages_before,
                "strategy": strategy,
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
        base_dir = storage_dir or os.path.join(os.path.expanduser("~/.nerdvana-cli/sessions"))
        if not os.path.exists(base_dir):
            return None
        files = sorted(
            [f for f in os.listdir(base_dir) if f.endswith(".jsonl")],
            key=lambda f: os.path.getmtime(os.path.join(base_dir, f)),
            reverse=True,
        )
        return files[0].replace(".jsonl", "") if files else None
