"""Constants for the Welkom integration."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

DOMAIN = "welkom"

CONF_HOME_ID = "home_id"

# Whether to create the presence entities at all. Turn off to run the
# integration purely for auth routing — e.g. when presence is already provided
# another way (a bridge from another HA instance) and you only want welkom here
# for the auth provider. The coordinator still runs (auth needs each person's
# assigned role); this only gates whether entities are published.
CONF_CREATE_ENTITIES = "create_entities"
DEFAULT_CREATE_ENTITIES = True

FRONTEND_SCRIPT_URL = f"/{DOMAIN}/welkom-activity.js"
FRONTEND_SCRIPT_VERSION = 10  # bump to cache-bust browsers when the script changes

# Dedicated ping endpoints for the frontend script. Natural frontend URLs
# (like /manifest.json) are also fetched by clients in the background, so the
# beacons need paths that only the script ever requests.
PING_CLAIM_URL = f"/{DOMAIN}/claim"
PING_SUSTAIN_URL = f"/{DOMAIN}/sustain"

# --- Auth routing -----------------------------------------------------------
# Welkom's forward auth stamps identity onto every request as `X-Welcome-*`
# response headers. When auth routing is enabled, the integration injects an
# auth provider that logs a request in as the Home Assistant user mapped from
# those fields, so people reach HA already identified without a login prompt.
# The config is expressed in welkom *fields*; this is the only place the header
# spelling lives.
WELKOM_FIELD_HEADERS: dict[str, str] = {
    "person": "X-Welcome-Person-Id",
    "role": "X-Welcome-Role-Id",
}

# Options keys (stored on the config entry's options).
CONF_AUTH_ENABLED = "auth_enabled"
CONF_ALLOW_BYPASS_LOGIN = "auth_allow_bypass_login"
CONF_PERSON_USERS = "auth_person_users"  # {welkom person id: HA user id}
CONF_ROLE_USERS = "auth_role_users"  # {welkom role id: HA user id}
CONF_DEFAULT_USER = "auth_default_user"  # HA user id, or "" for none
# When set, a person is only auto-identified into their own account on networks
# that grant them their *full* assigned role (capped role == assigned role). On
# a lower-trust network they are downgraded, so identity falls back to the role
# account — a spoofed MAC can't reach a higher-privilege account than the
# network's max role allows. See auth.py.
CONF_REQUIRE_FULL_ROLE = "auth_require_full_role"

DEFAULT_AUTH_ENABLED = False
DEFAULT_ALLOW_BYPASS_LOGIN = True
DEFAULT_REQUIRE_FULL_ROLE = True

# Bundled script that persists the auth token to localStorage so the browser
# does not re-run the login flow on every refresh.
AUTH_SCRIPT_URL = f"/{DOMAIN}/store-token.js"
AUTH_SCRIPT_VERSION = 1


def resolve_mapped_user_id(
    config: Mapping[str, Any],
    headers: Mapping[str, str],
    person_trusted: bool = True,
) -> str | None:
    """Resolve welkom identity headers to an HA user id via the config maps.

    Order: person map -> role map -> default user. ``person_trusted`` gates the
    person step: when False (the network downgraded the person below their
    assigned role, so their identity isn't trusted here) the person map is
    skipped and resolution starts at the role. ``headers`` is any mapping with
    ``.get`` (e.g. an aiohttp request's headers). Kept here, free of Home
    Assistant imports, so it can be unit-tested in isolation.
    """
    person = (headers.get(WELKOM_FIELD_HEADERS["person"]) or "").strip()
    if person and person_trusted:
        user_id = config.get(CONF_PERSON_USERS, {}).get(person)
        if user_id:
            return user_id

    role = (headers.get(WELKOM_FIELD_HEADERS["role"]) or "").strip()
    if role:
        user_id = config.get(CONF_ROLE_USERS, {}).get(role)
        if user_id:
            return user_id

    return config.get(CONF_DEFAULT_USER) or None
