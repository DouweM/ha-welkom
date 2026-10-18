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
import json
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
TURN_SLACK = 50.0

T0 = datetime(2026, 9, 12, 9, 29, tzinfo=UTC)


def check(label: str, got: object, want: object) -> None:
    status = "ok  " if got == want else "FAIL"
    print(f"{status} {label}: got {got!r}, want {want!r}")
    if got != want:
        sys.exit(1)


def step(
    trip,
    fix,
    at: float,
    place: str | None = None,
    radius: float = 100.0,
    home_places: frozenset[str] = frozenset(),
):
    """`place` is given as a bare name here; the module wants its size too, and
    only the replays at the bottom care what that size is."""
    return follow(
        trip,
        fix,
        HOME,
        T0 + timedelta(seconds=at),
        place=trip_mod.Place(name=place, radius=radius) if place else None,
        settle_radius=SETTLE_RADIUS,
        place_radius=PLACE_RADIUS,
        dwell=DWELL,
        away_floor=AWAY_FLOOR,
        still_speed=STILL_SPEED,
        turn_slack=TURN_SLACK,
        home_places=home_places,
    )


def been(trip, home_places: frozenset[str] = frozenset()) -> tuple[str | None, ...]:
    """The trip's itinerary by name, for readable expectations."""
    return tuple(
        stay.place
        for stay in trip.been_to(
            HOME,
            dwell=DWELL,
            min_trip=MIN_TRIP,
            away_floor=AWAY_FLOOR,
            still_speed=STILL_SPEED,
            home_places=home_places,
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
route = step(None, at(800), 0, "Lomas", 1006)
route = step(route, at(900), 60, "School", 48)  # the turn: brief, and tight
route = step(route, at(850), 120, "Lomas", 1006)
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


def place_of(latitude: float, longitude: float, accuracy: float):
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
            best, best_radius = trip_mod.Place(name=name, radius=radius), radius
    return best


def replay(
    fixes, *, lomas: bool = False, home_places: frozenset[str] = frozenset()
) -> Any:
    """Run a recorded journey through the follower, ticking every 30s like the
    coordinator does. Returns the trip as it stood when they got home.

    `lomas` names every fix within a kilometre of the house that no zone
    claims "Lomas", the way the geocode does now that the Lomas circles are
    gone and the colonia is a household place matched by sub-locality."""
    trip = None
    clock = 0.0
    for offset, latitude, longitude, accuracy in fixes:
        while clock + 30 <= offset:
            clock += 30
            trip = step(trip, None, clock, home_places=home_places)
        clock = offset
        found = place_of(latitude, longitude, accuracy)
        if (
            found is None
            and lomas
            and location.distance(latitude, longitude, HOME.latitude, HOME.longitude)
            < 1000
        ):
            found = trip_mod.Place(name="Lomas", radius=1000.0)
        trip = step(
            trip,
            Fix(latitude=latitude, longitude=longitude, accuracy=accuracy),
            offset,
            found.name if found else None,
            found.radius if found else 100.0,
            home_places,
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
check("...and the turn was in the park", chapu.turn_place, "Chapu II")
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

# Gaby, Thu 2026-09-17 08:53-09:06: the same school run as above, a week later,
# and the one the first turn rule got wrong. She turned at 779 m out on the road
# and the fix inside the 48 m school zone came nine seconds later at 775 m, so
# the single furthest fix named the whole journey after the one-kilometre zone
# she was driving through. Both are within `turn_slack` of the far end; the
# tighter one is what she meant.
SCHOOL_RUN_0917 = [
    (0, 19.419153, -99.206473, 5.0),
    (9, 19.419255, -99.206715, 5.0),
    (15, 19.41932, -99.206955, 5.0),
    (24, 19.419418, -99.207183, 5.0),
    (35, 19.41948, -99.20737, 5.0),
    (45, 19.419564, -99.207535, 5.0),
    (61, 19.419995, -99.207717, 12.9),
    (117, 19.418777, -99.210589, 5.0),
    (213, 19.421829, -99.212337, 5.0),
    (222, 19.421624, -99.212772, 5.0),
    (231, 19.421372, -99.212826, 5.0),
    (431, 19.420893, -99.212005, 5.0),
    (436, 19.420979, -99.211742, 5.0),
    (449, 19.421152, -99.211238, 5.0),
    (555, 19.422296, -99.207605, 5.0),
    (574, 19.422326, -99.207513, 5.0),
    (708, 19.421172, -99.205666, 5.0),
    (750, 19.41991, -99.205922, 5.0),
    (752, 19.419795, -99.205911, 5.0),
    (756, 19.419706, -99.20595, 5.0),
    (766, 19.419513, -99.205926, 5.0),
]

sept17 = replay(SCHOOL_RUN_0917)
check("the 09-17 school run turns at the school", sept17.turn_place, "School")
check("...and says so", been(sept17), ("School",))
check("...not the neighbourhood it drove through", "Lomas" in been(sept17), False)


puente = replay(EVENING_AT_THE_PUENTE)
check("the evening walk went to the bridge", "Puente" in been(puente), True)
check("...which is inside the away floor", puente.furthest < AWAY_FLOOR, True)
check("...so it only counts because it has a name", puente.ventured, False)

# --- surviving a restart -----------------------------------------------------
# The whole object has to come back, not the sensor's summary of it: `chapu` is
# a real trip with a finished stay, an open one, visits and a turn, and every
# one of those is something the next fix will be measured against.
MAX_AGE = timedelta(minutes=30)

written = trip_mod.trip_as_dict(chapu)
read_back = trip_mod.trip_from_dict(written)
assert read_back is not None
check("a trip round-trips through JSON unchanged", read_back == chapu, True)
check(
    "...including the stays that were already over",
    [stay.place for stay in read_back.stays],
    ["Puente", "Chapu II"],
)
check("...and the turn it got to", read_back.turn_place, "Chapu II")
check(
    "what was written is really JSON",
    json.loads(json.dumps(written)) == written,
    True,
)

check("nothing to read is nothing", trip_mod.trip_from_dict(None), None)
check(
    "a shape from another version is refused", trip_mod.trip_from_dict({"v": 99}), None
)
check(
    "so is one missing what a trip needs",
    trip_mod.trip_from_dict({"v": 1, "left_at": T0.isoformat()}),
    None,
)
check(
    "and so is a moment that is not one",
    trip_mod.trip_from_dict({**written, "seen_at": "half past"}),
    None,
)


# The rule `WelkomCoordinator.observe` applies before it claims a remembered
# trip: the same `stale` the sensor reports with, against the same
# `TRIP_MAX_AGE`, so nothing is resurrected that the live path would decline to
# vouch for.
def restorable(trip, now) -> bool:
    return not trip_mod.stale(trip, now, MAX_AGE)


parked = step(step(None, at(600), 0, "Chapu II"), None, 300)
check(
    "a trip whose phone spoke 29 minutes ago is still claimable",
    restorable(parked, parked.seen_at + timedelta(minutes=29)),
    True,
)
check(
    "...and one 31 minutes quiet is not",
    restorable(parked, parked.seen_at + timedelta(minutes=31)),
    False,
)
carried = trip_mod.trip_from_dict(trip_mod.trip_as_dict(parked))
assert carried is not None
check(
    "a restart of a few minutes keeps the real departure",
    carried.left_at,
    parked.left_at,
)


# --- a zone crossed at speed is the road, not a visit ------------------------
# Lomas is a kilometre across and a car is through it in three minutes. Before
# the pace test, five minutes of driving anywhere inside a named zone reported
# having BEEN there -- which is how a school run in traffic came home saying
# "Lomas". A walk through the same ground at the same length is a visit.
def _crossing(speed_ms: float, place: str = "Lomas") -> object:
    """A trip that spends six minutes inside one named zone at `speed_ms`."""
    start = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
    trip = None
    metres = 0.0
    # Seven minutes inside the zone, then on past it -- so the FAR end of the
    # trip has no name and the turn cannot put the zone back on the list. A
    # journey that ends in Lomas has been to Lomas whatever its speed; this is
    # about one that only passes through.
    for step in range(12):
        now = start + timedelta(minutes=step)
        metres += speed_ms * 60
        inside = step < 7
        trip = follow(
            trip,
            at(2000 + metres + (0 if inside else 4000), accuracy=5.0),
            HOME,
            now,
            place=trip_mod.Place(name=place, radius=1000.0) if inside else None,
            settle_radius=SETTLE_RADIUS,
            place_radius=PLACE_RADIUS,
            dwell=DWELL,
            away_floor=AWAY_FLOOR,
            still_speed=STILL_SPEED,
            turn_slack=TURN_SLACK,
        )
    return trip


walked = _crossing(1.0)
check("six minutes walked through a zone is a visit", been(walked), ("Lomas",))
driven = _crossing(8.0)
check("six minutes driven through the same zone is not", been(driven), ())

# --- A trip from a travel router ----------------------------------------------
#
# The Belvedere Hotel, where AwaySys was on 2026-10-12. A walk from there is
# measured from the hotel: two blocks out is two blocks, not 3,300 km.

HOTEL = Circle(latitude=40.7617, longitude=-73.9880, radius=55)


def walk(trip, metres_north: float, at_s: float, home=HOTEL):
    return follow(
        trip,
        Fix(
            latitude=HOTEL.latitude + metres_north / 111_000,
            longitude=HOTEL.longitude,
            accuracy=5.0,
        ),
        home,
        T0 + timedelta(seconds=at_s),
        settle_radius=SETTLE_RADIUS,
        place_radius=PLACE_RADIUS,
        dwell=DWELL,
        away_floor=AWAY_FLOOR,
        still_speed=STILL_SPEED,
        turn_slack=TURN_SLACK,
    )


stroll = walk(None, 400, 0)
check("a trip remembers the home it left from", stroll.home, HOTEL)
check("...and measures from it", round(stroll.furthest, -1), 400)
# The caller may pass the main home again later (a restart forgets the base);
# a trip under way keeps measuring from where it left.
stroll = walk(stroll, 800, 600, home=HOME)
check("a trip under way keeps its own home", stroll.home, HOTEL)
check("...so it stays a walk", round(stroll.furthest, -1), 800)
check(
    "its home survives a restart",
    trip_mod.trip_from_dict(trip_mod.trip_as_dict(stroll)),
    stroll,
)
older = {k: v for k, v in trip_mod.trip_as_dict(stroll).items() if k != "home"}
check(
    "one written before trips knew their home leaves it to the caller",
    trip_mod.trip_from_dict(older).home,
    None,
)

# --- the neighbourhood the house is in is not somewhere they went -------------
# Douwe, 2026-10-17 21:01 to 2026-10-18 07:25: out through Lomas, ten hours at
# a friend's in Jesús del Monte -- open ground, 10 km out -- and home through
# Lomas again, slowly, for the last five minutes. The door card said "arriving
# from Lomas and Jesús del Monte": Lomas first, though it came last, and at all,
# though the house is in it. The shape of it, at the house's own coordinates.
HOME_PLACES = frozenset({"Lomas"})
LOMAS = 1000.0


def night_out(home_places: frozenset[str] = frozenset()):
    trip = None
    for t, fix, place in [
        # Out: two fixes in Lomas, then open road.
        (0, at(300), "Lomas"),
        (60, at(800), "Lomas"),
        (300, at(4000, 3000), None),
        # The friend's house, all night.
        (900, at(9000, 6000), None),
        (1200, None, None),
        (18000, at(9000, 6010), None),
        # Home: back into Lomas at walking pace in traffic, six minutes of it.
        (36000, at(3000, 1000), None),
        (36060, at(900), "Lomas"),
        (36180, at(800), "Lomas"),
        (36300, at(700), "Lomas"),
        (36420, at(650), "Lomas"),
    ]:
        trip = step(
            trip, fix, t, place, LOMAS if place else 100.0, home_places=home_places
        )
    return trip


naive = night_out()
check(
    "a zone crossed on the way out and again ten hours later is two visits",
    [visit.since for visit in naive.visits],
    [T0, T0 + timedelta(seconds=36060)],
)
check(
    "...so, with nothing said about home, Lomas comes after the night out",
    been(naive),
    (None, "Lomas"),
)
check(
    "the night out, knowing the house is in Lomas, was the night out",
    been(night_out(HOME_PLACES), HOME_PLACES),
    (None,),
)
# Each of the two ways in on its own, so neither hides behind the other.
check(
    "...because follow never names a fix by the house's own colonia",
    been(night_out(HOME_PLACES)),
    (None,),
)
check(
    "...and been_to drops it from a trip that learned it before anybody knew",
    been(naive, HOME_PLACES),
    (None,),
)

# Nor the turn: an errand whose far end is still in Lomas.
errand = step(None, at(300), 0, "Lomas", LOMAS)
errand = step(errand, at(700), 60, "Lomas", LOMAS)
errand = step(errand, at(300), 180)
check("an errand inside Lomas used to turn at Lomas", been(errand), ("Lomas",))
check("...which been_to drops after the fact", been(errand, HOME_PLACES), ())
errand = step(None, at(300), 0, "Lomas", LOMAS, HOME_PLACES)
errand = step(errand, at(700), 60, "Lomas", LOMAS, HOME_PLACES)
errand = step(errand, at(300), 180, home_places=HOME_PLACES)
check("...and now has no turn to name", errand.turn_place, None)

# A wait at the gate in Lomas: settled, and named, until Lomas is home's.
gate = step(None, at(120), 0, "Lomas", LOMAS)
gate = step(gate, at(600, 400), 60, "Lomas", LOMAS)
gate = step(gate, at(130), 200, "Lomas", LOMAS)
gate = step(gate, None, 600)
check("a wait at the gate in Lomas used to be a stay in Lomas", been(gate), ("Lomas",))
check("...and been_to unnames it after the fact", been(gate, HOME_PLACES), ())
quiet_gate = step(None, at(120), 0, "Lomas", LOMAS, HOME_PLACES)
quiet_gate = step(quiet_gate, at(600, 400), 60, "Lomas", LOMAS, HOME_PLACES)
quiet_gate = step(quiet_gate, at(130), 200, "Lomas", LOMAS, HOME_PLACES)
quiet_gate = step(quiet_gate, None, 600, home_places=HOME_PLACES)
check("...and is now the doorstep", been(quiet_gate, HOME_PLACES), ())
check("...which is not a destination", quiet_gate.arrived(HOME, AWAY_FLOOR), False)

# A neighbourhood that is not home's still counts, by stay or by turn.
polanco = step(None, at(300), 0, "Lomas", LOMAS, HOME_PLACES)
polanco = step(polanco, at(-3000, 3000), 300, "Polanco", LOMAS, HOME_PLACES)
polanco = step(polanco, None, 900, home_places=HOME_PLACES)
polanco = step(polanco, at(800), 3000, "Lomas", LOMAS, HOME_PLACES)
polanco = step(polanco, at(400), 3060, "Lomas", LOMAS, HOME_PLACES)
check(
    "an afternoon in Polanco is still Polanco", been(polanco, HOME_PLACES), ("Polanco",)
)

# And places INSIDE the house's colonia are still places: the Puente is a zone
# of its own, tighter than Lomas, so that is what the fixes there are called.
puente_home = replay(EVENING_AT_THE_PUENTE, lomas=True, home_places=HOME_PLACES)
check(
    "the evening at the bridge is the bridge, in Lomas or not",
    been(puente_home, HOME_PLACES),
    ("Puente",),
)
chapu_home = replay(WALK_TO_CHAPU, lomas=True, home_places=HOME_PLACES)
check(
    "...and the park walk still went to the park, by way of the bridge",
    been(chapu_home, HOME_PLACES),
    ("Puente", "Chapu II"),
)

# Out of a zone for a moment is not leaving it: a neighbourhood is a geocode,
# and a fix whose geocode has not landed yet is open ground mid-park.
blink = step(None, at(-3000, 3000), 0, "Polanco", LOMAS)
blink = step(blink, at(-3000, 3100), 60, "Polanco", LOMAS)
blink = step(blink, at(-3000, 3200), 120)
blink = step(blink, at(-3000, 3300), 180, "Polanco", LOMAS)
check("a fix's worth of open ground does not split a visit", len(blink.visits), 1)

# Douwe, 2026-10-17 19:18-19:57, in Chapu II -- a geocoded place, so named
# only when the phone's geocode has landed. Real fixes, seconds from 19:18:25.
# His phone went quiet for sixteen minutes, and its first fix back had no
# geocode yet. Silence is not leaving, and neither is one fix outside.
CHAPU_II_EVENING = [
    (0, 19.419117717056032, -99.20327264754249, 47.3, "Chapu II"),
    (36, 19.419247740296314, -99.20264605594339, 51.2, "Chapu II"),
    (63, 19.419359861008505, -99.20240146649263, 34.6, "Chapu II"),
    (90, 19.419380292175898, -99.20238617153784, 35.1, "Chapu II"),
    (118, 19.419260115635577, -99.20293349240602, 32.5, "Chapu II"),
    (144, 19.419301682607102, -99.20250316785913, 45.8, "Chapu II"),
    (171, 19.419316379818248, -99.2024240495767, 44.4, "Chapu II"),
    (199, 19.41948069784662, -99.20237856167627, 29.3, "Chapu II"),
    (226, 19.419404458200372, -99.20210590239626, 25.7, "Chapu II"),
    (314, 19.41940678224741, -99.20182771016482, 26.6, "Chapu II"),
    (1271, 19.4170869732469, -99.19947309958762, 76.9, None),
    (1272, 19.4170869732469, -99.19947309958762, 76.9, "Chapu II"),
    # Then thirteen minutes somewhere the geocoder did not call the park, seen
    # by three fixes, and back: that IS leaving, and coming back.
    (1546, 19.417635261044587, -99.19797885920582, 15.3, None),
    (1577, 19.417501348667976, -99.19785452887086, 9.4, None),
    (1824, 19.417501348667976, -99.19785452887086, 9.4, None),
    (2325, 19.41680056486383, -99.19977134917674, 16.1, "Chapu II"),
]


def park_evening(fixes):
    trip = None
    for t, latitude, longitude, accuracy, name in fixes:
        trip = step(
            trip,
            Fix(latitude=latitude, longitude=longitude, accuracy=accuracy),
            t,
            name,
            LOMAS if name else 100.0,
        )
    return trip


quiet_park = park_evening(CHAPU_II_EVENING[:12])
check(
    "sixteen quiet minutes and a fix without its geocode are one visit",
    [(v.place, (v.until - v.since).total_seconds()) for v in quiet_park.visits],
    [("Chapu II", 1272.0)],
)
check(
    "...and thirteen minutes SEEN outside it is a second",
    [v.since - T0 for v in park_evening(CHAPU_II_EVENING).visits],
    [timedelta(0), timedelta(seconds=2325)],
)
check(
    "the visit's watch on leaving survives a restart",
    (left := park_evening(CHAPU_II_EVENING[:11])) is not None
    and trip_mod.trip_from_dict(trip_mod.trip_as_dict(left)) == left
    and left.visits[-1].outside_since is not None,
    True,
)

# Coming back in clears the watch: two blinks far apart are not one long one.
blinks = step(None, at(-3000, 3000), 0, "Polanco", LOMAS)
blinks = step(blinks, at(-3000, 3100), 60)
blinks = step(blinks, at(-3000, 3200), 120, "Polanco", LOMAS)
blinks = step(blinks, at(-3000, 3300), 1000)
blinks = step(blinks, at(-3000, 3400), 1100, "Polanco", LOMAS)
check("two brief blinks out of a visit leave it one visit", len(blinks.visits), 1)

print("all trip checks passed")
