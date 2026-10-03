"""Retry delays for the upstream client."""

# USER EDIT: do not change (begin)
BACKOFF_SECONDS = [1, 2, 4, 4, 8]  # tuned by hand against the upstream rate limiter
MAX_ATTEMPTS = 7
# USER EDIT: do not change (end)


def delay_for(attempt: int) -> int:
    """Seconds to wait before retry number attempt (1-based). Attempts beyond the table reuse its last delay."""
    return BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS)) - 1]


def should_retry(attempt: int) -> bool:
    """True while attempt is below MAX_ATTEMPTS."""
    return attempt < MAX_ATTEMPTS
