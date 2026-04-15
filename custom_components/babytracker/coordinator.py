"""Data update coordinator that polls BabyTracker for all configured children."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import AuthError, BabyTrackerClient, BabyTrackerError
from .const import (
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    EVENT_NEW_DIAPER,
    EVENT_NEW_FEEDING,
    EVENT_NEW_MEDICATION,
    EVENT_NEW_SLEEP,
    EVENT_NEW_TEMPERATURE,
    EVENT_TIMER_STARTED,
    EVENT_TIMER_STOPPED,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class ChildSnapshot:
    """Aggregated state for one child."""

    child: dict
    last_feeding: dict | None = None
    last_sleep: dict | None = None
    last_diaper: dict | None = None
    last_temperature: dict | None = None
    last_medication: dict | None = None
    feedings_today: int = 0
    feeding_volume_today: float = 0.0
    sleep_minutes_today: int = 0
    diapers_today: int = 0
    diapers_wet_today: int = 0
    diapers_solid_today: int = 0
    active_timer: dict | None = None
    # Latest growth measurements — None when the child has no entry yet.
    latest_weight: dict | None = None
    latest_height: dict | None = None
    latest_head_circumference: dict | None = None
    latest_bmi: dict | None = None


@dataclass
class BackupStatus:
    """Per-destination backup health. Populated from the latest backups list
    query; `last_success` is the newest backup that reached the destination."""

    destination: dict
    last_success: datetime | None = None
    total_backups: int = 0


@dataclass
class BabyTrackerData:
    """Top-level data returned by the coordinator."""

    children: list[dict] = field(default_factory=list)
    snapshots: dict[int, ChildSnapshot] = field(default_factory=dict)
    backup_status: dict[int, BackupStatus] = field(default_factory=dict)


def _start_of_today_utc() -> datetime:
    now = datetime.now(timezone.utc).astimezone()
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight.astimezone(timezone.utc)


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        # Backend emits RFC3339; Python 3.11+ handles "Z" suffix
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _duration_minutes(item: dict, start_field: str = "start", end_field: str = "end") -> int:
    start = _parse_iso(item.get(start_field))
    end = _parse_iso(item.get(end_field))
    if not start or not end:
        return 0
    return max(0, int((end - start).total_seconds() // 60))


class BabyTrackerCoordinator(DataUpdateCoordinator[BabyTrackerData]):
    """Polls BabyTracker on a fixed interval."""

    def __init__(self, hass: HomeAssistant, client: BabyTrackerClient, entry_id: str) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=DEFAULT_SCAN_INTERVAL,
        )
        self.client = client
        self.entry_id = entry_id
        # Track previously-seen entry IDs per (child, kind) so we can emit
        # events only for newly-created entries (not on first refresh).
        self._seen: dict[tuple[int, str], set[int]] = {}
        self._seen_timers: set[int] = set()
        self._first_refresh_done = False

    async def _async_update_data(self) -> BabyTrackerData:
        try:
            children = await self.client.list_children()
            timers = await self.client.list_timers()

            today_start = _start_of_today_utc()
            snapshots: dict[int, ChildSnapshot] = {}
            child_by_id = {c["id"]: c for c in children}

            for child in children:
                cid = child["id"]
                feedings = await self.client.list_feedings(cid, limit=100)
                sleeps = await self.client.list_sleep(cid, limit=100)
                changes = await self.client.list_changes(cid, limit=100)
                temps = await self.client.list_temperature(cid, limit=1)
                meds = await self.client.list_medications(cid, limit=1)
                weights = await self.client.list_weight(cid, limit=1)
                heights = await self.client.list_height(cid, limit=1)
                head_circs = await self.client.list_head_circumference(cid, limit=1)
                bmis = await self.client.list_bmi(cid, limit=1)

                snap = ChildSnapshot(child=child)
                snap.last_feeding = feedings[0] if feedings else None
                snap.last_sleep = sleeps[0] if sleeps else None
                snap.last_diaper = changes[0] if changes else None
                snap.last_temperature = temps[0] if temps else None
                snap.last_medication = meds[0] if meds else None
                snap.latest_weight = weights[0] if weights else None
                snap.latest_height = heights[0] if heights else None
                snap.latest_head_circumference = head_circs[0] if head_circs else None
                snap.latest_bmi = bmis[0] if bmis else None

                for f in feedings:
                    start = _parse_iso(f.get("start"))
                    if start and start >= today_start:
                        snap.feedings_today += 1
                        amount = f.get("amount")
                        if isinstance(amount, (int, float)):
                            snap.feeding_volume_today += float(amount)
                for s in sleeps:
                    start = _parse_iso(s.get("start"))
                    if start and start >= today_start:
                        snap.sleep_minutes_today += _duration_minutes(s)
                for d in changes:
                    t = _parse_iso(d.get("time"))
                    if t and t >= today_start:
                        snap.diapers_today += 1
                        if d.get("wet"):
                            snap.diapers_wet_today += 1
                        if d.get("solid"):
                            snap.diapers_solid_today += 1

                # First running timer for this child (most setups have ≤1 at a time)
                snap.active_timer = next((t for t in timers if t.get("child") == cid), None)
                snapshots[cid] = snap

                # Fire HA events for newly-seen entries (skip on the first ever refresh
                # so we don't dump every historical entry as an event on integration
                # startup).
                self._emit_new_entries(cid, child, "feeding", feedings, EVENT_NEW_FEEDING)
                self._emit_new_entries(cid, child, "sleep", sleeps, EVENT_NEW_SLEEP)
                self._emit_new_entries(cid, child, "diaper", changes, EVENT_NEW_DIAPER)
                self._emit_new_entries(cid, child, "temperature", temps, EVENT_NEW_TEMPERATURE)
                self._emit_new_entries(cid, child, "medication", meds, EVENT_NEW_MEDICATION)

            # Timer events: detect started (in current set, not previously seen)
            # and stopped (previously seen, not in current set).
            current_timer_ids = {t["id"] for t in timers}
            if self._first_refresh_done:
                for t in timers:
                    if t["id"] not in self._seen_timers:
                        cid = t.get("child")
                        child = child_by_id.get(cid, {})
                        self.hass.bus.async_fire(
                            EVENT_TIMER_STARTED,
                            {
                                "child_id": cid,
                                "child_name": child.get("first_name"),
                                "timer": t,
                            },
                        )
                for tid in self._seen_timers - current_timer_ids:
                    self.hass.bus.async_fire(
                        EVENT_TIMER_STOPPED,
                        {"timer_id": tid},
                    )
            self._seen_timers = current_timer_ids
            self._first_refresh_done = True

            backup_status = await self._collect_backup_status()
            return BabyTrackerData(
                children=children,
                snapshots=snapshots,
                backup_status=backup_status,
            )
        except AuthError as err:
            raise UpdateFailed(f"authentication error: {err}") from err
        except BabyTrackerError as err:
            raise UpdateFailed(str(err)) from err

    async def _collect_backup_status(self) -> dict[int, BackupStatus]:
        """Query the backup list and derive per-destination health.

        Tolerant of failures: the backup endpoints are admin-only and the user
        might have scoped their API token to a non-admin user, in which case
        the calls 403 — we just return an empty map and the sensors stay
        unavailable rather than crashing the whole coordinator.
        """
        try:
            destinations = await self.client.list_backup_destinations()
            backups = await self.client.list_backups()
        except BabyTrackerError as err:
            _LOGGER.debug("backup status fetch skipped: %s", err)
            return {}

        # One BackupStatus per destination; key by ID so sensor unique_ids
        # stay stable even if the destination is renamed.
        status: dict[int, BackupStatus] = {}
        for d in destinations:
            status[d["id"]] = BackupStatus(destination=d)

        # Walk the deduped backup list — each entry carries the destinations
        # that hold a copy. The "date" field is formatted by the backend as
        # YYYY-MM-DD HH:MM:SS in local time of the server. Not ideal for
        # timezone correctness but good enough for "last backup ran at X".
        for entry in backups:
            date_str = entry.get("date", "")
            when: datetime | None = None
            try:
                when = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                pass
            for dref in entry.get("destinations", []) or []:
                did = dref.get("id")
                if did not in status:
                    continue
                status[did].total_backups += 1
                if when and (status[did].last_success is None or when > status[did].last_success):
                    status[did].last_success = when
        return status

    def _emit_new_entries(
        self,
        child_id: int,
        child: dict,
        kind: str,
        items: list[dict],
        event_name: str,
    ) -> None:
        """Fire an HA event for each new entry since the last poll."""
        key = (child_id, kind)
        current_ids = {it["id"] for it in items if "id" in it}
        previous = self._seen.get(key, set())

        if self._first_refresh_done:
            for item in items:
                iid = item.get("id")
                if iid is None or iid in previous:
                    continue
                self.hass.bus.async_fire(
                    event_name,
                    {
                        "child_id": child_id,
                        "child_name": child.get("first_name"),
                        "entry": item,
                    },
                )
        self._seen[key] = current_ids
