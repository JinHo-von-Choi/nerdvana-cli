"""Monotonic security guards: the rules no hook and no permission setting can turn off.

A BEFORE_TOOL hook may veto a call and a permission rule may allow one, but
neither may switch an owner invariant off. The guards here run in the tool
waterfall after both, and they can only deny: a guard with nothing to say
abstains, and the first deny refuses the call.

Task 1.2 of the Harness2/DSH enhancement plan.

The invariants enforced here:

- the harness's own invariant files (``.iron-laws.yml``, ``.env``) are never
  written by the model, by an edit tool or by a shell command, because changing
  them changes the rules the run is judged by;
- the test files a task contract protects are never written by the model, so a
  run cannot weaken the tests that judge it;
- a command that hands a secret file to a network sink is refused.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nerdvana_cli.core.safety.edit_tools import edited_path, is_applied_edit

# A guard decides about one tool call: the tool name and the arguments it was called with.
GuardFn = Callable[[str, dict[str, Any]], "GuardVerdict"]


@dataclass(frozen=True)
class GuardVerdict:
    """Monotonic guard outcome: only deny or abstain (never force-allow)."""

    denied: bool
    reason: str = ""
    guard_name: str = ""

    @classmethod
    def abstain(cls) -> GuardVerdict:
        """The guard has nothing to say about this call."""
        return cls(denied=False)

    @classmethod
    def deny(cls, reason: str, guard_name: str = "") -> GuardVerdict:
        """The call breaks an invariant and must not run."""
        return cls(denied=True, reason=reason, guard_name=guard_name)


class MonotonicGuardChain:
    """Evaluates registered monotonic guards in deterministic registration order.

    The chain can only deny. There is no verdict that allows a call, so a guard
    that abstains leaves the decision to the rest of the waterfall.

    Time complexity: O(G) where G is the number of registered guards.
    """

    def __init__(self) -> None:
        self._guards: list[tuple[str, GuardFn]] = []

    def register(self, name: str, fn: GuardFn) -> None:
        """Add a guard to the chain; the name says which rule it enforces."""
        if not name.strip():
            raise ValueError("Guard name must not be empty")
        self._guards.append((name, fn))

    @property
    def guard_names(self) -> tuple[str, ...]:
        """The names of the registered guards, in the order they run."""
        return tuple(name for name, _ in self._guards)

    def evaluate(self, tool_name: str, tool_args: dict[str, Any]) -> GuardVerdict:
        """The first deny among the guards, or an abstention when they all abstain."""
        for name, fn in self._guards:
            verdict = fn(tool_name, tool_args)
            if verdict.denied:
                return GuardVerdict.deny(verdict.reason or f"Denied by guard {name}", guard_name=name)
        return GuardVerdict.abstain()


# The files that hold the harness's own invariants. Writing one of them changes the rules the
# run is judged by, so no model may write one: the user edits them, or the rules are not rules.
INVARIANT_FILE_NAMES: frozenset[str] = frozenset({".iron-laws.yml", ".env"})


def _is_invariant(name: str, names: frozenset[str]) -> bool:
    """True when *name* is one of the invariant files (``.env.local`` counts for ``.env``)."""
    if name in names:
        return True
    return any(name.startswith(f"{invariant}.") for invariant in names if invariant.startswith("."))


def _invariant_reason(name: str) -> str:
    """Why an invariant file may not be written."""
    return (
        f"Modifying {name} is forbidden by invariant c2: it holds the rules this run is judged by. "
        "Ask the user to change it."
    )


# A shell redirection and the target it writes to.
_REDIRECT = re.compile(r">>?\s*([^\s;|&<>]+)")

# A command that changes a file, and where the file it changes sits in the command line.
_WRITE_COMMAND = re.compile(r"\b(tee|cp|mv|rm|install|ln|sed\s+-i|touch|chmod|chown|truncate)\b")

# The commands whose file is the last word of the line; the others name it wherever they like.
_LAST_WORD_COMMANDS: frozenset[str] = frozenset({"cp", "mv", "install", "ln", "sed -i"})


def _written_invariant(command: str, names: frozenset[str]) -> str | None:
    """The invariant file the command writes, or None when it writes none."""
    for match in _REDIRECT.finditer(command):
        name = Path(match.group(1)).name
        if _is_invariant(name, names):
            return name
    writer = _WRITE_COMMAND.search(command)
    if writer is None:
        return None
    words      = [token.strip("'\"") for token in command.split() if token.strip("'\"")]
    candidates = [words[-1]] if writer.group(1) in _LAST_WORD_COMMANDS else words
    for candidate in candidates:
        name = Path(candidate).name
        if _is_invariant(name, names):
            return name
    return None


def invariant_file_guard(names: frozenset[str] = INVARIANT_FILE_NAMES) -> GuardFn:
    """A guard that refuses a write of an invariant file, by an edit tool or by a shell command."""

    def guard(tool_name: str, tool_args: dict[str, Any]) -> GuardVerdict:
        if tool_name == "Bash":
            command = tool_args.get("command")
            if not isinstance(command, str):
                return GuardVerdict.abstain()
            written = _written_invariant(command, names)
            return GuardVerdict.deny(_invariant_reason(written)) if written else GuardVerdict.abstain()
        if not is_applied_edit(tool_name, tool_args):
            return GuardVerdict.abstain()
        target = edited_path(tool_args)
        if not target:
            return GuardVerdict.abstain()
        name = Path(target).name
        if not _is_invariant(name, names):
            return GuardVerdict.abstain()
        return GuardVerdict.deny(_invariant_reason(name))

    return guard


def protected_test_guard(root: Path, paths: frozenset[str]) -> GuardFn:
    """A guard that refuses an edit of a test file a task contract protects."""

    def guard(tool_name: str, tool_args: dict[str, Any]) -> GuardVerdict:
        if not paths or not is_applied_edit(tool_name, tool_args):
            return GuardVerdict.abstain()
        target = edited_path(tool_args)
        if not target:
            return GuardVerdict.abstain()
        resolved = (root / target).resolve()
        if not any(resolved == (root / protected).resolve() for protected in paths):
            return GuardVerdict.abstain()
        return GuardVerdict.deny(
            f"{target} is protected by the task contract: the tests that judge this run may not be changed. "
            "Add a new test instead of changing the existing one."
        )

    return guard


# A file name that holds a secret: the environment file, a credential file, a private key.
_SECRET_BASENAME = re.compile(
    r"(?:^|[\s'\"=/@:,-])((?:\.env(?:\.[\w-]+)*|\.netrc|\.npmrc|\.pypirc"
    r"|credentials|id_rsa|id_ed25519|[\w-]*\.pem))(?=$|[\s'\"=/@:,-])",
    re.IGNORECASE,
)

# A file name that only looks like a secret: a template the project ships, not a secret it holds.
_EXAMPLE_SUFFIXES: tuple[str, ...] = (".example", ".sample", ".template", ".dist")

# A command that carries something to another machine.
_SINK_COMMAND = re.compile(
    r"\b(curl|wget|nc|ncat|netcat|socat|ssh|scp|sftp|rsync|telnet|mail|mailx|sendmail)\b",
    re.IGNORECASE,
)


def _secret_in_command(command: str) -> str | None:
    """The secret file the command reads, or None when it reads none."""
    for match in _SECRET_BASENAME.finditer(command):
        name = match.group(1)
        if name.lower().endswith(_EXAMPLE_SUFFIXES):
            continue
        return name
    return None


def secret_sink_guard() -> GuardFn:
    """A guard that refuses a command that hands a secret file to a network sink."""

    def guard(tool_name: str, tool_args: dict[str, Any]) -> GuardVerdict:
        if tool_name != "Bash":
            return GuardVerdict.abstain()
        command = tool_args.get("command")
        if not isinstance(command, str) or not command.strip():
            return GuardVerdict.abstain()
        if not _SINK_COMMAND.search(command):
            return GuardVerdict.abstain()
        secret = _secret_in_command(command)
        if secret is None:
            return GuardVerdict.abstain()
        return GuardVerdict.deny(
            f"The command reads {secret} and hands it to a network sink, which would leak the secret. "
            "Transfer the file yourself, or ask the user to do it."
        )

    return guard


def default_guard_chain(root: Path, protected_tests: frozenset[str] = frozenset()) -> MonotonicGuardChain:
    """The guards a tool call runs through: the invariants, the protected tests and the secret sinks."""
    chain = MonotonicGuardChain()
    chain.register("invariant_files", invariant_file_guard())
    chain.register("protected_tests", protected_test_guard(root, protected_tests))
    chain.register("secret_sink", secret_sink_guard())
    return chain
