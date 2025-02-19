"""The Welkom integration."""

from __future__ import annotations

from homeassistant.const import CONF_ID, CONF_URL, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .client import WelkomClient
from .const import CONF_HOME_ID, DOMAIN
from .coordinator import WelkomConfigEntry, WelkomCoordinator

_PLATFORMS: list[Platform] = [
    Platform.DEVICE_TRACKER,
    Platform.BINARY_SENSOR,
    Platform.SENSOR,
]


async def async_setup_entry(
    hass: HomeAssistant, config_entry: WelkomConfigEntry
) -> bool:
    """Set up Welkom from a config entry."""

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

    await hass.config_entries.async_forward_entry_setups(config_entry, _PLATFORMS)

    return True


async def async_unload_entry(
    hass: HomeAssistant, config_entry: WelkomConfigEntry
) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(config_entry, _PLATFORMS)
