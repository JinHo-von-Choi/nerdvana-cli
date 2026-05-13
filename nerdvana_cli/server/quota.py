"""Per-tenant rate-limit and quota enforcement for the MCP server.

Layered between ACL approval and tool dispatch. Decisions are computed in
constant time against an in-memory sliding-window store; an optional Redis
adapter slot is reserved for future multi-process deployments but the default
in-process implementation is sufficient for the single-host MCP server.

Design notes
------------
- Sliding window is implemented as a deque of monotonic timestamps trimmed on
  every check. This is O(N) in the number of events within the window — fine
  for thousands of req/min per tenant; rewrite to a token bucket if hot
  tenants ever sustain > 100k rpm.
- Policy resolution: per-tenant override > role > default. A missing tenant
  defaults to the policy in ``default`` (typically a generous public quota or
  zero, depending on operator preference).
- Decisions carry a ``retry_after_seconds`` hint that the HTTP layer surfaces
  as ``Retry-After``; stdio callers can format an error string with it.

The module is dependency-free except for the standard library so it loads in
minimal extras environments alongside ``server/acl.py`` and ``server/auth.py``.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class QuotaPolicy:
    """Limits applied to one tenant (or the default).

    Any limit set to ``0`` is treated as "unlimited" so a policy can opt in to
    only the dimensions it cares about (e.g. concurrency-only).
    """

    rpm:             int = 0  # requests per rolling 60s
    rph:             int = 0  # requests per rolling 3600s
    daily_tokens:    int = 0  # input+output tokens over rolling 24h
    max_concurrent:  int = 0  # in-flight tool calls

    def is_unconstrained(self) -> bool:
        return self.rpm == 0 and self.rph == 0 and self.daily_tokens == 0 and self.max_concurrent == 0


@dataclass(frozen=True)
class QuotaDecision:
    """Outcome of a single quota evaluation."""

    allowed:             bool
    reason:              str = ""
    retry_after_seconds: int = 0
    limit_name:          str = ""  # "rpm" | "rph" | "daily_tokens" | "max_concurrent" | ""


@dataclass
class _TenantState:
    """Mutable per-tenant counters protected by the store-level lock."""

    requests_minute: deque[float] = field(default_factory=deque)
    requests_hour:   deque[float] = field(default_factory=deque)
    tokens_day:      deque[tuple[float, int]] = field(default_factory=deque)
    in_flight:       int = 0


def _trim(window: deque[float], now: float, span: float) -> None:
    while window and now - window[0] > span:
        window.popleft()


def _trim_tokens(window: deque[tuple[float, int]], now: float, span: float) -> None:
    while window and now - window[0][0] > span:
        window.popleft()


def _sum_tokens(window: deque[tuple[float, int]]) -> int:
    return sum(n for _, n in window)


class QuotaStore:
    """In-memory sliding-window counter shared across MCP request handlers."""

    _WINDOW_MINUTE = 60.0
    _WINDOW_HOUR   = 3600.0
    _WINDOW_DAY    = 86400.0

    def __init__(self) -> None:
        self._lock:    threading.Lock           = threading.Lock()
        self._states:  dict[str, _TenantState]  = {}

    def _state(self, tenant: str) -> _TenantState:
        st = self._states.get(tenant)
        if st is None:
            st = _TenantState()
            self._states[tenant] = st
        return st

    def check(self, tenant: str, policy: QuotaPolicy, *, now: float | None = None) -> QuotaDecision:
        """Evaluate ``policy`` for ``tenant`` and tentatively reserve one slot.

        Reserving means: on ``allowed=True`` the call is recorded as in-flight
        and counted toward the rpm/rph windows. The caller MUST invoke
        ``release(tenant, tokens=...)`` exactly once after the tool completes
        so the in-flight gauge decrements and observed token usage is logged.
        """
        if policy.is_unconstrained():
            with self._lock:
                self._state(tenant).in_flight += 1
            return QuotaDecision(allowed=True)

        ts = time.monotonic() if now is None else now
        with self._lock:
            st = self._state(tenant)
            _trim(st.requests_minute, ts, self._WINDOW_MINUTE)
            _trim(st.requests_hour,   ts, self._WINDOW_HOUR)
            _trim_tokens(st.tokens_day, ts, self._WINDOW_DAY)

            if policy.max_concurrent and st.in_flight >= policy.max_concurrent:
                return QuotaDecision(
                    allowed              = False,
                    reason               = f"max_concurrent ({policy.max_concurrent}) exceeded",
                    retry_after_seconds  = 1,
                    limit_name           = "max_concurrent",
                )
            if policy.rpm and len(st.requests_minute) >= policy.rpm:
                oldest = st.requests_minute[0]
                return QuotaDecision(
                    allowed              = False,
                    reason               = f"rpm ({policy.rpm}) exceeded",
                    retry_after_seconds  = max(1, int(self._WINDOW_MINUTE - (ts - oldest)) + 1),
                    limit_name           = "rpm",
                )
            if policy.rph and len(st.requests_hour) >= policy.rph:
                oldest = st.requests_hour[0]
                return QuotaDecision(
                    allowed              = False,
                    reason               = f"rph ({policy.rph}) exceeded",
                    retry_after_seconds  = max(1, int(self._WINDOW_HOUR - (ts - oldest)) + 1),
                    limit_name           = "rph",
                )
            if policy.daily_tokens and _sum_tokens(st.tokens_day) >= policy.daily_tokens:
                oldest_ts = st.tokens_day[0][0]
                return QuotaDecision(
                    allowed              = False,
                    reason               = f"daily_tokens ({policy.daily_tokens}) exceeded",
                    retry_after_seconds  = max(1, int(self._WINDOW_DAY - (ts - oldest_ts)) + 1),
                    limit_name           = "daily_tokens",
                )

            st.requests_minute.append(ts)
            st.requests_hour.append(ts)
            st.in_flight += 1
            return QuotaDecision(allowed=True)

    def release(self, tenant: str, *, tokens: int = 0, now: float | None = None) -> None:
        """Mark the reserved slot complete and record token consumption."""
        ts = time.monotonic() if now is None else now
        with self._lock:
            st = self._state(tenant)
            if st.in_flight > 0:
                st.in_flight -= 1
            if tokens > 0:
                st.tokens_day.append((ts, tokens))


_UNCONSTRAINED = QuotaPolicy()


class QuotaPolicyResolver:
    """Resolve which QuotaPolicy applies to a given (tenant, roles) pair."""

    def __init__(
        self,
        *,
        default:    QuotaPolicy | None             = None,
        per_tenant: dict[str, QuotaPolicy] | None  = None,
        per_role:   dict[str, QuotaPolicy] | None  = None,
    ) -> None:
        self._default    = default if default is not None else _UNCONSTRAINED
        self._per_tenant = dict(per_tenant or {})
        self._per_role   = dict(per_role or {})

    def resolve(self, tenant: str, roles: list[str] | None = None) -> QuotaPolicy:
        if tenant in self._per_tenant:
            return self._per_tenant[tenant]
        for role in roles or []:
            if role in self._per_role:
                return self._per_role[role]
        return self._default

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> QuotaPolicyResolver:
        """Build from a parsed YAML/dict mapping.

        Schema::

            default: {rpm: 60, rph: 1000, daily_tokens: 0, max_concurrent: 4}
            tenants:
              alice: {rpm: 120, ...}
            roles:
              admin: {rpm: 0, ...}
        """
        def _policy(d: dict[str, Any]) -> QuotaPolicy:
            return QuotaPolicy(
                rpm            = int(d.get("rpm", 0)),
                rph            = int(d.get("rph", 0)),
                daily_tokens   = int(d.get("daily_tokens", 0)),
                max_concurrent = int(d.get("max_concurrent", 0)),
            )

        default    = _policy(data.get("default") or {})
        per_tenant = {k: _policy(v or {}) for k, v in (data.get("tenants") or {}).items()}
        per_role   = {k: _policy(v or {}) for k, v in (data.get("roles")   or {}).items()}
        return cls(default=default, per_tenant=per_tenant, per_role=per_role)

    @classmethod
    def from_yaml(cls, path: Path) -> QuotaPolicyResolver:
        """Load from a YAML file. Returns an empty resolver if the file is absent."""
        if not path.is_file():
            return cls()
        import yaml  # type: ignore[import-untyped]
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return cls.from_dict(data)


class QuotaExceeded(Exception):  # noqa: N818
    """Raised by the MCP dispatch path when a tenant exceeds its policy."""

    def __init__(self, reason: str, retry_after_seconds: int, limit_name: str) -> None:
        super().__init__(reason)
        self.reason              = reason
        self.retry_after_seconds = retry_after_seconds
        self.limit_name          = limit_name
