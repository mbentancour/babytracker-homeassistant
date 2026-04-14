"""Service handlers for the BabyTracker integration."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

from .api import BabyTrackerError
from .const import DOMAIN
from .coordinator import BabyTrackerCoordinator

_LOGGER = logging.getLogger(__name__)

SERVICE_LOG_FEEDING = "log_feeding"
SERVICE_LOG_SLEEP = "log_sleep"
SERVICE_LOG_DIAPER = "log_diaper"
SERVICE_START_TIMER = "start_timer"
SERVICE_STOP_TIMER = "stop_timer"
SERVICE_SET_SLIDESHOW = "set_slideshow"

SCHEMA_LOG_FEEDING = vol.Schema(
    {
        vol.Required("child_id"): vol.Coerce(int),
        vol.Required("type"): cv.string,
        vol.Required("method"): cv.string,
        vol.Optional("amount"): vol.Coerce(float),
        vol.Optional("duration_minutes", default=0): vol.All(vol.Coerce(int), vol.Range(min=0)),
        vol.Optional("notes", default=""): cv.string,
    }
)

SCHEMA_LOG_SLEEP = vol.Schema(
    {
        vol.Required("child_id"): vol.Coerce(int),
        vol.Required("duration_minutes"): vol.All(vol.Coerce(int), vol.Range(min=1)),
        vol.Optional("nap", default=True): cv.boolean,
        vol.Optional("notes", default=""): cv.string,
    }
)

SCHEMA_LOG_DIAPER = vol.Schema(
    {
        vol.Required("child_id"): vol.Coerce(int),
        vol.Optional("wet", default=False): cv.boolean,
        vol.Optional("solid", default=False): cv.boolean,
        vol.Optional("color", default=""): vol.In(["", "black", "brown", "green", "yellow"]),
        vol.Optional("notes", default=""): cv.string,
    }
)

SCHEMA_START_TIMER = vol.Schema(
    {
        vol.Required("child_id"): vol.Coerce(int),
        vol.Required("name"): cv.string,
    }
)

SCHEMA_STOP_TIMER = vol.Schema(
    {
        vol.Required("timer_id"): vol.Coerce(int),
    }
)

SCHEMA_SET_SLIDESHOW = vol.Schema(
    {
        vol.Required("enabled"): cv.boolean,
        vol.Optional("device"): cv.string,
    }
)


def _now_iso() -> str:
    """ISO 8601 timestamp with timezone, suitable for the BabyTracker API."""
    return datetime.now(timezone.utc).isoformat()


def _iso_minutes_ago(minutes: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()


async def _resolve_coordinator(hass: HomeAssistant) -> BabyTrackerCoordinator:
    """Pick the first configured BabyTracker instance.

    The integration supports only one instance per host, so in nearly all
    setups there's a single coordinator. If multiple are configured, services
    target the first one — users can run a second HA instance per BabyTracker
    if true isolation is needed.
    """
    coordinators: dict[str, BabyTrackerCoordinator] = hass.data.get(DOMAIN, {})
    if not coordinators:
        raise HomeAssistantError("BabyTracker integration is not configured")
    return next(iter(coordinators.values()))


async def _log_feeding(call: ServiceCall) -> None:
    coord = await _resolve_coordinator(call.hass)
    duration = call.data.get("duration_minutes", 0)
    payload = {
        "child": call.data["child_id"],
        "start": _iso_minutes_ago(duration),
        "end": _now_iso(),
        "type": call.data["type"],
        "method": call.data["method"],
        "notes": call.data.get("notes", ""),
    }
    if "amount" in call.data:
        payload["amount"] = call.data["amount"]
    try:
        await coord.client.create_feeding(payload)
    except BabyTrackerError as err:
        raise HomeAssistantError(f"Failed to log feeding: {err}") from err
    await coord.async_request_refresh()


async def _log_sleep(call: ServiceCall) -> None:
    coord = await _resolve_coordinator(call.hass)
    payload = {
        "child": call.data["child_id"],
        "start": _iso_minutes_ago(call.data["duration_minutes"]),
        "end": _now_iso(),
        "nap": call.data.get("nap", True),
        "notes": call.data.get("notes", ""),
    }
    try:
        await coord.client.create_sleep(payload)
    except BabyTrackerError as err:
        raise HomeAssistantError(f"Failed to log sleep: {err}") from err
    await coord.async_request_refresh()


async def _log_diaper(call: ServiceCall) -> None:
    coord = await _resolve_coordinator(call.hass)
    payload = {
        "child": call.data["child_id"],
        "time": _now_iso(),
        "wet": call.data.get("wet", False),
        "solid": call.data.get("solid", False),
        "color": call.data.get("color", ""),
        "notes": call.data.get("notes", ""),
    }
    try:
        await coord.client.create_diaper(payload)
    except BabyTrackerError as err:
        raise HomeAssistantError(f"Failed to log diaper: {err}") from err
    await coord.async_request_refresh()


async def _start_timer(call: ServiceCall) -> None:
    coord = await _resolve_coordinator(call.hass)
    payload = {
        "child": call.data["child_id"],
        "name": call.data["name"],
        "start": _now_iso(),
    }
    try:
        await coord.client.create_timer(payload)
    except BabyTrackerError as err:
        raise HomeAssistantError(f"Failed to start timer: {err}") from err
    await coord.async_request_refresh()


async def _stop_timer(call: ServiceCall) -> None:
    coord = await _resolve_coordinator(call.hass)
    try:
        await coord.client.delete_timer(call.data["timer_id"])
    except BabyTrackerError as err:
        raise HomeAssistantError(f"Failed to stop timer: {err}") from err
    await coord.async_request_refresh()


async def _set_slideshow(call: ServiceCall) -> None:
    coord = await _resolve_coordinator(call.hass)
    payload = {"picture_frame": call.data["enabled"]}
    if device := call.data.get("device"):
        payload["device"] = device
    try:
        await coord.client.set_display(payload)
    except BabyTrackerError as err:
        raise HomeAssistantError(f"Failed to set slideshow: {err}") from err


async def async_register_services(hass: HomeAssistant) -> None:
    """Register all services. Idempotent — safe to call once at startup."""
    if hass.services.has_service(DOMAIN, SERVICE_LOG_FEEDING):
        return  # already registered

    hass.services.async_register(DOMAIN, SERVICE_LOG_FEEDING, _log_feeding, schema=SCHEMA_LOG_FEEDING)
    hass.services.async_register(DOMAIN, SERVICE_LOG_SLEEP, _log_sleep, schema=SCHEMA_LOG_SLEEP)
    hass.services.async_register(DOMAIN, SERVICE_LOG_DIAPER, _log_diaper, schema=SCHEMA_LOG_DIAPER)
    hass.services.async_register(DOMAIN, SERVICE_START_TIMER, _start_timer, schema=SCHEMA_START_TIMER)
    hass.services.async_register(DOMAIN, SERVICE_STOP_TIMER, _stop_timer, schema=SCHEMA_STOP_TIMER)
    hass.services.async_register(DOMAIN, SERVICE_SET_SLIDESHOW, _set_slideshow, schema=SCHEMA_SET_SLIDESHOW)
