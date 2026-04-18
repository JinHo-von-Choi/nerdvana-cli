"""Role-based ACL for NerdVana MCP server — Phase G1.

Loads ``~/.nerdvana/mcp_acl.yml`` and enforces per-tool permission checks.

YAML schema:
  roles:
    read-only:    [symbol_overview, find_symbol, ...]
    edit:         [replace_symbol_body, insert_before_symbol, ...]
    write-memory: [WriteMemory, EditMemory]
    admin:        [restart_language_server, DeleteMemory, safe_delete_symbol]
  clients:
    claude-code-prod:
      roles: [read-only]
    cursor-dev:
      roles: [read-only, edit]

Unknown clients receive the ``read-only`` role (v3.1 §3.1).

작성자: 최진호
작성일: 2026-04-18
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml  # type: ignore[import-untyped]

# ---------------------------------------------------------------------------
# Built-in defaults (v3 §7.2)
# ---------------------------------------------------------------------------

_DEFAULT_ROLE_TOOLS: dict[str, list[str]] = {
    "read-only": [
        "symbol_overview",
        "find_symbol",
        "find_referencing_symbols",
        "ReadMemory",
        "ListMemories",
        "GetCurrentConfig",
    ],
    "edit": [
        "replace_symbol_body",
        "insert_before_symbol",
        "insert_after_symbol",
        "RenameSymbol",
    ],
    "write-memory": [
        "WriteMemory",
        "EditMemory",
    ],
    "admin": [
        "restart_language_server",
        "DeleteMemory",
        "safe_delete_symbol",
    ],
}

_DEFAULT_ROLE = "read-only"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class ACLDecision:
    """Result of an ACL check."""

    allowed: bool
    reason:  str = ""


@dataclass
class ClientACL:
    """Per-client role assignments."""

    client_name: str
    roles:       list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# ACLManager
# ---------------------------------------------------------------------------


class ACLManager:
    """Enforces tool-level access control per client identity.

    Parameters
    ----------
    acl_path:
        Path to ``mcp_acl.yml``.  Defaults to ``~/.nerdvana/mcp_acl.yml``.
    """

    def __init__(self, acl_path: Path | None = None) -> None:
        self._acl_path: Path = acl_path or Path.home() / ".nerdvana" / "mcp_acl.yml"
        # role → frozenset of allowed tool names
        self._role_tools: dict[str, frozenset[str]] = {
            k: frozenset(v) for k, v in _DEFAULT_ROLE_TOOLS.items()
        }
        # client_name → [role, ...]
        self._client_roles: dict[str, list[str]] = {}
        self._loaded: bool = False

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def load(self) -> None:
        """(Re)load ACL configuration from the YAML file.

        Falls back to built-in defaults when the file is absent.
        """
        self._loaded = True

        if not self._acl_path.exists():
            return

        with self._acl_path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}

        # Roles override (merge with defaults — file wins per role)
        roles_raw = data.get("roles") or {}
        if isinstance(roles_raw, dict):
            for role_name, tools in roles_raw.items():
                if isinstance(tools, list):
                    self._role_tools[str(role_name)] = frozenset(str(t) for t in tools)

        # Clients
        clients_raw = data.get("clients") or {}
        if isinstance(clients_raw, dict):
            for cname, cdata in clients_raw.items():
                if isinstance(cdata, dict):
                    roles = [str(r) for r in (cdata.get("roles") or [])]
                    self._client_roles[str(cname)] = roles

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.load()

    # ------------------------------------------------------------------
    # Decision
    # ------------------------------------------------------------------

    def check(self, client_identity: str, tool_name: str) -> ACLDecision:
        """Return whether *client_identity* may call *tool_name*.

        Parameters
        ----------
        client_identity:
            Identity string produced by AuthManager (e.g. ``"claude-code-prod"``).
        tool_name:
            The MCP tool name being called.
        """
        self._ensure_loaded()
        roles = self._effective_roles(client_identity)
        for role in roles:
            allowed_tools = self._role_tools.get(role, frozenset())
            if tool_name in allowed_tools:
                return ACLDecision(allowed=True)
        return ACLDecision(
            allowed = False,
            reason  = f"tool '{tool_name}' not in any of roles {roles} for '{client_identity}'",
        )

    def _effective_roles(self, client_identity: str) -> list[str]:
        """Return effective roles for *client_identity*.

        Unknown clients receive [``read-only``] (v3.1 §3.1).
        """
        # Exact match
        if client_identity in self._client_roles:
            return list(self._client_roles[client_identity])
        # Unknown — default to read-only
        return [_DEFAULT_ROLE]

    def allowed_tools(self, client_identity: str) -> frozenset[str]:
        """Return the union of all tools visible to *client_identity*."""
        self._ensure_loaded()
        result: set[str] = set()
        for role in self._effective_roles(client_identity):
            result.update(self._role_tools.get(role, frozenset()))
        return frozenset(result)

    # ------------------------------------------------------------------
    # Admin operations
    # ------------------------------------------------------------------

    def revoke(self, key_prefix: str) -> list[str]:
        """Remove all clients whose name starts with *key_prefix*.

        Returns the list of removed client names.
        """
        self._ensure_loaded()
        removed = [k for k in self._client_roles if k.startswith(key_prefix)]
        for k in removed:
            del self._client_roles[k]
        return removed

    def add_client(self, client_name: str, roles: list[str]) -> None:
        """Register (or overwrite) *client_name* → *roles* mapping."""
        self._ensure_loaded()
        self._client_roles[str(client_name)] = [str(r) for r in roles]

    def list_clients(self) -> dict[str, list[str]]:
        """Return a copy of the client→roles mapping."""
        self._ensure_loaded()
        return {k: list(v) for k, v in self._client_roles.items()}

    def list_roles(self) -> dict[str, list[str]]:
        """Return a copy of the role→tools mapping."""
        self._ensure_loaded()
        return {k: sorted(v) for k, v in self._role_tools.items()}
