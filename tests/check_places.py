"""Standalone checks for household places: geocode matchers and polygons.

Run with ``uv run python tests/check_places.py``. Not a pytest module, for the
same reason as ``check_trip.py``: the integration is content-in-root, so
collecting a test here would import the whole package and Home Assistant.
``places.py`` has no Home Assistant imports and is loaded by path.

The geocodes below are what the companion app's geocoded-location sensor
actually wrote, attribute names and blanks included, over the two weeks to
2026-09-20 — so the matchers are checked against Apple's real spelling of
these places rather than against a guess at it.
"""

from __future__ import annotations

import importlib.util
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


_load("location")
_load("trip")
places = _load("places")

Geocode = places.Geocode
recognise = places.recognise
places_from_config = places.places_from_config


def check(label: str, got: object, want: object) -> None:
    status = "ok  " if got == want else "FAIL"
    print(f"{status} {label}: got {got!r}, want {want!r}")
    if got != want:
        sys.exit(1)


def geocode(**attrs: object) -> Geocode:
    """A geocode the way the companion app writes one, blanks and all."""
    base = {
        "Administrative Area": "CDMX",
        "Areas Of Interest": "N/A",
        "Country": "Mexico",
        "Inland Water": "N/A",
        "ISO Country Code": "MX",
        "Locality": "Miguel Hidalgo",
        "Ocean": "N/A",
        "Sub Administrative Area": "Mexico City",
        "Time Zone": "America/Mexico_City",
        "Zones": ["CDMX"],
    }
    base.update(attrs)
    return Geocode.from_attributes(base)


# --- what Apple said, verbatim ---------------------------------------------------
AT_HOME = geocode(
    **{
        "Sub Locality": "Lomas Virreyes",
        "Thoroughfare": "Acueducto Río Hondo",
        "Sub Thoroughfare": "330",
        "Postal Code": "11000",
        "Name": "Acueducto Río Hondo 330",
        "Location": [19.419314969341126, -99.20573935316176],
    }
)
CHAPU_II = geocode(
    **{
        "Sub Locality": "Bosque de Chapultepec II",
        "Areas Of Interest": ["Chapultepec Park"],
        "Thoroughfare": "Avenida de Los Compositores",
    }
)
CHAPU_III_ROAD = geocode(
    **{
        "Sub Locality": "Bosque de Chapultepec III",
        "Thoroughfare": "Avenida de Las Lomas",
    }
)
LOMAS = geocode(
    **{
        "Sub Locality": "Lomas de Chapultepec",
        "Thoroughfare": "Boulevard de Los Virreyes",
    }
)
JUAREZ = geocode(
    **{
        "Sub Locality": "Juárez",
        "Locality": "Cuauhtémoc",
        "Thoroughfare": "Calle Liverpool",
    }
)
POLANCO_IV = geocode(
    **{"Sub Locality": "Polanco IV Sección", "Areas Of Interest": ["Chapultepec Park"]}
)
SATELITE = geocode(
    **{
        "Sub Locality": "Ciudad Satélite",
        "Locality": "Naucalpan de Juárez",
        "Administrative Area": "Estado de México",
        "Thoroughfare": "Circuito Economistas",
    }
)
DEPORTIVA = geocode(
    **{
        "Sub Locality": "Granjas México",
        "Locality": "Iztacalco",
        "Areas Of Interest": ["Ciudad Deportiva Magdalena Mixhuca"],
        "Location": [19.405073184196024, -99.09801005117949],
    }
)

# --- the household's places, as welkom.yml would carry them ----------------------
CONFIG = [
    {"name": "Chapu I", "geocodes_as": {"sub_locality": "Bosque de Chapultepec I"}},
    {"name": "Chapu II", "geocodes_as": {"sub_locality": "Bosque de Chapultepec II"}},
    {"name": "Chapu III", "geocodes_as": {"sub_locality": "Bosque de Chapultepec III"}},
    # The park as a whole, by area of interest: tighter than a sub-locality by
    # rank, so it has to be listed AFTER the sections and lose to them -- see
    # the precedence check below.
    {
        "name": "Lomas",
        "geocodes_as": {"sub_locality": ["Lomas de Chapultepec", "Lomas Virreyes"]},
    },
    {"name": "Roma", "geocodes_as": {"sub_locality": ["Roma Norte", "roma sur"]}},
    {"name": "Juárez", "geocodes_as": {"Sub Locality": "Juárez"}},
    {
        "name": "Polanco",
        "geocodes_as": [
            {"sub_locality": "Polanco"},
            {"sub_locality": "Polanco IV Sección"},
        ],
    },
    # All-of: the colonia AND the town, so a same-named colonia elsewhere
    # cannot claim it.
    {
        "name": "Satélite",
        "geocodes_as": {
            "sub_locality": "Ciudad Satélite",
            "locality": "Naucalpan de Juárez",
        },
    },
    {
        "name": "Sports",
        "geocodes_as": {"area_of_interest": "Ciudad Deportiva Magdalena Mixhuca"},
    },
    # A hand-drawn triangle around the Molino shops, ~150 m on a side.
    {
        "name": "Molino",
        "polygon": [[19.4238, -99.2052], [19.4224, -99.2038], [19.4222, -99.2058]],
    },
    # And the ones that must be dropped rather than break anything.
    {"geocodes_as": {"sub_locality": "Nameless"}},
    {"name": "Empty"},
    {"name": "Blank matcher", "geocodes_as": {"sub_locality": "N/A"}},
    {"name": "Two points", "polygon": [[1, 2], [3, 4]]},
    "not even a mapping",
]
SPECS = places_from_config(CONFIG)


def where(geo: Geocode | None, lat: float = 19.41, lon: float = -99.20, **kw: object):
    place = recognise(SPECS, latitude=lat, longitude=lon, geocode=geo, **kw)
    return place.name if place else None


# --- config reading ------------------------------------------------------------
check(
    "bad entries are dropped, good ones kept",
    [s.name for s in SPECS],
    [
        "Chapu I",
        "Chapu II",
        "Chapu III",
        "Lomas",
        "Roma",
        "Juárez",
        "Polanco",
        "Satélite",
        "Sports",
        "Molino",
    ],
)
check(
    "a matcher normalises its attribute name",
    SPECS[5].geocodes_as[0],
    {"sub_locality": ("Juárez",)},
)
check("a list of matchers is several", len(SPECS[6].geocodes_as), 2)

# --- reading a geocode ---------------------------------------------------------
check("blanks are not values", AT_HOME.get("area_of_interest"), ())
check(
    "an area of interest list is kept whole",
    CHAPU_II.get("Areas Of Interest"),
    ("Chapultepec Park",),
)
check(
    "position comes from Location",
    (round(AT_HOME.latitude or 0, 5), round(AT_HOME.longitude or 0, 5)),
    (19.41931, -99.20574),
)
check(
    "a geocode describes the fix it was taken at",
    AT_HOME.describes(19.419315, -99.205739, 50),
    True,
)
check("and not one 300 m away", AT_HOME.describes(19.4220, -99.2057, 50), False)
check(
    "a geocode with no position describes anything", CHAPU_II.describes(0, 0, 50), True
)

# --- matching ------------------------------------------------------------------
check("park section by sub-locality", where(CHAPU_II), "Chapu II")
check(
    "the road along the park is the section it is in",
    where(CHAPU_III_ROAD),
    "Chapu III",
)
check("the neighbourhood", where(LOMAS), "Lomas")
check("the house's own colonia is Lomas too", where(AT_HOME), "Lomas")
check("case-insensitive, either way round", where(JUAREZ), "Juárez")
check("any of several matchers", where(POLANCO_IV), "Polanco")
check("all-of holds when both do", where(SATELITE), "Satélite")
check(
    "all-of fails when one does not",
    where(geocode(**{"Sub Locality": "Ciudad Satélite"})),
    None,
)
check("an area of interest names a venue", where(DEPORTIVA), "Sports")
check("nothing configured for it", where(geocode(**{"Sub Locality": "Escandón"})), None)
check("no geocode, no match", where(None), None)

# --- precedence ---------------------------------------------------------------
check("a real circle drawn smaller wins", where(CHAPU_II, tighter_than=500), None)
check("a coarse real circle loses", where(CHAPU_II, tighter_than=15000), "Chapu II")
check(
    "polygon by position, regardless of geocode",
    where(LOMAS, lat=19.4228, lon=-99.2049),
    "Molino",
)
check(
    "outside the polygon it is the geocode again",
    where(LOMAS, lat=19.4250, lon=-99.2049),
    "Lomas",
)
molino = places.polygon_radius(SPECS[9].polygon)
check("a ~160 m triangle is worth ~75 m", 65 < molino < 85, True)
check(
    "which beats a neighbourhood", molino < places.GEOCODE_RADIUS["sub_locality"], True
)
check(
    "an area of interest beats a sub-locality",
    places.matcher_radius({"area_of_interest": ("x",)})
    < places.matcher_radius({"sub_locality": ("x",)}),
    True,
)
check(
    "all-of is worth its tightest attribute",
    places.matcher_radius({"sub_locality": ("x",), "locality": ("y",)}),
    1000.0,
)
check(
    "an unknown attribute never matches", places.matcher_radius({"vibes": ("x",)}), None
)

# --- polygon arithmetic --------------------------------------------------------
square = [(19.0, -99.0), (19.0, -99.001), (19.001, -99.001), (19.001, -99.0)]
check("inside a square", places.point_in_polygon(19.0005, -99.0005, square), True)
check("outside a square", places.point_in_polygon(19.002, -99.0005, square), False)
side = 0.001 * 111_320
check(
    "equivalent radius of a ~111 m square",
    abs(
        places.polygon_radius(square)
        - math.sqrt(side * side * math.cos(math.radians(19)) / math.pi)
    )
    < 1,
    True,
)

print("all place checks passed")
