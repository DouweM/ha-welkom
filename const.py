"""Constants for the Welkom integration."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
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

# --- Location: welkom placement + phone GPS -----------------------------------
# Per-person phone tracker feeding the combined `device_tracker.<person>_location`
# entity (see device_tracker.WelkomLocationTracker). Options key holds
# {welkom person id: device_tracker entity id}; a person's
# `attrs.homeassistant.gps_tracker` in welkom is the fallback.
CONF_PERSON_GPS_TRACKERS = "person_gps_trackers"
# A phone fix older than this can't contradict welkom's placement: a phone
# that stopped reporting may be dead or left behind, while welkom seeing the
# person's laptop is a current fact. Fresh fixes arrive within a minute or two
# of actually moving (significant-location-change), so this only bites when
# the phone has gone quiet.
GPS_MAX_AGE = timedelta(minutes=15)
# How long welkom's last placement is kept after welkom stops reporting the
# person, as long as the phone's fix stays within the home: an idle phone drops
# off the controller's online window for a few minutes at a time without
# anyone having moved.
WELKOM_HOLD = timedelta(minutes=5)

# --- Trips: where somebody went while they were out --------------------------
# See trip.py for the state machine these drive.
#
# How far a fix may land from the anchor and still be the same spot. Wide
# enough to swallow a phone's own noise -- median 20 m over the week to
# 2026-09-12, 43 m at the ninetieth percentile -- and a building, narrow enough
# that the next block is somewhere else.
TRIP_SETTLE_RADIUS = 150.0
# How far a stay reaches. Somebody at dinner walks to the bar; somebody in a
# park crosses it twice in an hour. Steps inside this of where the stay began
# are the same visit rather than a new destination.
TRIP_PLACE_RADIUS = 300.0
# How long an anchor has to hold before it is somewhere they went rather than
# the road. Long enough to sit out a traffic light, short enough that a coffee
# is a destination.
TRIP_DWELL = timedelta(minutes=5)
# A trip has to get this far out to have a destination at all. Douwe's walks to
# the Puente turn round 160 m from the house and the return leg of every real
# trip passes through the same ground, so anything nearer is the doorstep.
TRIP_AWAY_FLOOR = 250.0
# Silence counts as stillness -- a phone that has arrived stops reporting, and
# waiting for a confirming fix would mean never announcing an arrival until the
# person left again. Past this, though, it means the phone is off, flat or out
# of signal, and the anchor is the last thing it said rather than where anybody
# is.
TRIP_MAX_AGE = timedelta(minutes=30)
# The fastest somebody can have been moving, in m/s, and still be said to have
# stayed put through a silence. Walking pace: anything quicker was the road.
# Silence is the only evidence a parked car and a moving one differ in, and
# they differ in it not at all — this is how the difference is settled, once
# the phone speaks again.
TRIP_STILL_SPEED = 1.4
# A trip shorter than this never happened. One bad fix hands the phone control
# for a couple of seconds; twice in the week to 2026-09-12 that put Douwe on a
# journey while he was standing in the garden.
TRIP_MIN = timedelta(minutes=2)
# A zone wider than this is a region rather than a destination. `zone.home`
# aside, Oasis's zones run from a 48 m school to a 15.6 km `CDMX` that contains
# every journey anybody here has ever made; naming a trip after that one would
# mean every trip ended in the same place.
TRIP_COARSE_ZONE = 5000.0

FRONTEND_SCRIPT_URL = f"/{DOMAIN}/welkom-activity.js"
FRONTEND_SCRIPT_VERSION = 10  # bump to cache-bust browsers when the script changes

# Dedicated ping endpoints for the frontend script. Natural frontend URLs
# (like /manifest.json) are also fetched by clients in the background, so the
# beacons need paths that only the script ever requests.
PING_CLAIM_URL = f"/{DOMAIN}/claim"
PING_SUSTAIN_URL = f"/{DOMAIN}/sustain"

# --- Pictures ---------------------------------------------------------------
# Welkom's avatars and home images are proxied from HA's own origin under this
# prefix (see images.py), mirroring welkom's /api paths: a welkom URL of
# `<welkom>/api/people/x/avatar` becomes `/welkom/people/x/avatar` here.
IMAGE_URL_PREFIX = f"/{DOMAIN}"
# ha-map-card plugin that overlays one of a home's images on the map.
MAP_IMAGE_SCRIPT_URL = f"/{DOMAIN}/map-image.js"

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


def person_trust(
    config: Mapping[str, Any],
    headers: Mapping[str, str],
    assigned_role: str | None,
    people_known: bool,
) -> bool | None:
    """Whether a request's person identity may unlock that person's own account.

    True unless ``require_full_role`` is set and the network downgraded the
    person below their assigned role (capped role != assigned role), in which
    case the MAC-derived identity isn't trustworthy here.

    ``None`` means the question cannot be answered, because welkom's people were
    never loaded. That is deliberately distinct from False: False sends the
    request on to the role map, which is only "closed" if the role account is
    the less privileged of the two — and it needn't be. Someone whose person
    account is a plain user and whose role maps to an admin one would be handed
    the admin account by an outage. Callers must decline to identify instead.

    Kept here, free of Home Assistant imports, like ``resolve_mapped_user_id``,
    so it can be unit-tested in isolation.
    """
    if not config.get(CONF_REQUIRE_FULL_ROLE, True):
        return True

    person = (headers.get(WELKOM_FIELD_HEADERS["person"]) or "").strip()
    if not person:
        return True  # no person to gate; the role/default path decides

    if assigned_role is None and not people_known:
        return None

    capped_role = (headers.get(WELKOM_FIELD_HEADERS["role"]) or "").strip()
    return assigned_role is not None and assigned_role == capped_role


def gps_tracker_entity_id(
    config: Mapping[str, Any], person_id: str, configured: str | None
) -> str | None:
    """The device_tracker entity carrying a person's phone GPS, if any.

    The integration's options win; ``configured`` is welkom's own
    ``attrs.homeassistant.gps_tracker`` on the person, the fallback, so the
    mapping can live next to the person in welkom.yml. A bare object id
    ("douwe_s_iphone") is taken to be a device_tracker. Kept here, free of
    Home Assistant imports, so it can be unit-tested in isolation.
    """
    value = config.get(CONF_PERSON_GPS_TRACKERS, {}).get(person_id) or configured
    if not value or not (value := value.strip()):
        return None
    return value if "." in value else f"device_tracker.{value}"


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
