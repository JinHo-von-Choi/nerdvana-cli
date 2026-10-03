"""Confine shell commands to a write scope with Linux Landlock.

Author: 최진호
Date:   2026-10-03

Landlock is a kernel feature (5.13 and later) that lets an unprivileged process
restrict itself and everything it starts. No helper program, container or root
access is needed, and it works where user namespaces are off, as on hosts that
restrict them with AppArmor.

What is confined: creating, writing, truncating, renaming and deleting files outside
the writable paths. With a kernel that has Landlock ABI 4 (Linux 6.7), TCP connect and
bind can be refused too. What is not: reading files, running programs, UDP and local
sockets. A confined command can still read a secret and print it.

The module is also a launcher. ``python sandbox.py [--write PATH]... [--no-network] --
COMMAND`` restricts itself and then replaces itself with ``/bin/sh -c COMMAND``. It
imports nothing from the package, so a command starts without loading the rest of
the application.
"""

from __future__ import annotations

import ctypes
import functools
import os
import platform
import struct
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

MODES = ("off", "auto", "require")

# Exit status of the launcher when the restriction could not be set up. The command
# never runs in that case.
SETUP_FAILED = 125

_SYS_CREATE_RULESET = 444
_SYS_ADD_RULE       = 445
_SYS_RESTRICT_SELF  = 446
_CREATE_VERSION     = 1 << 0
_RULE_PATH_BENEATH  = 1
_PR_SET_NO_NEW_PRIVS = 38

_FS_WRITE_FILE  = 1 << 1
_FS_REMOVE_DIR  = 1 << 4
_FS_REMOVE_FILE = 1 << 5
_FS_MAKE_CHAR   = 1 << 6
_FS_MAKE_DIR    = 1 << 7
_FS_MAKE_REG    = 1 << 8
_FS_MAKE_SOCK   = 1 << 9
_FS_MAKE_FIFO   = 1 << 10
_FS_MAKE_BLOCK  = 1 << 11
_FS_MAKE_SYM    = 1 << 12
_FS_REFER       = 1 << 13
_FS_TRUNCATE    = 1 << 14

_NET_BIND_TCP    = 1 << 0
_NET_CONNECT_TCP = 1 << 1

_FS_BASE = (
    _FS_WRITE_FILE | _FS_REMOVE_DIR | _FS_REMOVE_FILE | _FS_MAKE_CHAR | _FS_MAKE_DIR
    | _FS_MAKE_REG | _FS_MAKE_SOCK | _FS_MAKE_FIFO | _FS_MAKE_BLOCK | _FS_MAKE_SYM
)
# The only rights that can be granted on something that is not a directory.
_FS_FILE_RIGHTS = _FS_WRITE_FILE | _FS_TRUNCATE


class SandboxError(RuntimeError):
    """The restriction could not be set up."""


@dataclass(frozen=True)
class SandboxPolicy:
    """The ``sandbox`` configuration section as the Bash tool sees it."""

    mode:        str                = "off"
    network:     bool               = True
    write_paths: tuple[str, ...]    = ()
    project:     bool               = True   # the project directory is writable
    scratch:     bool               = True   # /tmp and the system temporary directory are writable


@dataclass(frozen=True)
class Launch:
    """How to start one command: the argument list (None = the plain shell) and any notice."""

    argv:    list[str] | None = None
    notice:  str              = ""
    refused: bool             = False


def _libc() -> ctypes.CDLL:
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    return libc


def _syscall(number: int, *args: object) -> int:
    result = _libc().syscall(number, *args)
    if result < 0:
        error = ctypes.get_errno()
        raise SandboxError(f"{os.strerror(error)} (errno {error})")
    return int(result)


@functools.lru_cache(maxsize=1)
def landlock_abi() -> int:
    """The Landlock version the running kernel offers, 0 when there is none."""
    if not sys.platform.startswith("linux") or platform.machine() not in ("x86_64", "aarch64"):
        return 0
    try:
        return _syscall(_SYS_CREATE_RULESET, None, 0, _CREATE_VERSION)
    except (SandboxError, OSError, AttributeError):
        return 0


def _fs_mask(abi: int) -> int:
    mask = _FS_BASE
    if abi >= 2:
        mask |= _FS_REFER
    if abi >= 3:
        mask |= _FS_TRUNCATE
    return mask


def confine(write_paths: list[str], allow_network: bool) -> None:
    """Restrict this process and its future children; irreversible.

    Raises SandboxError, leaving the process unrestricted, when Landlock is missing
    or cannot express the request.
    """
    abi = landlock_abi()
    if abi < 1:
        raise SandboxError("Landlock is not available on this system")
    if not allow_network and abi < 4:
        raise SandboxError(f"this kernel offers Landlock ABI {abi}; refusing network access needs ABI 4 (Linux 6.7)")

    fs_mask  = _fs_mask(abi)
    net_mask = 0 if allow_network else _NET_BIND_TCP | _NET_CONNECT_TCP
    attr     = struct.pack("<QQ", fs_mask, net_mask) if abi >= 4 else struct.pack("<Q", fs_mask)
    buffer   = ctypes.create_string_buffer(attr, len(attr))
    ruleset  = _syscall(_SYS_CREATE_RULESET, buffer, len(attr), 0)
    try:
        for path in write_paths:
            try:
                handle = os.open(path, os.O_PATH | os.O_CLOEXEC)
            except OSError:
                continue
            try:
                rights = fs_mask if os.path.isdir(path) else fs_mask & _FS_FILE_RIGHTS
                rule   = ctypes.create_string_buffer(struct.pack("<Qi", rights, handle), 12)
                _syscall(_SYS_ADD_RULE, ruleset, _RULE_PATH_BENEATH, rule, 0)
            finally:
                os.close(handle)
        _syscall_prctl()
        _syscall(_SYS_RESTRICT_SELF, ruleset, 0)
    finally:
        os.close(ruleset)


def _syscall_prctl() -> None:
    if _libc().prctl(_PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
        raise SandboxError("could not set no_new_privs")


def writable_paths(policy: SandboxPolicy, cwd: str) -> list[str]:
    """Where a confined command may write: the project, scratch space and the configured extras.

    ``/dev`` is always included (commands write to ``/dev/null``); ``project`` and ``scratch`` can switch the
    project directory and the temporary directories off for a role that must not change anything.
    """
    candidates = [
        *([cwd] if policy.project else []),
        *([tempfile.gettempdir(), "/tmp", "/var/tmp"] if policy.scratch else []),  # noqa: S108
        "/dev",
        *policy.write_paths,
    ]
    seen: list[str] = []
    for candidate in candidates:
        resolved = os.path.realpath(os.path.expanduser(candidate))
        if os.path.exists(resolved) and resolved not in seen:
            seen.append(resolved)
    return seen


def wrap_command(command: str, write_paths: list[str], allow_network: bool) -> list[str]:
    """The argument list that runs *command* through the launcher below."""
    argv = [sys.executable or "python3", "-I", str(Path(__file__).resolve())]
    for path in write_paths:
        argv += ["--write", path]
    if not allow_network:
        argv.append("--no-network")
    return [*argv, "--", command]


def plan_launch(policy: SandboxPolicy | None, command: str, cwd: str) -> Launch:
    """Decide how to start *command* under *policy*."""
    if policy is None or policy.mode == "off":
        return Launch()
    abi = landlock_abi()
    if abi < 1:
        reason = "Landlock is not available on this system"
    elif not policy.network and abi < 4:
        reason = f"this kernel offers Landlock ABI {abi}; refusing network access needs ABI 4 (Linux 6.7)"
    else:
        return Launch(argv=wrap_command(command, writable_paths(policy, cwd), policy.network))
    if policy.mode == "require":
        return Launch(notice=f"sandbox required but unavailable: {reason}", refused=True)
    return Launch(notice=f"sandbox unavailable ({reason}); the command runs without confinement")


def main(argv: list[str]) -> int:
    """Launcher entry: confine, then become ``/bin/sh -c COMMAND``."""
    write_paths: list[str] = []
    network = True
    rest    = list(argv)
    command = ""
    while rest:
        item = rest.pop(0)
        if item == "--write" and rest:
            write_paths.append(rest.pop(0))
        elif item == "--no-network":
            network = False
        elif item == "--" and rest:
            command = rest.pop(0)
            break
        else:
            print(f"sandbox: unexpected argument {item!r}", file=sys.stderr)
            return SETUP_FAILED
    if not command:
        print("sandbox: no command given", file=sys.stderr)
        return SETUP_FAILED
    try:
        confine(write_paths, network)
    except SandboxError as exc:
        print(f"sandbox: {exc}", file=sys.stderr)
        return SETUP_FAILED
    os.execv("/bin/sh", ["/bin/sh", "-c", command])
    return SETUP_FAILED  # unreachable: execv replaces the process


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
