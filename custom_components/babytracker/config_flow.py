"""Config flow for BabyTracker."""
from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import AuthError, BabyTrackerClient, BabyTrackerError
from .const import CONF_TOKEN, CONF_URL, CONF_VERIFY_SSL, DOMAIN


def _build_schema(defaults: dict[str, Any]) -> vol.Schema:
    """Render the initial/options form with defaults pre-filled.

    Shared between the initial config flow and the options flow so there's
    one place to add a new field and both paths pick it up automatically.
    """
    return vol.Schema(
        {
            vol.Required(CONF_URL, default=defaults.get(CONF_URL, "http://babytracker:8099")): str,
            vol.Required(CONF_TOKEN, default=defaults.get(CONF_TOKEN, "")): str,
            vol.Optional(CONF_VERIFY_SSL, default=defaults.get(CONF_VERIFY_SSL, False)): bool,
        }
    )


async def _probe_credentials(
    hass, url: str, token: str, verify_ssl: bool
) -> str | None:
    """Hit the backend with the supplied creds; return an error key on failure."""
    session = async_get_clientsession(hass)
    client = BabyTrackerClient(session=session, url=url, token=token, verify_ssl=verify_ssl)
    try:
        await client.get_config()
        await client.list_children()
    except AuthError:
        return "invalid_auth"
    except BabyTrackerError:
        return "cannot_connect"
    return None


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for BabyTracker."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            url = user_input[CONF_URL].strip().rstrip("/")
            parsed = urlparse(url)
            if not parsed.scheme or not parsed.netloc:
                errors[CONF_URL] = "invalid_url"
            else:
                err = await _probe_credentials(
                    self.hass, url, user_input[CONF_TOKEN],
                    user_input.get(CONF_VERIFY_SSL, True),
                )
                if err:
                    errors["base"] = err
                else:
                    await self.async_set_unique_id(parsed.netloc)
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(
                        title=f"BabyTracker ({parsed.netloc})",
                        data={
                            CONF_URL: url,
                            CONF_TOKEN: user_input[CONF_TOKEN],
                            CONF_VERIFY_SSL: user_input.get(CONF_VERIFY_SSL, True),
                        },
                    )

        return self.async_show_form(
            step_id="user",
            data_schema=_build_schema(user_input or {}),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> "OptionsFlow":
        return OptionsFlow(config_entry)


class OptionsFlow(config_entries.OptionsFlow):
    """Edit URL / token / verify-SSL after the integration is already set up.

    We update the config entry's `data` (not `options`) because the existing
    code paths read credentials from there — and trigger a reload so the
    coordinator picks up the new client immediately.
    """

    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}
        current = self._entry.data

        if user_input is not None:
            url = user_input[CONF_URL].strip().rstrip("/")
            parsed = urlparse(url)
            if not parsed.scheme or not parsed.netloc:
                errors[CONF_URL] = "invalid_url"
            else:
                err = await _probe_credentials(
                    self.hass, url, user_input[CONF_TOKEN],
                    user_input.get(CONF_VERIFY_SSL, True),
                )
                if err:
                    errors["base"] = err
                else:
                    new_data = {
                        **current,
                        CONF_URL: url,
                        CONF_TOKEN: user_input[CONF_TOKEN],
                        CONF_VERIFY_SSL: user_input.get(CONF_VERIFY_SSL, True),
                    }
                    self.hass.config_entries.async_update_entry(self._entry, data=new_data)
                    await self.hass.config_entries.async_reload(self._entry.entry_id)
                    return self.async_create_entry(title="", data={})

        return self.async_show_form(
            step_id="init",
            data_schema=_build_schema(user_input or current),
            errors=errors,
        )
