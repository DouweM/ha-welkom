"""Welkom auth routing.

When enabled, this injects a Home Assistant auth provider that logs a request in
as the HA user mapped from welkom's forward-auth identity. Resolution order:

1. ``X-Welcome-Person-Id`` -> the HA user mapped to that welkom person,
2. else ``X-Welcome-Role-Id`` -> the HA user mapped to that welkom role,
3. else the configured default user.

The person/role -> user maps and the default are edited in the integration's
options UI and stored on the config entry; the live copy the provider reads is
held in ``hass.data[DOMAIN]["auth"]`` and mutated in place so remapping takes
effect without a restart.

Getting the request (and thus welkom's headers) to an auth provider requires
replacing HA's ``LoginFlowIndexView`` so it puts the request in the flow
context — HA's own view (Apache-2.0) does not. That view and the provider
injection follow the approach proven by BeryJu/hass-auth-header, reimplemented
here against HA's own APIs.
"""

from __future__ import annotations

from collections import OrderedDict
from http import HTTPStatus
from ipaddress import ip_address
import logging
import os.path
from typing import Any, cast

from aiohttp.web import Request, Response
import voluptuous as vol

from homeassistant.auth.models import Credentials, UserMeta
from homeassistant.auth.providers import (
    AUTH_PROVIDERS,
    AuthFlowResult,
    AuthProvider,
    LoginFlow,
)
from homeassistant.auth.providers.trusted_networks import (
    InvalidAuthError,
    InvalidUserError,
    IPAddress,
)
from homeassistant.components import frontend
from homeassistant.components.auth import DOMAIN as AUTH_DOMAIN, indieauth
from homeassistant.components.auth.login_flow import LoginFlowIndexView
from homeassistant.components.http import StaticPathConfig
from homeassistant.components.http.ban import log_invalid_auth
from homeassistant.components.http.data_validator import RequestDataValidator
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import UnknownHandler, UnknownStep

from .const import (
    AUTH_SCRIPT_URL,
    AUTH_SCRIPT_VERSION,
    CONF_ALLOW_BYPASS_LOGIN,
    CONF_AUTH_ENABLED,
    DOMAIN,
    WELKOM_FIELD_HEADERS,
    person_trust,
    resolve_mapped_user_id,
)

_LOGGER = logging.getLogger(__name__)

# Key under hass.data[DOMAIN] holding the live auth config dict the provider
# reads on every flow, and a flag recording that injection has happened.
DATA_AUTH_CONFIG = "auth_config"
DATA_AUTH_INJECTED = "auth_injected"

PROVIDER_TYPE = "welkom"


@AUTH_PROVIDERS.register(PROVIDER_TYPE)
class WelkomAuthProvider(AuthProvider):
    """Resolve a request to an HA user via welkom's identity headers."""

    DEFAULT_TITLE = "Welkom"

    @property
    def type(self) -> str:
        return PROVIDER_TYPE

    @property
    def support_mfa(self) -> bool:
        """Header-derived identity does not support MFA."""
        return False

    def _people_loaded(self) -> bool:
        """Whether any entry has actually fetched welkom's people.

        `runtime_data` is assigned before the coordinator's first refresh, so a
        setup that failed — welkom unreachable during an options reload, say —
        leaves a coordinator in place with `people` still None. That is not the
        same as "this person is unknown", and the two must not be conflated.
        """
        return any(
            getattr(getattr(entry, "runtime_data", None), "people", None) is not None
            for entry in self.hass.config_entries.async_entries(DOMAIN)
        )

    def _assigned_role(self, person_id: str) -> str | None:
        """The person's uncapped assigned role, from the welkom coordinator.

        Sourced from ``/api/people`` (via the coordinator's people cache), not a
        header, so no forge-able input decides trust. Returns None when unknown.
        """
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            coordinator = getattr(entry, "runtime_data", None)
            people = getattr(coordinator, "people", None)
            person = people.get(person_id) if people else None
            if person is not None:
                return getattr(person, "role_id", None)
        return None

    def _person_trusted(self, request: Request) -> bool | None:
        """Whether the request's person identity may unlock their own account.

        The decision itself lives in `person_trust`; this just gathers what it
        needs out of Home Assistant. None means "can't tell" — see there.
        """
        person_id = request.headers.get(WELKOM_FIELD_HEADERS["person"], "").strip()
        return person_trust(
            self.config,
            request.headers,
            self._assigned_role(person_id) if person_id else None,
            self._people_loaded(),
        )

    def _resolve_user_id(self, request: Request | None) -> str | None:
        """Map the request's welkom fields to an HA user id, or None."""
        if not self.config.get(CONF_AUTH_ENABLED) or request is None:
            return None

        person_trusted = self._person_trusted(request)
        if person_trusted is None:
            # We can't judge this identity, so we don't act on it. No account is
            # unlocked and the normal login form takes over — the one outcome
            # that can't hand anybody an account that isn't theirs.
            _LOGGER.warning(
                "Welkom people are unavailable; not auto-identifying this request"
            )
            return None

        return resolve_mapped_user_id(self.config, request.headers, person_trusted)

    async def async_login_flow(self, context: dict[str, Any] | None) -> LoginFlow:
        """Return a login flow pre-resolved to the mapped user (if any)."""
        assert context is not None
        request = cast("Request | None", context.get("request"))
        user_id = self._resolve_user_id(request)
        return WelkomLoginFlow(
            self,
            user_id,
            cast("IPAddress", context.get("conn_ip_address")),
            bool(self.config.get(CONF_ALLOW_BYPASS_LOGIN, True)),
        )

    async def async_get_or_create_credentials(
        self, flow_result: dict[str, str]
    ) -> Credentials:
        """Return the credential linking the resolved user to this provider."""
        user_id = flow_result["user"]
        users = await self.store.async_get_users()
        for user in users:
            if user.system_generated or not user.is_active or user.id != user_id:
                continue
            for credential in await self.async_credentials():
                if credential.data["user_id"] == user_id:
                    return credential
            cred = self.async_create_credentials({"user_id": user_id})
            await self.store.async_link_user(user, cred)
            return cred
        # Never create a user; only ever log in as one that already exists.
        raise InvalidUserError

    async def async_user_meta_for_credentials(
        self, credentials: Credentials
    ) -> UserMeta:
        """Not supported: this provider never creates users."""
        raise NotImplementedError

    @callback
    def async_validate_access(self, ip_addr: IPAddress) -> None:
        """Ensure the direct connection came from a configured trusted proxy.

        Otherwise anyone able to reach HA directly could forge welkom's headers.
        """
        if not self.hass.http.trusted_proxies:
            _LOGGER.warning("trusted_proxies is not configured")
            raise InvalidAuthError("trusted_proxies is not configured")
        if not any(
            ip_addr in trusted_network
            for trusted_network in self.hass.http.trusted_proxies
        ):
            _LOGGER.warning("Remote IP not in trusted proxies: %s", ip_addr)
            raise InvalidAuthError("Not in trusted_proxies")


class WelkomLoginFlow(LoginFlow):
    """Finish the login as the pre-resolved user, after a trusted-proxy check."""

    def __init__(
        self,
        auth_provider: WelkomAuthProvider,
        user_id: str | None,
        ip_address: IPAddress,
        allow_bypass_login: bool,
    ) -> None:
        """Initialise with the already-resolved HA user id (or None)."""
        super().__init__(auth_provider)
        self._user_id = user_id
        self._ip_address = ip_address
        self._allow_bypass_login = allow_bypass_login

    async def async_step_init(
        self, user_input: dict[str, str] | None = None
    ) -> AuthFlowResult:
        """Resolve to the mapped user, or hand back to other providers.

        With no mapped user we show the (empty) form rather than aborting, so
        the request falls through to HA's other providers instead of looping.
        """
        if self._user_id is None:
            return self.async_show_form(step_id="init", data_schema=None)

        if user_input is None and not self._allow_bypass_login:
            return self.async_show_form(step_id="init", data_schema=None)

        provider = cast("WelkomAuthProvider", self._auth_provider)
        try:
            provider.async_validate_access(self._ip_address)
        except InvalidAuthError as exc:
            _LOGGER.debug("Invalid auth: %s", exc)
            return self.async_show_form(step_id="init", data_schema=None)

        users = await provider.store.async_get_users()
        if any(
            user.id == self._user_id and user.is_active and not user.system_generated
            for user in users
        ):
            return await self.async_finish({"user": self._user_id})

        _LOGGER.warning("Mapped HA user %s no longer exists/active", self._user_id)
        return self.async_show_form(step_id="init", data_schema=None)


def _get_actual_ip(request: Request) -> str:
    """Return the direct peer IP, before HA overrides ``remote`` with XFF.

    HA replaces ``request.remote`` with the ``X-Forwarded-For`` value behind a
    reverse proxy, but we need the actual peer to verify it is a trusted proxy.
    """
    peername = request._transport_peername
    if isinstance(peername, (list, tuple)):
        return peername[0]
    return cast("str", peername)


class RequestLoginFlowIndexView(LoginFlowIndexView):
    """HA's login-flow init view, extended to put the request in the context.

    Adapted from Home Assistant core's ``LoginFlowIndexView`` (Apache-2.0); the
    only additions are ``request`` and ``conn_ip_address`` in the flow context,
    which auth providers otherwise cannot see.
    """

    @RequestDataValidator(
        vol.Schema(
            {
                vol.Required("client_id"): str,
                vol.Required("handler"): vol.Any(str, list),
                vol.Required("redirect_uri"): str,
                vol.Optional("type", default="authorize"): str,
            }
        )
    )
    @log_invalid_auth
    async def post(self, request: Request, data: dict[str, Any]) -> Response:
        """Create a new login flow, carrying the request into the context."""
        client_id: str = data["client_id"]
        redirect_uri: str = data["redirect_uri"]

        if not indieauth.verify_client_id(client_id):
            return self.json_message("Invalid client id", HTTPStatus.BAD_REQUEST)

        handler: tuple[str, ...] | str
        if isinstance(data["handler"], list):
            handler = tuple(data["handler"])
        else:
            handler = data["handler"]

        try:
            result = await self._flow_mgr.async_init(
                handler,  # type: ignore[arg-type]
                context={
                    "request": request,
                    "ip_address": ip_address(request.remote),  # type: ignore[arg-type]
                    "conn_ip_address": ip_address(_get_actual_ip(request)),
                    "credential_only": data.get("type") == "link_user",
                    "redirect_uri": redirect_uri,
                },
            )
        except UnknownHandler:
            return self.json_message("Invalid handler specified", HTTPStatus.NOT_FOUND)
        except UnknownStep:
            return self.json_message(
                "Handler does not support init", HTTPStatus.BAD_REQUEST
            )

        return await self._async_flow_result_to_response(request, client_id, result)


@callback
def _replace_login_flow_view(hass: HomeAssistant) -> None:
    """Swap HA's login-flow init view for the request-carrying one."""
    router = hass.http.app.router
    for route in list(router._resources):
        if route.canonical == RequestLoginFlowIndexView.url:
            router._resources.remove(route)
    resource_index = getattr(router, "_resource_index", None)
    if isinstance(resource_index, dict):
        routes = resource_index.get(RequestLoginFlowIndexView.url)
        if routes:
            for route in list(routes):
                if route.canonical == RequestLoginFlowIndexView.url:
                    routes.remove(route)

    store_result = hass.data[AUTH_DOMAIN]
    hass.http.register_view(
        RequestLoginFlowIndexView(hass.auth.login_flow, store_result)
    )


async def async_setup_welkom_auth(hass: HomeAssistant, config: dict[str, Any]) -> None:
    """Inject the auth provider and register the token-persistence script.

    Injection happens once per HA instance; the provider then reads a single
    stable dict (held in ``hass.data[DOMAIN]``) that later updates mutate in
    place, so remapping takes effect without a restart. Idempotent.
    """
    domain_data = hass.data.setdefault(DOMAIN, {})

    if domain_data.get(DATA_AUTH_INJECTED):
        async_update_welkom_auth(hass, config)
        return

    live = dict(config)
    domain_data[DATA_AUTH_CONFIG] = live
    domain_data[DATA_AUTH_INJECTED] = True

    provider = WelkomAuthProvider(hass, hass.auth._store, live)
    providers: OrderedDict = OrderedDict()
    providers[(provider.type, provider.id)] = provider
    providers.update(hass.auth._providers)
    hass.auth._providers = providers
    _LOGGER.debug("Injected welkom auth provider")

    _replace_login_flow_view(hass)

    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(
                AUTH_SCRIPT_URL,
                os.path.join(os.path.dirname(__file__), "store-token.js"),
                True,
            )
        ]
    )
    frontend.add_extra_js_url(hass, f"{AUTH_SCRIPT_URL}?v={AUTH_SCRIPT_VERSION}")


@callback
def async_update_welkom_auth(hass: HomeAssistant, config: dict[str, Any]) -> None:
    """Refresh the live auth config in place (mutating the shared dict)."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    live = domain_data.get(DATA_AUTH_CONFIG)
    if live is None:
        domain_data[DATA_AUTH_CONFIG] = dict(config)
        return
    live.clear()
    live.update(config)
