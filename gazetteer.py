"""What the phones' geocoders have said, kept by where they said it.

iOS reverse-geocodes a fix when it feels like it, and in a car that is rarely:
at 21:07 UTC on 2026-10-17 Gaby was driving through Lomas Virreyes and her
phone's last geocode was 2.8 km back down the road, so the only name left for
the fix was the zone drawn around the whole city. But somebody's phone has
geocoded that street before -- the household drives the same roads every day
-- and what it said then is as true now. So every geocode that describes its
fix is written down here against its position, for every phone, and a fix
nobody geocoded borrows the nearest one.

An entry is a position, the geocode's matching-relevant values (as
`places.Geocode.values`, keys normalised the way `places` does), and when it
was said. Entries are deduplicated on a grid of about GRID metres, newest
first, so a phone parked in one spot for a day is one entry rather than a
thousand; and the whole is capped, oldest out first, so a year of driving
stays a few megabytes (`const.GAZETTEER_MAX_ENTRIES`).

`nearest` looks only at the buckets a search radius can reach, so naming a
fix costs a few dozen distances rather than twenty thousand.

No Home Assistant imports, on purpose: ``tests/check_gazetteer.py`` loads this
by path. Storage and seeding live in ``gazetteer_store.py``.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import math
from typing import Any

from .location import distance
from .places import Geocode, normalise_values

# Metres per degree of latitude, which is close enough to constant for a grid
# whose only job is to make nearby things land in the same cell.
_METRES_PER_DEGREE = 111_320.0

# The dedupe cell, in metres: two geocodes this close are of the same spot,
# and only the newer one is worth keeping. A few houses, well under a block.
GRID = 25.0

# The search bucket, in metres. Each holds at most (BUCKET / GRID)^2 cells; a
# search reaches ceil(radius / BUCKET) buckets each way.
BUCKET = 100.0


@dataclass(frozen=True, slots=True)
class Entry:
    """One geocode, where and when it was said. Never mutated once made."""

    latitude: float
    longitude: float
    values: Mapping[str, tuple[str, ...]]
    at: float
    """When, as a POSIX timestamp."""

    def geocode(self) -> Geocode:
        return Geocode(
            values=self.values, latitude=self.latitude, longitude=self.longitude
        )


def _cell(latitude: float, longitude: float, size: float) -> tuple[int, int]:
    """The grid cell a position falls in, `size` metres north to south.

    Degrees of longitude are the same size as degrees of latitude here, which
    makes cells narrower than they are tall away from the equator -- 24 m by
    25 m in Mexico City. That costs nothing: the dedupe only has to be "about"
    the grid, and `nearest` widens its search by the same factor.
    """
    step = size / _METRES_PER_DEGREE
    return math.floor(latitude / step), math.floor(longitude / step)


class Gazetteer:
    """Every geocode worth keeping, by where it was said."""

    def __init__(self, max_entries: int) -> None:
        self.max_entries = max_entries
        # Fine cell -> entry, oldest first: the order eviction takes them in.
        self._entries: OrderedDict[tuple[int, int], Entry] = OrderedDict()
        # Bucket -> the fine cells in it, for `nearest`.
        self._buckets: dict[tuple[int, int], set[tuple[int, int]]] = {}

    def __len__(self) -> int:
        return len(self._entries)

    def entries(self) -> tuple[Entry, ...]:
        """Every entry, oldest first. A snapshot: safe to hand to another thread."""
        return tuple(self._entries.values())

    def add(self, entry: Entry) -> bool:
        """Keep this entry unless its cell already has one as new; whether it did.

        Eviction is in the order entries were added, which is age order as long
        as they are added in time order: live geocodes are, and `from_entries`
        sorts first. A geocode with no values names nothing and is not kept.
        """
        if not entry.values:
            return False
        cell = _cell(entry.latitude, entry.longitude, GRID)
        if (current := self._entries.get(cell)) is not None:
            if current.at >= entry.at:
                return False
            # Moved to the end: it is the newest now.
            del self._entries[cell]
        else:
            bucket = _cell(entry.latitude, entry.longitude, BUCKET)
            self._buckets.setdefault(bucket, set()).add(cell)
        self._entries[cell] = entry
        while len(self._entries) > self.max_entries:
            old_cell, old = self._entries.popitem(last=False)
            self._forget(old_cell, old)
        return True

    def _forget(self, cell: tuple[int, int], entry: Entry) -> None:
        bucket = _cell(entry.latitude, entry.longitude, BUCKET)
        if (cells := self._buckets.get(bucket)) is not None:
            cells.discard(cell)
            if not cells:
                del self._buckets[bucket]

    def nearest(
        self, latitude: float, longitude: float, max_distance: float
    ) -> Geocode | None:
        """What was said nearest this position, if anything within `max_distance`."""
        if not self._entries or max_distance < 0:
            return None
        row, column = _cell(latitude, longitude, BUCKET)
        reach_lat = math.ceil(max_distance / BUCKET)
        # A degree of longitude shrinks with latitude, so the same distance
        # spans more buckets east to west. Clamped short of the poles.
        shrink = max(math.cos(math.radians(latitude)), 0.01)
        reach_lon = math.ceil(max_distance / (BUCKET * shrink))
        best: Entry | None = None
        best_distance = max_distance
        for r in range(row - reach_lat, row + reach_lat + 1):
            for c in range(column - reach_lon, column + reach_lon + 1):
                for cell in self._buckets.get((r, c), ()):
                    entry = self._entries[cell]
                    d = distance(latitude, longitude, entry.latitude, entry.longitude)
                    if d <= best_distance:
                        best, best_distance = entry, d
        return best.geocode() if best is not None else None

    def absorb(self, other: Gazetteer) -> None:
        """Take over `other`'s entries, then put back what was added here since.

        For a gazetteer built away from the event loop -- loaded or seeded --
        while live geocodes kept arriving here: those are newer than anything
        it holds, so they go on top.
        """
        live = self.entries()
        self._entries, self._buckets = other._entries, other._buckets
        other._entries, other._buckets = OrderedDict(), {}
        for entry in live:
            self.add(entry)

    @classmethod
    def from_entries(cls, entries: Iterable[Entry], max_entries: int) -> Gazetteer:
        """A gazetteer of these entries, in any order: oldest are added first."""
        gazetteer = cls(max_entries)
        for entry in sorted(entries, key=lambda entry: entry.at):
            gazetteer.add(entry)
        return gazetteer

    @staticmethod
    def entries_as_dict(entries: Iterable[Entry]) -> dict[str, Any]:
        """A snapshot (see `entries`) as plain data, for storage.

        Each distinct set of values is written once and referred to by its
        index: a colonia's hundred entries mostly say the same thing, and a
        full gazetteer would otherwise be ten megabytes of "Mexico City".
        """
        table: dict[tuple[tuple[str, tuple[str, ...]], ...], int] = {}
        rows: list[list[float | int]] = []
        for entry in entries:
            key = tuple(sorted(entry.values.items()))
            index = table.setdefault(key, len(table))
            rows.append([entry.latitude, entry.longitude, entry.at, index])
        return {
            "values": [{name: list(found) for name, found in key} for key in table],
            "entries": rows,
        }

    def as_dict(self) -> dict[str, Any]:
        return self.entries_as_dict(self._entries.values())

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any] | None, max_entries: int) -> Gazetteer:
        """Read `as_dict`'s output back; unreadable entries are dropped.

        Values are normalised the way `places` reads a live geocode, so a
        stored entry holds exactly the matchers the geocode did.
        """
        if not isinstance(raw, Mapping):
            return cls(max_entries)
        table = raw.get("values")
        values = [
            normalise_values(item)
            for item in (table if isinstance(table, list) else ())
        ]
        rows = raw.get("entries")
        entries: list[Entry] = []
        for row in rows if isinstance(rows, list) else ():
            try:
                latitude, longitude, at, index = row
                found = values[int(index)] if int(index) >= 0 else None
                entry = Entry(
                    latitude=float(latitude),
                    longitude=float(longitude),
                    values=found or {},
                    at=float(at),
                )
            except (IndexError, TypeError, ValueError):
                continue
            if entry.values:
                entries.append(entry)
        return cls.from_entries(entries, max_entries)


def search_radius(accuracy: float, *, minimum: float, maximum: float) -> float:
    """How far from a fix to look for a geocode: its accuracy, within bounds.

    A vague fix could be anywhere in its disc, so a geocode anywhere in it is
    as likely to be about it; but never narrower than `minimum`, the spacing
    of the roads, nor wider than `maximum`, past which it is another place.
    """
    return min(max(minimum, accuracy), maximum)
