"""Standalone checks for trip following and destination detection.

Run with ``uv run python tests/check_trip.py``. Not a pytest module, for the
same reason as ``check_location.py``: the integration is content-in-root, so
collecting a test here would import the whole package and Home Assistant.
``trip.py`` and ``location.py`` have no Home Assistant imports and are loaded
by path.

The replays at the bottom are real journeys, with Oasis's real zones, so the
rules are answerable against what actually happened rather than against a
sketch of it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import importlib.util
import pathlib
import sys
import types
from typing import Any

_ROOT = pathlib.Path(__file__).resolve().parent.parent

# `trip` imports `from .location import ...`, so the two have to be loaded as a
# package rather than as loose files.
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


location = _load("location")
trip_mod = _load("trip")

Fix, Circle = location.Fix, location.Circle
follow = trip_mod.follow

# Oasis, and the settings the integration ships.
HOME = Circle(latitude=19.41933, longitude=-99.205755, radius=55)
SETTLE_RADIUS = 150.0
PLACE_RADIUS = 300.0
DWELL = timedelta(minutes=5)
AWAY_FLOOR = 250.0
COARSE = 5000.0
MIN_TRIP = timedelta(minutes=2)
STILL_SPEED = 1.4

T0 = datetime(2026, 9, 12, 9, 29, tzinfo=UTC)


def check(label: str, got: object, want: object) -> None:
    status = "ok  " if got == want else "FAIL"
    print(f"{status} {label}: got {got!r}, want {want!r}")
    if got != want:
        sys.exit(1)


def step(trip, fix, at: float, place: str | None = None):
    return follow(
        trip,
        fix,
        HOME,
        T0 + timedelta(seconds=at),
        place=place,
        settle_radius=SETTLE_RADIUS,
        place_radius=PLACE_RADIUS,
        dwell=DWELL,
        away_floor=AWAY_FLOOR,
        still_speed=STILL_SPEED,
    )


def been(trip) -> tuple[str | None, ...]:
    """The trip's itinerary by name, for readable expectations."""
    return tuple(
        stay.place
        for stay in trip.been_to(
            HOME, dwell=DWELL, min_trip=MIN_TRIP, away_floor=AWAY_FLOOR
        )
    )


def at(metres_south: float, metres_east: float = 0.0, accuracy: float = 5.0):
    """A fix offset from the house, in meters."""
    return Fix(
        latitude=HOME.latitude - metres_south / 111_000,
        longitude=HOME.longitude + metres_east / 104_700,
        accuracy=accuracy,
    )


# --- the empty cases ---------------------------------------------------------
check("no trip without a fix", step(None, None, 0), None)

# --- a trip begins -----------------------------------------------------------
t = step(None, at(300), 0)
check("a first fix starts a trip", t is not None, True)
check("a new trip has not settled", t.settled, False)
check("a fix past the floor counts as venturing out", t.ventured, True)

# --- time alone settles it ---------------------------------------------------
check("a tick short of the dwell settles nothing", step(t, None, 299).settled, False)
t2 = step(t, None, 300)
check("a tick past the dwell settles it", t2.settled, True)
check("the stay is dated from the anchor, not the tick", t2.settled_at, t.anchor_since)
check(
    "a settled anchor past the floor is a destination",
    t2.arrived(HOME, AWAY_FLOOR),
    True,
)

# --- an unnamed spot near the house is not a destination ---------------------
near = step(step(None, at(160), 0), None, 600)
check("a long sit near the house settles", near.settled, True)
check("...but is not a destination", near.arrived(HOME, AWAY_FLOOR), False)
check("...and never counts as venturing out", near.ventured, False)

# --- unless it has a name ----------------------------------------------------
# Douwe's walks to the Puente turn round 160 m from the house, nearer than the
# floor, and are still somewhere he went.
bridge = step(step(None, at(160), 0, "Puente"), None, 600)
check("a named place that near still settles", bridge.settled, True)
check("...and IS a destination", bridge.arrived(HOME, AWAY_FLOOR), True)

# --- moving on ---------------------------------------------------------------
moved = step(t2, at(300, 400), 400)
check(
    "a fix beyond the radius re-anchors",
    moved.anchor_since,
    T0 + timedelta(seconds=400),
)
check("...and ends the stay", moved.settled, False)
check("...while the furthest point is remembered", round(moved.furthest) >= 400, True)

jitter = step(t2, at(340), 400)
check("a fix inside the radius does not re-anchor", jitter.anchor_since, t.anchor_since)
check("...and leaves the stay standing", jitter.settled, True)

# The two radii answer different questions: the anchor is about standing still,
# the place is about being somewhere.
nearby = step(t2, at(500), 400)
check(
    "a step out of the anchor but inside the place keeps the stay", nearby.settled, True
)
check("...and the stay keeps its start", nearby.settled_at, t2.settled_at)
check(
    "...while the anchor follows them", 495 <= nearby.distance_from(HOME) <= 505, True
)
check("a step beyond the place ends the stay", step(t2, at(700), 400).settled, False)
vague = step(
    t2, Fix(latitude=at(700).latitude, longitude=at(700).longitude, accuracy=450), 400
)
check(
    "...unless the fix is that vague about itself", vague.anchor_since, t2.anchor_since
)

# A zone answers the same question better: inside it, distance is irrelevant.
park = step(step(None, at(600), 0, "Chapu II"), None, 300)
check("a stay in a zone is settled", park.settled, True)
wander = step(park, at(600, 500), 400, "Chapu II")
check("crossing the zone is still the same visit", wander.settled_at, park.settled_at)
check(
    "leaving it for another zone is not",
    step(park, at(600, 500), 400, "Lomas").settled,
    False,
)
check("leaving it for open ground is not", step(park, at(600, 500), 400).settled, False)

# --- the anchor must not drift -----------------------------------------------
# Every step is inside the radius of the one before it. If the anchor followed
# the fixes, a car in slow traffic would never stop being "here".
drift = step(None, at(300), 0)
for i in range(1, 6):
    drift = step(drift, at(300 + 100 * i), 60 * i)
check("a walking anchor is not one place", drift.settled_at, None)

# --- silence -----------------------------------------------------------------
quiet = step(t2, None, 3600)
check(
    "an hour of silence is stale",
    trip_mod.stale(quiet, T0 + timedelta(seconds=3600), timedelta(minutes=30)),
    True,
)
check(
    "...but ten minutes is not",
    trip_mod.stale(quiet, T0 + timedelta(seconds=900), timedelta(minutes=30)),
    False,
)

# --- the itinerary -----------------------------------------------------------
route = step(None, at(800), 0, "Lomas")
route = step(route, at(900), 60, "School")  # the turn: brief, and furthest out
route = step(route, at(850), 120, "Lomas")
route = step(route, at(300), 180)
check(
    "a brief turn counts, and the long road through does not",
    been(route),
    ("School",),
)


# --- replays -----------------------------------------------------------------
# Oasis's real zones. Several share a name on purpose — five overlapping
# rectangles make up Chapu III — and the name is the identity, so crossing from
# one into the next is not leaving.
ZONES = [
    ("Airport", 19.436324, -99.068527, 2275),
    ("AwaySys", 0.000000, 0.000000, 100),
    ("CDMX", 19.407021, -99.190063, 15584),
    ("Chapu I", 19.420864, -99.185858, 777),
    ("Chapu II", 19.417636, -99.201093, 420),
    ("Chapu II", 19.413902, -99.199119, 547),
    ("Chapu II", 19.410897, -99.200342, 329),
    ("Chapu III", 19.402730, -99.219847, 571),
    ("Chapu III", 19.405149, -99.215480, 642),
    ("Chapu III", 19.417145, -99.206747, 188),
    ("Chapu III", 19.414975, -99.208345, 233),
    ("Chapu III", 19.411797, -99.210749, 443),
    ("Condesa", 19.414064, -99.173670, 764),
    ("Football", 19.413851, -99.203893, 106),
    ("Lomas", 19.423859, -99.215212, 1006),
    ("Lomas", 19.427258, -99.207745, 345),
    ("Molino", 19.423029, -99.204526, 326),
    ("Polanco", 19.435352, -99.194698, 1146),
    ("Puente", 19.418283, -99.205041, 57),
    ("Santa Fe", 19.361438, -99.273920, 1955),
    ("School", 19.421066, -99.213125, 48),
]


def place_of(latitude: float, longitude: float, accuracy: float) -> str | None:
    """The smallest zone the fix is in, by Home Assistant's own overlap rule.

    `COARSE` drops the ones too big to be a destination: `zone.mexico_city` is
    15.6 km across and contains every trip in this file, so calling it a place
    would mean every journey ended in the same one.
    """
    best, best_radius = None, float("inf")
    for name, zone_lat, zone_lon, radius in ZONES:
        if radius > COARSE or radius >= best_radius:
            continue
        if location.distance(latitude, longitude, zone_lat, zone_lon) - radius < max(
            accuracy, 1
        ):
            best, best_radius = name, radius
    return best


def replay(fixes) -> Any:
    """Run a recorded journey through the follower, ticking every 30s like the
    coordinator does. Returns the trip as it stood when they got home."""
    trip = None
    clock = 0.0
    for offset, latitude, longitude, accuracy in fixes:
        while clock + 30 <= offset:
            clock += 30
            trip = step(trip, None, clock)
        clock = offset
        trip = step(
            trip,
            Fix(latitude=latitude, longitude=longitude, accuracy=accuracy),
            offset,
            place_of(latitude, longitude, accuracy),
        )
    assert trip is not None
    return trip


# Douwe, Sat 2026-09-12 09:29-11:04: out through the Puente to Chapultepec II,
# an hour in the park, and back through the park on the other side.
WALK_TO_CHAPU = [
    (0, 19.418654, -99.205758, 18.8),
    (24, 19.418384, -99.205456, 24.3),
    (59, 19.418233, -99.205216, 23.5),
    (60, 19.41821, -99.205206, 17.3),
    (285, 19.418125, -99.204711, 4.3),
    (286, 19.418136, -99.204721, 4.0),
    (787, 19.419289, -99.20251, 69.9),
    (813, 19.419296, -99.202518, 48.7),
    (1273, 19.417222, -99.202513, 24.4),
    (1855, 19.417124, -99.203017, 20.6),
    (2008, 19.416973, -99.202961, 16.5),
    (2052, 19.416971, -99.202962, 27.6),
    (2554, 19.416731, -99.202882, 39.0),
    (3251, 19.416988, -99.20297, 15.6),
    (3406, 19.417088, -99.203015, 13.0),
    (3418, 19.417121, -99.203131, 4.6),
    (3524, 19.41702, -99.202975, 18.7),
    (3820, 19.41866, -99.202463, 24.7),
    (4440, 19.419196, -99.202729, 34.5),
    (4484, 19.419133, -99.203019, 34.3),
    (4494, 19.419247, -99.202606, 63.8),
    (4541, 19.417938, -99.205036, 72.5),
    (4552, 19.418459, -99.204541, 35.4),
    (5482, 19.418649, -99.20588, 9.8),
    (5513, 19.418973, -99.206448, 20.0),
    (5537, 19.419198, -99.206741, 26.4),
    (5549, 19.419247, -99.205779, 13.8),
    (5630, 19.419499, -99.205902, 16.5),
]

chapu = replay(WALK_TO_CHAPU)
# The bridge is on the list because his phone went quiet for eight minutes
# after its last fix there, and silence is read as stillness — he may well have
# sat on it. Walking out through a place and stopping in it are the same
# picture from here, and on the way home the two collapse into one entry.
check(
    "the park walk went to the park, by way of the bridge",
    been(chapu),
    ("Puente", "Chapu II"),
)
check("...and the turn was in the park", chapu.furthest_place, "Chapu II")
check("...about 400m out", 380 <= chapu.furthest <= 430, True)

# Gaby, Mon 2026-09-07 12:53-13:18: drive to school, drop off, drive back. The
# whole point of the trip is a place she is inside for seven seconds of
# reporting -- and then parked, silent, for fourteen minutes.
SCHOOL_RUN = [
    (0, 19.41885, -99.206116, 5.0),
    (11, 19.41918, -99.206528, 5.0),
    (15, 19.419264, -99.206806, 5.0),
    (19, 19.419368, -99.207044, 5.0),
    (23, 19.419465, -99.207325, 5.0),
    (29, 19.419578, -99.207553, 5.0),
    (38, 19.420015, -99.207721, 5.0),
    (51, 19.42045, -99.207881, 5.0),
    (86, 19.41948, -99.209703, 5.0),
    (251, 19.421829, -99.21236, 5.0),
    (265, 19.421578, -99.212761, 5.0),
    (272, 19.421344, -99.212841, 5.0),
    (1146, 19.420809, -99.212167, 5.0),
    (1150, 19.420914, -99.21188, 5.0),
    (1165, 19.421177, -99.211116, 5.0),
    (1311, 19.422061, -99.205794, 5.0),
    (1353, 19.421072, -99.205729, 5.0),
    (1367, 19.420585, -99.205856, 5.0),
    (1372, 19.420327, -99.20591, 5.0),
    (1380, 19.419854, -99.205948, 5.0),
    (1386, 19.419613, -99.20595, 5.0),
    (1414, 19.419496, -99.205924, 2.6),
]

school = replay(SCHOOL_RUN)
check("the school run went to school", been(school), ("School",))
check(
    "...and Lomas, four minutes of driving through it, is not on the list",
    "Lomas" in been(school),
    False,
)


# Douwe, Wed 2026-09-09 22:00-22:19: out to the bridge, ten minutes there, home.
# Never more than 177 m from the house.
EVENING_AT_THE_PUENTE = [
    (0, 19.418634, -99.205883, 21.2),
    (0, 19.418448, -99.205545, 38.5),
    (1, 19.418504, -99.205752, 38.5),
    (29, 19.418284, -99.205387, 25.4),
    (45, 19.418064, -99.205285, 14.0),
    (151, 19.418135, -99.204977, 10.7),
    (609, 19.418031, -99.204787, 6.3),
    (956, 19.418842, -99.205784, 40.8),
    (966, 19.418586, -99.205922, 33.7),
    (1007, 19.418667, -99.206017, 32.1),
    (1024, 19.418865, -99.206189, 24.4),
    (1046, 19.419085, -99.206192, 22.5),
    (1103, 19.419515, -99.205892, 14.0),
]

puente = replay(EVENING_AT_THE_PUENTE)
check("the evening walk went to the bridge", "Puente" in been(puente), True)
check("...which is inside the away floor", puente.furthest < AWAY_FLOOR, True)
check("...so it only counts because it has a name", puente.ventured, False)

print("all trip checks passed")
