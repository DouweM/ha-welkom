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


def placement_holds(home: Circle | None, fix: Fix | None, max_age: timedelta) -> bool:
    """Whether welkom's placement of a person at ``home`` survives their phone's fix.

    It holds unless a *fresh* fix puts the phone clearly outside the home's
    zone. Without a zone to check against, or without a fix, welkom is the only
    evidence there is. A stale fix isn't evidence against welkom either: a
    phone that hasn't reported in a while may be dead or left on a desk
    somewhere while its owner is home with their laptop, and welkom seeing that
    laptop is the more current fact.
    """
    if home is None or fix is None:
        return True
    if fix.age > max_age:
        return True
    return fix_in_circle(fix, home)
