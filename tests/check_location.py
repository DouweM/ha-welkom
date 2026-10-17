"""Standalone checks for the welkom-placement vs phone-fix merge.

Run with ``uv run python tests/check_location.py``. Not a pytest module, for
the same reason as ``check_resolution.py``: the integration is content-in-root,
so collecting a test here would import the whole package and Home Assistant.
``location.py`` and ``const.py`` have no Home Assistant imports and are loaded
by path.
"""

from __future__ import annotations

from datetime import timedelta
import importlib.util
import pathlib
import sys

_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _ROOT / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolves string annotations through sys.modules
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


location = _load("location")
const = _load("const")

Fix, Circle = location.Fix, location.Circle
holds = location.placement_holds
MAX_AGE = timedelta(minutes=15)

# Oasis: zone.home, 55 m radius; the room zones sit inside it.
HOME = Circle(latitude=19.41933, longitude=-99.205755, radius=55)


def check(label: str, got: object, want: object) -> None:
    status = "ok  " if got == want else "FAIL"
    print(f"{status} {label}: got {got!r}, want {want!r}")
    if got != want:
        sys.exit(1)


# --- distance ---------------------------------------------------------------
# Puente is ~170 m south-east of the house; a 1° step of latitude is ~111 km.
check(
    "distance home->puente ~170m",
    round(location.distance(19.41933, -99.205755, 19.417972, -99.205057))
    in range(160, 180),
    True,
)
check(
    "distance is symmetric",
    location.distance(0, 0, 0, 1) == location.distance(0, 1, 0, 0),
    True,
)
check("distance of a point to itself", location.distance(19.4, -99.2, 19.4, -99.2), 0.0)

# --- fix_in_circle: HA's overlap rule ----------------------------------------
inside = Fix(latitude=19.419231, longitude=-99.205796, accuracy=6)
check("fix in a bedroom is in home", location.fix_in_circle(inside, HOME), True)
puente_precise = Fix(latitude=19.417972, longitude=-99.205057, accuracy=10)
check(
    "precise fix at puente is outside home",
    location.fix_in_circle(puente_precise, HOME),
    False,
)
puente_blurry = Fix(latitude=19.417972, longitude=-99.205057, accuracy=150)
check(
    "blurry fix overlapping home counts as inside",
    location.fix_in_circle(puente_blurry, HOME),
    True,
)

# --- placement_holds ---------------------------------------------------------
check("no fix: welkom holds", holds(HOME, None, MAX_AGE), True)
check("no zone for the home: welkom holds", holds(None, puente_precise, MAX_AGE), True)
check("fresh fix inside: welkom holds", holds(HOME, inside, MAX_AGE), True)
check(
    "fresh fix clearly outside: phone wins", holds(HOME, puente_precise, MAX_AGE), False
)
check(
    "fresh blurry fix near the edge: welkom holds",
    holds(HOME, puente_blurry, MAX_AGE),
    True,
)
stale_away = Fix(latitude=19.44, longitude=-99.19, accuracy=10, age=timedelta(hours=2))
check("stale fix far away: welkom holds", holds(HOME, stale_away, MAX_AGE), True)
just_fresh = Fix(latitude=19.44, longitude=-99.19, accuracy=10, age=MAX_AGE)
check("fix exactly max_age old still counts", holds(HOME, just_fresh, MAX_AGE), False)

# --- fix_clearly_in_circle: the whole accuracy disc has to fit ----------------
clearly = location.fix_clearly_in_circle
check("precise fix in a bedroom is clearly in home", clearly(inside, HOME), True)
check("blurry fix grazing home is not clearly in", clearly(puente_blurry, HOME), False)
check(
    "77m out with 25m accuracy is not clearly in",
    clearly(Fix(latitude=19.41864, longitude=-99.20544, accuracy=25), HOME),
    False,
)

# --- placement_lingers: hold the room while the phone stays home -------------
lingers = location.placement_lingers
check(
    "recent loss, phone inside: keep the room",
    lingers(HOME, inside, timedelta(minutes=2), MAX_AGE),
    True,
)
check(
    "loss too long ago: let go",
    lingers(HOME, inside, timedelta(minutes=20), MAX_AGE),
    False,
)
check(
    "phone outside: let go",
    lingers(HOME, puente_precise, timedelta(minutes=1), MAX_AGE),
    False,
)
check("no fix: let go", lingers(HOME, None, timedelta(minutes=1), MAX_AGE), False)
check(
    "no home zone: let go", lingers(None, inside, timedelta(minutes=1), MAX_AGE), False
)


# --- hysteresis: tonight's walk to the Puente, replayed -----------------------
# Distances/accuracies as recorded 2026-09-07 18:24-18:30 CDMX, when a blurry
# fix on the way out (and another on the way back) briefly put the person in
# the Hall, 3 m from the house, while they were most of a block away.
def fix_at(metres: float, accuracy: float, age: timedelta = timedelta(0)) -> object:
    """A fix `metres` due south of the house, blurred by `accuracy`."""
    return Fix(
        latitude=HOME.latitude - metres / 111_000,
        longitude=HOME.longitude,
        accuracy=accuracy,
        age=age,
    )


leaving = fix_at(90, 35)
check("90m/35m breaks welkom's placement", holds(HOME, leaving, MAX_AGE), False)
blurry_out = fix_at(79, 29)
check(
    "79m/29m used to hand the person back (the jump)",
    holds(HOME, blurry_out, MAX_AGE),
    True,
)
check(
    "79m/29m no longer does, once gps is in charge",
    holds(HOME, blurry_out, MAX_AGE, gps_in_charge=True),
    False,
)
returning_blurry = fix_at(71, 50)
check(
    "71m/50m on the way back stays gps",
    holds(HOME, returning_blurry, MAX_AGE, gps_in_charge=True),
    False,
)
back_inside = fix_at(22, 17)
check(
    "22m/17m hands the person back to welkom",
    holds(HOME, back_inside, MAX_AGE, gps_in_charge=True),
    True,
)
check(
    "a phone that went quiet hands back either way",
    holds(HOME, fix_at(5000, 10, age=timedelta(hours=1)), MAX_AGE, gps_in_charge=True),
    True,
)

# --- at home by the phone alone ---------------------------------------------
# Walking in, the phone has the person inside the home zone a minute or two
# before welkom sees it on the WiFi. The home zone is not a place `place_at`
# names, so the tracker used to read as the neighbourhood the house is in. It
# is home without a room: entered on a fix entirely inside the zone, kept
# while a fix may still be inside.
at_home = location.fix_at_home
street = fix_at(64, 23)
check(
    "64m/23m in the street: not coming home yet",
    at_home(HOME, street, was_home=False),
    False,
)
check(
    "...but not leaving either, once home", at_home(HOME, street, was_home=True), True
)
check(
    "79m/29m from the street stays out",
    at_home(HOME, blurry_out, was_home=False),
    False,
)
check("90m/35m is out, home or not", at_home(HOME, leaving, was_home=True), False)
check("no home zone: never home", at_home(None, back_inside, was_home=True), False)


def walk(fixes: list[object], was_home: bool = False) -> list[bool]:
    """The tracker's rule over successive fixes: each answer feeds the next."""
    said = []
    for fix in fixes:
        was_home = at_home(HOME, fix, was_home=was_home)
        said.append(was_home)
    return said


# The approach: the street, the gate, then indoors where the phone's
# accuracy balloons past the zone's own radius.
check(
    "coming in: street, gate, indoors",
    walk(
        [fix_at(64, 23), fix_at(27, 24), fix_at(10, 12), fix_at(23, 35), fix_at(23, 35)]
    ),
    [False, True, True, True, True],
)
check(
    "indoors at 65m accuracy keeps them home, tick after tick",
    walk([fix_at(15, 10), fix_at(15, 65), fix_at(15, 65), fix_at(30, 65)]),
    [True, True, True, True],
)
check(
    "...but never brings them home on its own",
    walk([fix_at(15, 65), fix_at(23, 35)]),
    [False, False],
)
check(
    "walking out: home until no fix could be inside",
    walk(
        [fix_at(20, 15), fix_at(64, 23), fix_at(90, 20), fix_at(50, 20)], was_home=True
    ),
    [True, True, False, False],
)

# --- gps_tracker_entity_id: option wins, welkom attr is the fallback -----------
resolve = const.gps_tracker_entity_id
OPTS = {const.CONF_PERSON_GPS_TRACKERS: {"douwe": "device_tracker.douwe_s_iphone"}}
check(
    "option wins over welkom attr",
    resolve(OPTS, "douwe", "other_phone"),
    "device_tracker.douwe_s_iphone",
)
check(
    "welkom attr as fallback, domain added",
    resolve(OPTS, "gaby", "gabys_iphone"),
    "device_tracker.gabys_iphone",
)
check(
    "welkom attr with a domain kept as is",
    resolve({}, "gaby", "device_tracker.gabys_iphone"),
    "device_tracker.gabys_iphone",
)
check("nothing configured", resolve({}, "belem", None), None)
check("blank attr is nothing", resolve({}, "belem", "  "), None)

print("all location checks passed")
