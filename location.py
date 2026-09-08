"""Merge welkom's network placement with a phone's GPS fix.

Welkom sees *devices* on the network. That is authoritative for "which room"
while the person is home, but it overshoots at the edges: a phone still
associated to the garden access point from the street, or a client the
controller hasn't aged out yet after the person drove off, both still read as
"home" for a few minutes. The phone's own GPS fix is the better evidence
there. This module holds that decision as pure functions, free of Home
Assistant imports, so ``tests/check_location.py`` can exercise it without
booting HA.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import math

_EARTH_RADIUS_M = 6_371_008.8


@dataclass(frozen=True, kw_only=True)
class Fix:
    """A GPS fix: where a phone says it is, how sure it is, and how old that is."""

    latitude: float
    longitude: float
    accuracy: float = 0.0
    """Radius in meters the true position is within."""
    age: timedelta = timedelta(0)
    """How long ago the phone reported this fix."""


@dataclass(frozen=True, kw_only=True)
class Circle:
    """A zone's footprint: its center and radius in meters."""

    latitude: float
    longitude: float
    radius: float


def distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two points, in meters."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    return 2 * _EARTH_RADIUS_M * math.asin(math.sqrt(a))


def fix_in_circle(fix: Fix, circle: Circle) -> bool:
    """Whether the fix may lie inside the circle.

    The same rule Home Assistant uses for zone membership: the fix's accuracy
    disc overlapping the zone counts as being in it, so an imprecise fix near
    the edge doesn't read as "outside".
    """
    center_distance = distance(
        fix.latitude, fix.longitude, circle.latitude, circle.longitude
    )
    return center_distance - circle.radius < fix.accuracy


def fix_clearly_in_circle(fix: Fix, circle: Circle) -> bool:
    """Whether the fix lies inside the circle even at its blurriest.

    The whole accuracy disc has to fit, so a fix that merely *could* be in the
    zone doesn't count. This is the stricter half of the hysteresis: an
    imprecise fix from down the street overlaps a house-sized zone easily, and
    that is not enough to declare someone back home.
    """
    center_distance = distance(
        fix.latitude, fix.longitude, circle.latitude, circle.longitude
    )
    return center_distance + fix.accuracy <= circle.radius


def placement_holds(
    home: Circle | None,
    fix: Fix | None,
    max_age: timedelta,
    *,
    gps_in_charge: bool = False,
) -> bool:
    """Whether welkom's placement of a person at ``home`` survives their phone's fix.

    Asymmetric on purpose, because the question changes once someone has left.

    While welkom is speaking, its placement holds unless a *fresh* fix puts the
    phone clearly outside the home's zone: a room reading is worth keeping
    until the phone contradicts it beyond doubt.

    Once the phone has taken over (``gps_in_charge``), the reverse applies —
    welkom only gets the person back when a fresh fix is *clearly* inside the
    home. Without that, one imprecise fix from down the street, whose accuracy
    disc happens to graze the home zone, hands the person back to a network
    placement that says they are standing in the hall.

    Without a zone to check against, or without a fix, welkom is the only
    evidence there is. A stale fix isn't evidence either way: a phone that
    hasn't reported in a while may be dead or left on a desk somewhere while
    its owner is home with their laptop, and welkom seeing that laptop is the
    more current fact.
    """
    if home is None or fix is None:
        return True
    if fix.age > max_age:
        return True
    if gps_in_charge:
        return fix_clearly_in_circle(fix, home)
    return fix_in_circle(fix, home)


def placement_lingers(
    home: Circle | None, fix: Fix | None, since: timedelta, max_hold: timedelta
) -> bool:
    """Whether a placement welkom has *stopped* reporting is still worth keeping.

    An idle phone on WiFi goes quiet for minutes at a time, and the network
    controller ages it out before it speaks again, so welkom loses a person who
    hasn't moved. Dropping to a bare "home" (from the phone's GPS) for those
    minutes reads as a room change that never happened. Keep the last room
    while the phone's fix still sits within the home and the placement is
    recent enough (``since`` is how long ago welkom last confirmed it). Without
    a fix, or with one outside the home, there is nothing backing the room up.

    This only answers "has the phone moved since"; the caller must also know
    that the person never *left* in between. A room resurrected after a walk
    around the block would be a placement nobody ever observed — see
    ``device_tracker.WelkomTracker._async_update_attrs``.
    """
    if home is None or fix is None:
        return False
    if since > max_hold:
        return False
    return fix_in_circle(fix, home)
