"""Cron and interval schedule parsing and next-firing computation with fixed datetimes.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from nerdvana_cli.cli.cron import CronSchedule, IntervalSchedule, ScheduleError, parse_schedule


def _cron(expression: str) -> CronSchedule:
    schedule = parse_schedule(expression)
    assert isinstance(schedule, CronSchedule)
    return schedule


def _next(expression: str, after: datetime) -> datetime:
    return _cron(expression).next_after(after)


class TestFieldParsing:
    def test_star_covers_the_whole_range(self) -> None:
        schedule = _cron("* * * * *")
        assert schedule.minutes  == frozenset(range(60))
        assert schedule.hours    == frozenset(range(24))
        assert schedule.days     == frozenset(range(1, 32))
        assert schedule.months   == frozenset(range(1, 13))
        assert schedule.weekdays == frozenset(range(7))

    def test_single_number(self) -> None:
        assert _cron("5 * * * *").minutes == frozenset({5})

    def test_range_is_inclusive(self) -> None:
        assert _cron("10-13 * * * *").minutes == frozenset({10, 11, 12, 13})

    def test_step_over_star(self) -> None:
        assert _cron("*/20 * * * *").minutes == frozenset({0, 20, 40})

    def test_step_over_range(self) -> None:
        assert _cron("10-30/10 * * * *").minutes == frozenset({10, 20, 30})

    def test_step_from_a_start_runs_to_the_maximum(self) -> None:
        assert _cron("50/5 * * * *").minutes == frozenset({50, 55})

    def test_list_of_numbers_and_ranges(self) -> None:
        assert _cron("1,5-7,30 * * * *").minutes == frozenset({1, 5, 6, 7, 30})

    def test_list_of_steps(self) -> None:
        assert _cron("0-10/5,50 * * * *").minutes == frozenset({0, 5, 10, 50})

    def test_hour_day_and_month_bounds(self) -> None:
        schedule = _cron("0 0-23/12 31 12 *")
        assert schedule.hours  == frozenset({0, 12})
        assert schedule.days   == frozenset({31})
        assert schedule.months == frozenset({12})

    def test_sunday_is_both_zero_and_seven(self) -> None:
        assert _cron("0 0 * * 0").weekdays == _cron("0 0 * * 7").weekdays == frozenset({0})

    def test_weekday_range_through_seven(self) -> None:
        assert _cron("0 0 * * 5-7").weekdays == frozenset({5, 6, 0})

    def test_weekday_star_step_stops_at_saturday(self) -> None:
        assert _cron("0 0 * * */2").weekdays == frozenset({0, 2, 4, 6})

    def test_weekday_step_from_a_start_stops_at_saturday(self) -> None:
        assert _cron("0 0 * * 4/2").weekdays == frozenset({4, 6})

    def test_extra_whitespace_between_fields_is_fine(self) -> None:
        assert _cron("  5   4  *  *   * ").minutes == frozenset({5})

    @pytest.mark.parametrize("expression", [
        "",
        "   ",
        "* * * *",
        "* * * * * *",
        "60 * * * *",
        "* 24 * * *",
        "* * 0 * *",
        "* * 32 * *",
        "* * * 0 *",
        "* * * 13 *",
        "* * * * 8",
        "*/0 * * * *",
        "*/-1 * * * *",
        "*/ * * * *",
        "5-3 * * * *",
        "1-2-3 * * * *",
        "-5 * * * *",
        "a * * * *",
        "1,,2 * * * *",
        "1, * * * *",
        ", * * * *",
        "*-5 * * * *",
        "mon * * * *",
        "* * * jan *",
        "* * * * mon",
        "@daily",
        "5.5 * * * *",
        "٣ * * * *",
        "0-60 * * * *",
        "0 0 * * 1-8",
    ])
    def test_malformed_expressions_are_rejected(self, expression: str) -> None:
        with pytest.raises(ScheduleError):
            parse_schedule(expression)

    def test_the_error_names_the_field(self) -> None:
        with pytest.raises(ScheduleError, match="hour"):
            parse_schedule("0 25 * * *")

    @pytest.mark.parametrize("expression", ["0 0 30 2 *", "0 0 31 4 *", "0 0 31 6,9,11 *"])
    def test_a_date_that_never_exists_is_rejected(self, expression: str) -> None:
        with pytest.raises(ScheduleError, match="never fires"):
            parse_schedule(expression)


class TestNextFiring:
    def test_every_minute(self) -> None:
        assert _next("* * * * *", datetime(2026, 1, 5, 12, 0, 30)) == datetime(2026, 1, 5, 12, 1)

    def test_next_is_strictly_after_an_exact_minute(self) -> None:
        assert _next("* * * * *", datetime(2026, 1, 5, 12, 0)) == datetime(2026, 1, 5, 12, 1)

    def test_seconds_and_microseconds_are_dropped(self) -> None:
        assert _next("30 12 * * *", datetime(2026, 1, 5, 12, 29, 59, 999999)) == datetime(2026, 1, 5, 12, 30)

    def test_quarter_hours(self) -> None:
        assert _next("*/15 * * * *", datetime(2026, 1, 5, 12, 7)) == datetime(2026, 1, 5, 12, 15)
        assert _next("*/15 * * * *", datetime(2026, 1, 5, 12, 45)) == datetime(2026, 1, 5, 13, 0)

    def test_rolls_over_midnight(self) -> None:
        assert _next("*/15 * * * *", datetime(2026, 1, 5, 23, 50)) == datetime(2026, 1, 6, 0, 0)

    def test_daily_at_three(self) -> None:
        assert _next("0 3 * * *", datetime(2026, 1, 5, 2, 59)) == datetime(2026, 1, 5, 3, 0)
        assert _next("0 3 * * *", datetime(2026, 1, 5, 3, 0)) == datetime(2026, 1, 6, 3, 0)

    def test_hour_list(self) -> None:
        assert _next("15 6,18 * * *", datetime(2026, 1, 5, 7, 0)) == datetime(2026, 1, 5, 18, 15)

    def test_monthly_on_the_first(self) -> None:
        assert _next("30 4 1 * *", datetime(2026, 1, 15, 0, 0)) == datetime(2026, 2, 1, 4, 30)

    def test_year_rollover(self) -> None:
        assert _next("30 4 1 * *", datetime(2026, 12, 15, 0, 0)) == datetime(2027, 1, 1, 4, 30)

    def test_month_restriction(self) -> None:
        assert _next("0 0 1 6 *", datetime(2026, 7, 1, 0, 0)) == datetime(2027, 6, 1, 0, 0)

    def test_day_thirty_one_skips_short_months(self) -> None:
        assert _next("0 0 31 * *", datetime(2026, 1, 31, 0, 0)) == datetime(2026, 3, 31, 0, 0)

    def test_leap_day(self) -> None:
        assert _next("0 0 29 2 *", datetime(2025, 3, 1, 0, 0)) == datetime(2028, 2, 29, 0, 0)

    def test_weekday_range_skips_the_weekend(self) -> None:
        friday_evening = datetime(2026, 1, 2, 17, 0)
        assert _next("0 9 * * 1-5", friday_evening) == datetime(2026, 1, 5, 9, 0)

    def test_sunday_as_zero_and_as_seven(self) -> None:
        assert _next("0 9 * * 0", datetime(2026, 1, 1, 0, 0)) == datetime(2026, 1, 4, 9, 0)
        assert _next("0 9 * * 7", datetime(2026, 1, 1, 0, 0)) == datetime(2026, 1, 4, 9, 0)

    def test_weekday_step(self) -> None:
        assert _next("0 0 * * */2", datetime(2026, 1, 5, 0, 0)) == datetime(2026, 1, 6, 0, 0)
        assert _next("0 0 * * */2", datetime(2026, 1, 6, 0, 0)) == datetime(2026, 1, 8, 0, 0)

    def test_restricted_day_and_weekday_match_on_either(self) -> None:
        assert _next("0 0 1 * 1", datetime(2026, 1, 2, 0, 0)) == datetime(2026, 1, 5, 0, 0)
        assert _next("0 0 1 * 1", datetime(2026, 1, 5, 0, 0)) == datetime(2026, 1, 12, 0, 0)
        assert _next("0 0 1 * 1", datetime(2026, 1, 26, 0, 0)) == datetime(2026, 2, 1, 0, 0)

    def test_a_star_day_requires_the_weekday_too(self) -> None:
        assert _next("0 0 * * 1", datetime(2026, 1, 5, 0, 0)) == datetime(2026, 1, 12, 0, 0)

    def test_a_star_step_weekday_still_restricts_a_numbered_day(self) -> None:
        # 1st of the month AND an even weekday: 2026-02-01 is a Sunday (0), so it qualifies.
        assert _next("0 0 1 * */2", datetime(2026, 1, 2, 0, 0)) == datetime(2026, 2, 1, 0, 0)

    def test_every_result_matches_the_expression(self) -> None:
        schedule = _cron("7,37 */5 10-20 3,9 1-5")
        moment   = datetime(2026, 1, 1, 0, 0)
        for _ in range(40):
            moment = schedule.next_after(moment)
            assert moment.minute in schedule.minutes
            assert moment.hour in schedule.hours
            assert moment.month in schedule.months

    def test_successive_firings_are_strictly_increasing(self) -> None:
        schedule = _cron("*/10 8-9 * * *")
        moment   = datetime(2026, 1, 5, 8, 0)
        seen     = []
        for _ in range(12):
            moment = schedule.next_after(moment)
            seen.append(moment)
        assert seen == sorted(set(seen))
        assert seen[0] == datetime(2026, 1, 5, 8, 10)
        assert seen[5] == datetime(2026, 1, 5, 9, 0)
        assert seen[-1] == datetime(2026, 1, 6, 8, 0)


class TestInterval:
    @pytest.mark.parametrize(("text", "seconds"), [
        ("every 1m", 60),
        ("every 15m", 900),
        ("every 2h", 7200),
        ("every 1d", 86400),
        ("EVERY 5M", 300),
        ("every   3 h", 10800),
        ("  every 10m  ", 600),
    ])
    def test_intervals_parse(self, text: str, seconds: int) -> None:
        schedule = parse_schedule(text)
        assert isinstance(schedule, IntervalSchedule)
        assert schedule.seconds == seconds

    @pytest.mark.parametrize("text", ["every 0m", "every m", "every 5s", "every 5", "every -5m", "every 1.5h", "every 5mm", "each 5m", "every 5 minutes"])
    def test_bad_intervals_are_rejected(self, text: str) -> None:
        with pytest.raises(ScheduleError):
            parse_schedule(text)

    def test_next_after_adds_the_interval(self) -> None:
        schedule = parse_schedule("every 90m")
        assert schedule.next_after(datetime(2026, 1, 5, 12, 0, 15)) == datetime(2026, 1, 5, 13, 30, 15)


class TestDueWindow:
    CATCH_UP = timedelta(seconds=120)

    def test_cron_is_due_when_a_firing_minute_lies_in_the_window(self) -> None:
        schedule = _cron("1 12 * * *")
        assert not schedule.due(datetime(2026, 1, 5, 12, 0, 40), datetime(2026, 1, 5, 12, 0, 55), self.CATCH_UP)
        assert schedule.due(datetime(2026, 1, 5, 12, 0, 55), datetime(2026, 1, 5, 12, 1, 10), self.CATCH_UP)

    def test_cron_is_due_exactly_on_the_boundary(self) -> None:
        assert _cron("1 12 * * *").due(datetime(2026, 1, 5, 12, 0, 59), datetime(2026, 1, 5, 12, 1, 0), self.CATCH_UP)

    def test_cron_does_not_fire_twice_for_one_minute(self) -> None:
        schedule = _cron("1 12 * * *")
        assert not schedule.due(datetime(2026, 1, 5, 12, 1, 10), datetime(2026, 1, 5, 12, 1, 25), self.CATCH_UP)

    def test_a_long_gap_is_cut_so_missed_firings_are_not_replayed(self) -> None:
        schedule = _cron("0 3 * * *")
        assert not schedule.due(datetime(2026, 1, 4, 0, 0), datetime(2026, 1, 5, 12, 0), self.CATCH_UP)

    def test_a_gap_within_the_catch_up_still_fires(self) -> None:
        schedule = _cron("0 3 * * *")
        assert schedule.due(datetime(2026, 1, 5, 2, 58), datetime(2026, 1, 5, 3, 0, 30), self.CATCH_UP)

    def test_cron_rearm_moves_to_now(self) -> None:
        now = datetime(2026, 1, 5, 12, 0)
        assert _cron("* * * * *").rearm(datetime(2026, 1, 5, 11, 0), now, fired=False) == now

    def test_interval_is_due_after_the_interval_has_passed(self) -> None:
        schedule = parse_schedule("every 10m")
        start    = datetime(2026, 1, 5, 12, 0)
        assert not schedule.due(start, start + timedelta(minutes=9, seconds=59), self.CATCH_UP)
        assert schedule.due(start, start + timedelta(minutes=10), self.CATCH_UP)

    def test_interval_is_not_cut_by_the_catch_up(self) -> None:
        schedule = parse_schedule("every 1d")
        start    = datetime(2026, 1, 5, 12, 0)
        assert schedule.due(start, start + timedelta(days=1), self.CATCH_UP)

    def test_interval_rearm_restarts_only_on_a_firing(self) -> None:
        schedule = parse_schedule("every 10m")
        start    = datetime(2026, 1, 5, 12, 0)
        later    = start + timedelta(minutes=3)
        assert schedule.rearm(start, later, fired=False) == start
        assert schedule.rearm(start, later, fired=True) == later
