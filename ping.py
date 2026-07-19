"""HTTP views for the frontend activity beacons."""

from __future__ import annotations

from datetime import UTC, datetime
import re
from typing import ClassVar

from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant, callback
from homeassistant.util import slugify

from .const import DOMAIN, PING_CLAIM_URL, PING_SUSTAIN_URL
from .coordinator import WelkomCoordinator
from .models import Activity, Metadata

# Network id from the connection summary: "... @ network_id (...)"
_CONNECTION_NETWORK_RE = re.compile(r"@ (\S+) \(")


class WelkomPingView(HomeAssistantView):
    """Applies beacon pings instantly using welkom's forward-auth headers.

    The ping request traverses the reverse proxy's forward auth, so it arrives
    carrying X-Welcome-* headers identifying the viewing device. Applying them
    here updates the current-device sensor in the same round trip as the ping;
    welkom records the same ping authoritatively, and the regular poll
    reconciles.
    """

    url = PING_CLAIM_URL
    extra_urls: ClassVar[list[str]] = [PING_SUSTAIN_URL]
    name = f"api:{DOMAIN}:ping"
    requires_auth = False

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the view."""
        self.hass = hass

    async def get(self, request: web.Request) -> web.Response:
        """Handle a beacon ping."""
        self._apply(request)
        return web.Response(text="ok")

    @callback
    def _apply(self, request: web.Request) -> None:
        headers = request.headers
        person_id = headers.get("X-Welcome-Person-Id")
        device = headers.get("X-Welcome-Device")
        if not person_id or not device:
            return

        summary = headers.get("X-Welcome-Connection")
        network_id = headers.get("X-Welcome-Network-Id")
        if (
            not network_id
            and summary
            and (match := _CONNECTION_NETWORK_RE.search(summary))
        ):
            network_id = match.group(1)

        sustain = request.path == PING_SUSTAIN_URL

        for entry in self.hass.config_entries.async_entries(DOMAIN):
            coordinator = getattr(entry, "runtime_data", None)
            if not isinstance(coordinator, WelkomCoordinator):
                continue
            data = coordinator.data.people.get(person_id) if coordinator.data else None
            if data is None:
                continue

            current = data.activity
            if sustain and current and current.device != device:
                continue  # sustains never steal

            # Carry over what the headers don't cover from the previous
            # activity (same device) or the device's known connection.
            carry = current if current and current.device == device else None
            device_data = (coordinator.data.devices or {}).get(slugify(device))

            data.activity = Activity(
                device=device,
                device_type=(device_data.device.type if device_data else None)
                or (carry.device_type if carry else None),
                network_id=network_id or (carry.network_id if carry else "unknown"),
                role_id=headers.get("X-Welcome-Role-Id")
                or (carry.role_id if carry else ""),
                room_id=carry.room_id if carry else None,
                host=request.host,
                last_seen_at=datetime.now(UTC),
                summary=summary or (carry.summary if carry else None),
                metadata=(
                    device_data.connection.metadata
                    if device_data and device_data.connection
                    else (carry.metadata if carry else Metadata())
                ),
            )
            coordinator.async_update_listeners()
