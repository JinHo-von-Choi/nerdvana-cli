"""Tests for ACLManager — Phase G1.

7 test cases:
  - unknown client → read-only default (v3.1 §3.1)
  - role tool mapping: read-only, edit, write-memory, admin
  - explicit client role assignment
  - revoke by prefix
  - add_client / list operations
  - allowed_tools union
  - denied tool with correct role
"""

from __future__ import annotations

import pytest

from nerdvana_cli.server.acl import ACLManager

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def full_acl(tmp_path) -> ACLManager:
    content = (
        "roles:\n"
        "  read-only:\n"
        "    - symbol_overview\n"
        "    - find_symbol\n"
        "    - find_referencing_symbols\n"
        "    - ReadMemory\n"
        "    - ListMemories\n"
        "    - GetCurrentConfig\n"
        "  edit:\n"
        "    - replace_symbol_body\n"
        "    - insert_before_symbol\n"
        "    - insert_after_symbol\n"
        "    - RenameSymbol\n"
        "  write-memory:\n"
        "    - WriteMemory\n"
        "    - EditMemory\n"
        "  admin:\n"
        "    - restart_language_server\n"
        "    - DeleteMemory\n"
        "    - safe_delete_symbol\n"
        "clients:\n"
        "  claude-code-prod:\n"
        "    roles: [read-only]\n"
        "  cursor-dev:\n"
        "    roles: [read-only, edit]\n"
        "  mem-writer:\n"
        "    roles: [read-only, write-memory]\n"
        "  superuser:\n"
        "    roles: [read-only, edit, write-memory, admin]\n"
    )
    path = tmp_path / "mcp_acl.yml"
    path.write_text(content, encoding="utf-8")
    mgr = ACLManager(acl_path=path)
    mgr.load()
    return mgr


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_unknown_client_gets_read_only(full_acl):
    """Unknown client identity must be assigned read-only by default (v3.1 §3.1)."""
    decision = full_acl.check("nobody", "symbol_overview")
    assert decision.allowed


def test_unknown_client_denied_write(full_acl):
    """Unknown client must be denied write-tool access."""
    decision = full_acl.check("nobody", "replace_symbol_body")
    assert not decision.allowed


def test_read_only_role_mapping(full_acl):
    """read-only client can access all six default tools."""
    tools = ["symbol_overview", "find_symbol", "find_referencing_symbols",
             "ReadMemory", "ListMemories", "GetCurrentConfig"]
    for t in tools:
        d = full_acl.check("claude-code-prod", t)
        assert d.allowed, f"expected allowed for tool '{t}'"


def test_edit_role_extends_read_only(full_acl):
    """cursor-dev with [read-only, edit] can call edit tools."""
    assert full_acl.check("cursor-dev", "replace_symbol_body").allowed
    assert full_acl.check("cursor-dev", "symbol_overview").allowed
    # but NOT write-memory
    assert not full_acl.check("cursor-dev", "WriteMemory").allowed


def test_revoke_by_prefix(full_acl):
    """revoke removes all clients whose name starts with the prefix."""
    removed = full_acl.revoke("cursor")
    assert "cursor-dev" in removed
    # After revoke the client falls back to read-only (unknown)
    assert full_acl.check("cursor-dev", "replace_symbol_body").allowed is False


def test_add_client(tmp_path):
    """add_client registers a new client with given roles."""
    mgr = ACLManager(acl_path=tmp_path / "empty.yml")
    mgr.load()
    mgr.add_client("new-bot", ["read-only", "edit"])
    full_roles = mgr.list_clients().get("new-bot")
    assert full_roles is not None
    assert "edit" in full_roles


def test_allowed_tools_union(full_acl):
    """allowed_tools returns union of all role tool sets for the client."""
    tools = full_acl.allowed_tools("cursor-dev")
    # has read-only + edit
    assert "symbol_overview"    in tools
    assert "replace_symbol_body" in tools
    # but not write-memory or admin
    assert "WriteMemory" not in tools
    assert "DeleteMemory" not in tools


def test_effective_roles_public_api(full_acl):
    """effective_roles() public method returns the same list as _effective_roles()."""
    # Known client with explicit role assignment
    assert full_acl.effective_roles("cursor-dev") == ["read-only", "edit"]
    # Unknown client falls back to read-only default
    assert full_acl.effective_roles("nobody") == ["read-only"]
    # superuser has all four roles
    roles = full_acl.effective_roles("superuser")
    assert set(roles) == {"read-only", "edit", "write-memory", "admin"}


def test_effective_roles_triggers_load(tmp_path):
    """effective_roles() loads config on first call (lazy load)."""
    content = (
        "clients:\n"
        "  special-bot:\n"
        "    roles: [read-only, edit]\n"
    )
    path = tmp_path / "mcp_acl.yml"
    path.write_text(content, encoding="utf-8")
    mgr = ACLManager(acl_path=path)
    # Do NOT call load() explicitly — effective_roles() must trigger it.
    roles = mgr.effective_roles("special-bot")
    assert "edit" in roles
