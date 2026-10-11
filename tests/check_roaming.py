"""Standalone checks for placing a travel router by the phones on it.

Run with ``uv run python tests/check_roaming.py``. Not a pytest module, for the
same reason as ``check_location.py``: ``roaming.py`` has no Home Assistant
imports and is loaded by path.

The timeline is the real one from 2026-10-10/11: Gaby's phone at the airport
in Mexico City at 06:32, silent for 4.6 hours of flight, then New York, with
the router repeating the Belvedere Hotel's WiFi overnight while both phones
stayed quiet for hours at a time.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import importlib.util
import pathlib
import sys
import types

_ROOT = pathlib.Path(__file__).resolve().parent.parent

_pkg = types.ModuleType("welkom")
_pkg.__path__ = [str(_ROOT)]
sys.modules["welkom"] = _pkg


def _load(name: str):
    spec = importlib.util.spec_from_file_location(
        f"welkom.{name}", _ROOT / f"{name}.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[f"welkom.{name}"] = module
    spec.loader.exec_module(module)
    return module


_load("location")
roaming = _load("roaming")
Roaming = roaming.Roaming
MAX_DARK = timedelta(minutes=15)
CONFIRM = timedelta(minutes=10)

ROOM = 40.7617  # the Belvedere, on 48th Street
CAFE = ROOM + 0.004  # ~450 m up the avenue


def check(label: str, got: object, want: object) -> None:
    status = "ok  " if got == want else "FAIL"
    print(f"{status} {label}: got {got!r}, want {want!r}")
    if got != want:
        sys.exit(1)


def at(hour: int, minute: int = 0, day: int = 11) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=UTC)


LONG_AGO = at(0, 0, day=1)


def fix(
    state,
    when: datetime,
    *,
    by: str = "gaby",
    lat: float = ROOM,
    accuracy: float = 35.0,
    on_since: datetime = LONG_AGO,
):
    return roaming.locate(
        state,
        latitude=lat,
        longitude=-73.9880,
        accuracy=accuracy,
        at=when,
        by=by,
        on_since=on_since,
        max_accuracy=500.0,
        reach=100.0,
        confirm=CONFIRM,
    )


def phones(state, when: datetime, *on: str):
    return roaming.on_phones(state, set(on), when, CONFIRM)


def where(state):
    return (state.position.latitude, state.position.at) if state.position else None


HOTEL = roaming.uplink_key("wwan", "Belvedere Hotel")
PLANE = roaming.uplink_key("wwan", "United_Wi-Fi")

# --- uplink_key ---------------------------------------------------------------
check("repeater keyed by its SSID", HOTEL, "wwan:Belvedere Hotel")
check("a cable has no SSID", roaming.uplink_key("wan"), "wan")
check("no uplink", roaming.uplink_key(None, "x"), None)

# --- a night in the hotel ---------------------------------------------------------
state = roaming.on_uplink(Roaming(), HOTEL, at(0, 50))
check("first answer starts the uplink", state.uplink_since, at(0, 50))
check("...with no position yet", state.position, None)

state = fix(state, at(0, 52))
check("a phone on the router places it", where(state), (ROOM, at(0, 52)))

for hour in range(1, 9):
    state = roaming.on_uplink(state, HOTEL, at(hour))
check("hours of silence on the same uplink keep it", where(state), (ROOM, at(0, 52)))

check(
    "a sharper fix in range sharpens it",
    fix(state, at(9, 0), by="douwe", lat=ROOM + 0.0002, accuracy=10).position.by,
    "douwe",
)
check(
    "a blurrier one in range does not move it",
    where(fix(state, at(9, 0), lat=ROOM + 0.0005, accuracy=80)),
    (ROOM, at(0, 52)),
)
check(
    "a fix from before the phone joined does not count",
    fix(
        Roaming(uplink=HOTEL, uplink_since=at(0, 50)), at(9, 0), on_since=at(9, 5)
    ).position,
    None,
)
check(
    "a vague fix does not count",
    where(fix(state, at(9, 5), accuracy=1500)),
    (ROOM, at(0, 52)),
)

# --- walking out of the room ------------------------------------------------------
walked = fix(state, at(9, 1), by="douwe", lat=CAFE)
check("a fix out of range does not move it", where(walked), (ROOM, at(0, 52)))
check("...but asks the question", walked.moving_since, at(9, 1))
walked = phones(walked, at(9, 3), "douwe", "gaby")
check("still listed two minutes on: still asking", walked.moving is not None, True)
gone = phones(walked, at(9, 4), "gaby")
check("his phone dropped off: he walked out", gone.moving, None)
check("...and the router stayed", where(gone), (ROOM, at(0, 52)))
check(
    "back from the café, its fix from before he rejoined counts for nothing",
    where(fix(gone, at(9, 14), by="douwe", lat=CAFE, on_since=at(9, 15))),
    (ROOM, at(0, 52)),
)
check(
    "a fix from the room settles it the other way",
    fix(walked, at(9, 2), lat=ROOM).moving,
    None,
)

# Small steps: each within range of the last would have dragged it before.
stepped = state
for i, step in enumerate([0.0007, 0.0014, 0.0021, 0.0028]):
    stepped = fix(stepped, at(9, 1 + i), by="douwe", lat=ROOM + step)
stepped = phones(stepped, at(9, 6), "gaby")
check("walking out in short steps does not drag it", where(stepped), (ROOM, at(0, 52)))

check(
    "the router's own blur counts only up to WiFi range",
    fix(
        fix(Roaming(uplink=HOTEL, uplink_since=at(0, 50)), at(1, 0), accuracy=400),
        at(1, 5),
        by="douwe",
        lat=CAFE - 0.0005,
        accuracy=20,
    ).moving.by,
    "douwe",
)
asking = fix(state, at(9, 1), by="douwe", lat=CAFE)
went_dark = roaming.gone_dark(asking, at(9, 2), MAX_DARK)
check(
    "nothing is confirmed while the router is dark",
    phones(went_dark, at(9, 12), "douwe").position,
    state.position,
)

# Two people leaving in different directions do not confirm each other.
north = fix(state, at(9, 1), by="douwe", lat=CAFE)
south = fix(north, at(9, 5), by="gaby", lat=ROOM - 0.004)
check("one question at a time", south.moving.by, "douwe")
check("...and it dies with his phone", phones(south, at(9, 6), "gaby").moving, None)

# --- in a car on a SIM, or a plane --------------------------------------------------
driving = fix(state, at(9, 1), by="douwe", lat=CAFE)
driving = fix(driving, at(9, 6), by="douwe", lat=CAFE + 0.01)
driving = phones(driving, at(9, 8), "douwe", "gaby")
check("still on it eight minutes in: still asking", where(driving), (ROOM, at(0, 52)))
moved = phones(driving, at(9, 11), "douwe", "gaby")
check(
    "still on it ten minutes on: it went with him",
    where(moved),
    (CAFE + 0.01, at(9, 6)),
)
check("...and the question is answered", moved.moving, None)
check(
    "a later fix of his own answers it as well",
    where(fix(driving, at(9, 12), by="douwe", lat=CAFE + 0.02)),
    (CAFE + 0.02, at(9, 12)),
)

# --- set by hand -------------------------------------------------------------------
hand = roaming.Position(
    latitude=ROOM, longitude=-73.9880, accuracy=30, at=at(9, 0), by="manual"
)
check("placed by hand", roaming.place(walked, hand).position.by, "manual")
check("...which answers any question", roaming.place(walked, hand).moving, None)
check(
    "...but not on a router that has never answered",
    roaming.place(Roaming(), hand).position,
    None,
)

# --- an uplink the router can't name ------------------------------------------------
check(
    "an unknown uplink is not a move",
    roaming.on_uplink(state, None, at(9, 6)).position,
    state.position,
)
first = roaming.on_uplink(Roaming(), None, at(9, 6))
check("but an older welkom still gets started", first.uplink_since, at(9, 6))

# --- a WiFi blip, and a router in a bag ------------------------------------------
blip = roaming.gone_dark(state, at(9, 10), MAX_DARK)
check("a blip starts the dark clock", blip.dark_since, at(9, 10))
check("...and keeps the position", blip.position is not None, True)
check(
    "no fix while it is dark",
    where(fix(blip, at(9, 12), accuracy=5)),
    (ROOM, at(0, 52)),
)
back = roaming.on_uplink(blip, HOTEL, at(9, 14))
check("back on the same WiFi: it never left", where(back), (ROOM, at(0, 52)))
check("...and the clock stops", back.dark_since, None)

packed = roaming.gone_dark(blip, at(9, 26), MAX_DARK)
check("dark for longer than a blip: packed up", packed.position, None)
check("...and the uplink forgotten", packed.uplink, None)

# --- onto a plane --------------------------------------------------------------
boarding = roaming.on_uplink(state, PLANE, at(12, 40, day=10))
check("a new uplink drops the position", boarding.position, None)
check("...and starts again", boarding.uplink_since, at(12, 40, day=10))
check(
    "the gate fix from before boarding does not count",
    fix(boarding, at(12, 32, day=10)).position,
    None,
)
check(
    "a fix taken on the plane's WiFi does",
    fix(boarding, at(14, 0, day=10)).position.at,
    at(14, 0, day=10),
)

# --- the taxi from the airport -----------------------------------------------------
arriving = roaming.on_uplink(packed, HOTEL, at(23, 0, day=10))
check(
    "the phone's taxi fix, older than the router's uplink, does not count",
    fix(arriving, at(22, 50, day=10)).position,
    None,
)

# --- a restart ---------------------------------------------------------------------
saved = roaming.roaming_from_dict(roaming.roaming_as_dict(state))
check("round-trips through JSON", saved, state)
check("garbage reads as nothing", roaming.roaming_from_dict({"uplink_since": 5}), None)
check("nothing reads as nothing", roaming.roaming_from_dict(None), None)

polled = roaming.on_uplink(Roaming(), HOTEL, at(10, 0))
resumed = roaming.resume(polled, saved)
check("same uplink after a restart: still there", resumed.position, state.position)
check("...since the uplink was first seen", resumed.uplink_since, state.uplink_since)

# A restored phone tracker places the router before the router's entity has
# restored anything, and its fix may be its owner's café.
raced = fix(polled, at(10, 1), by="douwe", lat=CAFE)
check(
    "the remembered position beats one placed since the restart",
    roaming.resume(raced, saved).position,
    state.position,
)

moved_away = roaming.on_uplink(Roaming(), PLANE, at(10, 0))
check(
    "moved during the restart: forgotten",
    roaming.resume(moved_away, saved),
    moved_away,
)

waiting = roaming.resume(Roaming(), saved)
check("not answered yet: remembered, to be judged", waiting.position, state.position)
check(
    "...and judged by the first answer",
    roaming.on_uplink(waiting, PLANE, at(10, 1)).position,
    None,
)

dark_at_start = roaming.gone_dark(Roaming(), at(10, 0), MAX_DARK)
waiting = roaming.resume(dark_at_start, saved)
check("dark since the restart keeps the clock", waiting.dark_since, at(10, 0))
check(
    "...so it expires like any outage",
    roaming.gone_dark(waiting, at(10, 20), MAX_DARK).position,
    None,
)

print("all roaming checks passed")
