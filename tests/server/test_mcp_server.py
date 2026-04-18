"""Tests for NerdvanaMcpServer — Phase G1.

10 test cases covering:
  - tool registration (read-only vs write mode)
  - write-guard enforcement (allow_write flag + confirm field)
  - ACL routing in _dispatch
  - audit recording on allow/deny/error
  - host/port validation
  - transport property
"""

from __future__ import annotations

import pytest

from nerdvana_cli.server.acl       import ACLManager
from nerdvana_cli.server.audit     import AuditLogger
from nerdvana_cli.server.auth      import AuthManager
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
def acl_permit_all(tmp_path):
    """ACL that permits everything for 'testclient'."""
    acl_file = tmp_path / "mcp_acl.yml"
    acl_file.write_text(
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
        "    - WriteMemory\n"
        "    - EditMemory\n"
        "    - DeleteMemory\n"
        "    - safe_delete_symbol\n"
        "    - restart_language_server\n"
        "clients:\n"
        "  testclient:\n"
        "    roles: [read-only, edit]\n",
        encoding="utf-8",
    )
    mgr = ACLManager(acl_path=acl_file)
    mgr.load()
    return mgr


@pytest.fixture
def server_ro(tmp_audit, acl_permit_all):
    return NerdvanaMcpServer(
        allow_write  = False,
        transport    = "stdio",
        audit_logger = tmp_audit,
        acl_manager  = acl_permit_all,
    )


@pytest.fixture
def server_rw(tmp_audit, acl_permit_all):
    return NerdvanaMcpServer(
        allow_write  = True,
        transport    = "stdio",
        audit_logger = tmp_audit,
        acl_manager  = acl_permit_all,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_read_only_tools_registered(server_ro):
    """Read-only server must expose exactly the 6 default tools."""
    tool_names = {t.name for t in server_ro.fmcp._tool_manager.list_tools()}
    expected = {
        "symbol_overview", "find_symbol", "find_referencing_symbols",
        "ReadMemory", "ListMemories", "GetCurrentConfig",
    }
    assert expected == tool_names


def test_write_tools_registered_when_allow_write(server_rw):
    """Allow-write server must include write-tool names."""
    tool_names = {t.name for t in server_rw.fmcp._tool_manager.list_tools()}
    write_expected = {
        "replace_symbol_body", "insert_before_symbol", "insert_after_symbol",
        "RenameSymbol", "WriteMemory", "EditMemory", "DeleteMemory",
        "safe_delete_symbol", "restart_language_server",
    }
    assert write_expected.issubset(tool_names)


def test_write_tools_not_registered_when_read_only(server_ro):
    """Read-only server must NOT expose write tools."""
    tool_names = {t.name for t in server_ro.fmcp._tool_manager.list_tools()}
    write_names = {
        "replace_symbol_body", "insert_before_symbol", "WriteMemory",
        "DeleteMemory", "safe_delete_symbol",
    }
    assert write_names.isdisjoint(tool_names)


@pytest.mark.asyncio
async def test_dispatch_allowed_records_audit(server_ro, tmp_audit):
    """A permitted read-only dispatch must log decision=allowed."""
    await server_ro._dispatch("ReadMemory", {"name": "test"}, client_identity="testclient")
    rows = tmp_audit.recent(10)
    assert any(r["decision"] == "allowed" and r["tool_name"] == "ReadMemory" for r in rows)


@pytest.mark.asyncio
async def test_dispatch_acl_denied_records_audit(server_ro, tmp_audit):
    """Unknown client asking for write tool must be denied and logged."""
    with pytest.raises(PermissionError, match="ACL denied"):
        await server_ro._dispatch(
            "replace_symbol_body",
            {"name_path": "foo.bar", "new_body": "x"},
            client_identity="unknown-client",
        )
    rows = tmp_audit.recent(10)
    assert any(r["decision"] == "denied" for r in rows)


@pytest.mark.asyncio
async def test_write_guard_no_allow_write_flag(server_ro):
    """_check_write_confirm must raise when allow_write=False."""
    with pytest.raises(PermissionError, match="read-only mode"):
        server_ro._check_write_confirm(confirm=True)


@pytest.mark.asyncio
async def test_write_guard_missing_confirm(server_rw):
    """_check_write_confirm must raise when confirm=False even with allow_write."""
    with pytest.raises(PermissionError, match="confirm: true"):
        server_rw._check_write_confirm(confirm=False)


@pytest.mark.asyncio
async def test_write_guard_passes_with_both_flags(server_rw):
    """_check_write_confirm must not raise when allow_write=True and confirm=True."""
    server_rw._check_write_confirm(confirm=True)  # no exception


def test_transport_property(server_ro, server_rw):
    """Transport attribute is stored correctly."""
    assert server_ro.transport == "stdio"
    assert server_rw.transport == "stdio"


def test_http_transport_default_host():
    """HTTP server should default to 127.0.0.1."""
    s = NerdvanaMcpServer(transport="http", port=10831)
    assert s.host == "127.0.0.1"


@pytest.mark.asyncio
async def test_execute_tool_returns_string(server_ro):
    """_execute_tool must return a string (stub for now)."""
    result = await server_ro._execute_tool("ReadMemory", {"name": "x"})
    assert isinstance(result, str)
