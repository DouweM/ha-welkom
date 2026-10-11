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

And a fix only speaks for the router if it was taken by a PHONE while that
phone was ON the router — not by somebody welkom places there through the
laptop they left in the room, and not before the phone (re)joined: the taxi
from the airport, the café on the way back, still the phone's latest fix as it
connects.

That still leaves somebody walking out. A phone stays on the router's client
list for a minute or two after its owner has left the room, and its fixes from
the street would drag the router to the café, where it would stay overnight
while the phone left in the room sleeps. So **a fix out of WiFi range of the
router is a question, not an answer**: has the phone that took it gone, or has
the router gone with it? The phone answers by staying. If it is still on the
router `confirm` later, the router moved with it — a car on a SIM, a plane —
and takes its latest fix. If it drops off first, it walked out, and the
question is dropped with it. A fix in range of the router settles it the other
way. Fixes in range never move the router either, only sharpen it, so a walk
out in short steps can't drag it along.

Pure, like `location.py` and `trip.py`: no Home Assistant imports, so
``tests/check_roaming.py`` can exercise it without booting HA.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any

from .location import distance


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
    moving: Position | None = None
    """The latest fix out of range of `position`, while it is unclear whether
    its phone walked out or the router went with it."""
    moving_since: datetime | None = None
    """When that phone's first fix out of range was taken."""


def uplink_key(interface: str | None, ssid: str | None = None) -> str | None:
    """One string for "what the router is plugged into", to compare by equality.

    The SSID and not the access point: a hotel's corridor has a dozen BSSIDs
    and the repeater hops between them without anybody moving.
    """
    if interface is None:
        return None
    return f"{interface}:{ssid}" if ssid else interface


def on_uplink(roaming: Roaming, uplink: str | None, now: datetime) -> Roaming:
    """The router answered, plugged into `uplink`.

    None is "it can't say", not "it is plugged into nothing": a router that
    answers at all is carrying traffic over something. So an unknown uplink
    leaves a known one standing rather than reading as a move.
    """
    if roaming.uplink_since is not None and uplink in (roaming.uplink, None):
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


def _moved(roaming: Roaming, position: Position) -> Roaming:
    return replace(roaming, position=position, moving=None, moving_since=None)


def locate(
    roaming: Roaming,
    *,
    latitude: float,
    longitude: float,
    accuracy: float,
    at: datetime,
    by: str,
    on_since: datetime,
    max_accuracy: float,
    reach: float,
    confirm: timedelta,
) -> Roaming:
    """Fold in a fix from `by`'s phone, which has been on the router since `on_since`.

    A fix vaguer than `max_accuracy` is a phone guessing from cell towers
    indoors and would move the router to the next block. One further than
    `reach` (WiFi range, give or take both fixes' accuracy) from the router
    only moves it once its phone has stayed on the router for `confirm`; see
    the module docstring.
    """
    if roaming.uplink_since is None or roaming.dark_since is not None:
        return roaming
    if at < max(roaming.uplink_since, on_since) or accuracy > max_accuracy:
        return roaming
    fix = Position(
        latitude=latitude, longitude=longitude, accuracy=accuracy, at=at, by=by
    )
    current = roaming.position
    if current is None:
        return _moved(roaming, fix)
    if at <= current.at:
        return roaming

    away = distance(latitude, longitude, current.latitude, current.longitude)
    # The router's own blur counts only up to WiFi range: a vague first fix
    # must not make a walker's sharp one from down the block "in range".
    if away <= reach + accuracy + min(current.accuracy, reach):
        # A phone in range: the router is where it was. Sharpened by a better
        # fix, never moved by one, and whatever looked like a move wasn't.
        position = fix if accuracy < current.accuracy else current
        return _moved(roaming, position)

    if roaming.moving is None or roaming.moving.by != by:
        if roaming.moving is not None and at - roaming.moving.at < confirm:
            # Somebody else is already being asked; one question at a time.
            return roaming
        return replace(roaming, moving=fix, moving_since=at)
    if roaming.moving_since is not None and at - roaming.moving_since >= confirm:
        return _moved(roaming, fix)
    return replace(roaming, moving=fix)


def on_phones(
    roaming: Roaming, phones: set[str], now: datetime, confirm: timedelta
) -> Roaming:
    """Whose phones are on the router now; this is how a move gets answered.

    A phone that dropped off was walking out, and its question goes with it.
    One that is still on it `confirm` after its first fix out of range has
    taken the router along, which then stands at its latest fix.
    """
    moving = roaming.moving
    if moving is None or roaming.moving_since is None:
        return roaming
    if roaming.dark_since is not None:
        # Whatever list there is now is the last one from before it went
        # dark, and a phone on it may have walked out since.
        return roaming
    if moving.by not in phones:
        return replace(roaming, moving=None, moving_since=None)
    if now - roaming.moving_since >= confirm:
        return _moved(roaming, moving)
    return roaming


def place(roaming: Roaming, position: Position) -> Roaming:
    """Put the router somewhere by hand, held like any fix until it moves."""
    if roaming.uplink_since is None or roaming.dark_since is not None:
        return roaming
    return _moved(roaming, position)


def resume(live: Roaming, remembered: Roaming) -> Roaming:
    """Take back what outlived a restart, if the router has not moved since.

    The coordinator's first poll runs before any entity can restore anything,
    so by the time this arrives the router has usually answered already. If it
    is on the uplink the position was taken on, the router is where it was —
    and the remembered position wins over anything placed since the restart,
    because nothing placed since has been through the walking-out question:
    every phone "joined" the router at the first poll, and a restored phone
    tracker can carry its owner's café as its newest fix. If it has not
    answered yet, the remembered state waits for `on_uplink` to judge it like
    any other.
    """
    if remembered.uplink_since is None:
        return live
    if live.uplink_since is None:
        # Not answered yet, or dark since the restart: the dark clock that
        # started then is the one that decides, as for any other outage.
        return replace(remembered, dark_since=live.dark_since)
    if live.uplink == remembered.uplink:
        return replace(
            live,
            uplink_since=remembered.uplink_since,
            position=remembered.position or live.position,
            moving=None,
            moving_since=None,
        )
    return live


def roaming_as_dict(roaming: Roaming) -> dict[str, Any]:
    """What a restart should hand back, as plain JSON.

    Not a pending move: whether a phone stays on the router is a question
    about the next ten minutes, and a restart is not part of the answer.
    """
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
