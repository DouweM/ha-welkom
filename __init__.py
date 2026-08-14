"""The Welkom integration."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from homeassistant.components import frontend
from homeassistant.components.http import StaticPathConfig
from homeassistant.const import CONF_ID, CONF_URL, Platform
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.helpers import device_registry as dr

from .auth import (
    DATA_AUTH_INJECTED,
    async_setup_welkom_auth,
    async_update_welkom_auth,
)
from .client import WelkomClient
from .const import (
    CONF_ALLOW_BYPASS_LOGIN,
    CONF_AUTH_ENABLED,
    CONF_DEFAULT_USER,
    CONF_HOME_ID,
    CONF_PERSON_USERS,
    CONF_REQUIRE_FULL_ROLE,
    CONF_ROLE_USERS,
    DEFAULT_ALLOW_BYPASS_LOGIN,
    DEFAULT_AUTH_ENABLED,
    DEFAULT_REQUIRE_FULL_ROLE,
    DOMAIN,
    FRONTEND_SCRIPT_URL,
    FRONTEND_SCRIPT_VERSION,
)
from .coordinator import WelkomConfigEntry, WelkomCoordinator
from .ping import WelkomPingView

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

    async def _handle_set_device_suspended(call: ServiceCall) -> None:
        """Suspend/unsuspend a device's current-device activity in welkom.

        Wire this to real-world device state — e.g. suspend the MacBook when
        its companion app's Active sensor turns off — so a device that keeps
        rendering dashboards while nobody is at it can't hold the slot.
        """
        device = call.data["device"]
        suspended = call.data.get("suspended", True)
        for entry in hass.config_entries.async_entries(DOMAIN):
            coordinator = getattr(entry, "runtime_data", None)
            if isinstance(coordinator, WelkomCoordinator):
                await coordinator.client.set_device_suspended(device, suspended)
                await coordinator.async_request_refresh()

    hass.services.async_register(
        DOMAIN, "set_device_suspended", _handle_set_device_suspended
    )

    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(
                FRONTEND_SCRIPT_URL,
                str(Path(__file__).parent / "welkom-activity.js"),
                cache_headers=True,
            ),
        ]
    )
    # Dedicated beacon endpoints: welkom's forward auth counts these (and only
    # these) as current-device claims/sustains, and the view applies them to
    # the sensor instantly from the ping's own forward-auth headers.
    hass.http.register_view(WelkomPingView(hass))
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

    await _async_setup_auth(hass, config_entry)
    config_entry.async_on_unload(
        config_entry.add_update_listener(_async_options_updated)
    )

    await hass.config_entries.async_forward_entry_setups(config_entry, _PLATFORMS)

    return True


def _auth_config(config_entry: WelkomConfigEntry) -> dict[str, Any]:
    """Build the auth-routing config dict from the entry's options."""
    options = config_entry.options
    return {
        CONF_AUTH_ENABLED: options.get(CONF_AUTH_ENABLED, DEFAULT_AUTH_ENABLED),
        CONF_ALLOW_BYPASS_LOGIN: options.get(
            CONF_ALLOW_BYPASS_LOGIN, DEFAULT_ALLOW_BYPASS_LOGIN
        ),
        CONF_REQUIRE_FULL_ROLE: options.get(
            CONF_REQUIRE_FULL_ROLE, DEFAULT_REQUIRE_FULL_ROLE
        ),
        CONF_PERSON_USERS: dict(options.get(CONF_PERSON_USERS, {})),
        CONF_ROLE_USERS: dict(options.get(CONF_ROLE_USERS, {})),
        CONF_DEFAULT_USER: options.get(CONF_DEFAULT_USER, ""),
    }


async def _async_setup_auth(
    hass: HomeAssistant, config_entry: WelkomConfigEntry
) -> None:
    """Set up (or refresh) welkom auth routing from the entry's options.

    The provider is injected the first time routing is enabled and then stays;
    disabling it again takes full effect on the next restart, but is inert in
    the meantime (an unmapped request falls through to HA's other providers).
    """
    config = _auth_config(config_entry)
    injected = hass.data.get(DOMAIN, {}).get(DATA_AUTH_INJECTED)
    if config[CONF_AUTH_ENABLED] and not injected:
        await async_setup_welkom_auth(hass, config)
    else:
        async_update_welkom_auth(hass, config)


async def _async_options_updated(
    hass: HomeAssistant, config_entry: WelkomConfigEntry
) -> None:
    """Apply option changes (incl. the person/role -> user map) live."""
    await _async_setup_auth(hass, config_entry)


async def async_unload_entry(
    hass: HomeAssistant, config_entry: WelkomConfigEntry
) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(config_entry, _PLATFORMS)
