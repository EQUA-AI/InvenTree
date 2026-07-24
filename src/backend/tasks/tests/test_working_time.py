"""Direct unit tests for the working-time helpers (S6, plan §8.5a/§8.5c).

Run under ``USE_TZ=True`` because this project sets ``USE_TZ = not TESTING`` and
the whole point of these helpers is timezone/DST correctness — the default naive
test environment would let real bugs pass.
"""

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from django.test import SimpleTestCase, override_settings

from tasks.services.working_time import (
    CalendarSpec,
    NoWorkingTime,
    add_working_minutes,
    is_working_instant,
    next_working_instant,
    subtract_working_minutes,
    working_minutes_between,
)

MON_FRI_9_5 = CalendarSpec(
    tzname='UTC',
    windows={d: ((time(9, 0), time(17, 0)),) for d in range(5)},
    holidays=frozenset(),
)


def _dt(y, m, d, h=0, mi=0, tz='UTC'):
    return datetime(y, m, d, h, mi, tzinfo=ZoneInfo(tz))


@override_settings(USE_TZ=True)
class IsWorkingInstantTest(SimpleTestCase):
    def test_inside_window_is_working(self):
        # 2026-08-03 is a Monday.
        self.assertTrue(is_working_instant(MON_FRI_9_5, _dt(2026, 8, 3, 10)))

    def test_before_open_is_not_working(self):
        self.assertFalse(is_working_instant(MON_FRI_9_5, _dt(2026, 8, 3, 8)))

    def test_close_of_business_is_exclusive(self):
        self.assertFalse(is_working_instant(MON_FRI_9_5, _dt(2026, 8, 3, 17)))

    def test_weekend_is_not_working(self):
        # 2026-08-08 is a Saturday.
        self.assertFalse(is_working_instant(MON_FRI_9_5, _dt(2026, 8, 8, 10)))

    def test_holiday_is_not_working(self):
        spec = CalendarSpec(
            tzname='UTC',
            windows=MON_FRI_9_5.windows,
            holidays=frozenset({date(2026, 8, 3)}),
        )
        self.assertFalse(is_working_instant(spec, _dt(2026, 8, 3, 10)))


@override_settings(USE_TZ=True)
class NextWorkingInstantTest(SimpleTestCase):
    def test_returns_same_instant_when_already_working(self):
        instant = _dt(2026, 8, 3, 10)
        self.assertEqual(next_working_instant(MON_FRI_9_5, instant), instant)

    def test_advances_to_open_when_before_hours(self):
        self.assertEqual(
            next_working_instant(MON_FRI_9_5, _dt(2026, 8, 3, 6)),
            _dt(2026, 8, 3, 9),
        )

    def test_skips_weekend_to_monday(self):
        # Saturday 10:00 -> Monday 09:00.
        self.assertEqual(
            next_working_instant(MON_FRI_9_5, _dt(2026, 8, 8, 10)),
            _dt(2026, 8, 10, 9),
        )

    def test_after_close_advances_to_next_day(self):
        self.assertEqual(
            next_working_instant(MON_FRI_9_5, _dt(2026, 8, 3, 18)),
            _dt(2026, 8, 4, 9),
        )

    def test_no_working_time_raises(self):
        empty = CalendarSpec(tzname='UTC', windows={}, holidays=frozenset())
        with self.assertRaises(NoWorkingTime):
            next_working_instant(empty, _dt(2026, 8, 3, 10))


@override_settings(USE_TZ=True)
class WorkingMinutesBetweenTest(SimpleTestCase):
    def test_within_one_window(self):
        self.assertEqual(
            working_minutes_between(
                MON_FRI_9_5, _dt(2026, 8, 3, 9), _dt(2026, 8, 3, 12)
            ),
            180.0,
        )

    def test_clips_to_window_bounds(self):
        # 06:00 -> 20:00 on a Monday counts only 09:00–17:00 = 8h.
        self.assertEqual(
            working_minutes_between(
                MON_FRI_9_5, _dt(2026, 8, 3, 6), _dt(2026, 8, 3, 20)
            ),
            480.0,
        )

    def test_spans_weekend_counts_only_weekdays(self):
        # Fri 16:00 -> Mon 10:00: 1h Fri + 1h Mon = 120 min.
        self.assertEqual(
            working_minutes_between(
                MON_FRI_9_5, _dt(2026, 8, 7, 16), _dt(2026, 8, 10, 10)
            ),
            120.0,
        )

    def test_zero_when_end_before_start(self):
        self.assertEqual(
            working_minutes_between(
                MON_FRI_9_5, _dt(2026, 8, 3, 12), _dt(2026, 8, 3, 9)
            ),
            0.0,
        )

    def test_non_working_span_is_zero(self):
        # Entirely within a weekend.
        self.assertEqual(
            working_minutes_between(
                MON_FRI_9_5, _dt(2026, 8, 8, 9), _dt(2026, 8, 9, 17)
            ),
            0.0,
        )


@override_settings(USE_TZ=True)
class AddWorkingMinutesTest(SimpleTestCase):
    def test_within_one_window(self):
        self.assertEqual(
            add_working_minutes(MON_FRI_9_5, _dt(2026, 8, 3, 9), 120),
            _dt(2026, 8, 3, 11),
        )

    def test_rolls_over_to_next_day(self):
        # Start 16:00 Monday, add 2h: 1h Monday + 1h Tuesday -> Tue 10:00.
        self.assertEqual(
            add_working_minutes(MON_FRI_9_5, _dt(2026, 8, 3, 16), 120),
            _dt(2026, 8, 4, 10),
        )

    def test_start_before_hours_begins_at_open(self):
        # 06:00 Monday + 1h -> 10:00 Monday (consumed inside the window).
        self.assertEqual(
            add_working_minutes(MON_FRI_9_5, _dt(2026, 8, 3, 6), 60),
            _dt(2026, 8, 3, 10),
        )

    def test_crosses_weekend(self):
        # Fri 16:00 + 2h: 1h Fri + 1h Mon -> Mon 10:00.
        self.assertEqual(
            add_working_minutes(MON_FRI_9_5, _dt(2026, 8, 7, 16), 120),
            _dt(2026, 8, 10, 10),
        )

    def test_zero_budget_returns_start(self):
        start = _dt(2026, 8, 3, 13)
        self.assertEqual(add_working_minutes(MON_FRI_9_5, start, 0), start)

    def test_full_eight_hour_day(self):
        self.assertEqual(
            add_working_minutes(MON_FRI_9_5, _dt(2026, 8, 3, 9), 480),
            _dt(2026, 8, 3, 17),
        )


@override_settings(USE_TZ=True)
class SubtractWorkingMinutesTest(SimpleTestCase):
    def test_within_one_window(self):
        self.assertEqual(
            subtract_working_minutes(MON_FRI_9_5, _dt(2026, 8, 3, 15), 120),
            _dt(2026, 8, 3, 13),
        )

    def test_rolls_back_over_a_day(self):
        # End Tue 10:00, back 2h: 1h Tue + 1h Mon -> Mon 16:00.
        self.assertEqual(
            subtract_working_minutes(MON_FRI_9_5, _dt(2026, 8, 4, 10), 120),
            _dt(2026, 8, 3, 16),
        )

    def test_rolls_back_over_a_weekend(self):
        # End Mon 10:00, back 2h: 1h Mon + 1h Fri -> Fri 16:00.
        self.assertEqual(
            subtract_working_minutes(MON_FRI_9_5, _dt(2026, 8, 10, 10), 120),
            _dt(2026, 8, 7, 16),
        )

    def test_is_the_inverse_of_add(self):
        start = _dt(2026, 8, 3, 10)
        end = add_working_minutes(MON_FRI_9_5, start, 300)
        self.assertEqual(subtract_working_minutes(MON_FRI_9_5, end, 300), start)

    def test_zero_budget_returns_end(self):
        end = _dt(2026, 8, 3, 13)
        self.assertEqual(subtract_working_minutes(MON_FRI_9_5, end, 0), end)


@override_settings(USE_TZ=True)
class TimezoneAndDstTest(SimpleTestCase):
    """The reason these helpers exist: two zones differ, and DST shifts real time."""

    def test_same_shift_two_zones_differ_in_utc(self):
        ny = CalendarSpec(
            tzname='America/New_York',
            windows={d: ((time(9, 0), time(17, 0)),) for d in range(5)},
            holidays=frozenset(),
        )
        la = CalendarSpec(
            tzname='America/Los_Angeles',
            windows={d: ((time(9, 0), time(17, 0)),) for d in range(5)},
            holidays=frozenset(),
        )
        # 09:00 local is a different UTC instant in each zone (3h apart in summer).
        ny_open = next_working_instant(ny, _dt(2026, 8, 3, 0, tz='America/New_York'))
        la_open = next_working_instant(la, _dt(2026, 8, 3, 0, tz='America/Los_Angeles'))
        self.assertNotEqual(
            ny_open.astimezone(ZoneInfo('UTC')),
            la_open.astimezone(ZoneInfo('UTC')),
        )

    def test_night_shift_across_spring_forward_loses_an_hour(self):
        # US spring-forward 2026: 2026-03-08, clocks jump 02:00 -> 03:00.
        # A 22:00 -> 06:00 night shift over that night is 7 real hours, not 8.
        night = CalendarSpec(
            tzname='America/New_York',
            # Saturday night shift spanning into Sunday morning.
            windows={
                5: ((time(22, 0), time(23, 59)),),
                6: ((time(0, 0), time(6, 0)),),
            },
            holidays=frozenset(),
        )
        start = datetime(2026, 3, 7, 22, 0, tzinfo=ZoneInfo('America/New_York'))
        end = datetime(2026, 3, 8, 6, 0, tzinfo=ZoneInfo('America/New_York'))
        minutes = working_minutes_between(night, start, end)
        # 22:00–23:59 (119) + 00:00–06:00 which loses the 02:00 hour to DST
        # (real 5h = 300) => 419 minutes, i.e. one hour short of a naive 8h.
        self.assertAlmostEqual(minutes, 419.0, delta=1.0)

    def test_add_minutes_is_dst_aware(self):
        ny = CalendarSpec(
            tzname='America/New_York',
            windows={d: ((time(0, 0), time(23, 59)),) for d in range(7)},
            holidays=frozenset(),
        )
        # Starting before spring-forward, adding real minutes advances real time;
        # the wall clock reflects the lost hour.
        start = datetime(2026, 3, 8, 1, 30, tzinfo=ZoneInfo('America/New_York'))
        result = add_working_minutes(ny, start, 60)
        # 60 real minutes from 01:30 lands at 03:30 local (02:00–03:00 skipped).
        self.assertEqual(result.astimezone(ZoneInfo('America/New_York')).hour, 3)
        self.assertEqual(result.astimezone(ZoneInfo('America/New_York')).minute, 30)


@override_settings(USE_TZ=True)
class SplitShiftTest(SimpleTestCase):
    def test_split_shift_skips_the_lunch_gap(self):
        split = CalendarSpec(
            tzname='UTC',
            windows={
                0: ((time(9, 0), time(12, 0)), (time(13, 0), time(17, 0))),
            },
            holidays=frozenset(),
        )
        # 09:00 + 4h: 3h morning + 1h afternoon (lunch skipped) -> 14:00.
        self.assertEqual(
            add_working_minutes(split, _dt(2026, 8, 3, 9), 240),
            _dt(2026, 8, 3, 14),
        )
        # Noon–13:00 lunch is not working time.
        self.assertFalse(is_working_instant(split, _dt(2026, 8, 3, 12, 30)))
