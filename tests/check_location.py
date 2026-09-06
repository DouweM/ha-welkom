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
