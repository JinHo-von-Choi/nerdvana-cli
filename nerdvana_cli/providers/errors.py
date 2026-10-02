"""Classification of provider failures into actionable kinds.

Author: 최진호
Date:   2026-10-03

The agent loop decides between retrying, falling back to another model,
compacting the history, or giving up from the kind alone, so the decision does
not depend on the wording of an SDK's error message.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
from dataclasses import dataclass
from typing import Any

RETRYABLE      = "retryable"
CONTEXT_LIMIT  = "context_limit"
AUTH           = "auth"
DECODE         = "decode"
OTHER          = "other"

_RETRYABLE_STATUS: frozenset[int] = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 529})
_AUTH_STATUS:      frozenset[int] = frozenset({401, 403})

_RETRYABLE_CLASS_NAMES: frozenset[str] = frozenset({
    "RateLimitError",
    "APITimeoutError",
    "APIConnectionError",
    "InternalServerError",
    "OverloadedError",
    "ServiceUnavailableError",
    "ConnectError",
    "ReadTimeout",
    "ConnectTimeout",
    "RemoteProtocolError",
    "StreamTimeoutError",
})
_AUTH_CLASS_NAMES: frozenset[str] = frozenset({"AuthenticationError", "PermissionDeniedError"})

# Provider phrasings for a request that does not fit the model's window. Only
# consulted for 400/413 responses, where the status alone is ambiguous.
_CONTEXT_PHRASES: tuple[str, ...] = (
    "context length",
    "context window",
    "maximum context",
    "prompt is too long",
    "too many tokens",
    "input is too long",
    "exceeds the maximum",
)

_MAX_RETRY_AFTER = 120.0

# Last resort for exceptions that carry no status or known type, such as a
# transport wrapping the HTTP failure in a plain RuntimeError.
_RETRYABLE_TEXT = re.compile(
    r"\b(429|500|502|503|504|529)\b|timed? ?out|rate.?limit|too many requests|overloaded|service unavailable",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ProviderFailure:
    """What went wrong with a provider call, in loop-actionable terms."""

    kind:        str
    status_code: int | None   = None
    retry_after: float | None = None


def _status_of(exc: BaseException) -> int | None:
    for attr in ("status_code", "status", "code"):
        value = getattr(exc, attr, None)
        if isinstance(value, int) and 100 <= value <= 599:
            return value
    response = getattr(exc, "response", None)
    value    = getattr(response, "status_code", None)
    return value if isinstance(value, int) else None


def _retry_after_of(exc: BaseException) -> float | None:
    response = getattr(exc, "response", None)
    headers: Any = getattr(response, "headers", None)
    if headers is None:
        return None
    raw = None
    with contextlib.suppress(Exception):
        raw = headers.get("retry-after")
    if raw is None:
        return None
    try:
        return max(0.0, min(float(raw), _MAX_RETRY_AFTER))
    except (TypeError, ValueError):
        return None


def error_fields(exc: BaseException, kind: str | None = None) -> dict[str, Any]:
    """Keyword arguments describing *exc* for an error ``ProviderEvent``."""
    failure = classify_exception(exc)
    return {
        "error_kind":  kind or failure.kind,
        "status_code": failure.status_code,
        "retry_after": failure.retry_after,
    }


def classify_exception(exc: BaseException) -> ProviderFailure:
    """Map an exception raised by a provider SDK or transport to a failure kind."""
    if isinstance(exc, UnicodeDecodeError):
        return ProviderFailure(DECODE)

    status      = _status_of(exc)
    retry_after = _retry_after_of(exc)
    names       = {cls.__name__ for cls in type(exc).__mro__}

    if status in _AUTH_STATUS or names & _AUTH_CLASS_NAMES:
        return ProviderFailure(AUTH, status)
    if status in {400, 413}:
        text = str(exc).lower()
        if any(phrase in text for phrase in _CONTEXT_PHRASES):
            return ProviderFailure(CONTEXT_LIMIT, status)
        if status == 413:
            return ProviderFailure(CONTEXT_LIMIT, status)
    if (
        status in _RETRYABLE_STATUS
        or names & _RETRYABLE_CLASS_NAMES
        or isinstance(exc, (asyncio.TimeoutError, TimeoutError, ConnectionError))
    ):
        return ProviderFailure(RETRYABLE, status, retry_after)
    if status is None and _RETRYABLE_TEXT.search(str(exc)):
        return ProviderFailure(RETRYABLE)
    return ProviderFailure(OTHER, status)
