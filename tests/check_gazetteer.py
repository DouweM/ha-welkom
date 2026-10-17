"""Standalone checks for the gazetteer: remembered geocodes, by where.

Run with ``uv run python tests/check_gazetteer.py``. Not a pytest module, for
the same reason as ``check_places.py``: ``gazetteer.py`` has no Home Assistant
imports and is loaded by path.

The case it exists for: at 21:07 UTC on 2026-10-17 Gaby was driving at
(19.405049, -99.229612), 2.8 km from her phone's last geocode, and read as
"CDMX". A geocode somebody's phone wrote near there before should name it.
"""

from __future__ import annotations

import importlib.util
import json
import math
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


location = _load("location")
_load("trip")
places = _load("places")
gaz = _load("gazetteer")

Entry = gaz.Entry
Gazetteer = gaz.Gazetteer
Geocode = places.Geocode
distance = location.distance


def check(label: str, got: object, want: object) -> None:
    status = "ok  " if got == want else "FAIL"
    print(f"{status} {label}: got {got!r}, want {want!r}")
    if got != want:
        sys.exit(1)


def north(latitude: float, metres: float) -> float:
    return latitude + metres / 111_195.0


def east(latitude: float, longitude: float, metres: float) -> float:
    return longitude + metres / (111_195.0 * math.cos(math.radians(latitude)))


def entry(lat: float, lon: float, at: float, **values: str) -> Entry:
    return Entry(
        latitude=lat,
        longitude=lon,
        values={key: (value,) for key, value in values.items()},
        at=at,
    )


def colonia(geocode: Geocode | None) -> str | None:
    return geocode.first("sub_locality") if geocode else None


LAT, LON = 19.4193, -99.2057  # Acueducto Río Hondo

# --- nearest -------------------------------------------------------------------
g = Gazetteer(100)
g.add(entry(LAT, LON, 1.0, sub_locality="Lomas Virreyes"))
g.add(entry(north(LAT, 400), LON, 2.0, sub_locality="Lomas de Chapultepec"))
check(
    "100 m off, within 150",
    colonia(g.nearest(north(LAT, 100), LON, 150)),
    "Lomas Virreyes",
)
check("100 m off, outside 50", g.nearest(north(LAT, 100), LON, 50), None)
check(
    "the nearer of two",
    colonia(g.nearest(north(LAT, 260), LON, 300)),
    "Lomas de Chapultepec",
)
check(
    "exactly at the radius counts",
    colonia(g.nearest(LAT, LON, 0)),
    "Lomas Virreyes",
)
check("an empty gazetteer has nothing", Gazetteer(10).nearest(LAT, LON, 300), None)
found = g.nearest(north(LAT, 100), LON, 150)
check(
    "the geocode is about where it was said, not the query",
    (found.latitude, found.longitude) if found else None,
    (LAT, LON),
)
# East-west reach shrinks with latitude: a bucket of longitude is narrower in
# metres the further from the equator, and the search has to cover for it.
for lat in (0.0, 19.4, 60.0, 78.0):
    far = Gazetteer(10)
    far.add(entry(lat, east(lat, 10.0, 280), 1.0, sub_locality="East"))
    check(
        f"280 m east at {lat}° is found within 300",
        colonia(far.nearest(lat, 10.0, 300)),
        "East",
    )
    check(
        f"280 m east at {lat}° is not within 250",
        far.nearest(lat, 10.0, 250),
        None,
    )
# Across a bucket edge in every direction.
ring = Gazetteer(100)
for i in range(16):
    angle = 2 * math.pi * i / 16
    lat = north(LAT, 290 * math.cos(angle))
    ring.add(
        entry(lat, east(LAT, LON, 290 * math.sin(angle)), 1.0, sub_locality=f"R{i}")
    )
check(
    "every point of a 290 m ring is reachable within 300",
    sorted(
        colonia(
            ring.nearest(
                north(LAT, 20 * math.cos(2 * math.pi * i / 16)),
                east(LAT, LON, 20 * math.sin(2 * math.pi * i / 16)),
                300,
            )
        )
        or ""
        for i in range(16)
    ),
    sorted(f"R{i}" for i in range(16)),
)

# A radius that isn't a whole number of buckets: from the top of one bucket,
# 240 m north lands three buckets up, not two.
step = gaz.BUCKET / 111_320.0
top = (math.floor(LAT / step) + 0.9) * step
edge = Gazetteer(10)
edge.add(entry(north(top, 240), LON, 1.0, sub_locality="North"))
check(
    "240 m north from a bucket's top edge is within 250",
    colonia(edge.nearest(top, LON, 250)),
    "North",
)

# --- the grid: one spot, the newest word on it -----------------------------------
g = Gazetteer(100)
check("first is kept", g.add(entry(LAT, LON, 10.0, sub_locality="Old")), True)
check(
    "5 m on and newer replaces it",
    g.add(entry(north(LAT, 5), LON, 20.0, sub_locality="New")),
    True,
)
check("still one entry", len(g), 1)
check("the newer one answers", colonia(g.nearest(LAT, LON, 50)), "New")
check(
    "an older one there is refused",
    g.add(entry(LAT, LON, 15.0, sub_locality="Older")),
    False,
)
check("the same one again is no news", g.add(entry(LAT, LON, 20.0, x="y")), False)
check(
    "nor a geocode that names nothing",
    g.add(Entry(latitude=0.0, longitude=0.0, values={}, at=30.0)),
    False,
)
check(
    "60 m on is another spot",
    g.add(entry(north(LAT, 60), LON, 30.0, sub_locality="Next")),
    True,
)
check("two entries", len(g), 2)

# --- the cap: oldest out first ----------------------------------------------------
g = Gazetteer(3)
for i in range(4):
    g.add(entry(north(LAT, 100 * i), LON, float(i), sub_locality=f"E{i}"))
check("capped", len(g), 3)
check("the oldest is gone", g.nearest(LAT, LON, 50), None)
check("the newest stays", colonia(g.nearest(north(LAT, 300), LON, 50)), "E3")
check(
    "its bucket goes with it",
    sum(len(cells) for cells in g._buckets.values()),
    3,
)
# Replacing a cell makes it the newest, so it outlives older neighbours.
g = Gazetteer(2)
g.add(entry(LAT, LON, 1.0, sub_locality="A"))
g.add(entry(north(LAT, 100), LON, 2.0, sub_locality="B"))
g.add(entry(LAT, LON, 3.0, sub_locality="A2"))
g.add(entry(north(LAT, 200), LON, 4.0, sub_locality="C"))
check(
    "a refreshed cell is newer than the one after it",
    [e.values["sub_locality"][0] for e in g.entries()],
    ["A2", "C"],
)
check(
    "from_entries adds oldest first, whatever the order given",
    [
        e.at
        for e in Gazetteer.from_entries(
            [entry(north(LAT, 100 * i), LON, float(i), x="y") for i in (3, 1, 2, 0)],
            3,
        ).entries()
    ],
    [1.0, 2.0, 3.0],
)

# --- storage --------------------------------------------------------------------
g = Gazetteer(100)
g.add(entry(LAT, LON, 1.0, sub_locality="Lomas Virreyes", locality="Miguel Hidalgo"))
g.add(
    Entry(
        latitude=north(LAT, 100),
        longitude=LON,
        values={"area_of_interest": ("Chapultepec Park", "Bosque")},
        at=2.0,
    )
)
stored = json.loads(json.dumps(g.as_dict()))
back = Gazetteer.from_dict(stored, 100)
check("round trip through JSON", back.entries(), g.entries())
check(
    "the snapshot serialises the same",
    Gazetteer.entries_as_dict(g.entries()),
    g.as_dict(),
)
shared = Gazetteer(100)
for i in range(50):
    shared.add(entry(north(LAT, 30 * i), LON, float(i), sub_locality="Lomas Virreyes"))
check("one set of values is written once", len(shared.as_dict()["values"]), 1)
check(
    "and read back as one object",
    len({id(e.values) for e in Gazetteer.from_dict(shared.as_dict(), 100).entries()}),
    1,
)
stored["values"].append({"sub_locality": "N/A"})
stored["entries"] += [
    ["x", 1, 1, 0],
    [1, 1, 1, 2],  # values that are all blank
    [1, 1, 1, 9],  # no such values
    [1, 1, 1, -1],
    [1, 1, 1],
    "not a row",
]
check(
    "unreadable entries are dropped",
    len(Gazetteer.from_dict(stored, 100)),
    2,
)
check("nothing stored", len(Gazetteer.from_dict(None, 100)), 0)
check("something else stored", len(Gazetteer.from_dict({"entries": 3}, 100)), 0)

# --- normalisation, as places reads it -------------------------------------------
ATTRS = {
    "Sub Locality": "Lomas Virreyes",
    "Locality": "Miguel Hidalgo",
    "Areas Of Interest": "N/A",
    "Thoroughfare": " Acueducto Río Hondo ",
    "Zones": ["CDMX"],
    "Time Zone": "America/Mexico_City",
    "Location": [LAT, LON],
}
from_sensor = Geocode.from_attributes(ATTRS)
check(
    "normalise_values reads attributes the way from_attributes does",
    places.normalise_values(ATTRS),
    dict(from_sensor.values),
)
check(
    "keys normalised, blanks and unmatchable attributes dropped",
    places.normalise_values(
        {"Sub Locality": "X", "sub-locality": "Y", "Ocean": "Z", "postal code": ""}
    ),
    {"sub_locality": ("Y",)},
)
restored = Gazetteer.from_dict(
    {
        "values": [{"Sub Locality": ["lomas virreyes"], "Ocean": "N/A"}],
        "entries": [[LAT, LON, 1.0, 0]],
    },
    10,
).nearest(LAT, LON, 1)
check(
    "a stored entry is normalised as it is read",
    dict(restored.values) if restored else None,
    {"sub_locality": ("lomas virreyes",)},
)
check(
    "and its geocode holds a household matcher",
    places.matcher_holds({"sub_locality": ("Lomas Virreyes",)}, restored),
    True,
)
live = Gazetteer(10)
live.add(Entry(latitude=LAT, longitude=LON, values=from_sensor.values, at=1.0))
check(
    "a live geocode, stored and read back, holds it too",
    places.matcher_holds(
        {"sub_locality": ("Lomas Virreyes",), "locality": ("Miguel Hidalgo",)},
        Gazetteer.from_dict(json.loads(json.dumps(live.as_dict())), 10).nearest(
            LAT, LON, 1
        ),
    ),
    True,
)

# --- Gaby, 2026-10-17 21:07 UTC ------------------------------------------------------
SPECS = places.places_from_config(
    [
        {
            "name": "Lomas",
            "geocodes_as": {"sub_locality": ["Lomas de Chapultepec", "Lomas Virreyes"]},
        },
        {"name": "Roma", "geocodes_as": {"sub_locality": "Roma Norte"}},
    ]
)
GABY = (19.405049, -99.229612)
g = Gazetteer(1000)
# Earlier drives down Lomas Virreyes, every 30 m from home to the west.
for i in range(40):
    g.add(
        entry(
            north(LAT, -10 * i),
            east(LAT, LON, -30 * i),
            1000.0 + i,
            sub_locality="Lomas Virreyes",
        )
    )
# And somebody's geocode a block from where she was.
g.add(
    entry(
        north(GABY[0], 60),
        east(GABY[0], GABY[1], 40),
        2000.0,
        sub_locality="Lomas de Chapultepec",
        locality="Miguel Hidalgo",
    )
)
radius = gaz.search_radius(12.0, minimum=150.0, maximum=300.0)
borrowed = g.nearest(*GABY, radius)
check(
    "her fix finds the geocode a block away", colonia(borrowed), "Lomas de Chapultepec"
)
named = places.recognise(
    SPECS, latitude=GABY[0], longitude=GABY[1], geocode=borrowed, tighter_than=15600.0
)
check("which names the household's place", named.name if named else None, "Lomas")
check(
    "and is tighter than the city",
    places.tighter(named, than=places.Place(name="CDMX", radius=15600.0))
    if named
    else None,
    True,
)
check(
    "the drives down Lomas Virreyes are all out of reach",
    min(distance(*GABY, e.latitude, e.longitude) for e in g.entries()[:-1]) > 300,
    True,
)
alone = Gazetteer(1000)
for e in g.entries()[:-1]:
    alone.add(e)
check(
    "without the block's geocode, nothing within reach",
    alone.nearest(*GABY, radius),
    None,
)

# --- how far to look -------------------------------------------------------------------
check(
    "a sharp fix looks the minimum",
    gaz.search_radius(12.0, minimum=150.0, maximum=300.0),
    150.0,
)
check(
    "a vague one as far as it is vague",
    gaz.search_radius(220.0, minimum=150.0, maximum=300.0),
    220.0,
)
check(
    "but no further than the maximum",
    gaz.search_radius(1500.0, minimum=150.0, maximum=300.0),
    300.0,
)

print("all gazetteer checks passed")
