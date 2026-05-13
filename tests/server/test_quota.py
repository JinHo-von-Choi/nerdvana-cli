"""Unit tests for ``server/quota.py`` — policy, store, resolver."""

from __future__ import annotations

from pathlib import Path

import pytest

from nerdvana_cli.server.quota import (
    QuotaDecision,
    QuotaPolicy,
    QuotaPolicyResolver,
    QuotaStore,
)

# ---------------------------------------------------------------------------
# QuotaPolicy basics
# ---------------------------------------------------------------------------

def test_policy_unconstrained_defaults():
    assert QuotaPolicy().is_unconstrained() is True


def test_policy_with_any_limit_is_constrained():
    assert QuotaPolicy(rpm=10).is_unconstrained() is False
    assert QuotaPolicy(max_concurrent=1).is_unconstrained() is False
    assert QuotaPolicy(daily_tokens=1000).is_unconstrained() is False


# ---------------------------------------------------------------------------
# Store: allow path
# ---------------------------------------------------------------------------

def test_store_allows_unconstrained_policy_without_recording():
    store = QuotaStore()
    decision = store.check("alice", QuotaPolicy())
    assert decision.allowed is True
    assert decision.limit_name == ""


def test_store_records_in_flight_on_reservation():
    store = QuotaStore()
    policy = QuotaPolicy(max_concurrent=2)
    assert store.check("alice", policy).allowed is True
    assert store.check("alice", policy).allowed is True
    # Third in-flight should be blocked.
    blocked = store.check("alice", policy)
    assert blocked.allowed is False
    assert blocked.limit_name == "max_concurrent"
    assert blocked.retry_after_seconds >= 1


def test_release_decrements_in_flight():
    store = QuotaStore()
    policy = QuotaPolicy(max_concurrent=1)
    assert store.check("alice", policy).allowed is True
    assert store.check("alice", policy).allowed is False
    store.release("alice")
    assert store.check("alice", policy).allowed is True


# ---------------------------------------------------------------------------
# Store: rpm / rph windows
# ---------------------------------------------------------------------------

def test_rpm_blocks_after_threshold():
    store = QuotaStore()
    policy = QuotaPolicy(rpm=3)
    now = 1000.0
    for _ in range(3):
        assert store.check("alice", policy, now=now).allowed is True
    blocked = store.check("alice", policy, now=now)
    assert blocked.allowed is False
    assert blocked.limit_name == "rpm"
    assert blocked.retry_after_seconds <= 61  # within the minute window


def test_rpm_recovers_after_window_passes():
    store = QuotaStore()
    policy = QuotaPolicy(rpm=2)
    assert store.check("alice", policy, now=0.0).allowed is True
    assert store.check("alice", policy, now=10.0).allowed is True
    # Same second — blocked
    assert store.check("alice", policy, now=10.0).allowed is False
    # 61s later — entries fall out of the rolling minute window
    assert store.check("alice", policy, now=71.5).allowed is True


def test_rph_blocks_after_threshold():
    store = QuotaStore()
    policy = QuotaPolicy(rph=2)
    assert store.check("alice", policy, now=0.0).allowed is True
    assert store.check("alice", policy, now=1.0).allowed is True
    blocked = store.check("alice", policy, now=2.0)
    assert blocked.allowed is False
    assert blocked.limit_name == "rph"


# ---------------------------------------------------------------------------
# Store: daily token quota
# ---------------------------------------------------------------------------

def test_daily_tokens_blocks_only_after_observed_usage():
    store = QuotaStore()
    policy = QuotaPolicy(daily_tokens=1000)
    # No tokens recorded yet — three requests pass.
    for _ in range(3):
        assert store.check("alice", policy, now=0.0).allowed is True
    # Record observed usage above the budget.
    store.release("alice", tokens=1500, now=0.0)
    blocked = store.check("alice", policy, now=1.0)
    assert blocked.allowed is False
    assert blocked.limit_name == "daily_tokens"


# ---------------------------------------------------------------------------
# Tenant isolation
# ---------------------------------------------------------------------------

def test_tenants_are_isolated():
    store = QuotaStore()
    policy = QuotaPolicy(rpm=1)
    assert store.check("alice", policy, now=0.0).allowed is True
    assert store.check("alice", policy, now=0.0).allowed is False
    # Bob's bucket is untouched by Alice.
    assert store.check("bob",   policy, now=0.0).allowed is True


# ---------------------------------------------------------------------------
# Resolver: precedence
# ---------------------------------------------------------------------------

def test_resolver_default_only():
    r = QuotaPolicyResolver(default=QuotaPolicy(rpm=10))
    assert r.resolve("alice").rpm == 10


def test_resolver_per_tenant_beats_per_role_beats_default():
    r = QuotaPolicyResolver(
        default    = QuotaPolicy(rpm=10),
        per_tenant = {"alice": QuotaPolicy(rpm=100)},
        per_role   = {"admin": QuotaPolicy(rpm=50)},
    )
    assert r.resolve("alice", roles=["admin"]).rpm == 100
    assert r.resolve("bob",   roles=["admin"]).rpm == 50
    assert r.resolve("bob",   roles=["guest"]).rpm == 10
    assert r.resolve("carol").rpm == 10


def test_resolver_from_dict():
    r = QuotaPolicyResolver.from_dict({
        "default": {"rpm": 5, "max_concurrent": 2},
        "tenants": {"alice": {"rpm": 50}},
        "roles":   {"admin": {"daily_tokens": 100000}},
    })
    assert r.resolve("alice").rpm == 50
    assert r.resolve("bob", roles=["admin"]).daily_tokens == 100000
    assert r.resolve("bob").rpm == 5
    assert r.resolve("bob").max_concurrent == 2


def test_resolver_from_yaml_handles_missing_file(tmp_path: Path):
    r = QuotaPolicyResolver.from_yaml(tmp_path / "nonexistent.yml")
    assert r.resolve("alice").is_unconstrained() is True


def test_resolver_from_yaml_loads(tmp_path: Path):
    yml = tmp_path / "quota.yml"
    yml.write_text(
        "default:\n"
        "  rpm: 30\n"
        "tenants:\n"
        "  alice:\n"
        "    rpm: 120\n",
        encoding="utf-8",
    )
    r = QuotaPolicyResolver.from_yaml(yml)
    assert r.resolve("alice").rpm == 120
    assert r.resolve("bob").rpm == 30


# ---------------------------------------------------------------------------
# Retry-After hint sanity
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "policy,expected_limit",
    [
        (QuotaPolicy(rpm=1),            "rpm"),
        (QuotaPolicy(rph=1),            "rph"),
        (QuotaPolicy(max_concurrent=1), "max_concurrent"),
    ],
)
def test_retry_after_is_positive_when_blocked(policy: QuotaPolicy, expected_limit: str):
    store = QuotaStore()
    decision1 = store.check("alice", policy, now=0.0)
    assert decision1.allowed is True
    decision2 = store.check("alice", policy, now=0.0)
    assert decision2.allowed is False
    assert decision2.limit_name == expected_limit
    assert decision2.retry_after_seconds >= 1


def test_decision_dataclass_defaults():
    d = QuotaDecision(allowed=True)
    assert d.reason == "" and d.retry_after_seconds == 0 and d.limit_name == ""
