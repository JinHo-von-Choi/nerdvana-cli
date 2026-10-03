"""An unexplained prompt-cache miss is counted; expected ones are not.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

from nerdvana_cli.core import signals
from nerdvana_cli.core.telemetry.cache_watch import CacheWatch
from nerdvana_cli.ui.widgets.status_bar import StatusBar


def _usage(read: int) -> dict[str, int]:
    return {"input_tokens": 1000, "output_tokens": 10, "cache_read_tokens": read}


def test_a_read_that_falls_to_zero_with_nothing_changed_is_a_miss() -> None:
    watch = CacheWatch()
    assert watch.observe("m", _usage(800), {}) is False
    assert watch.observe("m", _usage(0), {}) is True


def test_the_first_request_and_a_steady_cache_are_not_misses() -> None:
    watch = CacheWatch()
    assert [watch.observe("m", _usage(read), {}) for read in (0, 0, 900, 950)] == [False, False, False, False]


def test_compaction_masking_and_model_switches_explain_a_miss() -> None:
    for name in (signals.COMPACTION, signals.OBSERVATIONS_MASKED, signals.ESCALATED, signals.PROVIDER_FALLBACK):
        watch = CacheWatch()
        watch.observe("m", _usage(800), {})
        assert watch.observe("m", _usage(0), {name: 1}) is False, name
    watch = CacheWatch()
    watch.observe("a", _usage(800), {})
    assert watch.observe("b", _usage(0), {}) is False


def test_the_status_bar_shows_the_cache_share_only_when_there_is_one() -> None:
    shown: list[str] = []
    bar = StatusBar()
    bar.update = shown.append  # type: ignore[method-assign]
    bar.update_status(model="m", provider="p", tokens_in=1000, tokens_out=5, cache_read=800)
    bar.update_status(model="m", provider="p", tokens_in=1000, tokens_out=5, cache_read=0)
    assert "cache: 80%" in shown[0]
    assert "cache:" not in shown[1]
