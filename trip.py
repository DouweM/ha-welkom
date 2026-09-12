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
    the turn    — the named zone holding the furthest fix of the whole trip

and a trip is told as both, plus the named zones it passed through on the way.

Zones do not decide stillness, only identity. A hand-drawn zone is a place the
household has already declared it cares about, so it names a stay, merges a
stay that wanders inside it, and excuses a stay from `away_floor` — Douwe's
walks to the Puente sit 160 m from the house, which is nearer than the floor
and is still somewhere he went. What zones must NOT do is lower the bar for
holding still, or four minutes of driving through a one-kilometre zone would
read as an evening in it.

Pure, like `location.py`: no Home Assistant imports, so `tests/check_trip.py`
exercises the whole state machine without booting HA.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from .location import Circle, Fix, distance


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
    stay_place: str | None = None
    """Where the stay began. A place is bigger than an anchor — somebody at
    dinner walks to the bar — so a step within `place_radius` of THIS point
    keeps the stay rather than starting a new one. Measured from where the stay
    began and never from the latest anchor, for the same reason the anchor
    itself does not drift: hops of `place_radius` would otherwise carry one stay
    clean across the city. Unused while `place` is set, which is a stronger
    answer to the same question.

    `stay_place` is the zone it happened in, remembered rather than read off
    `place` when the stay ends: by then `place` is wherever they have moved on
    to, and a stay labelled with the place they left for is worse than one with
    no label at all."""

    visits: tuple[Visit, ...] = ()
    """Every named zone the trip has been through, in order, with the last one
    still running. The itinerary, which is what a welcome-home line wants: they
    were at the park, and stopped at the bridge on the way back."""

    stays: tuple[Stay, ...] = ()
    """The stays that are over. The live one, if there is one, is `stay`."""

    furthest: float = 0.0
    furthest_at: datetime | None = None
    furthest_latitude: float | None = None
    furthest_longitude: float | None = None
    furthest_place: str | None = None
    """The most distant fix of the trip, where and when. A destination can be
    nearer than the turning point on the way back, and a school run has no stay
    at all — the turn is the whole of what happened."""

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
            place=self.stay_place,
            since=self.settled_at,
            until=self.seen_at,
        )

    @property
    def turn(self) -> Stay | None:
        """The furthest point, as somewhere they were.

        Not a stay — they may have been there for eleven seconds — but the same
        shape, because to everything downstream it is the same kind of fact.
        """
        if self.furthest_at is None or self.furthest_latitude is None:
            return None
        return Stay(
            latitude=self.furthest_latitude,
            longitude=self.furthest_longitude or 0.0,
            place=self.furthest_place,
            since=self.furthest_at,
            until=self.furthest_at,
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
        if (turn := self.turn) and turn.place is not None:
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
    place: str | None = None,
    settle_radius: float,
    place_radius: float,
    dwell: timedelta,
    away_floor: float,
    still_speed: float,
) -> Trip | None:
    """Fold one observation into the trip, returning the trip as it now stands.

    Called both when a fix arrives and on a plain tick with `fix` None, because
    the two carry different news and both matter: a fix can move the anchor, and
    the passage of time alone can settle it.

    `place` is the name of the smallest hand-drawn zone the fix falls in, which
    the caller resolves because zones live in Home Assistant. It should already
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

    if trip is None:
        return Trip(
            left_at=now,
            latitude=fix.latitude,
            longitude=fix.longitude,
            anchor_since=now,
            seen_at=now,
            seen_latitude=fix.latitude,
            seen_longitude=fix.longitude,
            place=place,
            visits=(Visit(place=place, since=now, until=now),) if place else (),
            furthest=from_home,
            furthest_at=now,
            furthest_latitude=fix.latitude,
            furthest_longitude=fix.longitude,
            furthest_place=place,
            ventured=from_home >= away_floor,
        )

    # The zone they were in BEFORE this fix. Read it now, because the trip is
    # about to be rewritten with the new one and the re-anchor below turns on
    # the difference between the two.
    was = trip.place

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
        place=place,
        visits=_visited(trip.visits, place, now),
        ventured=trip.ventured or from_home >= away_floor,
    )
    if from_home > trip.furthest:
        trip = replace(
            trip,
            furthest=from_home,
            furthest_at=now,
            furthest_latitude=fix.latitude,
            furthest_longitude=fix.longitude,
            furthest_place=place,
        )

    moved = distance(fix.latitude, fix.longitude, trip.latitude, trip.longitude)
    if moved <= max(settle_radius, fix.accuracy):
        # Still here. The anchor is deliberately NOT dragged towards the new
        # fix: letting it follow the noise lets a stay walk down the street a
        # few meters at a time and never trip the radius, which is how a moving
        # car reads as a place.
        return _settle(trip, now, dwell)

    # Out of the anchor. Still the same place if the zone says so, or failing a
    # zone, if it is a step inside the one they had settled into.
    if place is not None:
        within_stay = trip.settled and place == was
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
        return replace(trip, latitude=fix.latitude, longitude=fix.longitude)

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
    finished = trip.stay
    if finished and quiet_for > 0 and travelled / quiet_for > still_speed:
        finished = None
    return replace(
        trip,
        latitude=fix.latitude,
        longitude=fix.longitude,
        anchor_since=now,
        settled_at=None,
        stay_latitude=None,
        stay_longitude=None,
        stay_place=None,
        stays=(*trip.stays, finished) if finished else trip.stays,
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
        stay_place=trip.place,
    )


def stale(trip: Trip, now: datetime, max_age: timedelta) -> bool:
    """Whether the phone has gone quiet for longer than we'll vouch for.

    Silence usually means stillness, which is why the dwell is counted off the
    clock — but only up to a point. Past it the phone is off, flat or out of
    signal, and the anchor is the last thing it said rather than where anybody
    is.
    """
    return now - trip.seen_at > max_age
