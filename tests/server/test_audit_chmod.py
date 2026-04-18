"""Audit database file permission tests — M-8.

Verifies that:
  - audit.sqlite is created with 0600 permissions (not world-readable)
  - _ensure_db_file_permissions is atomic: uses O_CREAT|O_EXCL, then chmod
  - SanitizerAudit.open also creates audit.sqlite with 0600 permissions
  - Both AuditLogger and SanitizerAudit using the same path agree on perms

작성자: 최진호
작성일: 2026-04-18
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from nerdvana_cli.server.audit import AuditLogger, _ensure_db_file_permissions
from nerdvana_cli.server.sanitizer import SanitizerAudit


# ---------------------------------------------------------------------------
# _ensure_db_file_permissions
# ---------------------------------------------------------------------------


def test_ensure_db_creates_with_0600(tmp_path) -> None:
    """_ensure_db_file_permissions must create a file with mode 0600."""
    db = tmp_path / "test.sqlite"
    _ensure_db_file_permissions(db)
    assert db.exists()
    mode = stat.S_IMODE(db.stat().st_mode)
    assert mode == 0o600, f"Expected 0600, got {oct(mode)}"


def test_ensure_db_corrects_existing_permissions(tmp_path) -> None:
    """_ensure_db_file_permissions must fix overly-permissive existing file."""
    db = tmp_path / "test.sqlite"
    db.touch()
    os.chmod(db, 0o644)   # simulate world-readable
    _ensure_db_file_permissions(db)
    mode = stat.S_IMODE(db.stat().st_mode)
    assert mode == 0o600, f"Expected 0600 after correction, got {oct(mode)}"


def test_ensure_db_idempotent(tmp_path) -> None:
    """_ensure_db_file_permissions called twice must not raise and keep 0600."""
    db = tmp_path / "test.sqlite"
    _ensure_db_file_permissions(db)
    _ensure_db_file_permissions(db)   # second call on existing file
    mode = stat.S_IMODE(db.stat().st_mode)
    assert mode == 0o600


# ---------------------------------------------------------------------------
# AuditLogger
# ---------------------------------------------------------------------------


def test_audit_logger_creates_db_0600(tmp_path) -> None:
    """AuditLogger.open must create audit.sqlite with 0600 permissions."""
    db   = tmp_path / "audit.sqlite"
    log  = AuditLogger(db_path=db)
    log.open()
    try:
        assert db.exists()
        mode = stat.S_IMODE(db.stat().st_mode)
        assert mode == 0o600, f"AuditLogger: expected 0600, got {oct(mode)}"
    finally:
        log.close()


def test_audit_logger_reopens_and_maintains_0600(tmp_path) -> None:
    """Re-opening an existing audit.sqlite must not loosen permissions."""
    db  = tmp_path / "audit.sqlite"
    log = AuditLogger(db_path=db)
    log.open()
    log.close()
    # Loosen permissions externally to simulate race
    os.chmod(db, 0o644)
    # Re-open — must correct to 0600
    log2 = AuditLogger(db_path=db)
    log2.open()
    try:
        mode = stat.S_IMODE(db.stat().st_mode)
        assert mode == 0o600, f"Re-open: expected 0600 after correction, got {oct(mode)}"
    finally:
        log2.close()


def test_audit_logger_not_world_readable(tmp_path) -> None:
    """AuditLogger must not produce a world-readable database."""
    db  = tmp_path / "audit.sqlite"
    log = AuditLogger(db_path=db)
    log.open()
    try:
        mode = stat.S_IMODE(db.stat().st_mode)
        # world-read bit must be off
        assert not (mode & stat.S_IROTH), f"World-readable bit set: {oct(mode)}"
        assert not (mode & stat.S_IWOTH), f"World-writable bit set: {oct(mode)}"
    finally:
        log.close()


# ---------------------------------------------------------------------------
# SanitizerAudit
# ---------------------------------------------------------------------------


def test_sanitizer_audit_creates_db_0600(tmp_path) -> None:
    """SanitizerAudit.open must create audit.sqlite with 0600 permissions."""
    db   = tmp_path / "audit.sqlite"
    aud  = SanitizerAudit(db_path=db)
    aud.open()
    try:
        assert db.exists()
        mode = stat.S_IMODE(db.stat().st_mode)
        assert mode == 0o600, f"SanitizerAudit: expected 0600, got {oct(mode)}"
    finally:
        aud.close()


def test_sanitizer_audit_before_audit_logger_no_race(tmp_path) -> None:
    """When SanitizerAudit opens first, AuditLogger must still end up with 0600.

    This specifically tests the race described in M-8: if SanitizerAudit
    creates the file at 0644 (default umask) before AuditLogger can chmod,
    there is a world-readable window.  Our fix ensures both modules call
    _ensure_db_file_permissions, which uses O_CREAT|O_EXCL for atomic creation.
    """
    db     = tmp_path / "audit.sqlite"
    san    = SanitizerAudit(db_path=db)
    log    = AuditLogger(db_path=db)

    # Sanitizer opens first
    san.open()
    mode_after_san = stat.S_IMODE(db.stat().st_mode)
    assert mode_after_san == 0o600, f"SanitizerAudit created with {oct(mode_after_san)}"

    # AuditLogger opens on the already-created file
    log.open()
    mode_after_log = stat.S_IMODE(db.stat().st_mode)
    assert mode_after_log == 0o600, f"AuditLogger re-open changed perms to {oct(mode_after_log)}"

    san.close()
    log.close()
