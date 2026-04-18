"""Constant-time hash comparison tests — C-4.

Verifies that:
  - AuthManager.authenticate_bearer uses hmac.compare_digest (not ==)
  - The comparison is constant-time (structural check via source inspection)
  - Correct keys are accepted, wrong keys are rejected

작성자: 최진호
작성일: 2026-04-18
"""

from __future__ import annotations

import hashlib
import hmac
import inspect
import textwrap

import pytest

from nerdvana_cli.server.auth import AuthManager


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def auth_with_key(tmp_path):
    """AuthManager loaded with one test key."""
    raw_key = "super-secret-key-abc123"
    digest  = "sha256:" + hashlib.sha256(raw_key.encode()).hexdigest()
    keys_file = tmp_path / "mcp_keys.yml"
    keys_file.write_text(
        f"keys:\n"
        f"  - key_hash: \"{digest}\"\n"
        f"    client_name: test-harness\n"
        f"    roles: [read-only]\n",
        encoding="utf-8",
    )
    mgr = AuthManager(keys_path=keys_file)
    mgr.load()
    return mgr, raw_key


# ---------------------------------------------------------------------------
# Source-level structural check
# ---------------------------------------------------------------------------


def test_authenticate_bearer_uses_compare_digest() -> None:
    """authenticate_bearer source must use hmac.compare_digest, not bare ==."""
    src = inspect.getsource(AuthManager.authenticate_bearer)
    assert "hmac.compare_digest" in src, (
        "authenticate_bearer must use hmac.compare_digest for constant-time comparison. "
        "Found source:\n" + textwrap.indent(src, "  ")
    )
    # Also assert the old == pattern is absent on the key_hash comparison line
    assert "entry.key_hash ==" not in src, (
        "Timing-unsafe '==' comparison found; replace with hmac.compare_digest"
    )


# ---------------------------------------------------------------------------
# Functional correctness
# ---------------------------------------------------------------------------


def test_valid_key_accepted(auth_with_key) -> None:
    """A correct key must be authenticated."""
    mgr, raw_key = auth_with_key
    result = mgr.authenticate_bearer(raw_key)
    assert result.authenticated is True
    assert result.client_identity == "test-harness"
    assert "read-only" in result.roles


def test_wrong_key_rejected(auth_with_key) -> None:
    """A wrong key must not be authenticated."""
    mgr, _ = auth_with_key
    result = mgr.authenticate_bearer("totally-wrong-key")
    assert result.authenticated is False
    assert result.reason == "invalid_key"
    assert result.client_identity == ""


def test_empty_key_rejected(auth_with_key) -> None:
    """Empty string key must be rejected."""
    mgr, _ = auth_with_key
    result = mgr.authenticate_bearer("")
    assert result.authenticated is False


def test_similar_prefix_key_rejected(auth_with_key) -> None:
    """A key that shares a prefix with the real key must still be rejected."""
    mgr, raw_key = auth_with_key
    partial = raw_key[:5]   # only first 5 chars
    result  = mgr.authenticate_bearer(partial)
    assert result.authenticated is False


def test_hmac_compare_digest_is_constant_time() -> None:
    """hmac.compare_digest must be available and work correctly."""
    a = "abc" * 20
    b = "abc" * 20
    c = "xyz" * 20
    assert hmac.compare_digest(a, b) is True
    assert hmac.compare_digest(a, c) is False


def test_hash_key_helper_produces_sha256_prefix() -> None:
    """AuthManager.hash_key must return a sha256:-prefixed string."""
    result = AuthManager.hash_key("some-raw-key")
    assert result.startswith("sha256:")
    hex_part = result[len("sha256:"):]
    assert len(hex_part) == 64  # sha256 hex is 64 chars
    assert all(c in "0123456789abcdef" for c in hex_part)
