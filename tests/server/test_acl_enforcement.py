"""ACL enforcement tests — C-3 (tool visibility filtering).

Verifies that:
  - A client with only read-only role is denied write tools
  - A client with edit role is allowed write tools
  - anonymous client (default) gets read-only access only
  - ACL check is exercised per-call, not cached across identities

작성자: 최진호
작성일: 2026-04-18
"""

from __future__ import annotations

import pytest

from nerdvana_cli.server.acl import ACLManager, ACLDecision
from nerdvana_cli.server.audit import AuditLogger
from nerdvana_cli.server.mcp_server import NerdvanaMcpServer


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_audit(tmp_path):
    logger = AuditLogger(db_path=tmp_path / "audit.sqlite")
    logger.open()
    yield logger
    logger.close()


@pytest.fixture
def acl_multi_role(tmp_path):
    acl_file = tmp_path / "mcp_acl.yml"
    acl_file.write_text(
        "roles:\n"
        "  read-only:\n"
        "    - ListMemories\n"
        "    - GetCurrentConfig\n"
        "    - ReadMemory\n"
        "  write-memory:\n"
        "    - WriteMemory\n"
        "    - EditMemory\n"
        "    - DeleteMemory\n"
        "clients:\n"
        "  ro-client:\n"
        "    roles: [read-only]\n"
        "  rw-client:\n"
        "    roles: [read-only, write-memory]\n",
        encoding="utf-8",
    )
    mgr = ACLManager(acl_path=acl_file)
    mgr.load()
    return mgr


@pytest.fixture
def server_rw(tmp_audit, acl_multi_role, tmp_path):
    return NerdvanaMcpServer(
        allow_write  = True,
        transport    = "stdio",
        audit_logger = tmp_audit,
        acl_manager  = acl_multi_role,
        project_path = tmp_path,
    )


# ---------------------------------------------------------------------------
# Tests — ACL decisions
# ---------------------------------------------------------------------------


def test_acl_read_only_client_denied_write_tool(acl_multi_role) -> None:
    """ro-client must be denied WriteMemory."""
    dec = acl_multi_role.check("ro-client", "WriteMemory")
    assert dec.allowed is False


def test_acl_read_only_client_allowed_list_memories(acl_multi_role) -> None:
    """ro-client must be allowed ListMemories."""
    dec = acl_multi_role.check("ro-client", "ListMemories")
    assert dec.allowed is True


def test_acl_rw_client_allowed_write_memory(acl_multi_role) -> None:
    """rw-client must be allowed WriteMemory."""
    dec = acl_multi_role.check("rw-client", "WriteMemory")
    assert dec.allowed is True


def test_acl_unknown_client_gets_read_only_default(acl_multi_role) -> None:
    """An unregistered client must receive the default read-only role."""
    dec_ro = acl_multi_role.check("unknown-xyz", "ListMemories")
    assert dec_ro.allowed is True
    dec_wr = acl_multi_role.check("unknown-xyz", "WriteMemory")
    assert dec_wr.allowed is False


@pytest.mark.asyncio
async def test_dispatch_ro_client_denied_write_tool_records_denied(server_rw, tmp_audit) -> None:
    """_dispatch must record decision=denied when ro-client calls WriteMemory."""
    with pytest.raises(PermissionError, match="ACL denied"):
        await server_rw._dispatch(
            "WriteMemory",
            {"name": "x", "content": "y", "scope": "project_knowledge"},
            client_identity="ro-client",
        )
    rows = tmp_audit.recent(10)
    assert any(r["decision"] == "denied" and r["tool_name"] == "WriteMemory" for r in rows)


@pytest.mark.asyncio
async def test_dispatch_rw_client_allowed_write_tool(server_rw, tmp_audit) -> None:
    """_dispatch must allow rw-client to call WriteMemory."""
    result = await server_rw._dispatch(
        "WriteMemory",
        {"name": "acl_test_mem", "content": "data", "scope": "project_knowledge"},
        client_identity="rw-client",
    )
    assert isinstance(result, str)
    rows = tmp_audit.recent(10)
    assert any(
        r["client_identity"] == "rw-client"
        and r["tool_name"] == "WriteMemory"
        and r["decision"] in ("allowed", "error")
        for r in rows
    )


@pytest.mark.asyncio
async def test_dispatch_denied_then_allowed_distinct_identity(server_rw, tmp_audit) -> None:
    """ACL must be evaluated per-call — different identities get different decisions."""
    # ro-client denied
    with pytest.raises(PermissionError):
        await server_rw._dispatch("WriteMemory", {"name": "a", "content": "b", "scope": "user_global"}, client_identity="ro-client")

    # rw-client allowed (may succeed or error at tool level, but NOT PermissionError)
    try:
        await server_rw._dispatch("WriteMemory", {"name": "a", "content": "b", "scope": "user_global"}, client_identity="rw-client")
    except PermissionError:
        pytest.fail("rw-client must not receive PermissionError")
    except Exception:
        pass  # tool-level error is acceptable


def test_allowed_tools_view_differs_per_client(acl_multi_role) -> None:
    """allowed_tools must return different sets for ro-client vs rw-client."""
    ro_tools = acl_multi_role.allowed_tools("ro-client")
    rw_tools = acl_multi_role.allowed_tools("rw-client")
    assert "WriteMemory" not in ro_tools
    assert "WriteMemory" in rw_tools
    assert ro_tools.issubset(rw_tools)
