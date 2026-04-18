"""Authentication layer for NerdVana MCP server — Phase G1.

Three transport modes:
  - HTTP:  Authorization: Bearer <api_key>  →  sha256 hash match vs mcp_keys.yml
  - stdio: Unix socket at /tmp/nerdvana-mcp-<uid>.sock — UID equality check
  - mTLS:  peer certificate CN used as client_identity

YAML schema for ~/.nerdvana/mcp_keys.yml:
  keys:
    - key_hash: "sha256:..."
      client_name: "claude-code-prod"
      roles: [read-only]

작성자: 최진호
작성일: 2026-04-18
"""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path

import yaml  # type: ignore[import-untyped]

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class KeyEntry:
    """A single API-key record loaded from mcp_keys.yml."""

    key_hash:    str
    client_name: str
    roles:       list[str] = field(default_factory=list)


@dataclass
class AuthResult:
    """Result of an authentication check."""

    authenticated:   bool
    client_identity: str
    roles:           list[str] = field(default_factory=list)
    reason:          str       = ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sha256_hex(value: str) -> str:
    """Return lowercase hex SHA-256 digest of *value*."""
    return hashlib.sha256(value.encode()).hexdigest()


def _parse_hash_field(raw: str) -> str:
    """Strip optional 'sha256:' prefix and return the bare hex digest."""
    raw = raw.strip()
    if raw.startswith("sha256:"):
        return raw[len("sha256:"):]
    return raw


# ---------------------------------------------------------------------------
# AuthManager
# ---------------------------------------------------------------------------


class AuthManager:
    """Handles API-key, Unix-socket UID, and mTLS authentication.

    Parameters
    ----------
    keys_path:
        Path to ``mcp_keys.yml``.  Defaults to ``~/.nerdvana/mcp_keys.yml``.
    """

    def __init__(self, keys_path: Path | None = None) -> None:
        self._keys_path: Path = keys_path or Path.home() / ".nerdvana" / "mcp_keys.yml"
        self._entries:   list[KeyEntry] = []
        self._loaded:    bool           = False

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def load(self) -> None:
        """(Re)load key entries from the YAML file.

        Missing file is not an error — the server simply has no keys and will
        deny all HTTP bearer requests.
        """
        self._entries = []
        self._loaded  = True

        if not self._keys_path.exists():
            return

        with self._keys_path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}

        for raw in data.get("keys", []) or []:
            try:
                entry = KeyEntry(
                    key_hash    = _parse_hash_field(str(raw["key_hash"])),
                    client_name = str(raw["client_name"]),
                    roles       = list(raw.get("roles", []) or []),
                )
                self._entries.append(entry)
            except (KeyError, TypeError):
                pass  # skip malformed entries

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.load()

    # ------------------------------------------------------------------
    # HTTP bearer authentication
    # ------------------------------------------------------------------

    def authenticate_bearer(self, raw_key: str) -> AuthResult:
        """Validate an HTTP Bearer token against stored hashes.

        Parameters
        ----------
        raw_key:
            The plaintext API key extracted from the Authorization header.

        Returns
        -------
        AuthResult with ``authenticated=True`` and the matching client name /
        roles on success, or ``authenticated=False`` otherwise.
        """
        self._ensure_loaded()
        digest = _sha256_hex(raw_key)
        for entry in self._entries:
            if entry.key_hash == digest:
                return AuthResult(
                    authenticated   = True,
                    client_identity = entry.client_name,
                    roles           = list(entry.roles),
                )
        return AuthResult(authenticated=False, client_identity="", reason="invalid_key")

    # ------------------------------------------------------------------
    # stdio / Unix socket UID authentication
    # ------------------------------------------------------------------

    def authenticate_stdio(self, socket_path: Path | None = None) -> AuthResult:
        """Verify that the Unix socket is owned by the current user.

        Parameters
        ----------
        socket_path:
            Override the default socket path for testing.  When *None* the
            canonical path ``/tmp/nerdvana-mcp-<uid>.sock`` is used.

        Returns
        -------
        AuthResult — authenticated when socket file mode is 0600 and the
        owning UID matches ``os.getuid()``.
        """
        uid  = os.getuid()
        path = socket_path or Path(
            os.environ.get("XDG_RUNTIME_DIR", "/tmp")
        ) / f"nerdvana-mcp-{uid}.sock"

        if not path.exists():
            return AuthResult(
                authenticated   = False,
                client_identity = "",
                reason          = "socket_not_found",
            )

        st   = path.stat()
        mode = stat.S_IMODE(st.st_mode)

        if st.st_uid != uid:
            return AuthResult(
                authenticated   = False,
                client_identity = "",
                reason          = "uid_mismatch",
            )

        if mode != 0o600:
            return AuthResult(
                authenticated   = False,
                client_identity = "",
                reason          = "insecure_socket_permissions",
            )

        return AuthResult(
            authenticated   = True,
            client_identity = f"local-uid-{uid}",
            roles           = ["read-only"],
        )

    # ------------------------------------------------------------------
    # mTLS peer-certificate authentication
    # ------------------------------------------------------------------

    def authenticate_mtls(self, peer_cn: str) -> AuthResult:
        """Authenticate using the peer TLS certificate CN.

        The CN is used directly as the ``client_identity``.  If the CN
        also matches a known key entry's ``client_name`` the stored roles
        are applied; otherwise the default ``read-only`` role is assigned.

        Parameters
        ----------
        peer_cn:
            Common Name extracted from the peer certificate's subject.
        """
        if not peer_cn:
            return AuthResult(
                authenticated   = False,
                client_identity = "",
                reason          = "empty_peer_cn",
            )

        self._ensure_loaded()
        # Look for a key entry whose client_name matches the CN.
        for entry in self._entries:
            if entry.client_name == peer_cn:
                return AuthResult(
                    authenticated   = True,
                    client_identity = peer_cn,
                    roles           = list(entry.roles),
                )

        # Unknown CN: fall back to read-only (v3.1 §3.1)
        return AuthResult(
            authenticated   = True,
            client_identity = peer_cn,
            roles           = ["read-only"],
        )

    # ------------------------------------------------------------------
    # Helpers for tests / admin CLI
    # ------------------------------------------------------------------

    @property
    def entries(self) -> list[KeyEntry]:
        """Read-only snapshot of currently loaded entries."""
        self._ensure_loaded()
        return list(self._entries)

    @staticmethod
    def hash_key(raw_key: str) -> str:
        """Return the ``sha256:<hex>`` string suitable for storing in YAML."""
        return f"sha256:{_sha256_hex(raw_key)}"
