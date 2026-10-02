"""Shell command hooks declared in ``hooks.yml``.

Author: 최진호
Date:   2026-10-03

``~/.nerdvana/hooks.yml`` (always loaded) and ``<project>/.nerdvana/hooks.yml``
(loaded only with ``hooks.allow_project_hooks`` and an approved digest, exactly like
project Python hooks) hold a list of command hooks::

    hooks:
      - event: before_tool      # before_tool, after_tool, session_start or session_end
        match: "Bash"           # tool name glob, before_tool and after_tool only (default "*")
        command: "scripts/check-command.sh"
        timeout: 5              # seconds, 1 to 30 (default 5)

The command runs through the shell in the project directory and receives one JSON
object on standard input (``event``, ``tool_name``, ``tool_input``, ``cwd`` and, for
``after_tool``, a shortened ``tool_result``). The environment variables
``NERDVANA_HOOK_EVENT`` and ``NERDVANA_TOOL_NAME`` carry the same names.

Exit code 0 lets things go on. Exit code 2 blocks a ``before_tool`` call and reports
the command's output to the model, or hands the output of an ``after_tool`` command
to the model as a message. Any other exit code, a timeout, or a command that cannot
start only logs a warning and never stops the agent. The handler runs synchronously,
so the agent waits for the command; keep hooks fast.
"""

from __future__ import annotations

import fnmatch
import hashlib
import hmac
import json
import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped,unused-ignore]

from nerdvana_cli.core import paths
from nerdvana_cli.core.hooks import HookContext, HookEngine, HookEvent, HookResult
from nerdvana_cli.core.user_hooks import load_trust_record, project_hooks_enabled

logger = logging.getLogger(__name__)

HOOKS_FILENAME  = "hooks.yml"
DEFAULT_TIMEOUT = 5.0
MAX_TIMEOUT     = 30.0
BLOCK_EXIT_CODE = 2
_MAX_OUTPUT     = 2_000
_MAX_RESULT     = 4_000

_EVENTS = {
    "before_tool":   HookEvent.BEFORE_TOOL,
    "after_tool":    HookEvent.AFTER_TOOL,
    "session_start": HookEvent.SESSION_START,
    "session_end":   HookEvent.SESSION_END,
}


@dataclass(frozen=True)
class CommandHook:
    """One declared command hook."""

    event:   HookEvent
    command: str
    match:   str   = "*"
    timeout: float = DEFAULT_TIMEOUT
    source:  str   = ""


def parse_hooks(text: str, source: str = "") -> list[CommandHook]:
    """Parse a hooks file; entries that are malformed are skipped with a warning."""
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        logger.warning("%s is not valid YAML: %s", source or HOOKS_FILENAME, exc)
        return []
    entries = data.get("hooks") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        return []

    hooks: list[CommandHook] = []
    for index, entry in enumerate(entries):
        label = f"{source or HOOKS_FILENAME} entry {index + 1}"
        if not isinstance(entry, dict):
            logger.warning("%s is not a mapping; skipped", label)
            continue
        event   = _EVENTS.get(str(entry.get("event", "")))
        command = entry.get("command")
        if event is None or not isinstance(command, str) or not command.strip():
            logger.warning("%s needs a known event and a command; skipped", label)
            continue
        try:
            timeout = float(entry.get("timeout", DEFAULT_TIMEOUT))
        except (TypeError, ValueError):
            timeout = DEFAULT_TIMEOUT
        hooks.append(CommandHook(
            event   = event,
            command = command,
            match   = str(entry.get("match", "*")) or "*",
            timeout = min(max(timeout, 1.0), MAX_TIMEOUT),
            source  = source,
        ))
    return hooks


def _payload(hook: CommandHook, context: HookContext, cwd: str) -> dict[str, Any]:
    payload: dict[str, Any] = {"event": hook.event.value, "cwd": cwd}
    if hook.event in (HookEvent.BEFORE_TOOL, HookEvent.AFTER_TOOL):
        payload["tool_name"]  = context.tool_name
        payload["tool_input"] = context.tool_input
    if hook.event == HookEvent.AFTER_TOOL:
        result = getattr(context.tool_result, "content", context.tool_result)
        payload["tool_result"] = str(result)[:_MAX_RESULT] if result is not None else ""
    return payload


def _run(hook: CommandHook, context: HookContext, cwd: str) -> subprocess.CompletedProcess[str] | None:
    """Run the command; None when it could not run or timed out."""
    env = {**os.environ, "NERDVANA_HOOK_EVENT": hook.event.value, "NERDVANA_TOOL_NAME": context.tool_name}
    try:
        return subprocess.run(  # noqa: S602 - the command is the user's own declaration
            hook.command,
            shell          = True,
            input          = json.dumps(_payload(hook, context, cwd), default=str),
            capture_output = True,
            text           = True,
            timeout        = hook.timeout,
            cwd            = cwd,
            env            = env,
            check          = False,
        )
    except subprocess.TimeoutExpired:
        logger.warning("command hook %r timed out after %gs; ignored", hook.command, hook.timeout)
    except (OSError, ValueError) as exc:
        logger.warning("command hook %r could not run: %s", hook.command, exc)
    return None


def _output(done: subprocess.CompletedProcess[str]) -> str:
    return (done.stdout.strip() or done.stderr.strip())[:_MAX_OUTPUT]


def make_handler(hook: CommandHook, cwd: str) -> Any:
    """A HookEngine handler that runs *hook* for matching events."""

    def handler(context: HookContext) -> HookResult:
        if hook.event in (HookEvent.BEFORE_TOOL, HookEvent.AFTER_TOOL) and not fnmatch.fnmatchcase(context.tool_name, hook.match):
            return HookResult()
        done = _run(hook, context, cwd)
        if done is None:
            return HookResult()
        if done.returncode == 0:
            return HookResult()
        if done.returncode != BLOCK_EXIT_CODE:
            logger.warning("command hook %r exited with %d; ignored", hook.command, done.returncode)
            return HookResult()
        text = _output(done) or f"command hook {hook.command!r} refused this"
        if hook.event == HookEvent.BEFORE_TOOL:
            return HookResult(allow=False, message=text)
        if hook.event == HookEvent.AFTER_TOOL:
            return HookResult(inject_messages=[{"role": "user", "content": f"[hook {hook.command}] {text}"}])
        return HookResult()

    return handler


def _file_allowed(path: Path, is_project: bool, settings: Any) -> bool:
    """Global files always load; a project file needs the opt-in and a matching approved digest."""
    if not is_project:
        return True
    if not project_hooks_enabled(settings):
        logger.warning("project hooks file %s skipped: project-local hooks are off", path)
        return False
    try:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        logger.warning("project hooks file %s skipped: unreadable (%s)", path, exc)
        return False
    recorded = load_trust_record().get(str(path.resolve()))
    if recorded is None or not hmac.compare_digest(recorded, digest):
        logger.warning("project hooks file %s skipped: not approved, or changed since approval (nerdvana hook trust)", path)
        return False
    return True


def load_command_hooks(engine: HookEngine, settings: Any) -> list[CommandHook]:
    """Register every allowed command hook on *engine*; returns the ones registered."""
    cwd = str(getattr(settings, "cwd", "") or ".")
    registered: list[CommandHook] = []
    for path, is_project in (
        (paths.user_data_home() / HOOKS_FILENAME, False),
        (Path(cwd) / ".nerdvana" / HOOKS_FILENAME, True),
    ):
        if not path.is_file() or not _file_allowed(path, is_project, settings):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            logger.warning("hooks file %s could not be read: %s", path, exc)
            continue
        for hook in parse_hooks(text, str(path)):
            engine.register(hook.event, make_handler(hook, cwd))
            registered.append(hook)
    return registered
