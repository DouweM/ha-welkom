"""Turn a phone's fixes into trips, and a trip into somewhere they went.

`location.py` answers "where are they right now". This answers the question a
notification wants: "they left, and then what — where did they end up?"

A destination is not a position. Somebody in a taxi on Reforma and somebody at
dinner on Reforma report the same fix, and only time tells them apart — so a
trip is followed by anchoring. The first fix after they leave is an anchor,
every later fix that lands near it extends that anchor's stay, and one that
lands away starts a new anchor. An anchor that holds for `dwell` is somewhere
they went. Anything short of that is the road.

But a stay is not the only kind of destination, and this part is measured
rather than guessed. Over the week to 2026-09-12, Gaby's four school runs put
her inside `zone.peterson` for between 6 seconds and 1.7 minutes — she pulls
up, the kid gets out, she drives away — while the SAME journeys spend four to
five minutes crossing `zone.lomas` on the way, which means nothing at all. No
dwell separates those two. What separates them is that the school is the
furthest point of the trip and the turn for home; Lomas is passed through.

So there are two ways to have been somewhere:

    a stay      — still, in one spot, for `dwell`
    the turn    — the tightest named zone near the far end of the trip

and a trip is told as both, plus the named zones it passed through on the way.

Zones do not decide stillness, only identity. A hand-drawn zone is a place the
household has already declared it cares about, so it names a stay, merges a
stay that wanders inside it, and excuses a stay from `away_floor` — Douwe's
walks to the Puente sit 160 m from the house, which is nearer than the floor
and is still somewhere he went. What zones must NOT do is lower the bar for
holding still, or four minutes of driving through a one-kilometre zone would
read as an evening in it.

A trip outlives a restart. It has to: Home Assistant restarted by itself on
2026-09-17, and the one on 2026-09-12 landed in the middle of an evening out
and left both sensors reporting `left_at` as 20:15 — when HA came back — for
something that started at 14:15, with an empty itinerary behind it. So the
whole object is written out and read back by `trip_as_dict` / `trip_from_dict`,
and the coordinator continues it on the next fix rather than starting again.

Pure, like `location.py`: no Home Assistant imports, so `tests/check_trip.py`
exercises the whole state machine without booting HA.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any

from .location import Circle, Fix, distance


@dataclass(frozen=True, kw_only=True)
class Place:
    """A hand-drawn zone a fix fell in: what it is called, and how big it is.

    The size travels with the name because the turn has to choose between two
    zones that both contain the far end of a trip, and the tighter one is the
    one that means something. On 2026-09-17 Gaby's school run reported "Lomas",
    a one-kilometre zone she was merely driving through, because the single
    furthest fix of the trip landed 779 m out on the road and the school fix
    landed 775 m out nine seconds later. Four metres decided the name of the
    journey.
    """

    name: str
    radius: float


@dataclass(frozen=True, kw_only=True)
class Visit:
    """A stretch of a trip spent inside one named zone."""

    place: str
    since: datetime
    until: datetime

    @property
    def duration(self) -> timedelta:
        """How long they were in it."""
        return self.until - self.since


@dataclass(frozen=True, kw_only=True)
class Stay:
    """Somewhere they were, as opposed to somewhere they passed.

    `place` is the zone's name when they stopped inside one and None when they
    stopped in open ground — which is most long journeys, because the useful
    destinations are the ones nobody thought to draw a zone around. An unnamed
    stay carries only its coordinates, and naming it is the caller's problem:
    this module has no geocoder and should not grow one.
    """

    latitude: float
    longitude: float
    place: str | None
    since: datetime
    until: datetime

    @property
    def duration(self) -> timedelta:
        """How long they were there."""
        return self.until - self.since


@dataclass(frozen=True, kw_only=True)
class Trip:
    """One spell away from home, as far as it has got.

    Immutable and replaced on every observation, so a caller that wants to know
    whether anything changed can compare the old against the new.
    """

    left_at: datetime
    """When welkom stopped speaking for them and the phone took over."""

    latitude: float
    longitude: float
    """The anchor: where they have been since `anchor_since`."""

    anchor_since: datetime
    """When they last arrived at the anchor — reset by every move."""

    anchor_place: str | None = None
    anchor_radius: float | None = None
    """The tightest zone seen while THIS anchor has held, which is what a stay
    in it is called.

    Neither end of the anchor will do, and both were tried. The zone of the
    latest fix names a stay after wherever they have got to by the time the
    clock settles it: Gaby parks at the school, her phone goes quiet, and five
    minutes later the newest fix is 200 m down the road and inside the
    kilometre-wide neighbourhood instead. The zone of the FIRST fix names it
    after the approach: the fix that lays the anchor is 110 m short of the
    school gate, on the same road, and the school fix arrives twenty seconds
    later without moving far enough to re-anchor.

    An anchor is a patch of ground held over minutes, not a point, so it is
    named the way the turn is: the smallest zone anything in it fell inside.
    A school inside a neighbourhood is the school."""

    anchor_seen_at: datetime | None = None
    """The last fix that actually landed in the anchor, as opposed to the last
    tick that passed over it. The difference is what tells a stay somebody was
    *watched* sitting through from one the clock inferred out of silence."""

    seen_at: datetime
    seen_latitude: float = 0.0
    seen_longitude: float = 0.0
    """The last fix, whether or not it moved them, and where it put them. A
    phone that has gone silent is not evidence of stillness; see `stale` and
    `still_speed`."""

    place: str | None = None
    """The named zone the latest fix is in, if any. Identity, not geography:
    two zones of the same name are one place, which is what makes the five
    overlapping `Chapu III` zones a single stretch of park."""

    settled_at: datetime | None = None
    """When the current anchor became a stay, or None while still moving."""

    stay_latitude: float | None = None
    stay_longitude: float | None = None
    """Where the stay began. A place is bigger than an anchor — somebody at
    dinner walks to the bar — so a step within `place_radius` of THIS point
    keeps the stay rather than starting a new one. Measured from where the stay
    began and never from the latest anchor, for the same reason the anchor
    itself does not drift: hops of `place_radius` would otherwise carry one stay
    clean across the city. Unused while `place` is set, which is a stronger
    answer to the same question.

    What it is CALLED is `anchor_place`, not anything recorded here: a stay is
    named after the tightest zone its anchor ever saw."""

    visits: tuple[Visit, ...] = ()
    """Every named zone the trip has been through, in order, with the last one
    still running. The itinerary, which is what a welcome-home line wants: they
    were at the park, and stopped at the bridge on the way back."""

    stays: tuple[Stay, ...] = ()
    """The stays that are over. The live one, if there is one, is `stay`."""

    furthest: float = 0.0
    """How far out the trip got, in meters. A destination can be nearer than
    the turning point on the way back."""

    turn_place: str | None = None
    turn_radius: float | None = None
    turn_distance: float = 0.0
    turn_at: datetime | None = None
    turn_latitude: float | None = None
    turn_longitude: float | None = None
    """The turn: the tightest named zone holding a fix near the far end of the
    trip, and where and when that fix was.

    Near the far end rather than AT it, which is the whole point. You reach a
    destination by driving towards it, so the single furthest fix is usually
    the road just short of the gate — and a school run has no stay to fall back
    on, so that one fix is all the trip has to say for itself. Any fix within
    `turn_slack` of the furthest is a candidate, and the smallest zone among
    them wins, because a school inside a neighbourhood is what somebody meant
    by where they went."""

    ventured: bool = False
    """Whether any fix got beyond `away_floor`. A trip that never did, and
    touched no named zone, is a tracker artefact or a walk to the gate."""

    @property
    def settled(self) -> bool:
        """Whether they have stopped somewhere."""
        return self.settled_at is not None

    @property
    def stay(self) -> Stay | None:
        """The stay they are in the middle of, or None while still moving."""
        if self.settled_at is None:
            return None
        return Stay(
            latitude=self.stay_latitude
            if self.stay_latitude is not None
            else self.latitude,
            longitude=self.stay_longitude
            if self.stay_longitude is not None
            else self.longitude,
            place=self.anchor_place,
            since=self.settled_at,
            until=self.seen_at,
        )

    @property
    def turn(self) -> Stay | None:
        """The furthest point, as somewhere they were.

        Not a stay — they may have been there for eleven seconds — but the same
        shape, because to everything downstream it is the same kind of fact.
        """
        if self.turn_place is None or self.turn_at is None:
            return None
        return Stay(
            latitude=self.turn_latitude or 0.0,
            longitude=self.turn_longitude or 0.0,
            place=self.turn_place,
            since=self.turn_at,
            until=self.turn_at,
        )

    def distance_from(self, home: Circle) -> float:
        """How far the anchor is from home, in meters."""
        return distance(self.latitude, self.longitude, home.latitude, home.longitude)

    def arrived(self, home: Circle, away_floor: float) -> bool:
        """Whether the anchor is somewhere worth calling a destination.

        Settled, and then either named or far enough out — different questions,
        and a place only has to answer one. Sitting on an unnamed bench 160 m
        down the road answers neither.
        """
        if not self.settled:
            return False
        return self.place is not None or self.distance_from(home) >= away_floor

    def been_to(
        self,
        home: Circle,
        *,
        dwell: timedelta,
        min_trip: timedelta,
        away_floor: float,
    ) -> tuple[Stay, ...]:
        """Everywhere this trip amounts to, in the order it happened.

        Three kinds of thing get on the list, and each is here because the
        others miss it:

        the stays       — settled somewhere, named or not. The unnamed ones are
                          most of the long journeys: Gaby's afternoon in
                          Ciudad Satélite is 10 km out and inside no zone
                          anybody drew, and a list that could only say zone
                          names would report the neighbourhood she drove home
                          through instead of where she spent three hours.
        the long visits — a named zone that held them for `dwell` without the
                          anchor ever settling, which is what wandering around
                          a park looks like.
        the turn        — the named zone at the furthest point. A school run
                          has no stay and no long visit: she pulls up, the kid
                          gets out, she drives away, and the six seconds she
                          was inside `zone.peterson` are the entire point of
                          the journey.

        A visit is measured between FIXES and not off the clock, unlike the
        dwell. Letting a visit run on through silence was tried and reverted:
        on 2026-09-12 Douwe walked out through `zone.puente` and his phone then
        said nothing for eight minutes while he crossed to Chapultepec, and
        stretching the visit over that silence put "the bridge" on a list that
        should only have said "the park".

        `away_floor` applies to the unnamed ones only, and for the same reason
        it applies to `arrived`: an unnamed spot near the house is the doorstep.
        Welkom losing a phone that never left puts Gaby "out" for eleven
        minutes at 29 m from the front door, and that is not a place she went.

        `min_trip` throws away the journeys that never happened. A single bad
        fix hands the phone control for a couple of seconds — twice in the week
        to 2026-09-12, both while Douwe was in fact in the garden — and a trip
        whose whole life is shorter than this has no story to tell.
        """
        if self.seen_at - self.left_at < min_trip:
            return ()

        found: list[Stay] = [*self.stays]
        if stay := self.stay:
            found.append(stay)
        found.extend(
            Stay(
                latitude=self.latitude,
                longitude=self.longitude,
                place=visit.place,
                since=visit.since,
                until=visit.until,
            )
            for visit in self.visits
            if visit.duration >= dwell
        )
        if turn := self.turn:
            found.append(turn)

        found.sort(key=lambda stay: stay.since)

        # One entry per place. A named zone left and returned to is one answer
        # to "where did they go", and the turn is usually inside a stay that is
        # already on the list. Unnamed stays are kept apart by their moment,
        # since there is no name to collapse them by.
        seen: set[object] = set()
        unique: list[Stay] = []
        for stay in found:
            if stay.place is None and (
                distance(stay.latitude, stay.longitude, home.latitude, home.longitude)
                < away_floor
            ):
                continue
            key = stay.place if stay.place is not None else stay.since
            if key in seen:
                continue
            seen.add(key)
            unique.append(stay)
        return tuple(unique)


def follow(
    trip: Trip | None,
    fix: Fix | None,
    home: Circle,
    now: datetime,
    *,
    place: Place | None = None,
    settle_radius: float,
    place_radius: float,
    dwell: timedelta,
    away_floor: float,
    still_speed: float,
    turn_slack: float,
) -> Trip | None:
    """Fold one observation into the trip, returning the trip as it now stands.

    Called both when a fix arrives and on a plain tick with `fix` None, because
    the two carry different news and both matter: a fix can move the anchor, and
    the passage of time alone can settle it.

    `place` is the smallest hand-drawn zone the fix falls in, which the caller
    resolves because zones live in Home Assistant. `turn_slack` is how far back
    from the trip's furthest point a fix may be and still say where they were
    heading. It should already
    exclude the ones too big to be a destination — a zone drawn around a whole
    city says nothing about where in it somebody is.

    `still_speed` is the fastest somebody can have been moving, in meters per
    second, and still be said to have stayed put through a silence.

    `settle_radius` is how far a fix may land from the anchor and still count as
    the same spot: wide enough to swallow a phone's own noise and a building,
    narrow enough that the next block is somewhere else. It is widened by the
    fix's own claimed accuracy, so a vague reading never invents a move.
    """
    if fix is None:
        # Nothing new was said. Time still passes, and time is what turns an
        # anchor into a stay.
        return None if trip is None else _settle(trip, now, dwell)

    from_home = distance(fix.latitude, fix.longitude, home.latitude, home.longitude)

    name = place.name if place else None

    if trip is None:
        return Trip(
            left_at=now,
            latitude=fix.latitude,
            longitude=fix.longitude,
            anchor_since=now,
            anchor_place=name,
            anchor_radius=place.radius if place else None,
            anchor_seen_at=now,
            seen_at=now,
            seen_latitude=fix.latitude,
            seen_longitude=fix.longitude,
            place=name,
            visits=(Visit(place=name, since=now, until=now),) if name else (),
            furthest=from_home,
            turn_place=name,
            turn_radius=place.radius if place else None,
            turn_distance=from_home,
            turn_at=now if place else None,
            turn_latitude=fix.latitude,
            turn_longitude=fix.longitude,
            ventured=from_home >= away_floor,
        )

    # The zone they were in BEFORE this fix. Read it now, because the trip is
    # about to be rewritten with the new one and the re-anchor below turns on
    # the difference between the two.
    was = trip.place
    # Whether fixes, and not merely the clock, held this anchor for the dwell.
    watched = (
        trip.anchor_seen_at is not None
        and trip.anchor_seen_at - trip.anchor_since >= dwell
    )

    # How far they have come since the phone last spoke, and how long it took.
    # Read before the trip is rewritten, because it is the only thing that can
    # tell a quiet hour at a restaurant from a quiet hour on the highway.
    travelled = distance(
        fix.latitude, fix.longitude, trip.seen_latitude, trip.seen_longitude
    )
    quiet_for = (now - trip.seen_at).total_seconds()

    trip = replace(
        trip,
        seen_at=now,
        seen_latitude=fix.latitude,
        seen_longitude=fix.longitude,
        place=name,
        visits=_visited(trip.visits, name, now),
        ventured=trip.ventured or from_home >= away_floor,
        furthest=max(trip.furthest, from_home),
    )
    trip = _turn(trip, fix, place, from_home, now, turn_slack)

    moved = distance(fix.latitude, fix.longitude, trip.latitude, trip.longitude)
    if moved <= max(settle_radius, fix.accuracy):
        trip = _tighten(replace(trip, anchor_seen_at=now), place)
        # Still here. The anchor is deliberately NOT dragged towards the new
        # fix: letting it follow the noise lets a stay walk down the street a
        # few meters at a time and never trip the radius, which is how a moving
        # car reads as a place.
        return _settle(trip, now, dwell)

    # Out of the anchor. Still the same place if the zone says so, or failing a
    # zone, if it is a step inside the one they had settled into.
    if name is not None:
        within_stay = trip.settled and name == was
    else:
        within_stay = (
            trip.settled
            and trip.stay_latitude is not None
            and trip.stay_longitude is not None
            and distance(
                fix.latitude, fix.longitude, trip.stay_latitude, trip.stay_longitude
            )
            <= place_radius
        )

    if within_stay:
        return _tighten(
            replace(
                trip,
                latitude=fix.latitude,
                longitude=fix.longitude,
                anchor_seen_at=now,
            ),
            place,
        )

    # They have left. A stay that was running is now a stay that happened, and
    # is kept: the itinerary is the whole point of asking where somebody went.
    #
    # Unless the silence it was made of turns out to have been the road. The
    # dwell has to count silence as stillness -- a phone that has arrived stops
    # reporting -- but a car on the highway goes just as quiet, and without
    # this a drive to Naucalpan settles half a dozen times at motorway
    # junctions. When the phone speaks again it says which it was: eight
    # kilometres in eight minutes is not somebody who was sitting down, and two
    # kilometres in two hours is. Speed, not silence, and it is only knowable
    # afterwards.
    #
    # Only for a stay the clock inferred. A stay that fixes actually held for
    # the dwell -- somebody sat there and their phone kept saying so -- is not
    # up for reconsideration, and testing it destroyed real evenings: leaving
    # anywhere is faster than walking pace, so the first version threw away
    # every stay at the moment it ended and `been_to` never held more than the
    # one they were in. Douwe and Gaby's evening out on 2026-09-12 came home
    # remembering the last five minutes of itself.
    finished = trip.stay
    if (
        finished
        and not watched
        and quiet_for > 0
        and travelled / quiet_for > still_speed
    ):
        finished = None
    return replace(
        trip,
        latitude=fix.latitude,
        longitude=fix.longitude,
        anchor_since=now,
        anchor_place=name,
        anchor_radius=place.radius if place else None,
        anchor_seen_at=now,
        settled_at=None,
        stay_latitude=None,
        stay_longitude=None,
        stays=(*trip.stays, finished) if finished else trip.stays,
    )


def _tighten(trip: Trip, place: Place | None) -> Trip:
    """Let a fix that belongs to the current anchor narrow what it is called."""
    if place is None:
        return trip
    if trip.anchor_radius is not None and place.radius >= trip.anchor_radius:
        return trip
    return replace(trip, anchor_place=place.name, anchor_radius=place.radius)


def _turn(
    trip: Trip,
    fix: Fix,
    place: Place | None,
    from_home: float,
    now: datetime,
    turn_slack: float,
) -> Trip:
    """Keep the tightest named zone near the far end of the trip so far.

    The window only ever moves outwards, because `furthest` only ever grows —
    so a candidate that falls out of it is gone for good and there is nothing
    to reconsider later. That is what makes this an O(1) running answer rather
    than a list of every far fix: once they have driven past somewhere, it
    stops being where they were heading.
    """
    floor = trip.furthest - turn_slack

    if trip.turn_place is not None and trip.turn_distance < floor:
        # They went further. Whatever named the old far end is now the road.
        trip = replace(trip, turn_place=None, turn_radius=None)

    if place is None or from_home < floor:
        return trip
    if trip.turn_radius is not None and place.radius >= trip.turn_radius:
        return trip

    return replace(
        trip,
        turn_place=place.name,
        turn_radius=place.radius,
        turn_distance=from_home,
        turn_at=now,
        turn_latitude=fix.latitude,
        turn_longitude=fix.longitude,
    )


def _visited(
    visits: tuple[Visit, ...], place: str | None, now: datetime
) -> tuple[Visit, ...]:
    """Extend the open visit, or open a new one when the place changes.

    Leaving a zone for open ground closes the visit without opening another:
    the gaps between named places are the road, and the road has no name worth
    keeping.
    """
    if place is None:
        return visits
    if visits and visits[-1].place == place:
        return (*visits[:-1], replace(visits[-1], until=now))
    return (*visits, Visit(place=place, since=now, until=now))


def _settle(trip: Trip, now: datetime, dwell: timedelta) -> Trip:
    """Promote an anchor that has held long enough into a stay."""
    if trip.settled or now - trip.anchor_since < dwell:
        return trip
    return replace(
        trip,
        settled_at=trip.anchor_since,
        stay_latitude=trip.latitude,
        stay_longitude=trip.longitude,
    )


_SCHEMA = 2


def _moment(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _read_moment(value: Any) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def trip_as_dict(trip: Trip) -> dict[str, Any]:
    """The whole trip as plain JSON, for a restart to hand back.

    Everything, and not the sensor's own attributes, because those are a
    summary written for templates: they flatten `been_to` into names and
    minutes and say nothing at all about the anchor, `anchor_seen_at`, or where
    the last fix actually landed. A trip rebuilt from them could be displayed
    but not *continued* — the next fix has to be measured against the anchor to
    decide whether anybody moved, and against `seen_at` to decide whether the
    silence before it was a restaurant or a motorway.

    Datetimes are written out as ISO strings by hand, and read back the same
    way. Home Assistant's store would happily stringify them on the way out on
    its own, which is exactly the trap: nothing would stringify them back, and
    the restored `Trip` would carry `str` where the arithmetic expects
    `datetime` and fail on the first tick.
    """
    return {
        "v": _SCHEMA,
        "left_at": _moment(trip.left_at),
        "latitude": trip.latitude,
        "longitude": trip.longitude,
        "anchor_since": _moment(trip.anchor_since),
        "anchor_place": trip.anchor_place,
        "anchor_radius": trip.anchor_radius,
        "anchor_seen_at": _moment(trip.anchor_seen_at),
        "seen_at": _moment(trip.seen_at),
        "seen_latitude": trip.seen_latitude,
        "seen_longitude": trip.seen_longitude,
        "place": trip.place,
        "settled_at": _moment(trip.settled_at),
        "stay_latitude": trip.stay_latitude,
        "stay_longitude": trip.stay_longitude,
        "visits": [
            {
                "place": visit.place,
                "since": _moment(visit.since),
                "until": _moment(visit.until),
            }
            for visit in trip.visits
        ],
        "stays": [
            {
                "latitude": stay.latitude,
                "longitude": stay.longitude,
                "place": stay.place,
                "since": _moment(stay.since),
                "until": _moment(stay.until),
            }
            for stay in trip.stays
        ],
        "furthest": trip.furthest,
        "turn_place": trip.turn_place,
        "turn_radius": trip.turn_radius,
        "turn_distance": trip.turn_distance,
        "turn_at": _moment(trip.turn_at),
        "turn_latitude": trip.turn_latitude,
        "turn_longitude": trip.turn_longitude,
        "ventured": trip.ventured,
    }


def trip_from_dict(data: Mapping[str, Any] | None) -> Trip | None:
    """Read back what `trip_as_dict` wrote, or None if it cannot be trusted.

    None rather than an exception for anything unreadable — a shape this
    version does not know, a key that moved, a half-written store. The cost of
    declining is one trip that starts fresh; the cost of raising is a sensor
    that fails to set up at all, and losing an itinerary is not worth losing
    the entity over.
    """
    if not data or data.get("v") != _SCHEMA:
        return None

    try:
        return Trip(
            left_at=datetime.fromisoformat(data["left_at"]),
            latitude=data["latitude"],
            longitude=data["longitude"],
            anchor_since=datetime.fromisoformat(data["anchor_since"]),
            anchor_place=data.get("anchor_place"),
            anchor_radius=data.get("anchor_radius"),
            anchor_seen_at=_read_moment(data.get("anchor_seen_at")),
            seen_at=datetime.fromisoformat(data["seen_at"]),
            seen_latitude=data.get("seen_latitude", 0.0),
            seen_longitude=data.get("seen_longitude", 0.0),
            place=data.get("place"),
            settled_at=_read_moment(data.get("settled_at")),
            stay_latitude=data.get("stay_latitude"),
            stay_longitude=data.get("stay_longitude"),
            visits=tuple(
                Visit(
                    place=visit["place"],
                    since=datetime.fromisoformat(visit["since"]),
                    until=datetime.fromisoformat(visit["until"]),
                )
                for visit in data.get("visits", ())
            ),
            stays=tuple(
                Stay(
                    latitude=stay["latitude"],
                    longitude=stay["longitude"],
                    place=stay.get("place"),
                    since=datetime.fromisoformat(stay["since"]),
                    until=datetime.fromisoformat(stay["until"]),
                )
                for stay in data.get("stays", ())
            ),
            furthest=data.get("furthest", 0.0),
            turn_place=data.get("turn_place"),
            turn_radius=data.get("turn_radius"),
            turn_distance=data.get("turn_distance", 0.0),
            turn_at=_read_moment(data.get("turn_at")),
            turn_latitude=data.get("turn_latitude"),
            turn_longitude=data.get("turn_longitude"),
            ventured=data.get("ventured", False),
        )
    except (KeyError, TypeError, ValueError):
        return None


def stale(trip: Trip, now: datetime, max_age: timedelta) -> bool:
    """Whether the phone has gone quiet for longer than we'll vouch for.

    Silence usually means stillness, which is why the dwell is counted off the
    clock — but only up to a point. Past it the phone is off, flat or out of
    signal, and the anchor is the last thing it said rather than where anybody
    is.
    """
    return now - trip.seen_at > max_age
