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

    An entry saved from a paused timer keeps its real end and carries the
    time it spent paused in ``paused_seconds`` (BabyTracker 1.11+), so the
    raw span overstates it. The individual pauses aren't on the entry, so the
    paused time is taken as spread evenly over the span and the overlap is
    scaled by net/gross — the same rule the BabyTracker web UI uses. An entry
    wholly inside the window contributes exactly its net duration.
    """
    start = parse_iso(item.get("start"))
    if not start:
        return 0
    end = parse_iso(item.get("end")) or window_end
    overlap_start = max(start, window_start)
    overlap_end = min(end, window_end)
    if overlap_end <= overlap_start:
        return 0
    overlap = (overlap_end - overlap_start).total_seconds()
    span = (end - start).total_seconds()
    try:
        paused = float(item.get("paused_seconds") or 0)
    except (TypeError, ValueError):
        paused = 0
    if span > 0 and paused > 0:
        overlap *= max(0.0, span - paused) / span
    return int(overlap // 60)


def timer_elapsed_seconds(timer: dict, now: datetime) -> int:
    """Seconds a timer has actually been running: time since its start minus
    every pause, including one that is still open. Timers can be paused from
    BabyTracker 1.11; older servers send no ``pauses`` and this is simply
    now - start. Never negative.
    """
    start = parse_iso(timer.get("start"))
    if not start:
        return 0
    if start.tzinfo is None:
        start = start.replace(tzinfo=now.tzinfo)
    elapsed = (now - start).total_seconds()
    for pause in timer.get("pauses") or []:
        if not isinstance(pause, dict):
            continue
        p_start = parse_iso(pause.get("start"))
        if not p_start:
            continue
        p_end = parse_iso(pause.get("end")) or now
        if p_start.tzinfo is None:
            p_start = p_start.replace(tzinfo=now.tzinfo)
        if p_end.tzinfo is None:
            p_end = p_end.replace(tzinfo=now.tzinfo)
        # Clip to the timer's own lifetime so a malformed pause can't
        # subtract more than the time it could have covered.
        p_start = max(p_start, start)
        p_end = min(p_end, now)
        if p_end > p_start:
            elapsed -= (p_end - p_start).total_seconds()
    return max(0, int(elapsed))


def is_nap(local_start: datetime, minutes: float) -> bool:
    """Classify a sleep as a nap when nobody said which it was: it starts
    between 07:00 and 19:00 and lasts under four hours. ``local_start`` must
    already be in the household's timezone. Same rule BabyTracker's importers
    use for sources that don't record naps.
    """
    return 7 <= local_start.hour < 19 and minutes < 4 * 60
