"""Service handlers for the BabyTracker integration.

Design notes
------------
- All "logging" services target a child via Home Assistant's device picker.
  The user selects "Lily" (a device) and we resolve it to the BabyTracker
  child ID internally — they never need to know the numeric ID.
- The BabyTracker API uses naive local datetimes formatted as
  ``YYYY-MM-DDTHH:MM:SS``. Always format with ``_local_iso``; never include
  a timezone suffix, or the API rejects the payload.
- Point-in-time events (diaper, temperature, medication, note) accept an
  optional ``when`` datetime; default = "now".
- Duration events (feeding, sleep, tummy time, pumping) accept
  ``duration_minutes`` (required) and optional ``ended_at`` (default = "now").
- Date-only events (weight/height/head circumference/milestone) accept
  optional ``date``; default = today.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
import logging
from typing import Any

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr

from .api import BabyTrackerError
from .const import DOMAIN, SERVICE_CREATE_BACKUP
from .coordinator import BabyTrackerCoordinator

_LOGGER = logging.getLogger(__name__)

# ---- Service names ----
SERVICE_LOG_FEEDING = "log_feeding"
SERVICE_LOG_SLEEP = "log_sleep"
SERVICE_LOG_DIAPER = "log_diaper"
SERVICE_LOG_TUMMY_TIME = "log_tummy_time"
SERVICE_LOG_PUMPING = "log_pumping"
SERVICE_LOG_TEMPERATURE = "log_temperature"
SERVICE_LOG_MEDICATION = "log_medication"
SERVICE_LOG_NOTE = "log_note"
SERVICE_LOG_MILESTONE = "log_milestone"
SERVICE_LOG_WEIGHT = "log_weight"
SERVICE_LOG_HEIGHT = "log_height"
SERVICE_LOG_HEAD_CIRCUMFERENCE = "log_head_circumference"
SERVICE_START_TIMER = "start_timer"
SERVICE_STOP_TIMER = "stop_timer"
SERVICE_SET_SLIDESHOW = "set_slideshow"
SERVICE_REFRESH = "refresh"

# ---- Choice constants (mirror the backend's allowed values) ----
FEEDING_TYPES = ["breast milk", "formula", "fortified breast milk", "solid food"]
FEEDING_METHODS = [
    "bottle",
    "left breast",
    "right breast",
    "both breasts",
    "parent fed",
    "self fed",
]
DIAPER_COLORS = ["", "black", "brown", "green", "yellow"]
MILESTONE_CATEGORIES = ["motor", "cognitive", "social", "language", "other"]
MEDICATION_UNITS = ["ml", "mg", "drop", "drops", "tablet", "tablets"]


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------


def _local_iso(dt: datetime | str | None = None) -> str:
    """Format a datetime as the API expects: YYYY-MM-DDTHH:MM:SS, no timezone,
    no fractional seconds.

    The BabyTracker backend parses with ``time.Parse("2006-01-02T15:04:05", ...)``
    which rejects timezone suffixes, fractional seconds, or a space separator.
    This helper normalises every input shape HA might pass us:

    - ``None`` → now (local time).
    - ``datetime`` (naive or aware) → stripped to naive, microseconds dropped.
    - ``str`` (already formatted) → parsed, then renormalised, so that a
      template value like ``{{ now() }}`` (which resolves to an ISO string
      with timezone offset and microseconds) survives the round-trip.
    """
    if dt is None:
        dt = datetime.now()
    elif isinstance(dt, str):
        # HA templates often return tz-aware ISO strings; normalise those.
        try:
            dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
        except ValueError as err:
            raise ValueError(f"unparseable datetime: {dt!r}") from err
    return dt.replace(tzinfo=None, microsecond=0).isoformat(timespec="seconds")


def _date_iso(d: date | str | None = None) -> str:
    """Format a date as the API expects: YYYY-MM-DD. Accepts datetime (takes
    the date component), date, ISO-8601 string, or None (= today)."""
    if d is None:
        return date.today().isoformat()
    if isinstance(d, datetime):
        return d.date().isoformat()
    if isinstance(d, date):
        return d.isoformat()
    if isinstance(d, str):
        # Accept both "YYYY-MM-DD" and longer ISO strings; keep only the date.
        return d[:10]
    raise TypeError(f"unsupported date value: {d!r}")


def _coordinator(hass: HomeAssistant) -> BabyTrackerCoordinator:
    coordinators: dict[str, BabyTrackerCoordinator] = hass.data.get(DOMAIN, {})
    if not coordinators:
        raise HomeAssistantError("BabyTracker integration is not configured")
    # Single-instance is the common case; if multiple, the first entry is used.
    # Cross-instance routing would need a per-service entry selector.
    return next(iter(coordinators.values()))


def _resolve_child_id(hass: HomeAssistant, device_id: str) -> int:
    """Look up a HA device and return its BabyTracker child ID.

    Each child is registered as a device with identifier
    ``(DOMAIN, "child-{id}")``. We invert that here.
    """
    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get(device_id)
    if device is None:
        raise ServiceValidationError(f"Device {device_id} not found")
    for domain, identifier in device.identifiers:
        if domain == DOMAIN and identifier.startswith("child-"):
            try:
                return int(identifier[len("child-"):])
            except ValueError:
                continue
    raise ServiceValidationError(
        "The selected device is not a BabyTracker child. Pick one of the "
        "child devices created by the BabyTracker integration."
    )


# ----------------------------------------------------------------------------
# Schemas
# ----------------------------------------------------------------------------


def _base_schema(extra: dict) -> vol.Schema:
    """Schema with device_id + optional notes, plus per-service extras."""
    fields = {
        vol.Required("device_id"): cv.string,
        vol.Optional("notes", default=""): cv.string,
    }
    fields.update(extra)
    return vol.Schema(fields)


SCHEMA_LOG_FEEDING = _base_schema({
    vol.Optional("type", default="breast milk"): vol.In(FEEDING_TYPES),
    vol.Optional("method", default="bottle"): vol.In(FEEDING_METHODS),
    vol.Optional("amount"): vol.Coerce(float),
    vol.Optional("duration_minutes", default=0): vol.All(
        vol.Coerce(int), vol.Range(min=0, max=24 * 60)
    ),
    vol.Optional("ended_at"): cv.datetime,
})

SCHEMA_LOG_SLEEP = _base_schema({
    vol.Required("duration_minutes"): vol.All(
        vol.Coerce(int), vol.Range(min=1, max=24 * 60)
    ),
    vol.Optional("nap", default=True): cv.boolean,
    vol.Optional("ended_at"): cv.datetime,
})

SCHEMA_LOG_DIAPER = _base_schema({
    # `type` is a multi-select — the list can contain "wet", "solid", or both.
    # Renders as checkboxes in the HA form. Required=True with a default
    # means the user sees "Wet" pre-ticked and can add "Solid" with one click.
    vol.Required("type", default=["wet"]): vol.All(
        cv.ensure_list, [vol.In(("wet", "solid"))],
    ),
    vol.Optional("color", default=""): vol.In(DIAPER_COLORS),
    vol.Optional("when"): cv.datetime,
})

SCHEMA_LOG_TUMMY_TIME = _base_schema({
    vol.Required("duration_minutes"): vol.All(
        vol.Coerce(int), vol.Range(min=1, max=240)
    ),
    # Renamed from `milestone` — a previous version of the schema shared that
    # name with the separate log_milestone service, which confused users.
    vol.Optional("highlight", default=""): cv.string,
    vol.Optional("ended_at"): cv.datetime,
})

SCHEMA_LOG_PUMPING = _base_schema({
    vol.Required("amount"): vol.Coerce(float),
    vol.Optional("duration_minutes"): vol.All(
        vol.Coerce(int), vol.Range(min=1, max=240)
    ),
    vol.Optional("ended_at"): cv.datetime,
})

SCHEMA_LOG_TEMPERATURE = _base_schema({
    vol.Required("temperature"): vol.Coerce(float),
    vol.Optional("when"): cv.datetime,
})

SCHEMA_LOG_MEDICATION = _base_schema({
    vol.Required("name"): cv.string,
    vol.Optional("dosage"): vol.Coerce(float),
    vol.Optional("dosage_unit", default="ml"): vol.In(MEDICATION_UNITS),
    vol.Optional("when"): cv.datetime,
})

SCHEMA_LOG_NOTE = _base_schema({
    vol.Required("note"): cv.string,
    vol.Optional("when"): cv.datetime,
})

SCHEMA_LOG_MILESTONE = _base_schema({
    vol.Required("title"): cv.string,
    vol.Optional("category", default="other"): vol.In(MILESTONE_CATEGORIES),
    # Renamed from `description` — avoids a naming clash with every service's
    # top-level `description` in strings.json.
    vol.Optional("details", default=""): cv.string,
    vol.Optional("date"): cv.date,
})

SCHEMA_LOG_WEIGHT = _base_schema({
    vol.Required("weight"): vol.All(vol.Coerce(float), vol.Range(min=0)),
    vol.Optional("date"): cv.date,
})

SCHEMA_LOG_HEIGHT = _base_schema({
    vol.Required("height"): vol.All(vol.Coerce(float), vol.Range(min=0)),
    vol.Optional("date"): cv.date,
})

SCHEMA_LOG_HEAD_CIRCUMFERENCE = _base_schema({
    vol.Required("head_circumference"): vol.All(vol.Coerce(float), vol.Range(min=0)),
    vol.Optional("date"): cv.date,
})

SCHEMA_START_TIMER = vol.Schema({
    vol.Required("device_id"): cv.string,
    vol.Required("name"): cv.string,
})

SCHEMA_STOP_TIMER = vol.Schema({
    vol.Required("device_id"): cv.string,
    vol.Optional("name"): cv.string,
})

SCHEMA_SET_SLIDESHOW = vol.Schema({
    vol.Required("enabled"): cv.boolean,
    vol.Optional("device"): cv.string,
})

SCHEMA_REFRESH = vol.Schema({})

# `destinations` is optional — if omitted the backend writes the archive to
# every enabled destination (matches what happens when the server's cron
# fires). Encrypted destinations without a stored passphrase are skipped
# server-side with a warning; this service doesn't ask for passphrases to
# keep the UX simple.
SCHEMA_CREATE_BACKUP = vol.Schema({
    vol.Optional("destinations"): vol.All(cv.ensure_list, [int]),
})


# ----------------------------------------------------------------------------
# Service handlers
# ----------------------------------------------------------------------------


def _build_duration_payload(call: ServiceCall, child_id: int) -> dict[str, Any]:
    """Compute start/end times for duration-based services."""
    end = call.data.get("ended_at") or datetime.now()
    start = end - timedelta(minutes=call.data["duration_minutes"]) if "duration_minutes" in call.data else end
    return {
        "child": child_id,
        "start": _local_iso(start),
        "end": _local_iso(end),
    }


async def _do_create(call: ServiceCall, fn_name: str, payload: dict) -> None:
    coord = _coordinator(call.hass)
    fn = getattr(coord.client, fn_name)
    # Log at debug so users can enable `logger: babytracker: debug` and see
    # exactly what we sent when a call fails — saves the "what did the
    # integration actually do?" debugging loop.
    _LOGGER.debug("calling %s with payload=%s", fn_name, payload)
    try:
        result = await fn(payload)
    except BabyTrackerError as err:
        # Include the payload in the user-visible error. HA's UI truncates
        # long messages but shows the full thing in the logbook entry, which
        # is enough to spot "wrong date format" issues at a glance.
        raise HomeAssistantError(
            f"BabyTracker {fn_name} failed: {err} — payload sent: {payload}"
        ) from err
    _LOGGER.debug("%s response: %s", fn_name, result)
    # Use async_refresh (not async_request_refresh) so the sensor values
    # update immediately after the service call. async_request_refresh is
    # debounced; with our 10-minute poll interval it can delay visible
    # updates long enough that users think the action didn't work.
    await coord.async_refresh()


async def _log_feeding(call: ServiceCall) -> None:
    cid = _resolve_child_id(call.hass, call.data["device_id"])
    duration = call.data.get("duration_minutes", 0)
    end = call.data.get("ended_at") or datetime.now()
    start = end - timedelta(minutes=duration)
    payload = {
        "child": cid,
        "start": _local_iso(start),
        "end": _local_iso(end),
        "type": call.data.get("type", "breast milk"),
        "method": call.data.get("method", "bottle"),
        "notes": call.data.get("notes", ""),
    }
    if (amount := call.data.get("amount")) is not None:
        payload["amount"] = amount
    await _do_create(call, "create_feeding", payload)


async def _log_sleep(call: ServiceCall) -> None:
    cid = _resolve_child_id(call.hass, call.data["device_id"])
    payload = _build_duration_payload(call, cid)
    payload["nap"] = call.data.get("nap", True)
    payload["notes"] = call.data.get("notes", "")
    await _do_create(call, "create_sleep", payload)


async def _log_diaper(call: ServiceCall) -> None:
    cid = _resolve_child_id(call.hass, call.data["device_id"])
    # type arrives as a list of strings — `cv.ensure_list` accepts a single
    # string too, which covers users calling via YAML with `type: wet`.
    types = set(call.data.get("type") or [])
    wet = "wet" in types
    solid = "solid" in types
    if not wet and not solid:
        raise ServiceValidationError(
            "Tick at least one of Wet or Solid."
        )
    when = call.data.get("when") or datetime.now()
    payload = {
        "child": cid,
        "time": _local_iso(when),
        "wet": wet,
        "solid": solid,
        # Color only applies to solid-containing changes; drop any stale
        # default the user left from a previous wet-only call.
        "color": call.data.get("color", "") if solid else "",
        "notes": call.data.get("notes", ""),
    }
    await _do_create(call, "create_diaper", payload)


async def _log_tummy_time(call: ServiceCall) -> None:
    cid = _resolve_child_id(call.hass, call.data["device_id"])
    payload = _build_duration_payload(call, cid)
    # Backend field is still called `milestone` — the rename is HA-side only
    # to disambiguate from the separate log_milestone service.
    payload["milestone"] = call.data.get("highlight", "")
    payload["notes"] = call.data.get("notes", "")
    await _do_create(call, "create_tummy_time", payload)


async def _log_pumping(call: ServiceCall) -> None:
    cid = _resolve_child_id(call.hass, call.data["device_id"])
    payload = _build_duration_payload(call, cid)
    if (amount := call.data.get("amount")) is not None:
        payload["amount"] = amount
    await _do_create(call, "create_pumping", payload)


async def _log_temperature(call: ServiceCall) -> None:
    cid = _resolve_child_id(call.hass, call.data["device_id"])
    when = call.data.get("when") or datetime.now()
    payload = {
        "child": cid,
        "time": _local_iso(when),
        "temperature": call.data["temperature"],
        "notes": call.data.get("notes", ""),
    }
    await _do_create(call, "create_temperature", payload)


async def _log_medication(call: ServiceCall) -> None:
    cid = _resolve_child_id(call.hass, call.data["device_id"])
    when = call.data.get("when") or datetime.now()
    payload = {
        "child": cid,
        "time": _local_iso(when),
        "name": call.data["name"],
        "dosage_unit": call.data.get("dosage_unit", "ml"),
        "notes": call.data.get("notes", ""),
    }
    if (dosage := call.data.get("dosage")) is not None:
        payload["dosage"] = dosage
    await _do_create(call, "create_medication", payload)


async def _log_note(call: ServiceCall) -> None:
    cid = _resolve_child_id(call.hass, call.data["device_id"])
    when = call.data.get("when") or datetime.now()
    payload = {
        "child": cid,
        "time": _local_iso(when),
        "note": call.data["note"],
    }
    await _do_create(call, "create_note", payload)


async def _log_milestone(call: ServiceCall) -> None:
    cid = _resolve_child_id(call.hass, call.data["device_id"])
    payload = {
        "child": cid,
        "date": _date_iso(call.data.get("date")),
        "title": call.data["title"],
        "category": call.data.get("category", "other"),
        # UI field is "details", backend stores it as "description".
        "description": call.data.get("details", ""),
    }
    await _do_create(call, "create_milestone", payload)


async def _log_weight(call: ServiceCall) -> None:
    cid = _resolve_child_id(call.hass, call.data["device_id"])
    payload = {
        "child": cid,
        "date": _date_iso(call.data.get("date")),
        "weight": call.data["weight"],
        "notes": call.data.get("notes", ""),
    }
    await _do_create(call, "create_weight", payload)


async def _log_height(call: ServiceCall) -> None:
    cid = _resolve_child_id(call.hass, call.data["device_id"])
    payload = {
        "child": cid,
        "date": _date_iso(call.data.get("date")),
        "height": call.data["height"],
        "notes": call.data.get("notes", ""),
    }
    await _do_create(call, "create_height", payload)


async def _log_head_circumference(call: ServiceCall) -> None:
    cid = _resolve_child_id(call.hass, call.data["device_id"])
    payload = {
        "child": cid,
        "date": _date_iso(call.data.get("date")),
        "head_circumference": call.data["head_circumference"],
        "notes": call.data.get("notes", ""),
    }
    await _do_create(call, "create_head_circumference", payload)


async def _start_timer(call: ServiceCall) -> None:
    cid = _resolve_child_id(call.hass, call.data["device_id"])
    coord = _coordinator(call.hass)
    payload = {
        "child": cid,
        "name": call.data["name"],
        "start": _local_iso(),
    }
    try:
        await coord.client.create_timer(payload)
    except BabyTrackerError as err:
        raise HomeAssistantError(f"Failed to start timer: {err}") from err
    await coord.async_request_refresh()


async def _stop_timer(call: ServiceCall) -> None:
    """Stop a timer for this child. If multiple are running and ``name`` is
    omitted, all of this child's timers are stopped."""
    cid = _resolve_child_id(call.hass, call.data["device_id"])
    coord = _coordinator(call.hass)
    name_filter = call.data.get("name", "").strip().lower() or None

    try:
        timers = await coord.client.list_timers()
    except BabyTrackerError as err:
        raise HomeAssistantError(f"Failed to list timers: {err}") from err

    matching = [
        t for t in timers
        if t.get("child") == cid
        and (name_filter is None or (t.get("name", "").lower() == name_filter))
    ]
    if not matching:
        raise ServiceValidationError(
            "No matching timer is running for this child."
        )

    for t in matching:
        try:
            await coord.client.delete_timer(t["id"])
        except BabyTrackerError as err:
            raise HomeAssistantError(f"Failed to stop timer {t['id']}: {err}") from err

    await coord.async_request_refresh()


async def _set_slideshow(call: ServiceCall) -> None:
    coord = _coordinator(call.hass)
    payload = {"picture_frame": call.data["enabled"]}
    if device := call.data.get("device"):
        payload["device"] = device
    try:
        await coord.client.set_display(payload)
    except BabyTrackerError as err:
        raise HomeAssistantError(f"Failed to set slideshow: {err}") from err


async def _refresh(call: ServiceCall) -> None:
    coord = _coordinator(call.hass)
    await coord.async_request_refresh()


async def _create_backup(call: ServiceCall) -> None:
    """Trigger an on-demand backup.

    The backend returns per-destination results; we don't surface them here
    because HA services don't expose structured responses well — operators who
    want results inspection can poll the backup_count / backup_last_success
    sensors after the call, or watch the logbook.
    """
    coord = _coordinator(call.hass)
    destinations = call.data.get("destinations")
    try:
        result = await coord.client.create_backup(destinations)
    except BabyTrackerError as err:
        raise HomeAssistantError(f"Backup failed: {err}") from err

    # Surface per-destination failures in the HA log so they don't silently
    # vanish. A single failed destination doesn't fail the whole call — the
    # backend reports each one independently.
    for entry in result.get("results", []) or []:
        if entry.get("error"):
            _LOGGER.warning(
                "Backup to %s failed: %s",
                entry.get("destination") or entry.get("destination_id"),
                entry["error"],
            )

    # Kick a coordinator refresh so the backup_last_success sensor updates
    # without waiting for the next scheduled poll.
    await coord.async_request_refresh()


_SERVICES = (
    (SERVICE_LOG_FEEDING, _log_feeding, SCHEMA_LOG_FEEDING),
    (SERVICE_LOG_SLEEP, _log_sleep, SCHEMA_LOG_SLEEP),
    (SERVICE_LOG_DIAPER, _log_diaper, SCHEMA_LOG_DIAPER),
    (SERVICE_LOG_TUMMY_TIME, _log_tummy_time, SCHEMA_LOG_TUMMY_TIME),
    (SERVICE_LOG_PUMPING, _log_pumping, SCHEMA_LOG_PUMPING),
    (SERVICE_LOG_TEMPERATURE, _log_temperature, SCHEMA_LOG_TEMPERATURE),
    (SERVICE_LOG_MEDICATION, _log_medication, SCHEMA_LOG_MEDICATION),
    (SERVICE_LOG_NOTE, _log_note, SCHEMA_LOG_NOTE),
    (SERVICE_LOG_MILESTONE, _log_milestone, SCHEMA_LOG_MILESTONE),
    (SERVICE_LOG_WEIGHT, _log_weight, SCHEMA_LOG_WEIGHT),
    (SERVICE_LOG_HEIGHT, _log_height, SCHEMA_LOG_HEIGHT),
    (SERVICE_LOG_HEAD_CIRCUMFERENCE, _log_head_circumference, SCHEMA_LOG_HEAD_CIRCUMFERENCE),
    (SERVICE_START_TIMER, _start_timer, SCHEMA_START_TIMER),
    (SERVICE_STOP_TIMER, _stop_timer, SCHEMA_STOP_TIMER),
    (SERVICE_SET_SLIDESHOW, _set_slideshow, SCHEMA_SET_SLIDESHOW),
    (SERVICE_REFRESH, _refresh, SCHEMA_REFRESH),
    (SERVICE_CREATE_BACKUP, _create_backup, SCHEMA_CREATE_BACKUP),
)


async def async_register_services(hass: HomeAssistant) -> None:
    """Register all services. Idempotent — safe to call multiple times."""
    for name, handler, schema in _SERVICES:
        if not hass.services.has_service(DOMAIN, name):
            hass.services.async_register(DOMAIN, name, handler, schema=schema)
