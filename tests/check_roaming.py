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

_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _ROOT / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


roaming = _load("roaming")
Roaming = roaming.Roaming
MAX_DARK = timedelta(minutes=15)
MAX_ACCURACY = 500.0


def check(label: str, got: object, want: object) -> None:
    status = "ok  " if got == want else "FAIL"
    print(f"{status} {label}: got {got!r}, want {want!r}")
    if got != want:
        sys.exit(1)


def at(hour: int, minute: int = 0, day: int = 11) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=UTC)


def fix(
    state,
    when: datetime,
    *,
    by: str = "gaby",
    lat: float = 40.7590,
    accuracy: float = 35.0,
):
    return roaming.locate(
        state,
        latitude=lat,
        longitude=-73.9845,
        accuracy=accuracy,
        at=when,
        by=by,
        max_accuracy=MAX_ACCURACY,
    )


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
check("a phone on the router places it", state.position.at, at(0, 52))

# Both phones then sleep for hours; the router keeps answering on the same WiFi.
for hour in range(1, 9):
    state = roaming.on_uplink(state, HOTEL, at(hour))
check("hours of silence on the same uplink keep it", state.position.at, at(0, 52))

later = fix(state, at(9, 0), by="douwe", lat=40.7591)
check("the newest fix wins, whoever took it", later.position.by, "douwe")
check(
    "an older fix arriving late is ignored",
    fix(later, at(8, 0)).position.at,
    at(9, 0),
)
check(
    "a vague fix does not move it",
    fix(state, at(9, 5), accuracy=1500).position.at,
    at(0, 52),
)

# --- a WiFi blip, and a router in a bag ------------------------------------------
blip = roaming.gone_dark(state, at(9, 10), MAX_DARK)
check("a blip starts the dark clock", blip.dark_since, at(9, 10))
check("...and keeps the position", blip.position is not None, True)
check("no fix while it is dark", fix(blip, at(9, 12)).position.at, at(0, 52))
back = roaming.on_uplink(blip, HOTEL, at(9, 14))
check("back on the same WiFi: it never left", back.position.at, at(0, 52))
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

moved = roaming.on_uplink(Roaming(), PLANE, at(10, 0))
check("moved during the restart: forgotten", roaming.resume(moved, saved), moved)

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
