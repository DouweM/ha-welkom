"""Where a roaming home is: a travel router, placed by the phones on it.

A travel router has no GPS, so its position is borrowed from the phones
connected to it — a fresh fix from one of them is, to within WiFi range, a fix
on the router. The hard part is not finding a position but knowing when to stop
believing one. The phones on a router in a hotel room go quiet for hours at a
time, overnight, exactly as they do on one in the overhead bin, so silence says
nothing either way.

What does say something is the router's UPLINK: the network it is repeating, the
cable, the SIM. A router cannot change hotels, or board a plane, without
changing what it is plugged into. So a position is held for exactly as long as
the uplink it was taken on, and no longer:

- **The uplink changes**: the router moved, or may have. The position goes,
  until a phone on the new uplink reports one.
- **The router goes dark** for longer than a WiFi blip: it was packed up. The
  position goes with it.
- **A fix older than the uplink** describes somewhere the router was not: the
  taxi from the airport, still the phone's latest fix as it joins the hotel's
  router. Only fixes taken on the current uplink count.

No timers beyond the dark one, and nothing about flights or cars: a router on a
plane's WiFi is on a new uplink, and one on a SIM in a moving car is followed
by whichever phone reports last, the same as one standing still.

Pure, like `location.py` and `trip.py`: no Home Assistant imports, so
``tests/check_roaming.py`` can exercise it without booting HA.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any


@dataclass(frozen=True, kw_only=True)
class Position:
    """A fix one of the phones on the router took, standing in for the router's."""

    latitude: float
    longitude: float
    accuracy: float
    at: datetime
    """When the phone took the fix."""
    by: str
    """The person whose phone it was."""


@dataclass(frozen=True, kw_only=True)
class Roaming:
    """What is known about a roaming home's whereabouts."""

    uplink: str | None = None
    """What the router is plugged into, as `uplink_key` spells it; None while
    it is unknown (an older welkom, or a router that has never answered)."""
    uplink_since: datetime | None = None
    """When the router was first seen on `uplink`. No fix from before this
    describes where it is now."""
    dark_since: datetime | None = None
    """When the router stopped answering, while it is not answering."""
    position: Position | None = None


def uplink_key(interface: str | None, ssid: str | None = None) -> str | None:
    """One string for "what the router is plugged into", to compare by equality.

    The SSID and not the access point: a hotel's corridor has a dozen BSSIDs
    and the repeater hops between them without anybody moving.
    """
    if interface is None:
        return None
    return f"{interface}:{ssid}" if ssid else interface


def on_uplink(roaming: Roaming, uplink: str | None, now: datetime) -> Roaming:
    """The router answered, plugged into `uplink`."""
    if roaming.uplink_since is not None and uplink == roaming.uplink:
        if roaming.dark_since is None:
            return roaming
        # Back from a blip on the same network: it never left.
        return replace(roaming, dark_since=None)
    return Roaming(uplink=uplink, uplink_since=now)


def gone_dark(roaming: Roaming, now: datetime, max_dark: timedelta) -> Roaming:
    """The router did not answer.

    Briefly, that is a hotel's WiFi dropping out and the position stands. For
    longer it is a router in a bag, and wherever it comes back up is somewhere
    nobody has a fix for yet.
    """
    dark_since = roaming.dark_since or now
    if now - dark_since > max_dark:
        return Roaming(dark_since=dark_since)
    if roaming.dark_since is None:
        return replace(roaming, dark_since=dark_since)
    return roaming


def locate(
    roaming: Roaming,
    *,
    latitude: float,
    longitude: float,
    accuracy: float,
    at: datetime,
    by: str,
    max_accuracy: float,
) -> Roaming:
    """Fold in a fix from the phone of somebody connected to the router.

    The newest fix wins, whoever took it: everybody on the router is within
    WiFi range of it, and when the router moves they all move with it. A fix
    vaguer than `max_accuracy` is a phone guessing from cell towers indoors and
    would move the router to the next block.
    """
    if roaming.uplink_since is None or roaming.dark_since is not None:
        return roaming
    if at < roaming.uplink_since or accuracy > max_accuracy:
        return roaming
    if roaming.position is not None and at <= roaming.position.at:
        return roaming
    return replace(
        roaming,
        position=Position(
            latitude=latitude,
            longitude=longitude,
            accuracy=accuracy,
            at=at,
            by=by,
        ),
    )


def resume(live: Roaming, remembered: Roaming) -> Roaming:
    """Take back what outlived a restart, if the router has not moved since.

    The coordinator's first poll runs before any entity can restore anything,
    so by the time this arrives the router has usually answered already — on
    an uplink first seen just now, with no position. If it is the uplink the
    position was taken on, the router is where it was, and the restart is not
    a reason to forget that. If it has not answered yet, the remembered state
    waits for `on_uplink` to judge it like any other.
    """
    if remembered.uplink_since is None:
        return live
    if live.uplink_since is None:
        # Not answered yet, or dark since the restart: the dark clock that
        # started then is the one that decides, as for any other outage.
        return replace(remembered, dark_since=live.dark_since)
    if live.uplink == remembered.uplink and live.position is None:
        return replace(
            live, uplink_since=remembered.uplink_since, position=remembered.position
        )
    return live


def roaming_as_dict(roaming: Roaming) -> dict[str, Any]:
    """Everything, as plain JSON, for a restart to hand back."""
    position = roaming.position
    return {
        "uplink": roaming.uplink,
        "uplink_since": roaming.uplink_since.isoformat()
        if roaming.uplink_since
        else None,
        "position": {
            "latitude": position.latitude,
            "longitude": position.longitude,
            "accuracy": position.accuracy,
            "at": position.at.isoformat(),
            "by": position.by,
        }
        if position
        else None,
    }


def roaming_from_dict(data: Mapping[str, Any] | None) -> Roaming | None:
    """Read back what `roaming_as_dict` wrote, or None if it cannot be trusted."""
    if not data:
        return None
    try:
        uplink_since = (
            datetime.fromisoformat(data["uplink_since"])
            if data.get("uplink_since")
            else None
        )
        raw = data.get("position")
        position = (
            Position(
                latitude=float(raw["latitude"]),
                longitude=float(raw["longitude"]),
                accuracy=float(raw["accuracy"]),
                at=datetime.fromisoformat(raw["at"]),
                by=str(raw["by"]),
            )
            if raw
            else None
        )
        return Roaming(
            uplink=data.get("uplink"), uplink_since=uplink_since, position=position
        )
    except (KeyError, TypeError, ValueError):
        return None
