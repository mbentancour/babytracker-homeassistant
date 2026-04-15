"""HA webhook endpoint that receives BabyTracker activity events.

Flow
----
On setup we register an HA webhook (``async_register``), then tell
BabyTracker to POST events to it. When an event arrives:

1. Verify the HMAC signature using the shared secret. Drop on mismatch.
2. Fire the corresponding HA event (``babytracker_new_feeding``, etc.) so
   automations can react.
3. Trigger a coordinator refresh so derived sensors (today-totals, age,
   "hours since…") update without waiting for the next poll.

On unload we remove both the HA webhook and the BabyTracker subscription
(best-effort — if either side has already forgotten about it, we log and
move on).
"""
from __future__ import annotations

import hmac
import json
import logging
import secrets
from hashlib import sha256
from typing import TYPE_CHECKING

from aiohttp.web import Request, Response
from homeassistant.components import webhook
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .api import BabyTrackerError
from .const import (
    DOMAIN,
    EVENT_NEW_DIAPER,
    EVENT_NEW_FEEDING,
    EVENT_NEW_MEDICATION,
    EVENT_NEW_SLEEP,
    EVENT_NEW_TEMPERATURE,
    EVENT_TIMER_STARTED,
    EVENT_TIMER_STOPPED,
)

if TYPE_CHECKING:
    from .coordinator import BabyTrackerCoordinator

_LOGGER = logging.getLogger(__name__)

# Entries in the config entry's data dict.
DATA_WEBHOOK_ID = "webhook_id"
DATA_WEBHOOK_SECRET = "webhook_secret"
DATA_SERVER_WEBHOOK_ID = "server_webhook_id"

# BabyTracker event → HA event bus name. Events we don't care about (e.g.
# note.created) are received and ack'd but not re-fired to keep the bus
# uncluttered. The create-entry events in this map cover everything the
# coordinator already surfaces as events today.
_EVENT_MAP = {
    "feeding.created": EVENT_NEW_FEEDING,
    "sleep.created": EVENT_NEW_SLEEP,
    "diaper.created": EVENT_NEW_DIAPER,
    "temperature.created": EVENT_NEW_TEMPERATURE,
    "medication.created": EVENT_NEW_MEDICATION,
    "timer.started": EVENT_TIMER_STARTED,
    "timer.stopped": EVENT_TIMER_STOPPED,
}


def generate_secret() -> str:
    """32 bytes of entropy hex-encoded — exceeds the backend's 16-char minimum
    by a wide margin. Generated once at integration setup and stored in the
    config entry so we can recompute the HMAC for every incoming event."""
    return secrets.token_hex(32)


async def async_register_webhook(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: "BabyTrackerCoordinator",
) -> bool:
    """Register the HA webhook and subscribe BabyTracker to it.

    Idempotent: if the entry already carries a webhook ID we just re-register
    the handler (HA's webhook registry doesn't survive a restart). Returns
    True on success; on failure the integration falls back to polling-only,
    so we don't want to block setup here.
    """
    data = dict(entry.data)

    webhook_id = data.get(DATA_WEBHOOK_ID)
    secret = data.get(DATA_WEBHOOK_SECRET)
    server_webhook_id = data.get(DATA_SERVER_WEBHOOK_ID)

    if not webhook_id:
        webhook_id = webhook.async_generate_id()
    if not secret:
        secret = generate_secret()

    # Always (re)register the HA handler — webhook registrations don't
    # survive HA restarts and the same ID can be re-used safely.
    webhook.async_register(
        hass,
        DOMAIN,
        "BabyTracker",
        webhook_id,
        _make_handler(coordinator, secret),
    )
    url = webhook.async_generate_url(hass, webhook_id)

    # Register (or re-register) with the BabyTracker side. If a server-side
    # row already exists we leave it; otherwise create one. Failures here are
    # non-fatal — the coordinator's 10-minute fallback poll picks up events
    # either way.
    if server_webhook_id is None:
        try:
            result = await coordinator.client.create_webhook(
                name=f"Home Assistant ({hass.config.location_name or 'HA'})",
                url=url,
                secret=secret,
                events="*",
            )
            server_webhook_id = result.get("id")
        except BabyTrackerError as err:
            _LOGGER.warning(
                "Could not register webhook with BabyTracker (falling back to polling): %s",
                err,
            )
            server_webhook_id = None

    new_data = {
        **data,
        DATA_WEBHOOK_ID: webhook_id,
        DATA_WEBHOOK_SECRET: secret,
        DATA_SERVER_WEBHOOK_ID: server_webhook_id,
    }
    if new_data != entry.data:
        hass.config_entries.async_update_entry(entry, data=new_data)

    return server_webhook_id is not None


async def async_unregister_webhook(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: "BabyTrackerCoordinator | None",
) -> None:
    """Tear down the webhook on both sides, best-effort."""
    webhook_id = entry.data.get(DATA_WEBHOOK_ID)
    server_webhook_id = entry.data.get(DATA_SERVER_WEBHOOK_ID)

    if webhook_id:
        try:
            webhook.async_unregister(hass, webhook_id)
        except Exception as err:  # noqa: BLE001 — webhook registry's own errors
            _LOGGER.debug("webhook unregister failed (may already be gone): %s", err)

    if server_webhook_id and coordinator is not None:
        try:
            await coordinator.client.delete_webhook(server_webhook_id)
        except BabyTrackerError as err:
            _LOGGER.debug("BabyTracker webhook delete failed (may already be gone): %s", err)


def _make_handler(coordinator: "BabyTrackerCoordinator", secret: str):
    """Return an HA webhook handler bound to this coordinator + secret."""

    async def handle(hass: HomeAssistant, webhook_id: str, request: Request) -> Response:
        body = await request.read()
        expected = request.headers.get("X-Webhook-Signature", "")
        mac = hmac.new(secret.encode(), body, sha256)
        actual = f"sha256={mac.hexdigest()}"
        if not hmac.compare_digest(expected, actual):
            _LOGGER.warning("rejecting webhook with bad signature")
            return Response(status=401)

        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            return Response(status=400)

        event_name = payload.get("event")
        data = payload.get("data") or {}

        ha_event = _EVENT_MAP.get(event_name)
        if ha_event is not None:
            child_id = data.get("child")
            child = next(
                (c for c in coordinator.data.children if c.get("id") == child_id),
                {},
            )
            hass.bus.async_fire(
                ha_event,
                {
                    "child_id": child_id,
                    "child_name": child.get("first_name"),
                    "entry": data if not event_name.startswith("timer.") else None,
                    "timer": data if event_name.startswith("timer.") else None,
                },
            )

        # Even for events we don't re-fire, refresh so derived sensors update.
        # request_refresh is debounced, so rapid-fire events coalesce.
        hass.async_create_task(coordinator.async_request_refresh())
        return Response(status=200)

    return handle
