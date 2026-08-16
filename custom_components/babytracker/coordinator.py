"""Data update coordinator that polls BabyTracker for all configured children."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from . import timeutil
from .api import AuthError, BabyTrackerClient, BabyTrackerError
from .const import (
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    EVENT_NEW_DIAPER,
    EVENT_NEW_FEEDING,
    EVENT_NEW_MEDICATION,
    EVENT_NEW_MILK_WASTE,
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
    # Estimated stored breast milk. None when the server predates 1.10.0 or
    # the token can't read pumping — the sensor is then never created.
    milk_stock: dict | None = None


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
    # Use HA's configured timezone, not the OS one — on containers/VMs left
    # at UTC the two differ and every *_today sensor would reset and bucket
    # entries at the wrong hour.
    return dt_util.start_of_local_day().astimezone(timezone.utc)


# Pure time helpers live in timeutil (no HA imports) so they're unit-testable
# standalone. Aliased here to keep the existing call sites unchanged.
_parse_iso = timeutil.parse_iso
_duration_minutes = timeutil.duration_minutes
_overlap_minutes = timeutil.overlap_minutes


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
        # Backend display preference ("metric"/"imperial"). Values are stored
        # as entered on the backend, so sensor units must follow this or an
        # imperial household's readings get labelled kg/cm/mL/°C in HA.
        self.unit_system: str = "metric"
        self._unit_system_loaded = False
        # Track previously-seen entry IDs per (child, kind) so we can emit
        # events only for newly-created entries (not on first refresh).
        self._seen: dict[tuple[int, str], set[int]] = {}
        self._seen_timers: set[int] = set()
        self._first_refresh_done = False

    async def _milk_stock(self, cid: int) -> dict | None:
        """Milk stock, or None if this server can't provide it.

        /api/milk-stock arrived in BabyTracker 1.10.0, so on an older server
        every call 404s. Swallowing that here matters: this runs inside the
        gather below, and asyncio.gather propagates the first exception, so an
        unguarded call would take down the entire refresh for every sensor.

        Re-attempted on each refresh rather than latched off, so the sensor
        appears by itself once the server is upgraded. Six wasted requests an
        hour per child is a fair price for not needing an HA restart.
        """
        try:
            return await self.client.get_milk_stock(cid)
        except BabyTrackerError as err:
            _LOGGER.debug("milk stock unavailable for child %s: %s", cid, err)
            return None

    async def _milk_waste(self, cid: int) -> list[dict]:
        """Recent uneaten-milk entries, or [] if this server can't provide them.

        Guarded for the same reason as _milk_stock. Polled as well as pushed so
        babytracker_new_milk_waste keeps firing on installs where webhook
        registration failed and the 10-minute poll is doing the work — the
        other five entry events already behave that way.
        """
        try:
            return await self.client.list_milk_waste(cid, limit=5)
        except BabyTrackerError as err:
            _LOGGER.debug("milk waste unavailable for child %s: %s", cid, err)
            return []

    async def _fetch_child_lists(self, cid: int) -> tuple[Any, ...]:
        """Fetch all per-child lists concurrently instead of ~10 serial calls
        per child — with several children and 10s request timeouts a single
        slow endpoint otherwise stalls the whole refresh."""
        return await asyncio.gather(
            self.client.list_feedings(cid, limit=100),
            self.client.list_sleep(cid, limit=100),
            self.client.list_changes(cid, limit=100),
            self.client.list_temperature(cid, limit=1),
            self.client.list_medications(cid, limit=1),
            self.client.list_weight(cid, limit=1),
            self.client.list_height(cid, limit=1),
            self.client.list_head_circumference(cid, limit=1),
            self.client.list_bmi(cid, limit=1),
            self._milk_stock(cid),
            self._milk_waste(cid),
        )

    async def _async_update_data(self) -> BabyTrackerData:
        try:
            if not self._unit_system_loaded:
                try:
                    cfg = await self.client.get_config()
                    self.unit_system = cfg.get("unit_system") or "metric"
                    self._unit_system_loaded = True
                except BabyTrackerError as err:
                    # Sensor units are fixed at entity __init__ from this value,
                    # and entities are created right after the first refresh —
                    # so if the unit system is still unknown on the first
                    # refresh, fail the whole refresh rather than create an
                    # imperial household's sensors mislabelled kg/cm/mL/°C.
                    # Later refreshes just retry silently (entities already
                    # exist; nothing to mislabel).
                    if not self._first_refresh_done:
                        raise UpdateFailed(f"could not determine unit system: {err}") from err
                    _LOGGER.debug("unit system fetch failed, will retry next refresh: %s", err)

            children, timers = await asyncio.gather(
                self.client.list_children(),
                self.client.list_timers(),
            )

            today_start = _start_of_today_utc()
            snapshots: dict[int, ChildSnapshot] = {}
            child_by_id = {c["id"]: c for c in children}

            per_child = await asyncio.gather(
                *(self._fetch_child_lists(c["id"]) for c in children)
            )

            for child, lists in zip(children, per_child):
                cid = child["id"]
                (feedings, sleeps, changes, temps, meds,
                 weights, heights, head_circs, bmis, milk_stock,
                 milk_waste) = lists

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
                snap.milk_stock = milk_stock

                today_end = today_start + timedelta(days=1)
                for f in feedings:
                    start = _parse_iso(f.get("start"))
                    if start and start >= today_start:
                        snap.feedings_today += 1
                        amount = f.get("amount")
                        if isinstance(amount, (int, float)):
                            snap.feeding_volume_today += float(amount)
                for s in sleeps:
                    # Use window overlap, not start-bucketed duration: an
                    # overnight sleep contributes only the minutes that fall
                    # inside today, regardless of which day its start is on.
                    snap.sleep_minutes_today += _overlap_minutes(s, today_start, today_end)
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
                self._emit_new_entries(cid, child, "milk_waste", milk_waste, EVENT_NEW_MILK_WASTE)

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
        # that hold a copy. The "date" field is RFC3339 UTC from modern
        # backends ("2026-04-16T10:35:22Z"); older backends emitted naive
        # "YYYY-MM-DD HH:MM:SS" against server-local time. Parse both.
        for entry in backups:
            date_str = entry.get("date", "")
            when: datetime | None = None
            try:
                # RFC3339 — fromisoformat handles "Z" natively on 3.11+;
                # the replace() keeps us compatible with 3.9/3.10.
                when = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            except ValueError:
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
