"""Config flow for the Welkom integration."""

from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_ID, CONF_URL
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_ALLOW_BYPASS_LOGIN,
    CONF_AUTH_ENABLED,
    CONF_CREATE_ENTITIES,
    CONF_DEFAULT_USER,
    CONF_HOME_ID,
    CONF_PERSON_USERS,
    CONF_REQUIRE_FULL_ROLE,
    CONF_ROLE_USERS,
    DEFAULT_ALLOW_BYPASS_LOGIN,
    DEFAULT_AUTH_ENABLED,
    DEFAULT_CREATE_ENTITIES,
    DEFAULT_REQUIRE_FULL_ROLE,
    DOMAIN,
)

# Sentinel option meaning "no explicit mapping — fall through to the next step
# of the resolution chain". Empty string so it round-trips cleanly through vol.
_UNMAPPED = ""

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ID): str,
        vol.Required(CONF_URL): str,
        vol.Optional(CONF_HOME_ID): str,
    }
)

STEP_RECONFIGURE_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_URL): str,
        vol.Optional(CONF_HOME_ID): str,
    }
)


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate the user input by connecting to the Welkom API."""
    session = async_get_clientsession(hass)
    url = data[CONF_URL].rstrip("/")

    try:
        async with session.get(f"{url}/api/homes") as response:
            response.raise_for_status()
    except (aiohttp.ClientError, TimeoutError) as err:
        raise CannotConnect from err

    return {"title": data[CONF_ID]}


class WelkomConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Welkom."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_ID])
            self._abort_if_unique_id_configured()

            try:
                info = await validate_input(self.hass, user_input)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(title=info["title"], data=user_input)

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: Any) -> WelkomOptionsFlow:
        """Return the options flow (auth routing + person/role -> user map)."""
        return WelkomOptionsFlow()

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle reconfiguration of an existing entry."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                await validate_input(self.hass, {**entry.data, **user_input})
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates=user_input,
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                data_schema=STEP_RECONFIGURE_DATA_SCHEMA,
                suggested_values={
                    CONF_URL: entry.data[CONF_URL],
                    CONF_HOME_ID: entry.data.get(CONF_HOME_ID),
                },
            ),
            errors=errors,
        )


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class WelkomOptionsFlow(OptionsFlow):
    """Options: enable auth routing and map welkom people/roles to HA users."""

    def __init__(self) -> None:
        """Initialise the accumulated options and per-step key maps."""
        self._options: dict[str, Any] = {}
        # display label -> welkom id, rebuilt each step so submitted form keys
        # (which are labels, for a readable UI) map back to welkom ids.
        self._person_keys: dict[str, str] = {}
        self._role_keys: dict[str, str] = {}

    async def _user_labels(self) -> dict[str, str]:
        """Return {HA user id: display name} for real, active users."""
        users = await self.hass.auth.async_get_users()
        return {
            user.id: (user.name or user.id)
            for user in users
            if user.is_active and not user.system_generated
        }

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """General auth-routing settings, then the person map."""
        users = await self._user_labels()
        current = self.config_entry.options

        if user_input is not None:
            self._options[CONF_CREATE_ENTITIES] = user_input[CONF_CREATE_ENTITIES]
            self._options[CONF_AUTH_ENABLED] = user_input[CONF_AUTH_ENABLED]
            self._options[CONF_ALLOW_BYPASS_LOGIN] = user_input[CONF_ALLOW_BYPASS_LOGIN]
            self._options[CONF_REQUIRE_FULL_ROLE] = user_input[CONF_REQUIRE_FULL_ROLE]
            self._options[CONF_DEFAULT_USER] = user_input.get(CONF_DEFAULT_USER, "")
            return await self.async_step_people()

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_CREATE_ENTITIES,
                    default=current.get(CONF_CREATE_ENTITIES, DEFAULT_CREATE_ENTITIES),
                ): bool,
                vol.Required(
                    CONF_AUTH_ENABLED,
                    default=current.get(CONF_AUTH_ENABLED, DEFAULT_AUTH_ENABLED),
                ): bool,
                vol.Required(
                    CONF_ALLOW_BYPASS_LOGIN,
                    default=current.get(
                        CONF_ALLOW_BYPASS_LOGIN, DEFAULT_ALLOW_BYPASS_LOGIN
                    ),
                ): bool,
                vol.Required(
                    CONF_REQUIRE_FULL_ROLE,
                    default=current.get(
                        CONF_REQUIRE_FULL_ROLE, DEFAULT_REQUIRE_FULL_ROLE
                    ),
                ): bool,
                vol.Optional(
                    CONF_DEFAULT_USER,
                    default=current.get(CONF_DEFAULT_USER, _UNMAPPED),
                ): vol.In({_UNMAPPED: "— none —", **users}),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)

    async def async_step_people(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """One HA-user dropdown per welkom person (auto-identified login)."""
        coordinator = getattr(self.config_entry, "runtime_data", None)
        known_people = getattr(coordinator, "people", None)
        people = list(known_people.values()) if known_people else []
        users = await self._user_labels()
        current: dict[str, str] = self.config_entry.options.get(CONF_PERSON_USERS, {})

        self._person_keys = {}
        for person in people:
            label = person.display_name
            if label in self._person_keys:
                label = f"{person.display_name} ({person.id})"
            self._person_keys[label] = person.id

        if user_input is not None:
            mapping = {
                self._person_keys[label]: user_id
                for label, user_id in user_input.items()
                if label in self._person_keys and user_id
            }
            self._options[CONF_PERSON_USERS] = mapping
            return await self.async_step_roles()

        if not self._person_keys:
            # "Welkom has no people" and "we couldn't ask welkom" both arrive
            # here as an empty list, and only the first is a reason to clear the
            # mapping. Clearing on the second throws away hand-built person
            # mappings because welkom happened to be down when options opened.
            if known_people is None:
                _LOGGER.warning(
                    "Welkom people are unavailable; keeping the existing person mapping"
                )
            self._options[CONF_PERSON_USERS] = (
                {} if known_people is not None else current
            )
            return await self.async_step_roles()

        options = {_UNMAPPED: "— fall back to role —", **users}
        schema = vol.Schema(
            {
                vol.Optional(label, default=current.get(person_id, _UNMAPPED)): vol.In(
                    options
                )
                for label, person_id in self._person_keys.items()
            }
        )
        return self.async_show_form(step_id="people", data_schema=schema)

    async def async_step_roles(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """One HA-user dropdown per welkom role (shared fallback account)."""
        coordinator = getattr(self.config_entry, "runtime_data", None)
        known_roles = getattr(coordinator, "roles", None)
        roles = list(known_roles) if known_roles else []
        users = await self._user_labels()
        current: dict[str, str] = self.config_entry.options.get(CONF_ROLE_USERS, {})

        self._role_keys = {}
        for role in roles:
            label = role.display_name
            if label in self._role_keys:
                label = f"{role.display_name} ({role.id})"
            self._role_keys[label] = role.id

        if user_input is not None:
            mapping = {
                self._role_keys[label]: user_id
                for label, user_id in user_input.items()
                if label in self._role_keys and user_id
            }
            self._options[CONF_ROLE_USERS] = mapping
            return self.async_create_entry(title="", data=self._options)

        if not self._role_keys:
            # Same distinction as the people step: only clear when welkom
            # actually told us there is nothing to map.
            if known_roles is None:
                _LOGGER.warning(
                    "Welkom roles are unavailable; keeping the existing role mapping"
                )
            self._options[CONF_ROLE_USERS] = {} if known_roles is not None else current
            return self.async_create_entry(title="", data=self._options)

        options = {_UNMAPPED: "— use default —", **users}
        schema = vol.Schema(
            {
                vol.Optional(label, default=current.get(role_id, _UNMAPPED)): vol.In(
                    options
                )
                for label, role_id in self._role_keys.items()
            }
        )
        return self.async_show_form(step_id="roles", data_schema=schema)
