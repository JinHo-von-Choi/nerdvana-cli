"""Path validation utilities."""

from __future__ import annotations

import contextlib
import errno
import os


def validate_path(path: str, cwd: str) -> str | None:
    """Validate that resolved path stays within cwd. Returns error message or None."""
    if os.path.isabs(path):
        return f"Absolute paths are not allowed: {path}"
    resolved = os.path.realpath(os.path.join(cwd, path))
    cwd_resolved = os.path.realpath(cwd)
    if not resolved.startswith(cwd_resolved + os.sep) and resolved != cwd_resolved:
        return f"Path traversal blocked: {path} resolves outside working directory"
    return None


# ---------------------------------------------------------------------------
# Safe symlink-aware open helpers
# ---------------------------------------------------------------------------
#
# The validate_path() check above is a single-shot realpath comparison and is
# vulnerable to TOCTOU races: between the check and the actual open() call an
# attacker (or a compromised earlier tool call) can swap a path component for
# a symlink that points outside cwd. The helpers below close that window by
# walking the path one component at a time using openat() semantics with
# O_NOFOLLOW at every step, which is the same approach used by systemd's
# chase-symlinks and several hardened file utilities.
#
# Note on portability: O_NOFOLLOW and O_DIRECTORY are POSIX features. On
# Windows neither is defined, so we fall back to getattr(..., 0) and the
# hardening is effectively Linux/macOS only there. A Windows-safe path is
# tracked separately and is out of scope for the current security patch.

_O_NOFOLLOW: int = getattr(os, "O_NOFOLLOW", 0)
_O_DIRECTORY: int = getattr(os, "O_DIRECTORY", 0)


def _split_segments(relative_path: str) -> list[str]:
    """Split a relative path into clean segments.

    Empty segments and "." are dropped. ".." is rejected outright because
    even a single .. would let the walk escape cwd, and validate_path is the
    caller's responsibility for the broader containment check anyway.
    """
    if os.path.isabs(relative_path):
        raise PermissionError(f"Absolute paths are not allowed: {relative_path}")
    segments: list[str] = []
    for raw in relative_path.replace("\\", "/").split("/"):
        if raw in ("", "."):
            continue
        if raw == "..":
            raise PermissionError(
                f"Parent traversal not allowed in safe open: {relative_path}"
            )
        segments.append(raw)
    return segments


def safe_open_fd(
    relative_path: str,
    cwd:           str,
    flags:         int,
    mode:          int = 0o644,
) -> int:
    """Open a file under cwd with symlink-following disabled at every segment.

    Walks ``relative_path`` component by component starting from a directory
    file descriptor opened on ``cwd``. Each intermediate component is opened
    with ``O_NOFOLLOW | O_DIRECTORY`` so that any symlinked directory raises
    ``OSError(errno.ELOOP)``. The final component is opened with the
    caller-supplied ``flags`` plus ``O_NOFOLLOW``, so a symlinked target file
    is also rejected.

    Args:
        relative_path: Path relative to ``cwd``. Absolute paths and ``..``
            segments are rejected with ``PermissionError``.
        cwd: Sandbox root directory; must already exist.
        flags: ``os.open`` flags for the final component (e.g. ``O_RDONLY``,
            ``O_WRONLY | O_CREAT | O_TRUNC``). ``O_NOFOLLOW`` is added
            automatically; do not include ``O_DIRECTORY``.
        mode: Permission bits used when ``O_CREAT`` is in ``flags``.

    Returns:
        A file descriptor for the opened file. The caller is responsible for
        closing it (typically by wrapping in :func:`os.fdopen`).

    Raises:
        PermissionError: ``relative_path`` is absolute or contains ``..``.
        OSError: Any segment is a symlink (``errno.ELOOP``), a parent does
            not exist (``errno.ENOENT``), or the open fails for any other
            reason. The ``ELOOP`` case is the security-critical one and
            indicates a TOCTOU attempt.

    Platform note:
        On Windows ``O_NOFOLLOW`` and ``O_DIRECTORY`` do not exist and the
        helper falls back to behaviour equivalent to ``os.open``. The
        hardening is therefore Linux/macOS only on the Windows fallback.
    """
    segments = _split_segments(relative_path)
    if not segments:
        raise PermissionError(f"Empty relative path is not openable: {relative_path}")

    cwd_fd = os.open(cwd, os.O_RDONLY | _O_DIRECTORY)
    walked_fds: list[int] = []
    try:
        parent_fd = cwd_fd
        for component in segments[:-1]:
            next_fd = os.open(
                component,
                os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW,
                dir_fd=parent_fd,
            )
            walked_fds.append(next_fd)
            parent_fd = next_fd

        final_fd = os.open(
            segments[-1],
            flags | _O_NOFOLLOW,
            mode,
            dir_fd=parent_fd,
        )
        return final_fd
    finally:
        for fd in walked_fds:
            with contextlib.suppress(OSError):
                os.close(fd)
        with contextlib.suppress(OSError):
            os.close(cwd_fd)


def safe_makedirs(
    relative_path: str,
    cwd:           str,
    mode:          int = 0o755,
) -> None:
    """Create ``relative_path`` under ``cwd`` with symlink-aware mkdir.

    Equivalent to ``os.makedirs(parent, exist_ok=True)`` but every component
    is verified to be a real directory (not a symlink) by re-opening it with
    ``O_NOFOLLOW | O_DIRECTORY``. If a component already exists as a symlink
    the call raises ``OSError(errno.ELOOP)`` instead of silently following.

    Empty paths and ``"."`` are no-ops, mirroring the behaviour callers
    expect when ``os.path.dirname`` returns ``""`` for a top-level file.

    Args:
        relative_path: Directory path relative to ``cwd``.
        cwd: Sandbox root directory; must already exist.
        mode: Permission bits for newly created directories.

    Raises:
        PermissionError: ``relative_path`` is absolute or contains ``..``.
        OSError: A component already exists as a symlink (``errno.ELOOP``),
            or directory creation fails for any other reason.

    Platform note:
        Same Windows caveat as :func:`safe_open_fd` — ``O_NOFOLLOW`` is not
        available there, so the symlink rejection only fires on POSIX.
    """
    segments = _split_segments(relative_path)
    if not segments:
        return

    cwd_fd = os.open(cwd, os.O_RDONLY | _O_DIRECTORY)
    walked_fds: list[int] = []
    try:
        parent_fd = cwd_fd
        for component in segments:
            try:
                next_fd = os.open(
                    component,
                    os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW,
                    dir_fd=parent_fd,
                )
            except OSError as exc:
                if exc.errno == errno.ENOENT:
                    os.mkdir(component, mode, dir_fd=parent_fd)
                    next_fd = os.open(
                        component,
                        os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW,
                        dir_fd=parent_fd,
                    )
                else:
                    raise
            walked_fds.append(next_fd)
            parent_fd = next_fd
    finally:
        for fd in walked_fds:
            with contextlib.suppress(OSError):
                os.close(fd)
        with contextlib.suppress(OSError):
            os.close(cwd_fd)
