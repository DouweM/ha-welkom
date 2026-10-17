"""Places a household recognises that no circle describes.

Home Assistant's zones are circles, and most of the places people actually
mean are not: a park with three named sections, a neighbourhood, a town. The
phone's reverse geocoder already knows those boundaries — Apple reports
"Bosque de Chapultepec II" as a sub-locality, and it is right at the edges
where a hand-drawn circle is wrong — so a place here is a NAME plus the ways it
can be recognised:

- `geocodes_as`: what the reverse geocode says when the phone is there. A
  matcher is a set of attributes that must all hold, each against one value or
  any of several; a place may list several matchers and any of them counts.
- `polygon`: a boundary drawn by hand, for the places the geocoder gets wrong
  or does not name.

Every match has a RADIUS, which is not decoration. The trip module names a stop
by the tightest zone that saw it, and these places have to take part in that
rule without the module learning a new one: a polygon is worth the radius of
the circle with its area, and a geocode match is worth the nominal size of the
most specific attribute it matched on — an area of interest is tighter than a
neighbourhood is tighter than a town. So a 48 m school circle still beats the
neighbourhood around it, a park section beats the borough, and nothing here
ever outranks a real circle drawn smaller.

No Home Assistant imports, on purpose. This is loaded by path in
``tests/check_places.py`` and replayed against recorded geocodes, the way
``trip.py`` is replayed against recorded journeys.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
import math
from typing import Any

from .location import distance
from .trip import Place

# How much a match on each attribute is worth, as the radius of a circle, most
# specific first. The numbers are nominal: they only have to ORDER against each
# other and against the real circles they compete with. A thoroughfare is a
# street's length, a sub-locality a neighbourhood, a locality a town or a
# borough; the two administrative levels and the country exist so that "Jalisco"
# or "Netherlands" can be a place too, and so that anything else always beats
# them.
GEOCODE_RADIUS: dict[str, float] = {
    "area_of_interest": 300.0,
    "thoroughfare": 500.0,
    "sub_locality": 1000.0,
    "postal_code": 1500.0,
    "locality": 5000.0,
    "sub_administrative_area": 20000.0,
    "administrative_area": 50000.0,
    "iso_country_code": 500000.0,
    "country": 500000.0,
}

# The companion app's attribute names, as this module calls them.
_ATTRIBUTE_KEYS: dict[str, str] = {
    "Areas Of Interest": "area_of_interest",
    "Thoroughfare": "thoroughfare",
    "Sub Locality": "sub_locality",
    "Postal Code": "postal_code",
    "Locality": "locality",
    "Sub Administrative Area": "sub_administrative_area",
    "Administrative Area": "administrative_area",
    "ISO Country Code": "iso_country_code",
    "Country": "country",
}

# What the companion app writes for an attribute the geocoder left blank.
_BLANK = frozenset({"", "n/a", "none", "unknown", "unavailable"})


def _key(name: str) -> str:
    """`Sub Locality`, `sub_locality` and `sub-locality` are one attribute."""
    return _ATTRIBUTE_KEYS.get(
        name, name.strip().lower().replace(" ", "_").replace("-", "_")
    )


def _values(raw: object) -> tuple[str, ...]:
    """One value or several, trimmed, blanks dropped."""
    if raw is None:
        return ()
    items: Iterable[object] = raw if isinstance(raw, (list, tuple, set)) else (raw,)
    out: list[str] = []
    for item in items:
        text = str(item).strip()
        if text and text.casefold() not in _BLANK:
            out.append(text)
    return tuple(out)


def normalise_values(raw: object) -> dict[str, tuple[str, ...]]:
    """Geocode values from anywhere -- stored, or written by hand -- the way
    `Geocode.from_attributes` reads them: keys normalised, blanks dropped, and
    only the attributes a matcher can ask about kept."""
    if not isinstance(raw, Mapping):
        return {}
    values: dict[str, tuple[str, ...]] = {}
    for name, value in raw.items():
        if not isinstance(name, str) or (key := _key(name)) not in GEOCODE_RADIUS:
            continue
        if found := _values(value):
            values[key] = found
    return values


@dataclass(frozen=True)
class Geocode:
    """What the phone's reverse geocoder said about a fix, normalised.

    `latitude` / `longitude` are the position the geocode DESCRIBES, which is
    not necessarily the position of the fix it is being read alongside: the
    companion app writes the tracker and the geocoded sensor as two states, and
    the second can lag the first by a poll. See `describes`.
    """

    values: Mapping[str, tuple[str, ...]]
    latitude: float | None = None
    longitude: float | None = None

    @classmethod
    def from_attributes(cls, attributes: Mapping[str, Any]) -> Geocode:
        """Read the companion app's geocoded-location sensor attributes."""
        values: dict[str, tuple[str, ...]] = {}
        for name, raw in attributes.items():
            if not isinstance(name, str) or name not in _ATTRIBUTE_KEYS:
                continue
            if found := _values(raw):
                values[_ATTRIBUTE_KEYS[name]] = found
        latitude = longitude = None
        location = attributes.get("Location")
        if isinstance(location, (list, tuple)) and len(location) == 2:
            try:
                latitude, longitude = float(location[0]), float(location[1])
            except (TypeError, ValueError):
                latitude = longitude = None
        return cls(values=values, latitude=latitude, longitude=longitude)

    def get(self, attribute: str) -> tuple[str, ...]:
        return self.values.get(_key(attribute), ())

    def first(self, attribute: str) -> str | None:
        """The attribute's first value; None when the geocoder left it blank."""
        found = self.get(attribute)
        return found[0] if found else None

    def describes(self, latitude: float, longitude: float, slack: float) -> bool:
        """Whether this geocode is about the given position.

        True when the geocode carries no position at all — there is nothing to
        check against, and the caller has nothing better — and otherwise only
        within `slack` metres. A geocode still describing the last fix must not
        name the place the phone just left.
        """
        if self.latitude is None or self.longitude is None:
            return True
        return distance(self.latitude, self.longitude, latitude, longitude) <= slack


Matcher = Mapping[str, tuple[str, ...]]
"""Attributes that must ALL hold, each against any of its values."""


@dataclass(frozen=True)
class PlaceSpec:
    """One place the household recognises, and how."""

    name: str
    geocodes_as: tuple[Matcher, ...] = ()
    polygon: tuple[tuple[float, float], ...] = ()

    @classmethod
    def from_config(cls, raw: Mapping[str, Any]) -> PlaceSpec | None:
        """Read one `places` entry from welkom.yml; None for one that says nothing.

        Tolerant on purpose. A typo in a household's config should cost that one
        place, not the integration: an entry without a name, or with neither a
        matcher nor a polygon, is dropped, and a matcher whose values are all
        blank is dropped from its place.
        """
        name = str(raw.get("name") or "").strip()
        if not name:
            return None

        matchers: list[Matcher] = []
        geocodes_as = raw.get("geocodes_as")
        for entry in geocodes_as if isinstance(geocodes_as, list) else [geocodes_as]:
            if not isinstance(entry, Mapping):
                continue
            matcher = {
                _key(str(attribute)): values
                for attribute, value in entry.items()
                if (values := _values(value))
            }
            if matcher:
                matchers.append(matcher)

        polygon: list[tuple[float, float]] = []
        for point in raw.get("polygon") or ():
            if isinstance(point, (list, tuple)) and len(point) == 2:
                try:
                    polygon.append((float(point[0]), float(point[1])))
                except (TypeError, ValueError):
                    polygon = []
                    break
        if len(polygon) < 3:
            polygon = []

        if not matchers and not polygon:
            return None
        return cls(name=name, geocodes_as=tuple(matchers), polygon=tuple(polygon))


def places_from_config(raw: object) -> tuple[PlaceSpec, ...]:
    """Every readable entry of a `places` list, in the order given."""
    if not isinstance(raw, list):
        return ()
    found: list[PlaceSpec] = []
    for entry in raw:
        if isinstance(entry, Mapping) and (spec := PlaceSpec.from_config(entry)):
            found.append(spec)
    return tuple(found)


# --- geocode matching ---------------------------------------------------------


def matcher_radius(matcher: Matcher) -> float | None:
    """What a match on this matcher is worth: its most specific attribute.

    None when no attribute in it is one the geocoder reports, which means the
    matcher can never hold and should not be consulted.
    """
    known = [GEOCODE_RADIUS[key] for key in matcher if key in GEOCODE_RADIUS]
    return min(known) if known else None


def matcher_holds(matcher: Matcher, geocode: Geocode) -> bool:
    """Whether every attribute of the matcher is among what the geocode says.

    Case-insensitive, because "Roma Norte" and "roma norte" are one place and
    a household should not have to know how Apple capitalises it.
    """
    for attribute, wanted in matcher.items():
        said = {value.casefold() for value in geocode.get(attribute)}
        if not said or not any(value.casefold() in said for value in wanted):
            return False
    return True


# --- polygons -----------------------------------------------------------------


def point_in_polygon(
    latitude: float, longitude: float, polygon: Sequence[tuple[float, float]]
) -> bool:
    """Ray casting in degrees, which is exact enough for a neighbourhood.

    Degrees are not metres — a degree of longitude is shorter than a degree of
    latitude here by a third — but the test is only whether a ray crosses an
    edge an odd number of times, and scaling one axis does not change that.
    """
    inside = False
    count = len(polygon)
    for i in range(count):
        lat1, lon1 = polygon[i]
        lat2, lon2 = polygon[(i + 1) % count]
        if (lat1 > latitude) == (lat2 > latitude):
            continue
        crossing = lon1 + (latitude - lat1) * (lon2 - lon1) / (lat2 - lat1)
        if longitude < crossing:
            inside = not inside
    return inside


def polygon_radius(polygon: Sequence[tuple[float, float]]) -> float:
    """The radius of the circle with the polygon's area, in metres.

    Shoelace on a local flat projection: latitude scaled to metres directly,
    longitude by the cosine of the polygon's latitude. Good to well under a
    percent at neighbourhood scale, and the number only has to rank.
    """
    if len(polygon) < 3:
        return 0.0
    mean_lat = sum(lat for lat, _ in polygon) / len(polygon)
    metres_per_deg = 111_320.0
    lon_scale = math.cos(math.radians(mean_lat))
    area2 = 0.0
    for i in range(len(polygon)):
        lat1, lon1 = polygon[i]
        lat2, lon2 = polygon[(i + 1) % len(polygon)]
        x1, y1 = lon1 * lon_scale * metres_per_deg, lat1 * metres_per_deg
        x2, y2 = lon2 * lon_scale * metres_per_deg, lat2 * metres_per_deg
        area2 += x1 * y2 - x2 * y1
    return math.sqrt(abs(area2) / 2 / math.pi)


# --- putting it together ----------------------------------------------------------


def recognise(
    specs: Iterable[PlaceSpec],
    *,
    latitude: float,
    longitude: float,
    geocode: Geocode | None,
    tighter_than: float = math.inf,
) -> Place | None:
    """The tightest configured place that holds for this position.

    Polygons are tested against the position; matchers against the geocode,
    if there is one. `tighter_than` is the radius of the best real circle the
    caller already has, so a place here only wins by being smaller — the same
    comparison the trip module makes between zones, made in the same units.
    """
    best: Place | None = None
    best_radius = tighter_than
    for spec in specs:
        if spec.polygon and point_in_polygon(latitude, longitude, spec.polygon):
            radius = polygon_radius(spec.polygon)
            if radius < best_radius:
                best, best_radius = Place(name=spec.name, radius=radius), radius
        if geocode is None:
            continue
        for matcher in spec.geocodes_as:
            radius = matcher_radius(matcher)
            if radius is None or radius >= best_radius:
                continue
            if matcher_holds(matcher, geocode):
                best, best_radius = Place(name=spec.name, radius=radius), radius
    return best


@dataclass(frozen=True)
class GeocodedArea:
    """A place named by the phone's geocode, and where the phone was when it said so."""

    place: Place
    latitude: float
    longitude: float


def lingering_area(
    area: GeocodedArea | None,
    *,
    latitude: float,
    longitude: float,
    bare: Place | None,
    max_distance: float,
) -> Place | None:
    """The name a fix with no geocode of its own still goes by, if not `bare`.

    `bare` is what the fix is called without a geocode: a real zone, a polygon,
    or nothing. A neighbourhood the phone named a short walk ago is a better
    answer than anything wider -- the city -- and a worse one than anything
    tighter, which is news. None when `bare` should stand.
    """
    if area is None:
        return None
    # Never further than the place itself is wide.
    reach = min(max_distance, area.place.radius)
    if distance(latitude, longitude, area.latitude, area.longitude) > reach:
        return None
    if not tighter(area.place, than=bare):
        return None
    return area.place


def tighter(place: Place, *, than: Place | None) -> bool:
    """Whether `place` is better news than `than`, a fix's name without it.

    Strictly smaller: a name only replaces one it says more than. Anything
    beats nothing.
    """
    return than is None or place.radius < than.radius


# --- the geocode's own colonia and municipality ----------------------------------

# What Apple puts in Administrative Area for Mexico City. Its alcaldías are
# the Locality there ("Cuajimalpa"); Sub Administrative Area is just "Mexico
# City" again.
_CAPITAL = frozenset(
    name.casefold()
    for name in ("CDMX", "Ciudad de México", "Mexico City", "D.F.", "Distrito Federal")
)

# Mexico's other states as Apple abbreviates or spells them. Their
# municipalities are the Sub Administrative Area ("Huixquilucan"), and the
# Locality is a town within one.
_STATES = frozenset(
    name.casefold()
    for name in (
        "Ags.", "Aguascalientes",
        "B.C.", "Baja California",
        "B.C.S.", "Baja California Sur",
        "Camp.", "Campeche",
        "Chis.", "Chiapas",
        "Chih.", "Chihuahua",
        "Coah.", "Coahuila", "Coahuila de Zaragoza",
        "Col.", "Colima",
        "Dgo.", "Durango",
        "Edo. Méx.", "Méx.", "Edomex", "Estado de México", "México",
        "Gto.", "Guanajuato",
        "Gro.", "Guerrero",
        "Hgo.", "Hidalgo",
        "Jal.", "Jalisco",
        "Mich.", "Michoacán", "Michoacán de Ocampo",
        "Mor.", "Morelos",
        "Nay.", "Nayarit",
        "N.L.", "Nuevo León",
        "Oax.", "Oaxaca",
        "Pue.", "Puebla",
        "Qro.", "Querétaro",
        "Q.R.", "Q. Roo", "Quintana Roo",
        "S.L.P.", "San Luis Potosí",
        "Sin.", "Sinaloa",
        "Son.", "Sonora",
        "Tab.", "Tabasco",
        "Tamps.", "Tamaulipas",
        "Tlax.", "Tlaxcala",
        "Ver.", "Veracruz", "Veracruz de Ignacio de la Llave",
        "Yuc.", "Yucatán",
        "Zac.", "Zacatecas",
    )
)  # fmt: skip


def colonia(geocode: Geocode | None) -> str | None:
    """The geocode's own neighbourhood: its Sub Locality, if it has one."""
    return geocode.first("sub_locality") if geocode else None


def municipality(geocode: Geocode | None) -> str | None:
    """The alcaldía or municipio the geocode is in, in Mexico; None elsewhere.

    Apple files them at different levels on either side of the state line:
    an alcaldía of Mexico City is the Locality, a municipio of a state is the
    Sub Administrative Area. Outside Mexico there is no such word, and no
    guess at one.
    """
    if geocode is None or (state := geocode.first("administrative_area")) is None:
        return None
    if state.casefold() in _CAPITAL:
        return geocode.first("locality")
    if state.casefold() in _STATES:
        return geocode.first("sub_administrative_area")
    return None
