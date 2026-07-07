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
