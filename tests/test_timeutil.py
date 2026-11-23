"""Unit tests for the pure time helpers.

timeutil has no Home Assistant imports, so we load it directly from the
component directory — this keeps the tests runnable with a bare `pytest` and
no HA test harness installed.
"""
import importlib.util
from datetime import datetime, timezone
from pathlib import Path

_MODULE_PATH = (
    Path(__file__).resolve().parent.parent
    / "custom_components"
    / "babytracker"
    / "timeutil.py"
)
_spec = importlib.util.spec_from_file_location("bt_timeutil", _MODULE_PATH)
timeutil = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(timeutil)


def _utc(y, mo, d, h=0, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc)


class TestParseIso:
    def test_none_and_empty(self):
        assert timeutil.parse_iso(None) is None
        assert timeutil.parse_iso("") is None

    def test_z_suffix(self):
        got = timeutil.parse_iso("2026-07-07T10:30:00Z")
        assert got == _utc(2026, 7, 7, 10, 30)

    def test_explicit_offset(self):
        got = timeutil.parse_iso("2026-07-07T12:30:00+02:00")
        assert got.astimezone(timezone.utc) == _utc(2026, 7, 7, 10, 30)

    def test_garbage_returns_none(self):
        assert timeutil.parse_iso("not-a-date") is None


class TestDurationMinutes:
    def test_basic(self):
        item = {"start": "2026-07-07T10:00:00Z", "end": "2026-07-07T10:45:00Z"}
        assert timeutil.duration_minutes(item) == 45

    def test_missing_fields(self):
        assert timeutil.duration_minutes({"start": "2026-07-07T10:00:00Z"}) == 0
        assert timeutil.duration_minutes({}) == 0

    def test_never_negative(self):
        # end before start (clock skew / bad data) clamps to 0
        item = {"start": "2026-07-07T10:00:00Z", "end": "2026-07-07T09:00:00Z"}
        assert timeutil.duration_minutes(item) == 0

    def test_custom_fields(self):
        item = {"begin": "2026-07-07T10:00:00Z", "finish": "2026-07-07T11:30:00Z"}
        assert timeutil.duration_minutes(item, "begin", "finish") == 90


class TestOverlapMinutes:
    WIN_START = _utc(2026, 7, 7, 0, 0)
    WIN_END = _utc(2026, 7, 8, 0, 0)

    def test_fully_inside(self):
        item = {"start": "2026-07-07T02:00:00Z", "end": "2026-07-07T05:00:00Z"}
        assert timeutil.overlap_minutes(item, self.WIN_START, self.WIN_END) == 180

    def test_starts_before_window(self):
        # Overnight sleep started the previous evening, ends 1h into today.
        item = {"start": "2026-07-06T22:00:00Z", "end": "2026-07-07T01:00:00Z"}
        assert timeutil.overlap_minutes(item, self.WIN_START, self.WIN_END) == 60

    def test_ends_after_window(self):
        item = {"start": "2026-07-07T23:00:00Z", "end": "2026-07-08T03:00:00Z"}
        assert timeutil.overlap_minutes(item, self.WIN_START, self.WIN_END) == 60

    def test_entirely_outside(self):
        item = {"start": "2026-07-08T01:00:00Z", "end": "2026-07-08T02:00:00Z"}
        assert timeutil.overlap_minutes(item, self.WIN_START, self.WIN_END) == 0

    def test_ongoing_entry_clipped_to_window_end(self):
        # No end field → treated as running to the window's upper bound.
        item = {"start": "2026-07-07T22:00:00Z"}
        assert timeutil.overlap_minutes(item, self.WIN_START, self.WIN_END) == 120

    def test_missing_start(self):
        assert timeutil.overlap_minutes({}, self.WIN_START, self.WIN_END) == 0


class TestOverlapMinutesWithPauses:
    def test_entry_inside_window_counts_its_net_duration(self):
        # 2h span with 30 paused minutes -> 90 minutes of sleep.
        item = {
            "start": "2026-10-22T10:00:00Z",
            "end": "2026-10-22T12:00:00Z",
            "paused_seconds": 1800,
        }
        assert timeutil.overlap_minutes(item, _utc(2026, 10, 22), _utc(2026, 10, 23)) == 90

    def test_straddling_entry_is_scaled_by_net_over_gross(self):
        # 22:00-02:00 with 1h paused: net is 3/4 of the span, and the two
        # hours that fall in the window count as 90 minutes.
        item = {
            "start": "2026-10-21T22:00:00Z",
            "end": "2026-10-22T02:00:00Z",
            "paused_seconds": 3600,
        }
        assert timeutil.overlap_minutes(item, _utc(2026, 10, 22), _utc(2026, 10, 23)) == 90

    def test_missing_zero_or_bad_paused_seconds_changes_nothing(self):
        base = {"start": "2026-10-22T10:00:00Z", "end": "2026-10-22T11:00:00Z"}
        window = (_utc(2026, 10, 22), _utc(2026, 10, 23))
        assert timeutil.overlap_minutes(base, *window) == 60
        assert timeutil.overlap_minutes({**base, "paused_seconds": 0}, *window) == 60
        assert timeutil.overlap_minutes({**base, "paused_seconds": None}, *window) == 60
        assert timeutil.overlap_minutes({**base, "paused_seconds": "x"}, *window) == 60

    def test_paused_longer_than_the_span_never_goes_negative(self):
        item = {
            "start": "2026-10-22T10:00:00Z",
            "end": "2026-10-22T11:00:00Z",
            "paused_seconds": 99999,
        }
        assert timeutil.overlap_minutes(item, _utc(2026, 10, 22), _utc(2026, 10, 23)) == 0


class TestTimerElapsedSeconds:
    now = _utc(2026, 10, 22, 12, 0)

    def test_no_pauses_is_now_minus_start(self):
        # Servers older than 1.11 send no `pauses` key at all.
        assert timeutil.timer_elapsed_seconds({"start": "2026-10-22T11:00:00Z"}, self.now) == 3600
        assert timeutil.timer_elapsed_seconds(
            {"start": "2026-10-22T11:00:00Z", "pauses": []}, self.now
        ) == 3600

    def test_closed_pauses_are_subtracted(self):
        timer = {
            "start": "2026-10-22T11:00:00Z",
            "pauses": [
                {"start": "2026-10-22T11:10:00Z", "end": "2026-10-22T11:20:00Z"},
                {"start": "2026-10-22T11:30:00Z", "end": "2026-10-22T11:35:00Z"},
            ],
        }
        assert timeutil.timer_elapsed_seconds(timer, self.now) == 45 * 60

    def test_open_pause_freezes_the_clock(self):
        timer = {
            "start": "2026-10-22T11:00:00Z",
            "is_paused": True,
            "pauses": [{"start": "2026-10-22T11:15:00Z", "end": None}],
        }
        assert timeutil.timer_elapsed_seconds(timer, self.now) == 15 * 60
        later = _utc(2026, 10, 22, 13, 0)
        assert timeutil.timer_elapsed_seconds(timer, later) == 15 * 60

    def test_fractional_seconds_and_offsets_parse(self):
        # The server stores pauses as JSON with microseconds and an offset.
        timer = {
            "start": "2026-10-22T11:00:00.123456+00:00",
            "pauses": [
                {"start": "2026-10-22T13:10:00.5+02:00", "end": "2026-10-22T13:20:00.5+02:00"},
            ],
        }
        assert timeutil.timer_elapsed_seconds(timer, self.now) == 3600 - 600 - 1

    def test_bad_input_is_tolerated(self):
        assert timeutil.timer_elapsed_seconds({}, self.now) == 0
        assert timeutil.timer_elapsed_seconds({"start": "nope"}, self.now) == 0
        timer = {
            "start": "2026-10-22T11:00:00Z",
            "pauses": [None, {"start": None}, {"start": "2026-10-22T10:00:00Z", "end": "2026-10-22T14:00:00Z"}],
        }
        # The malformed pause is clipped to the timer's lifetime, not beyond.
        assert timeutil.timer_elapsed_seconds(timer, self.now) == 0

    def test_start_in_the_future_is_zero(self):
        assert timeutil.timer_elapsed_seconds({"start": "2026-10-22T13:00:00Z"}, self.now) == 0


class TestIsNap:
    def test_short_daytime_sleep_is_a_nap(self):
        assert timeutil.is_nap(datetime(2026, 10, 22, 13, 0), 90) is True
        assert timeutil.is_nap(datetime(2026, 10, 22, 7, 0), 30) is True

    def test_night_and_evening_starts_are_not(self):
        assert timeutil.is_nap(datetime(2026, 10, 22, 19, 0), 60) is False
        assert timeutil.is_nap(datetime(2026, 10, 22, 6, 59), 60) is False
        assert timeutil.is_nap(datetime(2026, 10, 22, 2, 0), 45) is False

    def test_long_daytime_sleep_is_not(self):
        assert timeutil.is_nap(datetime(2026, 10, 22, 8, 0), 240) is False
        assert timeutil.is_nap(datetime(2026, 10, 22, 8, 0), 239) is True
