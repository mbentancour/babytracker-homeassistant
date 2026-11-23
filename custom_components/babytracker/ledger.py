"""Remembers which entries and timer changes an HA event was already fired for.

Events reach the integration two ways: BabyTracker pushes them to the webhook,
and the coordinator's poll notices anything new as a fallback. Both used to
fire on their own, so with a working webhook every event went out twice — once
from the webhook, then again when the refresh it triggers saw the new entry.

Both paths now ask this ledger first. Whichever sees something first fires and
records it; the other finds it recorded and stays quiet.

Free of Home Assistant imports so it can be unit-tested standalone (see
tests/test_ledger.py), like timeutil.
"""
from __future__ import annotations

# Webhook-announced entries the poll has not listed yet. Normally one refresh
# clears them; an entry logged far in the past may never show up in the poll's
# most-recent-N window, so the list is capped instead of growing forever.
_MAX_PENDING = 200


def _pause_seq(timer: dict) -> int:
    """A number that only ever grows over a timer's life: each pause adds an
    entry to ``pauses`` and each resume closes it. Lets a late or repeated
    report be told apart from a new change. Servers older than 1.11 send
    neither field, which gives a constant 0 — no pause events, as expected.
    """
    pauses = timer.get("pauses") or []
    return 2 * len(pauses) - (1 if timer.get("is_paused") else 0)


class EventLedger:
    """What has already been announced on the HA event bus."""

    def __init__(self) -> None:
        # (child_id, kind) -> entry IDs in the most recent poll.
        self._entries: dict[tuple, set] = {}
        # (child_id, kind) -> IDs a webhook announced, not yet seen in a poll.
        # A dict for its insertion order, so the oldest can be dropped.
        self._pending: dict[tuple, dict] = {}

        self._timers_polled = False
        # Timer IDs in the most recent poll.
        self._timers: set = set()
        # Announced as started by a webhook, not yet seen in a poll.
        self._started: set = set()
        # Announced as stopped by a webhook. Kept until a poll confirms the
        # timer is gone, so a poll that was already in flight and still lists
        # it doesn't announce it as started again.
        self._stopped: set = set()
        # Timer ID -> (pause sequence, is_paused) last announced or seen.
        self._pause: dict = {}

    # ---- entries -------------------------------------------------------

    def claim_entry(self, child_id, kind: str, entry_id) -> bool:
        """Webhook side. True if no event has gone out for this entry yet."""
        if entry_id is None:
            return True  # nothing to match on; better twice than never
        key = (child_id, kind)
        if entry_id in self._entries.get(key, ()):
            return False
        pending = self._pending.setdefault(key, {})
        if entry_id in pending:
            return False
        pending[entry_id] = None
        while len(pending) > _MAX_PENDING:
            del pending[next(iter(pending))]
        return True

    def poll_entries(self, child_id, kind: str, entry_ids: list) -> set:
        """Poll side. Record the current list and return the IDs that are new
        and not already announced. The first list seen for a child and kind is
        the baseline: nothing in it is new, so setting up the integration (or
        adding a child later) doesn't replay history as events.
        """
        key = (child_id, kind)
        current = {i for i in entry_ids if i is not None}
        previous = self._entries.get(key)
        pending = self._pending.get(key, {})
        if previous is None:
            new: set = set()
        else:
            new = {i for i in current if i not in previous and i not in pending}
        for i in current:
            pending.pop(i, None)
        self._entries[key] = current
        return new

    # ---- timers --------------------------------------------------------

    def claim_timer_started(self, timer: dict) -> bool:
        tid = timer.get("id")
        if tid is None:
            return True
        if tid in self._timers or tid in self._started:
            return False
        self._started.add(tid)
        self._pause[tid] = (_pause_seq(timer), bool(timer.get("is_paused")))
        return True

    def claim_timer_stopped(self, timer: dict) -> bool:
        tid = timer.get("id")
        if tid is None:
            return True
        if tid in self._stopped:
            return False
        self._timers.discard(tid)
        self._started.discard(tid)
        self._pause.pop(tid, None)
        self._stopped.add(tid)
        return True

    def claim_timer_pause_change(self, timer: dict) -> bool:
        """Webhook side, for timer.paused and timer.resumed. True unless this
        change (or a later one) was already announced."""
        tid = timer.get("id")
        if tid is None:
            return True
        seq = _pause_seq(timer)
        known = self._pause.get(tid)
        if known is not None and seq <= known[0]:
            return False
        self._pause[tid] = (seq, bool(timer.get("is_paused")))
        return True

    def poll_timers(self, timers: list[dict]) -> dict:
        """Poll side. Record the current timers and return what changed since
        the last poll and was not already announced::

            {"started": [timer, ...], "stopped": [timer_id, ...],
             "paused": [timer, ...], "resumed": [timer, ...]}

        The first poll is the baseline and reports nothing.
        """
        current = {t["id"]: t for t in timers if t.get("id") is not None}
        changes: dict = {"started": [], "stopped": [], "paused": [], "resumed": []}

        if self._timers_polled:
            for tid, t in current.items():
                if tid in self._stopped:
                    continue  # stale listing of a timer already announced stopped
                if tid not in self._timers and tid not in self._started:
                    changes["started"].append(t)
                    continue
                seq, paused = _pause_seq(t), bool(t.get("is_paused"))
                known = self._pause.get(tid)
                if known is not None and seq > known[0] and paused != known[1]:
                    changes["paused" if paused else "resumed"].append(t)
            changes["stopped"] = sorted(tid for tid in self._timers if tid not in current)

        for tid, t in current.items():
            if tid in self._stopped:
                continue
            seq = _pause_seq(t)
            known = self._pause.get(tid)
            if known is None or seq > known[0]:
                self._pause[tid] = (seq, bool(t.get("is_paused")))
        self._timers = {tid for tid in current if tid not in self._stopped}
        self._started -= set(current)
        self._stopped &= set(current)
        for tid in list(self._pause):
            if tid not in self._timers and tid not in self._started:
                del self._pause[tid]
        self._timers_polled = True
        return changes
