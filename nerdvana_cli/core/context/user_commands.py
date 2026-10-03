"""User-defined slash commands: markdown prompt templates.

Author: 최진호
Date:   2026-10-03

``<name>.md`` files under ``~/.nerdvana/commands`` and ``<project>/.nerdvana/commands``
become ``/<name>``. A file in a subdirectory is named ``dir:name``. Typing the command
sends the file's text as the prompt, with ``$ARGUMENTS`` replaced by everything typed
after the command and ``$1`` to ``$9`` by the individual words. The project directory
wins over the global one. Built-in commands and skills are looked up first, so a file
can never shadow them.
"""

from __future__ import annotations

import logging
import re
import shlex
from dataclasses import dataclass
from pathlib import Path

import yaml  # type: ignore[import-untyped,unused-ignore]

from nerdvana_cli.core.config import paths

logger = logging.getLogger(__name__)

MAX_COMMAND_BYTES = 64 * 1024

_NAME_RE       = re.compile(r"^[a-z0-9][a-z0-9:_-]*$")
_POSITIONAL_RE = re.compile(r"\$([1-9])(?!\d)")


@dataclass(frozen=True)
class UserCommand:
    """One command template."""

    name:        str
    description: str
    body:        str
    source:      Path

    @property
    def trigger(self) -> str:
        """What the user types."""
        return f"/{self.name}"

    def render(self, args: str) -> str:
        """The prompt to send for *args*.

        When the template has no placeholder at all, the arguments are appended on a
        new paragraph so they are never silently dropped.
        """
        try:
            words = shlex.split(args)
        except ValueError:
            words = args.split()
        uses_placeholder = "$ARGUMENTS" in self.body or bool(_POSITIONAL_RE.search(self.body))
        text = self.body.replace("$ARGUMENTS", args)
        text = _POSITIONAL_RE.sub(lambda m: words[int(m.group(1)) - 1] if len(words) >= int(m.group(1)) else "", text)
        if args and not uses_placeholder:
            text = f"{text}\n\n{args}"
        return text


def _read(path: Path, root: Path) -> UserCommand | None:
    """Parse one command file, or None when it must be skipped."""
    try:
        resolved = path.resolve()
        if not resolved.is_relative_to(root.resolve()):
            logger.warning("command %s resolves outside %s; skipped", path, root)
            return None
        if resolved.stat().st_size > MAX_COMMAND_BYTES:
            logger.warning("command %s is larger than %d bytes; skipped", path, MAX_COMMAND_BYTES)
            return None
        text = resolved.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning("command %s could not be read: %s", path, exc)
        return None

    relative = path.relative_to(root).with_suffix("")
    name     = ":".join(relative.parts).lower()
    if not _NAME_RE.match(name):
        logger.warning("command file %s has a name that is not usable; skipped", path)
        return None

    description = ""
    body        = text
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            try:
                meta = yaml.safe_load(parts[1]) or {}
            except yaml.YAMLError:
                meta = {}
            description = str(meta.get("description", "")) if isinstance(meta, dict) else ""
            body        = parts[2]
    body = body.strip()
    return UserCommand(name=name, description=description, body=body, source=path) if body else None


class UserCommandLoader:
    """Finds command files for a project."""

    def __init__(self, project_dir: str = ".", global_dir: str | None = None) -> None:
        self._project = Path(project_dir) / ".nerdvana" / "commands"
        self._global  = Path(global_dir) if global_dir else paths.user_data_home() / "commands"

    def list_commands(self) -> list[UserCommand]:
        """Every command, project definitions replacing global ones of the same name."""
        found: dict[str, UserCommand] = {}
        for root in (self._global, self._project):
            if not root.is_dir():
                continue
            for path in sorted(root.rglob("*.md")):
                command = _read(path, root)
                if command is not None:
                    found[command.name] = command
        return sorted(found.values(), key=lambda c: c.name)

    def get(self, trigger: str) -> UserCommand | None:
        """The command typed as *trigger* (with or without the slash), case-insensitively."""
        wanted = trigger.lstrip("/").lower()
        return next((c for c in self.list_commands() if c.name == wanted), None)
