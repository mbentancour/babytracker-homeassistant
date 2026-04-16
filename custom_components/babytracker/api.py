"""Async wrapper around the BabyTracker REST API."""
from __future__ import annotations

from typing import Any

import aiohttp
from aiohttp import ClientError, ClientTimeout


class BabyTrackerError(Exception):
    """Generic API error."""


class AuthError(BabyTrackerError):
    """Authentication failed."""


class BabyTrackerClient:
    """Minimal async client used by the integration."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        url: str,
        token: str,
        verify_ssl: bool = True,
    ) -> None:
        self._session = session
        self._base = url.rstrip("/")
        self._token = token
        self._verify_ssl = verify_ssl

    # ---- internal request helpers ----

    def _headers(self, json_body: bool = False) -> dict[str, str]:
        h = {"Authorization": f"Token {self._token}"}
        if json_body:
            h["Content-Type"] = "application/json"
        return h

    async def _request(self, method: str, path: str, *, params=None, json=None) -> Any:
        url = f"{self._base}{path}"
        try:
            async with self._session.request(
                method,
                url,
                headers=self._headers(json_body=json is not None),
                params=params,
                json=json,
                timeout=ClientTimeout(total=10),
                ssl=self._verify_ssl,
            ) as resp:
                if resp.status in (401, 403):
                    raise AuthError(f"authentication failed ({resp.status})")
                if resp.status >= 400:
                    text = await resp.text()
                    raise BabyTrackerError(f"HTTP {resp.status}: {text[:200]}")
                if resp.status == 204:
                    return None
                return await resp.json()
        except ClientError as err:
            raise BabyTrackerError(f"connection error: {err}") from err

    # ---- read methods ----

    async def list_children(self) -> list[dict]:
        data = await self._request("GET", "/api/children/")
        return data.get("results", [])

    async def list_feedings(self, child_id: int, limit: int = 50) -> list[dict]:
        data = await self._request(
            "GET", "/api/feedings/",
            params={"child": child_id, "limit": limit, "ordering": "-start"},
        )
        return data.get("results", [])

    async def list_sleep(self, child_id: int, limit: int = 50) -> list[dict]:
        data = await self._request(
            "GET", "/api/sleep/",
            params={"child": child_id, "limit": limit, "ordering": "-start"},
        )
        return data.get("results", [])

    async def list_changes(self, child_id: int, limit: int = 50) -> list[dict]:
        data = await self._request(
            "GET", "/api/changes/",
            params={"child": child_id, "limit": limit, "ordering": "-time"},
        )
        return data.get("results", [])

    async def list_temperature(self, child_id: int, limit: int = 5) -> list[dict]:
        data = await self._request(
            "GET", "/api/temperature/",
            params={"child": child_id, "limit": limit, "ordering": "-time"},
        )
        return data.get("results", [])

    async def list_medications(self, child_id: int, limit: int = 5) -> list[dict]:
        data = await self._request(
            "GET", "/api/medications/",
            params={"child": child_id, "limit": limit, "ordering": "-time"},
        )
        return data.get("results", [])

    async def list_timers(self) -> list[dict]:
        data = await self._request("GET", "/api/timers/")
        return data.get("results", [])

    async def get_config(self) -> dict:
        return await self._request("GET", "/api/config")

    # Growth — used for the latest_* sensors. Limit=1 because we only need the
    # most recent reading per child per metric.
    async def list_weight(self, child_id: int, limit: int = 1) -> list[dict]:
        data = await self._request(
            "GET", "/api/weight/",
            params={"child": child_id, "limit": limit, "ordering": "-date"},
        )
        return data.get("results", [])

    async def list_height(self, child_id: int, limit: int = 1) -> list[dict]:
        data = await self._request(
            "GET", "/api/height/",
            params={"child": child_id, "limit": limit, "ordering": "-date"},
        )
        return data.get("results", [])

    async def list_head_circumference(self, child_id: int, limit: int = 1) -> list[dict]:
        data = await self._request(
            "GET", "/api/head-circumference/",
            params={"child": child_id, "limit": limit, "ordering": "-date"},
        )
        return data.get("results", [])

    async def list_bmi(self, child_id: int, limit: int = 1) -> list[dict]:
        data = await self._request(
            "GET", "/api/bmi/",
            params={"child": child_id, "limit": limit, "ordering": "-date"},
        )
        return data.get("results", [])

    # Backup introspection — drives the "last successful backup" sensors and
    # the create_backup service acknowledgement.
    async def list_backups(self) -> list[dict]:
        data = await self._request("GET", "/api/backups/")
        return data.get("results", [])

    async def list_backup_destinations(self) -> list[dict]:
        data = await self._request("GET", "/api/backups/destinations")
        return data.get("results", [])

    async def create_backup(self, destination_ids: list[int] | None = None) -> dict:
        payload: dict[str, Any] = {}
        if destination_ids:
            payload["destination_ids"] = destination_ids
        return await self._request("POST", "/api/backups/", json=payload)

    # Webhook management — used by the integration's setup/unload to register
    # an HA webhook with BabyTracker so activity events are pushed instead of
    # polled. The secret must be ≥16 chars (backend enforces this).
    async def create_webhook(self, name: str, url: str, secret: str,
                             events: str = "*") -> dict:
        return await self._request("POST", "/api/webhooks/", json={
            "name": name,
            "url": url,
            "secret": secret,
            "events": events,
            "active": True,
        })

    async def delete_webhook(self, webhook_id: int) -> None:
        await self._request("DELETE", f"/api/webhooks/{webhook_id}/")

    # Tags — used by the log_* services to attach user-typed tag names to
    # the entries they create. Tags are auto-created if a name doesn't
    # exist yet (matches the behaviour of the BabyTracker web UI).
    async def list_tags(self) -> list[dict]:
        data = await self._request("GET", "/api/tags/")
        return data.get("results", [])

    async def create_tag(self, name: str, color: str = "#6C5CE7") -> dict:
        return await self._request("POST", "/api/tags/", json={"name": name, "color": color})

    async def set_entity_tags(
        self, entity_type: str, entity_id: int, tag_ids: list[int]
    ) -> None:
        await self._request(
            "PUT",
            f"/api/tags/{entity_type}/{entity_id}/",
            json={"tag_ids": tag_ids},
        )

    # ---- write methods ----

    async def create_feeding(self, payload: dict) -> dict:
        return await self._request("POST", "/api/feedings/", json=payload)

    async def create_sleep(self, payload: dict) -> dict:
        return await self._request("POST", "/api/sleep/", json=payload)

    async def create_diaper(self, payload: dict) -> dict:
        return await self._request("POST", "/api/changes/", json=payload)

    async def create_tummy_time(self, payload: dict) -> dict:
        return await self._request("POST", "/api/tummy-times/", json=payload)

    async def create_pumping(self, payload: dict) -> dict:
        return await self._request("POST", "/api/pumping/", json=payload)

    async def create_temperature(self, payload: dict) -> dict:
        return await self._request("POST", "/api/temperature/", json=payload)

    async def create_medication(self, payload: dict) -> dict:
        return await self._request("POST", "/api/medications/", json=payload)

    async def create_note(self, payload: dict) -> dict:
        return await self._request("POST", "/api/notes/", json=payload)

    async def create_milestone(self, payload: dict) -> dict:
        return await self._request("POST", "/api/milestones/", json=payload)

    async def create_weight(self, payload: dict) -> dict:
        return await self._request("POST", "/api/weight/", json=payload)

    async def create_height(self, payload: dict) -> dict:
        return await self._request("POST", "/api/height/", json=payload)

    async def create_head_circumference(self, payload: dict) -> dict:
        return await self._request("POST", "/api/head-circumference/", json=payload)

    async def create_timer(self, payload: dict) -> dict:
        return await self._request("POST", "/api/timers/", json=payload)

    async def delete_timer(self, timer_id: int) -> None:
        await self._request("DELETE", f"/api/timers/{timer_id}/")

    async def set_display(self, payload: dict) -> dict:
        return await self._request("PUT", "/api/display", json=payload)
