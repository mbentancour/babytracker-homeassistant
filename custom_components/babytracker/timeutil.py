"""Pure time helpers for the BabyTracker coordinator.

Kept free of any Home Assistant imports so the parsing/duration/overlap logic
that drives the daily-total sensors can be unit-tested standalone (see
tests/test_timeutil.py) without spinning up a HA test harness.
"""
from __future__ import annotations

from datetime import datetime


def parse_iso(value: str | None) -> datetime | None:
    """Parse an RFC3339 timestamp, tolerating a trailing 'Z'. Returns None on
    empty or unparseable input."""
    if not value:
        return None
    try:
        # Backend emits RFC3339; Python 3.11+ handles "Z" via the replace.
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def duration_minutes(item: dict, start_field: str = "start", end_field: str = "end") -> int:
    """Whole minutes between an item's start and end fields; 0 if either is
    missing or unparseable, never negative."""
    start = parse_iso(item.get(start_field))
    end = parse_iso(item.get(end_field))
    if not start or not end:
        return 0
    return max(0, int((end - start).total_seconds() // 60))


def overlap_minutes(item: dict, window_start: datetime, window_end: datetime) -> int:
    """Return how many minutes of an entry's [start, end] fall within
    [window_start, window_end]. Used by today-totals so an overnight sleep
    that straddles the day boundary contributes only the portion that's
    actually in today, not its whole duration (counted in full when start ∈
    window) or zero (counted as nothing when start ∉ window). Ongoing
    entries with no end are clipped to the window's upper bound.
    """
    start = parse_iso(item.get("start"))
    if not start:
        return 0
    end = parse_iso(item.get("end")) or window_end
    overlap_start = max(start, window_start)
    overlap_end = min(end, window_end)
    if overlap_end <= overlap_start:
        return 0
    return int((overlap_end - overlap_start).total_seconds() // 60)
