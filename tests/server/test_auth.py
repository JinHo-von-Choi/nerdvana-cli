"""Tests for AuthManager — Phase G1.

7 test cases:
  - HTTP bearer: valid key, invalid key, missing hash prefix
  - stdio: socket found + correct mode/uid, socket not found, wrong permissions
  - mTLS: known CN, unknown CN (read-only fallback)
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from nerdvana_cli.server.auth import AuthManager


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def keys_file(tmp_path) -> Path:
    raw_key   = "test-api-key-12345"
    key_hash  = AuthManager.hash_key(raw_key)
    content   = (
        "keys:\n"
        f"  - key_hash: \"{key_hash}\"\n"
        "    client_name: \"claude-code-prod\"\n"
        "    roles:\n"
        "      - read-only\n"
    )
    path = tmp_path / "mcp_keys.yml"
    path.write_text(content, encoding="utf-8")
    return path


@pytest.fixture
def auth(keys_file) -> AuthManager:
    mgr = AuthManager(keys_path=keys_file)
    mgr.load()
    return mgr


# ---------------------------------------------------------------------------
# HTTP bearer tests
# ---------------------------------------------------------------------------


def test_bearer_valid_key(auth):
    """Correct raw key must authenticate successfully."""
    result = auth.authenticate_bearer("test-api-key-12345")
    assert result.authenticated
    assert result.client_identity == "claude-code-prod"
    assert "read-only" in result.roles


def test_bearer_invalid_key(auth):
    """Wrong key must be rejected."""
    result = auth.authenticate_bearer("wrong-key")
    assert not result.authenticated
    assert result.reason == "invalid_key"


def test_bearer_key_with_sha256_prefix(tmp_path):
    """Keys stored with 'sha256:' prefix in YAML must match correctly."""
    raw_key   = "another-secret"
    key_hash  = AuthManager.hash_key(raw_key)          # already has sha256: prefix
    content   = (
        "keys:\n"
        f"  - key_hash: \"{key_hash}\"\n"
        "    client_name: \"cursor-dev\"\n"
        "    roles: [read-only, edit]\n"
    )
    path = tmp_path / "keys.yml"
    path.write_text(content, encoding="utf-8")
    mgr = AuthManager(keys_path=path)
    mgr.load()
    r = mgr.authenticate_bearer("another-secret")
    assert r.authenticated
    assert r.client_identity == "cursor-dev"


# ---------------------------------------------------------------------------
# stdio / Unix socket tests
# ---------------------------------------------------------------------------


def test_stdio_socket_not_found(tmp_path, auth):
    """Missing socket file must return authenticated=False."""
    result = auth.authenticate_stdio(socket_path=tmp_path / "nonexistent.sock")
    assert not result.authenticated
    assert result.reason == "socket_not_found"


def test_stdio_socket_correct_permissions(tmp_path, auth):
    """Socket with 0600 permissions and matching UID must authenticate."""
    sock = tmp_path / "test.sock"
    sock.touch()
    os.chmod(sock, 0o600)
    # Ownership is current user by default after touch — UID matches.
    result = auth.authenticate_stdio(socket_path=sock)
    assert result.authenticated
    assert str(os.getuid()) in result.client_identity


def test_stdio_socket_wrong_permissions(tmp_path, auth):
    """Socket with 0644 permissions must be rejected."""
    sock = tmp_path / "open.sock"
    sock.touch()
    os.chmod(sock, 0o644)
    result = auth.authenticate_stdio(socket_path=sock)
    assert not result.authenticated
    assert result.reason == "insecure_socket_permissions"


# ---------------------------------------------------------------------------
# mTLS tests
# ---------------------------------------------------------------------------


def test_mtls_known_cn(auth):
    """CN matching a key entry client_name must return stored roles."""
    result = auth.authenticate_mtls("claude-code-prod")
    assert result.authenticated
    assert result.client_identity == "claude-code-prod"
    assert "read-only" in result.roles


def test_mtls_unknown_cn_is_rejected(auth):
    """Unknown CN must be rejected (fail-closed) — C-3 mTLS security fix.

    Previous behaviour was fail-open (authenticated=True + roles=['read-only']),
    which allowed CN-spoofing clients unrestricted read access.  After the
    security fix unknown CNs must receive authenticated=False + reason='unknown_cn'.
    """
    result = auth.authenticate_mtls("new-client-xyz")
    assert not result.authenticated
    assert result.reason == "unknown_cn"
    assert result.client_identity == "new-client-xyz"
