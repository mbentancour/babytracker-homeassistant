"""Unit tests for the event ledger.

ledger.py has no Home Assistant imports, so it is loaded straight from the
component directory, like timeutil in test_timeutil.py.
"""
import importlib.util
from pathlib import Path

_MODULE_PATH = (
    Path(__file__).resolve().parent.parent
    / "custom_components"
    / "babytracker"
    / "ledger.py"
)
_spec = importlib.util.spec_from_file_location("bt_ledger", _MODULE_PATH)
ledger_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ledger_mod)
EventLedger = ledger_mod.EventLedger


def _timer(tid, pauses=0, paused=False):
    return {"id": tid, "child": 1, "is_paused": paused, "pauses": [{}] * pauses}


class TestEntries:
    def test_first_poll_is_a_baseline(self):
        led = EventLedger()
        assert led.poll_entries(1, "feeding", [3, 2, 1]) == set()

    def test_poll_reports_only_new_ids(self):
        led = EventLedger()
        led.poll_entries(1, "feeding", [2, 1])
        assert led.poll_entries(1, "feeding", [3, 2, 1]) == {3}
        assert led.poll_entries(1, "feeding", [3, 2, 1]) == set()

    def test_webhook_then_refresh_fires_once(self):
        # The duplicate this exists to prevent: the webhook announces entry 3,
        # then the refresh it triggers sees entry 3 as new.
        led = EventLedger()
        led.poll_entries(1, "feeding", [2, 1])
        assert led.claim_entry(1, "feeding", 3) is True
        assert led.poll_entries(1, "feeding", [3, 2, 1]) == set()
        assert led.poll_entries(1, "feeding", [3, 2, 1]) == set()

    def test_poll_then_webhook_fires_once(self):
        led = EventLedger()
        led.poll_entries(1, "feeding", [2, 1])
        assert led.poll_entries(1, "feeding", [3, 2, 1]) == {3}
        assert led.claim_entry(1, "feeding", 3) is False

    def test_repeated_webhook_delivery_fires_once(self):
        led = EventLedger()
        led.poll_entries(1, "feeding", [1])
        assert led.claim_entry(1, "feeding", 2) is True
        assert led.claim_entry(1, "feeding", 2) is False

    def test_stale_poll_between_webhook_and_refresh(self):
        # A poll already in flight when the entry was created comes back
        # without it. The entry must stay announced for the next poll.
        led = EventLedger()
        led.poll_entries(1, "feeding", [2, 1])
        assert led.claim_entry(1, "feeding", 3) is True
        assert led.poll_entries(1, "feeding", [2, 1]) == set()
        assert led.poll_entries(1, "feeding", [3, 2, 1]) == set()

    def test_kinds_and_children_are_separate(self):
        led = EventLedger()
        led.poll_entries(1, "feeding", [1])
        led.poll_entries(1, "sleep", [1])
        led.poll_entries(2, "feeding", [])
        assert led.claim_entry(1, "feeding", 5) is True
        assert led.poll_entries(1, "sleep", [5, 1]) == {5}
        assert led.poll_entries(2, "feeding", [5]) == {5}

    def test_child_added_later_does_not_replay_history(self):
        led = EventLedger()
        led.poll_entries(1, "feeding", [1])
        assert led.poll_entries(2, "feeding", [9, 8, 7]) == set()
        assert led.poll_entries(2, "feeding", [10, 9, 8, 7]) == {10}

    def test_entry_without_an_id_is_always_announced(self):
        led = EventLedger()
        assert led.claim_entry(1, "feeding", None) is True
        assert led.claim_entry(1, "feeding", None) is True
        assert led.poll_entries(1, "feeding", [None, 1]) == set()

    def test_pending_list_is_capped(self):
        # Backfilled entries never appear in the poll's recent-N window.
        led = EventLedger()
        led.poll_entries(1, "feeding", [])
        for i in range(ledger_mod._MAX_PENDING + 50):
            assert led.claim_entry(1, "feeding", i) is True
        assert len(led._pending[(1, "feeding")]) == ledger_mod._MAX_PENDING


class TestTimers:
    def test_first_poll_is_a_baseline(self):
        led = EventLedger()
        changes = led.poll_timers([_timer(1), _timer(2, pauses=1, paused=True)])
        assert changes == {"started": [], "stopped": [], "paused": [], "resumed": []}

    def test_poll_alone_reports_start_pause_resume_stop(self):
        led = EventLedger()
        led.poll_timers([])
        assert led.poll_timers([_timer(1)])["started"] == [_timer(1)]
        paused = _timer(1, pauses=1, paused=True)
        assert led.poll_timers([paused])["paused"] == [paused]
        resumed = _timer(1, pauses=1)
        assert led.poll_timers([resumed])["resumed"] == [resumed]
        assert led.poll_timers([resumed]) == {
            "started": [], "stopped": [], "paused": [], "resumed": [],
        }
        assert led.poll_timers([])["stopped"] == [1]

    def test_webhook_start_then_refresh_fires_once(self):
        led = EventLedger()
        led.poll_timers([])
        assert led.claim_timer_started(_timer(1)) is True
        assert led.claim_timer_started(_timer(1)) is False
        assert led.poll_timers([_timer(1)])["started"] == []
        assert led.claim_timer_started(_timer(1)) is False

    def test_webhook_stop_then_refresh_fires_once(self):
        led = EventLedger()
        led.poll_timers([_timer(1)])
        assert led.claim_timer_stopped(_timer(1)) is True
        assert led.claim_timer_stopped(_timer(1)) is False
        assert led.poll_timers([])["stopped"] == []

    def test_stale_poll_after_webhook_stop_does_not_restart_it(self):
        led = EventLedger()
        led.poll_timers([_timer(1)])
        assert led.claim_timer_stopped(_timer(1)) is True
        stale = led.poll_timers([_timer(1)])
        assert stale["started"] == [] and stale["stopped"] == []
        after = led.poll_timers([])
        assert after["started"] == [] and after["stopped"] == []

    def test_webhook_pause_then_refresh_fires_once(self):
        led = EventLedger()
        led.poll_timers([_timer(1)])
        paused = _timer(1, pauses=1, paused=True)
        assert led.claim_timer_pause_change(paused) is True
        assert led.claim_timer_pause_change(paused) is False
        assert led.poll_timers([paused])["paused"] == []
        resumed = _timer(1, pauses=1)
        assert led.claim_timer_pause_change(resumed) is True
        assert led.poll_timers([resumed])["resumed"] == []

    def test_stale_poll_after_webhook_pause_does_not_report_a_resume(self):
        led = EventLedger()
        led.poll_timers([_timer(1)])
        assert led.claim_timer_pause_change(_timer(1, pauses=1, paused=True)) is True
        stale = led.poll_timers([_timer(1)])  # fetched before the pause
        assert stale["paused"] == [] and stale["resumed"] == []
        fresh = led.poll_timers([_timer(1, pauses=1, paused=True)])
        assert fresh["paused"] == [] and fresh["resumed"] == []

    def test_started_and_stopped_between_polls_by_webhook(self):
        led = EventLedger()
        led.poll_timers([])
        assert led.claim_timer_started(_timer(1)) is True
        assert led.claim_timer_stopped(_timer(1)) is True
        assert led.poll_timers([]) == {
            "started": [], "stopped": [], "paused": [], "resumed": [],
        }

    def test_server_without_pause_support_never_reports_pauses(self):
        led = EventLedger()
        old = {"id": 1, "child": 1}  # no is_paused, no pauses
        led.poll_timers([old])
        assert led.poll_timers([old]) == {
            "started": [], "stopped": [], "paused": [], "resumed": [],
        }

    def test_timer_without_an_id_is_always_announced(self):
        led = EventLedger()
        assert led.claim_timer_started({}) is True
        assert led.claim_timer_stopped({}) is True
        assert led.claim_timer_pause_change({}) is True
