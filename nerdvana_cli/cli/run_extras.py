"""What ``nerdvana run`` adds around the agent loop: stream-json input, a JSON schema for the answer, a diagnostics folder."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

# How many times the model is asked again when its final answer does not fit the schema.
SCHEMA_RETRIES = 2

_FENCE = re.compile(r"^```(?:json)?\s*\n(?P<body>.*)\n```\s*$", re.S)


def stream_input_messages(lines: Iterable[str]) -> Iterator[str]:
    """The user messages of an NDJSON stream, one per line.

    A line is ``{"type": "user", "message": {"content": ...}}`` (the Claude Code shape, content a
    string or a list of text blocks), ``{"text": ...}`` or ``{"prompt": ...}``. Blank lines are
    skipped; a line that is none of these raises ValueError naming its number.
    """
    source = iter(lines)
    number = 0
    while True:
        try:
            line = next(source)
        except (StopIteration, EOFError):  # some stdin wrappers end with EOFError instead of StopIteration
            return
        number += 1
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"stdin line {number} is not JSON: {exc.msg}") from exc
        text = _message_text(data)
        if text is None:
            raise ValueError(f"stdin line {number} is not a user message (expected type user, text or prompt)")
        yield text


def _message_text(data: Any) -> str | None:
    if not isinstance(data, dict):
        return None
    for key in ("text", "prompt"):
        if isinstance(data.get(key), str):
            return str(data[key])
    message = data.get("message")
    if data.get("type") == "user" and isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = [block.get("text", "") for block in content if isinstance(block, dict) and block.get("type") == "text"]
            return "\n".join(part for part in parts if part)
    return None


def load_schema(spec: str) -> dict[str, Any]:
    """The JSON schema *spec* names: a path to a file, or the schema itself inline. Raises ValueError."""
    from jsonschema import Draft202012Validator  # type: ignore[import-untyped,unused-ignore]
    from jsonschema.exceptions import SchemaError  # type: ignore[import-untyped,unused-ignore]

    text = Path(spec).read_text(encoding="utf-8") if not spec.lstrip().startswith("{") else spec
    try:
        schema = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"--json-schema is not valid JSON: {exc.msg}") from exc
    if not isinstance(schema, dict):
        raise ValueError("--json-schema must be a JSON object")
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise ValueError(f"--json-schema is not a valid schema: {exc.message}") from exc
    return schema


def structured_answer(text: str, schema: dict[str, Any]) -> tuple[Any, str]:
    """(value, "") when *text* is JSON that fits *schema*, (None, why not) otherwise; a ```json fence is allowed."""
    from jsonschema import Draft202012Validator  # type: ignore[import-untyped,unused-ignore]

    body  = text.strip()
    fence = _FENCE.match(body)
    if fence:
        body = fence["body"]
    try:
        value = json.loads(body)
    except json.JSONDecodeError as exc:
        return None, f"the answer is not JSON ({exc.msg} at character {exc.pos})"
    errors = sorted(Draft202012Validator(schema).iter_errors(value), key=lambda e: list(e.path))
    if errors:
        where = "/".join(str(part) for part in errors[0].path) or "(root)"
        return None, f"{where}: {errors[0].message}"
    return value, ""


def schema_reminder(schema: dict[str, Any], problem: str) -> str:
    """The follow-up prompt when the answer did not fit the schema."""
    return (
        f"Your final answer must be only a JSON value that matches this JSON schema, with no other text:\n"
        f"{json.dumps(schema, ensure_ascii=False)}\n"
        f"The last answer did not: {problem}. Reply with the corrected JSON only."
    )


class Diagnostics:
    """A folder that keeps what a run did: every event, the settings it used, and its result."""

    def __init__(self, directory: str) -> None:
        self.path = Path(directory)
        if self.path.exists() and any(self.path.iterdir()):
            raise ValueError(f"--diagnostics-dir {directory} is not empty; give a new or empty directory")
        self.path.mkdir(parents=True, exist_ok=True)
        self._events = (self.path / "events.jsonl").open("a", encoding="utf-8")

    def event(self, event: dict[str, Any]) -> None:
        """Append one event, whatever the output format shows."""
        self._events.write(json.dumps(event, ensure_ascii=False) + "\n")
        self._events.flush()

    def settings(self, text: str) -> None:
        """The settings the run used, credentials masked."""
        (self.path / "settings.yml").write_text(text, encoding="utf-8")

    def result(self, result: dict[str, Any]) -> None:
        """The result object; closes the event log."""
        (self.path / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self._events.close()
