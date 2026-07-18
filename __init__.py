"""The Welkom integration."""

from __future__ import annotations

from pathlib import Path

from homeassistant.components import frontend
from homeassistant.components.http import StaticPathConfig
from homeassistant.const import CONF_ID, CONF_URL, Platform
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.helpers import device_registry as dr

from .client import WelkomClient
from .const import CONF_HOME_ID, DOMAIN, FRONTEND_SCRIPT_URL, FRONTEND_SCRIPT_VERSION
from .coordinator import WelkomConfigEntry, WelkomCoordinator

_PLATFORMS: list[Platform] = [
    Platform.DEVICE_TRACKER,
    Platform.BINARY_SENSOR,
    Platform.SENSOR,
]


async def _async_setup_shared(hass: HomeAssistant) -> None:
    """Register the refresh service and frontend script, once across entries.

    The frontend script pings a same-origin URL on dashboard load/foreground so
    the viewing device registers forward-auth activity with welkom, then calls
    welkom.refresh so the fresh activity shows up within seconds instead of on
    the next poll.
    """
    if hass.services.has_service(DOMAIN, "refresh"):
        return

    async def _handle_refresh(call: ServiceCall) -> None:
        for entry in hass.config_entries.async_entries(DOMAIN):
            coordinator = getattr(entry, "runtime_data", None)
            if isinstance(coordinator, WelkomCoordinator):
                await coordinator.async_request_refresh()

    hass.services.async_register(DOMAIN, "refresh", _handle_refresh)

    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(
                FRONTEND_SCRIPT_URL,
                str(Path(__file__).parent / "welkom-activity.js"),
                cache_headers=True,
            )
        ]
    )
    frontend.add_extra_js_url(
        hass, f"{FRONTEND_SCRIPT_URL}?v={FRONTEND_SCRIPT_VERSION}"
    )


async def async_setup_entry(
    hass: HomeAssistant, config_entry: WelkomConfigEntry
) -> bool:
    """Set up Welkom from a config entry."""

    await _async_setup_shared(hass)

    client = WelkomClient(
        id=config_entry.data[CONF_ID],
        url=config_entry.data[CONF_URL],
        home_id=config_entry.data.get(CONF_HOME_ID),
    )

    coordinator = WelkomCoordinator(hass, config_entry, client)
    config_entry.runtime_data = coordinator

    await coordinator.async_config_entry_first_refresh()

    # TODO: Auto-create through entity?
    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=config_entry.entry_id,
        identifiers={(DOMAIN, client.unique_id)},  # TODO: connections as well?
        manufacturer="@DouweM",
        name=f"Welkom: {client.id}",
        # sw_version=config.swversion,
        # hw_version=config.hwversion,
    )

    @callback
    def _prune_removed_people() -> None:
        """Remove devices for people no longer present in the configuration."""
        current_ids = set(coordinator.people or {})
        for device in dr.async_entries_for_config_entry(
            device_registry, config_entry.entry_id
        ):
            for domain, identifier in device.identifiers:
                if domain != DOMAIN or not identifier.startswith("person_"):
                    continue
                if identifier.removeprefix("person_") not in current_ids:
                    device_registry.async_remove_device(device.id)
                break

    _prune_removed_people()
    config_entry.async_on_unload(coordinator.async_add_listener(_prune_removed_people))

    await hass.config_entries.async_forward_entry_setups(config_entry, _PLATFORMS)

    return True


async def async_unload_entry(
    hass: HomeAssistant, config_entry: WelkomConfigEntry
) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(config_entry, _PLATFORMS)
