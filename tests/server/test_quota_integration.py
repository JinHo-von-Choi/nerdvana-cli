"""Integration tests — quota enforcement wired into NerdvanaMcpServer._dispatch.

Covers eight scenarios:
  1. No resolver configured → calls pass through without quota enforcement.
  2. Default rpm=2 → third call raises QuotaExceeded and records quota_denied in audit.
  3. Per-tenant override beats default — alice (rpm=5) ok at 3 calls when default is rpm=1.
  4. max_concurrent=1 → second concurrent call is denied (QuotaExceeded).
  5. release decrements the slot — third call succeeds after first two complete.
  6. daily_tokens → after release(tokens=1500) once, next check is denied when daily_tokens=1000.
  7. Per-role policy — bob with role admin matches per_role rpm=10, not default rpm=2.
  8. Token accounting — ToolResult(tokens=1500) saturates daily_tokens=1000 on next call.

작성자: 최진호
작성일: 2026-05-13
"""

from __future__ import annotations

import pytest

from nerdvana_cli.server.acl import ACLManager
from nerdvana_cli.server.audit import AuditLogger
from nerdvana_cli.server.mcp_server import NerdvanaMcpServer
from nerdvana_cli.server.quota import (
    QuotaExceeded,
    QuotaPolicy,
    QuotaPolicyResolver,
    QuotaStore,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_audit(tmp_path):
    logger = AuditLogger(db_path=tmp_path / "audit.sqlite")
    logger.open()
    yield logger
    logger.close()


@pytest.fixture
def acl_all_tools(tmp_path):
    """ACL that permits ReadMemory / ListMemories / GetCurrentConfig for all clients.

    Includes ``bob`` with the ``admin`` role so per-role quota tests can verify
    that ``effective_roles()`` feeds the resolver correctly.
    """
    acl_file = tmp_path / "mcp_acl.yml"
    acl_file.write_text(
        "roles:\n"
        "  read-only:\n"
        "    - ReadMemory\n"
        "    - ListMemories\n"
        "    - GetCurrentConfig\n"
        "  admin:\n"
        "    - ReadMemory\n"
        "    - ListMemories\n"
        "    - GetCurrentConfig\n"
        "clients:\n"
        "  alice:\n"
        "    roles: [read-only]\n"
        "  anonymous:\n"
        "    roles: [read-only]\n"
        "  bob:\n"
        "    roles: [admin]\n",
        encoding="utf-8",
    )
    mgr = ACLManager(acl_path=acl_file)
    mgr.load()
    return mgr


def _make_server(
    tmp_audit: AuditLogger,
    acl: ACLManager,
    tmp_path: object,
    *,
    quota_resolver: QuotaPolicyResolver | None = None,
    quota_store: QuotaStore | None = None,
) -> NerdvanaMcpServer:
    return NerdvanaMcpServer(
        allow_write     = False,
        transport       = "stdio",
        audit_logger    = tmp_audit,
        acl_manager     = acl,
        project_path    = tmp_path,  # type: ignore[arg-type]
        quota_resolver  = quota_resolver,
        quota_store     = quota_store,
    )


# ---------------------------------------------------------------------------
# Scenario 1 — quota disabled (no resolver) → calls succeed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_quota_disabled_no_resolver(tmp_audit, acl_all_tools, tmp_path) -> None:
    server = _make_server(tmp_audit, acl_all_tools, tmp_path)
    # Three rapid calls must all succeed; QuotaExceeded must never be raised.
    for _ in range(3):
        result = await server._dispatch("ListMemories", {"topic": ""}, client_identity="alice")
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# Scenario 2 — default rpm=2 → third call raises QuotaExceeded
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_default_rpm_exceeded_records_quota_denied(tmp_audit, acl_all_tools, tmp_path) -> None:
    resolver = QuotaPolicyResolver(default=QuotaPolicy(rpm=2))
    store    = QuotaStore()
    server   = _make_server(tmp_audit, acl_all_tools, tmp_path, quota_resolver=resolver, quota_store=store)

    # First two calls must succeed.
    await server._dispatch("ListMemories", {"topic": ""}, client_identity="alice")
    await server._dispatch("ListMemories", {"topic": ""}, client_identity="alice")

    # Third call must raise QuotaExceeded.
    with pytest.raises(QuotaExceeded) as exc_info:
        await server._dispatch("ListMemories", {"topic": ""}, client_identity="alice")

    assert exc_info.value.limit_name == "rpm"
    assert exc_info.value.retry_after_seconds >= 1

    # Audit must contain a quota_denied entry.
    rows = tmp_audit.recent(20)
    quota_denied_rows = [
        r for r in rows
        if r["error_class"] is not None and r["error_class"].startswith("quota_denied:")
    ]
    assert len(quota_denied_rows) >= 1, "Expected at least one quota_denied audit row"


# ---------------------------------------------------------------------------
# Scenario 3 — per-tenant override beats default
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_per_tenant_override_beats_default(tmp_audit, acl_all_tools, tmp_path) -> None:
    resolver = QuotaPolicyResolver(
        default    = QuotaPolicy(rpm=1),
        per_tenant = {"alice": QuotaPolicy(rpm=5)},
    )
    store  = QuotaStore()
    server = _make_server(tmp_audit, acl_all_tools, tmp_path, quota_resolver=resolver, quota_store=store)

    # alice has rpm=5 override — three calls must succeed.
    for _ in range(3):
        result = await server._dispatch("ListMemories", {"topic": ""}, client_identity="alice")
        assert isinstance(result, str)

    # anonymous has default rpm=1 — second call must be denied.
    await server._dispatch("ListMemories", {"topic": ""}, client_identity="anonymous")
    with pytest.raises(QuotaExceeded):
        await server._dispatch("ListMemories", {"topic": ""}, client_identity="anonymous")


# ---------------------------------------------------------------------------
# Scenario 4 — max_concurrent=1 → second concurrent call is denied
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_max_concurrent_blocks_second_concurrent_call(tmp_audit, acl_all_tools, tmp_path) -> None:
    resolver = QuotaPolicyResolver(default=QuotaPolicy(max_concurrent=1))
    store    = QuotaStore()
    server   = _make_server(tmp_audit, acl_all_tools, tmp_path, quota_resolver=resolver, quota_store=store)

    # Manually reserve a slot to simulate an in-flight call.
    policy = resolver.resolve("alice")
    first_decision = store.check("alice", policy)
    assert first_decision.allowed is True

    # Second concurrent check should be denied.
    second_decision = store.check("alice", policy)
    assert second_decision.allowed is False
    assert second_decision.limit_name == "max_concurrent"

    # Release the first slot.
    store.release("alice")

    # Now a dispatch call must succeed.
    result = await server._dispatch("ListMemories", {"topic": ""}, client_identity="alice")
    assert isinstance(result, str)


# ---------------------------------------------------------------------------
# Scenario 5 — release decrements slot → third call succeeds after first two complete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_release_allows_reuse_of_slot(tmp_audit, acl_all_tools, tmp_path) -> None:
    resolver = QuotaPolicyResolver(default=QuotaPolicy(max_concurrent=1))
    store    = QuotaStore()
    server   = _make_server(tmp_audit, acl_all_tools, tmp_path, quota_resolver=resolver, quota_store=store)

    # Sequential calls each acquire and release the single concurrent slot via _dispatch.
    for _ in range(3):
        result = await server._dispatch("ListMemories", {"topic": ""}, client_identity="alice")
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# Scenario 6 — daily_tokens limit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_daily_tokens_exceeded_after_release(tmp_audit, acl_all_tools, tmp_path) -> None:
    resolver = QuotaPolicyResolver(default=QuotaPolicy(daily_tokens=1000))
    store    = QuotaStore()

    # Record 1500 tokens consumed — exceeds the 1000 daily limit.
    store.release("alice", tokens=1500)

    # Any subsequent check must be denied because the token window is saturated.
    policy   = resolver.resolve("alice")
    decision = store.check("alice", policy)
    assert decision.allowed is False
    assert decision.limit_name == "daily_tokens"
    assert decision.retry_after_seconds >= 1


# ---------------------------------------------------------------------------
# Scenario 7 — per-role policy via effective_roles() public API
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_per_role_policy_via_effective_roles(tmp_audit, acl_all_tools, tmp_path) -> None:
    """bob has role=admin which maps to per_role rpm=10; default is rpm=2.

    Three calls for bob must all succeed (10 > 3); two calls for alice
    (read-only, default rpm=2) must succeed while the third raises
    QuotaExceeded — verifying that effective_roles() feeds the resolver.
    """
    resolver = QuotaPolicyResolver(
        default  = QuotaPolicy(rpm=2),
        per_role = {"admin": QuotaPolicy(rpm=10)},
    )
    store  = QuotaStore()
    server = _make_server(tmp_audit, acl_all_tools, tmp_path, quota_resolver=resolver, quota_store=store)

    # bob (admin role) has rpm=10 — three calls must succeed.
    for _ in range(3):
        result = await server._dispatch("ListMemories", {"topic": ""}, client_identity="bob")
        assert isinstance(result, str)

    # alice (read-only, default rpm=2) — second call succeeds, third is denied.
    await server._dispatch("ListMemories", {"topic": ""}, client_identity="alice")
    await server._dispatch("ListMemories", {"topic": ""}, client_identity="alice")
    with pytest.raises(QuotaExceeded) as exc_info:
        await server._dispatch("ListMemories", {"topic": ""}, client_identity="alice")
    assert exc_info.value.limit_name == "rpm"


# ---------------------------------------------------------------------------
# Scenario 8 — token accounting: ToolResult.tokens propagated to QuotaStore
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_token_accounting_blocks_next_call_when_daily_limit_exceeded(
    tmp_audit, acl_all_tools, tmp_path
) -> None:
    """A ToolResult with tokens=1500 saturates a daily_tokens=1000 limit.

    The first call succeeds.  The QuotaStore records 1500 tokens via
    _dispatch's finally block (populated from ToolResult.tokens on the raw
    result returned by _execute_tool).  The second call is denied because
    the daily token window is now saturated.

    This test monkey-patches _execute_tool to return a ToolResult with
    tokens=1500 so it does not require a live LLM.
    """
    from nerdvana_cli.types import ToolResult

    resolver = QuotaPolicyResolver(default=QuotaPolicy(daily_tokens=1000))
    store    = QuotaStore()
    server   = _make_server(tmp_audit, acl_all_tools, tmp_path, quota_resolver=resolver, quota_store=store)

    # Patch _call_tool_raw to return a ToolResult with tokens=1500.
    # _dispatch calls _call_tool_raw (not _execute_tool) to preserve the
    # raw ToolResult so it can extract the tokens field before str conversion.
    async def _fake_call_tool_raw(tool_name: str, args: dict) -> ToolResult:
        return ToolResult(tool_use_id="", content="ok", tokens=1500)

    server._call_tool_raw = _fake_call_tool_raw  # type: ignore[method-assign]

    # First call succeeds (token window was empty).
    result = await server._dispatch("ListMemories", {"topic": ""}, client_identity="alice")
    assert isinstance(result, str)

    # Second call must be denied — 1500 tokens > 1000 daily limit.
    with pytest.raises(QuotaExceeded) as exc_info:
        await server._dispatch("ListMemories", {"topic": ""}, client_identity="alice")
    assert exc_info.value.limit_name == "daily_tokens"
