import asyncio
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
import logging
from typing import Any, cast

import aiohttp
from pydantic import BaseModel

from homeassistant.components.zone import (
    DATA_ZONE_ENTITY_IDS,
    ENTITY_ID_HOME,
    async_get_enclosing_zones,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_LATITUDE,
    ATTR_LONGITUDE,
    STATE_HOME,
    STATE_UNAVAILABLE,
)
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, State, callback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util, slugify

from .client import WelkomClient
from .const import (
    TRIP_AWAY_FLOOR,
    TRIP_COARSE_ZONE,
    TRIP_DWELL,
    TRIP_MAX_AGE,
    TRIP_MIN,
    TRIP_PLACE_RADIUS,
    TRIP_SETTLE_RADIUS,
    TRIP_STILL_SPEED,
    TRIP_TURN_SLACK,
    gps_tracker_entity_id,
)
from .location import Circle, Fix, distance
from .models import (
    Activity,
    ConnectedPerson,
    Connection,
    Device,
    Home,
    Person,
    Role,
    Room,
)
from .places import Geocode, PlaceSpec, places_from_config, recognise
from .trip import Place, Trip, follow, stale

_LOGGER = logging.getLogger(__name__)

type WelkomConfigEntry = ConfigEntry[WelkomCoordinator]


class AreaData(BaseModel):
    """Data for an area."""

    people_count: int = 0
    people: list[str] = []

    known_people_count: int = 0
    known_people: list[str] = []

    unknown_people_count: int = 0
    unknown_people: list[str] = []


class HomeData(AreaData):
    """Data for a home."""


class RoomData(AreaData):
    """Data for a room."""


class PersonData(BaseModel):
    """Data for a person."""

    person: Person | None = None
    device: Device | None = None
    home: Home
    room: Room | None = None

    state: str | None = None
    """Welkom's placement as a tracker state: a room of the main home, `home`,
    or another home (`"Cabin"`, `"Cabin: Kitchen"`)."""

    latitude: float | None = None
    longitude: float | None = None
    """The center of the most specific HA zone the placement maps onto."""

    in_zones: list[str] = []
    """HA zones the placement maps onto: the room's, the home's, and whatever
    encloses the home — what a GPS tracker standing at that spot would report."""
    home_zone_id: str | None = None
    """The HA zone standing for `home`, when one exists."""

    activity: Activity | None = None

    # Do we need an eq method here for always_update=False to work?


class DeviceData(BaseModel):
    """Data for a (tracker or personal) device and its active connections."""

    device: Device
    connections: list[Connection] = []

    connection: Connection | None = None
    """The primary connection: online first, then highest role, then most recently seen."""

    @property
    def state(self) -> str | None:
        return self.connection.network.id if self.connection else None


class WelkomData(BaseModel):
    """Data for the Welkom component."""

    homes: dict[str, HomeData] = {}
    rooms: dict[str, RoomData] = {}
    people: dict[str, PersonData] = {}
    unknown_people: list[PersonData] = []
    devices: dict[str, DeviceData] = {}
    suspended_devices: set[str] = set()
    """Devices barred from the current-device slot (e.g. asleep per their
    companion app); beacon pings from them are not applied."""


class WelkomCoordinator(DataUpdateCoordinator[WelkomData]):
    """My custom coordinator."""

    homes: dict[str, Home] | None = None
    people: dict[str, Person] | None = None
    roles: list[Role] | None = None

    _home: Home | None = None
    _rooms: dict[str, Room] | None = None

    def __init__(
        self, hass: HomeAssistant, config_entry: WelkomConfigEntry, client: WelkomClient
    ):
        """Initialize my coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=f"Welkom ({client.id})",
            config_entry=config_entry,
            update_interval=timedelta(seconds=30),
            always_update=False,
        )
        self.client = client
        # Last good fetch per home, so a home that goes dark keeps its entities
        # populated while the others carry on updating.
        self._last_by_home: dict[
            str, tuple[list[ConnectedPerson], list[Connection]]
        ] = {}
        # Last known suspended set, for the same reason (see _suspended_devices).
        self._suspended: set[str] = set()
        # Where each person who is out has got to, and the moment their phone
        # last said something new — see `observe`.
        self._trips: dict[str, Trip] = {}
        self._seen: dict[str, datetime] = {}
        # Trips carried over a restart, waiting for the tracker to say whether
        # they are still happening — see `restore_trip`.
        self._remembered: dict[str, Trip] = {}
        # The last trip each person finished, and when — see `last_trip`.
        self._ended: dict[str, tuple[Trip, datetime]] = {}
        # The household's configured places, read on first use — see `places`.
        self._places: tuple[PlaceSpec, ...] | None = None
        # Told separately from the coordinator's own listeners, and that is not
        # tidiness. `observe` is called BY a listener — the person's tracker,
        # deciding whose evidence won — so telling the coordinator's listeners
        # from inside it re-enters the tracker, which observes again, which
        # tells them again. Home Assistant kills the dispatch after ten
        # thousand queued events and the instance stops answering.
        self._trip_listeners: list[Callable[[], None]] = []

    async def _async_setup(self):
        # A stay is made of time, so trips have to be advanced by the clock and
        # not only by what the phone says; see `_tick`.
        if self.config_entry:
            self.config_entry.async_on_unload(
                async_track_time_interval(self.hass, self._tick, timedelta(seconds=30))
            )

        async with asyncio.timeout(10):
            self.homes, self.people, self.roles = await asyncio.gather(
                # self.client.connection,
                self.client.homes,
                self.client.people,
                self.client.roles,
            )
            # Set self.home and self.rooms as well, no async necessary.

    @property
    def home(self) -> Home:
        if self._home is None:
            homes = self.homes
            if homes is None:
                raise ValueError("No homes found")

            home_id = self.client.home_id
            home = None
            if home_id:
                if home_id not in homes:
                    raise ValueError(
                        f"Home '{home_id}' not found. Available homes: {', '.join(homes.keys())}"
                    )

                home = homes[home_id]
            elif len(homes) == 1:
                home = next(iter(homes.values()))
            else:
                raise ValueError(
                    "You have multiple homes, please specify the 'home_id' in the configuration. "
                    f"Available homes: {', '.join(homes.keys())}"
                )

            self._home = home

        return self._home

    @property
    def rooms(self) -> dict[str, Room] | None:
        if self._rooms is None:
            home = self.home
            if home is None:
                return None

            self._rooms = {room.id: room for room in home.rooms}

        return self._rooms

    def _update_area_data(self, area_data: AreaData, conn: ConnectedPerson):
        area_data.people_count += 1  # TODO: Use list length instead?
        area_data.people.append(conn.person.display_name)

        if conn.known:
            area_data.known_people_count += 1
            area_data.known_people.append(conn.person.display_name)
        else:
            area_data.unknown_people_count += 1
            area_data.unknown_people.append(conn.person.display_name)

    def _role_priority(self, role_id: str) -> int:
        roles = self.roles or []
        return next((i for i, role in enumerate(roles) if role.id == role_id), -1)

    def _connection_priority(self, conn: Connection) -> tuple[bool, int, datetime]:
        last_seen = conn.metadata.last_seen or datetime.min
        if last_seen.tzinfo is None:
            last_seen = last_seen.replace(tzinfo=UTC)

        return (
            bool(conn.metadata.online),
            self._role_priority(conn.role.id),
            last_seen,
        )

    def _device_data(self, connections: list[Connection]) -> dict[str, DeviceData]:
        devices: dict[str, DeviceData] = {}
        for conn in connections:
            device = conn.device
            if not device.known or not (device.tracker or device.personal):
                continue

            key = slugify(device.display_name)
            data = devices.setdefault(key, DeviceData(device=device))
            data.connections.append(conn)

        for data in devices.values():
            data.connection = max(data.connections, key=self._connection_priority)

        return devices

    async def _async_update_data(self):
        try:
            return await self._fetch_data()
        except aiohttp.ClientError as err:
            # Welkom answers 503 when its network resolver can't see the
            # network, rather than an empty list that would read as "nobody is
            # home". Our entities go `unavailable` for the cycle — which is the
            # honest reading of "we can't see" — and HA's `person` component
            # ignores unavailable trackers, so `person.*` holds its last state
            # instead of everyone appearing to leave at once.
            raise UpdateFailed(f"Error talking to welkom: {err}") from err

    async def _fetch_home(
        self, home_id: str
    ) -> tuple[list[ConnectedPerson], list[Connection]]:
        conns, connections = await asyncio.gather(
            self.client.connected_people(home_id),
            self.client.connections(home_id),
        )
        return conns, connections

    async def _fetch_homes(self) -> tuple[list[ConnectedPerson], list[Connection]]:
        """Fetch every home, letting one unreachable home fail on its own.

        Welkom answers 503 for a home whose network resolver can't see its
        network. That's the signal we want for *this* home — it's what keeps a
        UniFi outage from publishing an empty house — but a second home going
        dark (a travel router with a broken API, say) must not blank the home
        the user actually lives in. Only this instance's own home is fatal;
        others keep whatever we last saw.
        """
        main_home_id = self.home.id
        home_ids = list(self.homes or {})
        results = await asyncio.gather(
            *(self._fetch_home(home_id) for home_id in home_ids),
            return_exceptions=True,
        )

        conns: list[ConnectedPerson] = []
        connections: list[Connection] = []
        for home_id, result in zip(home_ids, results, strict=True):
            if isinstance(result, BaseException):
                if home_id == main_home_id:
                    raise result

                _LOGGER.warning(
                    "Welkom home %s is unavailable, keeping its last known state: %s",
                    home_id,
                    result,
                )
                previous = self._last_by_home.get(home_id)
                if previous is None:
                    continue
                result = previous
            else:
                self._last_by_home[home_id] = result

            home_conns, home_connections = result
            conns.extend(home_conns)
            connections.extend(home_connections)

        return conns, connections

    async def _fetch_data(self) -> WelkomData:
        # Refresh the configured people so newly-added people are picked up
        # without a full integration reload. The platforms read
        # `coordinator.people` and add entities for any new ids.
        self.people = await self.client.fetch_people()

        (conns, connections), suspended = await asyncio.gather(
            self._fetch_homes(),
            self._suspended_devices(),
        )

        homes: dict[str, HomeData] = defaultdict(HomeData)
        rooms: dict[str, RoomData] = defaultdict(RoomData)
        people: dict[str, PersonData] = {}
        unknown_people: list[PersonData] = []

        people_by_state: dict[str, list[PersonData]] = defaultdict(list)
        for conn in conns:
            main_home = self.home

            home = conn.home or main_home
            room = conn.room

            state: str | None = None
            if home == main_home:
                state = room.display_name if room else STATE_HOME
            else:
                state = home.display_name
                if room:
                    state += f": {room.display_name}"

            person = PersonData(
                person=conn.person,
                device=conn.connection.device,
                home=home,
                room=room,
                state=state,
                activity=conn.activity,
            )

            if conn.known:
                people[conn.person.id] = person
            else:
                unknown_people.append(person)

            people_by_state[state].append(person)

            if home:
                self._update_area_data(homes[home.id], conn)

            if home == main_home and room:
                self._update_area_data(rooms[room.id], conn)

        zones = self._zones_by_name
        for state_people in people_by_state.values():
            # TODO: This affects locations on the map, should only be used for those actually shown?
            # distance_between_people = 0.00003
            # offset = (len(state_people) - 1) * (distance_between_people / 2) * -1
            for person in state_people:
                self._place(person, zones)

        return WelkomData(
            homes=homes,
            rooms=rooms,
            people=people,
            unknown_people=unknown_people,
            devices=self._device_data(connections),
            suspended_devices=suspended,
        )

    async def _suspended_devices(self) -> set[str]:
        """The suspended-devices set, empty when welkom predates the endpoint.

        A failed fetch keeps the last known set rather than emptying it: an
        empty set reads as "nothing is suspended", and the ping handler uses it
        to skip beacons welkom is going to ignore anyway. Guessing "nothing"
        there lets a sleeping device claim the current-device slot locally and
        then diverge from welkom.
        """
        try:
            self._suspended = await self.client.suspended_devices
        except Exception:
            _LOGGER.debug("Suspended-devices fetch failed", exc_info=True)

        return self._suspended

    @callback
    def add_trip_listener(self, update_callback: Callable[[], None]) -> CALLBACK_TYPE:
        """Listen for trip changes, which the poll cycle does not carry."""

        def remove() -> None:
            if update_callback in self._trip_listeners:
                self._trip_listeners.remove(update_callback)

        self._trip_listeners.append(update_callback)
        return remove

    @callback
    def _notify_trips(self) -> None:
        for update_callback in list(self._trip_listeners):
            update_callback()

    def trip(self, person_id: str) -> Trip | None:
        """The journey this person is on, or None while they are home."""
        return self._trips.get(person_id)

    def last_trip(self, person_id: str) -> tuple[Trip, datetime] | None:
        """The trip this person most recently finished, and when they got home.

        Kept because the moment anybody wants to say where somebody has been is
        the moment they walk back in — and by then `trip` says None. The front
        door's card is written as the bolt moves, seconds after welkom has the
        phone back; "arriving from School" can only be said if the finished
        trip is still readable then.

        Not carried across a restart, on purpose. `extra_restore_state_data`
        hands back only what was still running, and a finished trip resurrected
        into a house that has since had a night's sleep would name yesterday's
        errand on today's door. The cost of forgetting is a card that says
        "arriving" without "from"; the cost of misremembering is one that lies.
        """
        return self._ended.get(person_id)

    @callback
    def restore_trip(self, person_id: str, trip: Trip) -> None:
        """Take back a trip that outlived a restart, to be continued or dropped.

        Remembered rather than installed, and that is about ordering. The
        tracker builds itself and calls `observe` from its own constructor,
        before any entity has been added to hass, while this arrives from the
        sensor's `async_added_to_hass` — and the two live on different
        platforms, set up independently. Neither end can say which speaks
        first. Installing this as the live trip would therefore sometimes
        overwrite a fix the tracker had already folded in, and sometimes be
        overwritten by one; waiting to be claimed is the same answer whichever
        order they happen in.

        Nothing is claimed until the tracker next says the person is out. Until
        then the sensor reads `home`, which is the honest answer: welkom has
        not spoken yet, and a restart is not evidence about where anybody is.

        Unless the tracker already has -- and that case is not rare, it is the
        usual one. The tracker's `async_added_to_hass` folds the phone's fix in
        as soon as it has a state machine to read, and when that runs before
        this does, the fix has already started a NEW trip, timestamped at the
        restart, with an empty itinerary. On 2026-09-20 Gaby's evening out --
        two stops, an hour and a half -- became a trip that began at 02:29:46,
        and the door would have had nothing to say when she got home. A trip
        that began after the remembered one last spoke is that artefact, not a
        new journey, and the remembered one carries on in its place.
        """
        live = self._trips.get(person_id)
        if live is None:
            self._remembered[person_id] = trip
            return
        now = dt_util.utcnow()
        if live.left_at >= trip.seen_at and not stale(trip, now, TRIP_MAX_AGE):
            self._trips[person_id] = trip
            self._notify_trips()

    @callback
    def observe(
        self,
        person_id: str,
        fix: Fix | None,
        now: datetime,
        geocode: Geocode | None = None,
    ) -> None:
        """Fold what the person's tracker just decided into their trip.

        Called by `device_tracker.WelkomTracker` rather than read from the
        entity, because the tracker is the only thing that knows which evidence
        won: welkom's network placement or the phone. A trip starts when the
        phone takes over and ends when welkom gets them back, so the two can
        never disagree about whether somebody is out.

        `fix` is None when welkom has them — they are home, and any trip is
        over. `geocode` is what the phone's reverse geocoder said about the
        fix, if the tracker has one that describes it; it is how a stop in a
        neighbourhood the household named gets that name.
        """
        if fix is None:
            # Home. Whatever was remembered across the restart is over too —
            # they may well have walked in while Home Assistant was down.
            self._remembered.pop(person_id, None)
            if (over := self._trips.pop(person_id, None)) is not None:
                # Remembered only if it amounted to anything. welkom losing
                # the phone for five minutes hands the position to GPS and
                # back, and that is a trip by this module's definition -- but
                # it went nowhere, and letting it replace the afternoon out
                # would make "arriving from School" unsayable at the door ten
                # minutes later. `been_to` is already the judge of whether a
                # trip happened; a trip it has nothing to say about does not
                # displace one it did.
                home = self.home_circle
                if home is None or over.been_to(
                    home,
                    dwell=TRIP_DWELL,
                    min_trip=TRIP_MIN,
                    away_floor=TRIP_AWAY_FLOOR,
                ):
                    self._ended[person_id] = (over, now)
                self._seen.pop(person_id, None)
                self._notify_trips()
            return

        home = self.home_circle
        if home is None:
            return

        # The same fix arriving again is not news: the phone said it once and
        # has been quiet since, which is exactly what `Trip.seen_at` is for.
        spoke_at = now - fix.age
        if self._seen.get(person_id) == spoke_at:
            return
        self._seen[person_id] = spoke_at

        before = self._trips.get(person_id)
        if before is None:
            # A trip from before the restart, claimed by the first fix that
            # says they are still out — and dropped either way, so a stale one
            # cannot sit around waiting for a later chance.
            #
            # Judged here rather than when it was handed back, because this is
            # where `now` is real. `TRIP_MAX_AGE` and no new threshold: within
            # it, silence is already read as stillness everywhere else here —
            # the dwell counts it, an arrival is announced out of it — so a
            # trip carried across a two-minute restart is exactly as believable
            # as one that sat through two quiet minutes with Home Assistant up.
            # Past it the module already declines to vouch for the anchor; that
            # is what `stale` tells the sensor, and resurrecting a trip it
            # would refuse to stand behind is not a thing to do quietly.
            remembered = self._remembered.pop(person_id, None)
            if remembered is not None and not stale(remembered, now, TRIP_MAX_AGE):
                before = remembered

        after = follow(
            before,
            fix,
            home,
            now,
            place=self.place_at(fix, geocode),
            settle_radius=TRIP_SETTLE_RADIUS,
            place_radius=TRIP_PLACE_RADIUS,
            dwell=TRIP_DWELL,
            away_floor=TRIP_AWAY_FLOOR,
            still_speed=TRIP_STILL_SPEED,
            turn_slack=TRIP_TURN_SLACK,
        )
        if after is not None:
            self._trips[person_id] = after
        if after != before:
            self._notify_trips()

    @callback
    def _tick(self, now: datetime) -> None:
        """Let the clock run on every live trip.

        A stay is made of time, and a phone stops speaking the moment it has
        nothing new to say, so without this an arrival would only ever be
        noticed when the person moved again — which is to say, when it had
        stopped being true.

        Its own timer rather than the poll, because the coordinator runs with
        `always_update=False`: a cycle in which welkom says exactly what it said
        last time notifies nobody, and those are the cycles somebody sitting
        still generates.
        """
        home = self.home_circle
        if home is None:
            return

        changed = False
        for person_id, trip in self._trips.items():
            advanced = follow(
                trip,
                None,
                home,
                now,
                settle_radius=TRIP_SETTLE_RADIUS,
                place_radius=TRIP_PLACE_RADIUS,
                dwell=TRIP_DWELL,
                away_floor=TRIP_AWAY_FLOOR,
                still_speed=TRIP_STILL_SPEED,
                turn_slack=TRIP_TURN_SLACK,
            )
            if advanced is not None and advanced != trip:
                self._trips[person_id] = advanced
                changed = True

        if changed:
            self._notify_trips()

    @property
    def home_circle(self) -> Circle | None:
        """`zone.home`'s footprint, which every trip is measured against."""
        zone = self.hass.states.get(ENTITY_ID_HOME)
        if zone is None:
            return None
        zone_attrs = cast(dict[str, Any], zone.attributes)
        latitude = zone_attrs.get(ATTR_LATITUDE)
        longitude = zone_attrs.get(ATTR_LONGITUDE)
        if latitude is None or longitude is None:
            return None
        return Circle(
            latitude=latitude,
            longitude=longitude,
            radius=zone_attrs.get("radius") or 0,
        )

    @property
    def places(self) -> tuple[PlaceSpec, ...]:
        """The household's own places, from the home's `attrs.homeassistant`.

        Read once: homes are fetched at setup and a reload is how config
        changes arrive, the same as for the images.
        """
        if self._places is None:
            try:
                raw = self.home.attrs.homeassistant.places
            except ValueError:
                raw = []
            self._places = places_from_config(raw)
        return self._places

    def place_at(
        self,
        fix: Fix,
        geocode: Geocode | None = None,
        *,
        coarse: float = TRIP_COARSE_ZONE,
    ) -> Place | None:
        """The tightest place the fix is in, if any: a zone, or one of ours.

        Smallest because zones nest: a park sits inside a borough sits inside
        the city, and the useful name is the tightest one. By NAME, because
        that is what a person means by a place — five overlapping rectangles
        make up `Chapu III` here, and crossing from one into the next is not
        going anywhere.

        Home Assistant's own overlap rule, so a vague fix at the kerb outside
        a 48 m school zone is in it, the same way it would be for any tracker.
        Passive zones are skipped for the reason Home Assistant skips them: the
        twelve five-metre room zones are welkom's business, not a trip's — and
        the marker circles that stand for a geocoded place on the map must not
        claim anybody by geometry.

        The household's configured places compete on the same radius — see
        `places.recognise` — so a polygon or a geocoded neighbourhood only ever
        wins by being tighter than the best real circle. `coarse` is the radius
        nothing may reach: a trip ignores the zone drawn around the whole city,
        while a tracker naming its state wants it as the last resort.
        """
        best: Place | None = None
        best_radius = coarse
        for zone_entity_id in self.hass.data.get(DATA_ZONE_ENTITY_IDS, ()):
            if zone_entity_id == ENTITY_ID_HOME:
                continue
            zone = self.hass.states.get(zone_entity_id)
            if not zone or zone.state == STATE_UNAVAILABLE:
                continue

            zone_attrs = cast(dict[str, Any], zone.attributes)
            if zone_attrs.get("passive"):
                continue
            latitude = zone_attrs.get(ATTR_LATITUDE)
            longitude = zone_attrs.get(ATTR_LONGITUDE)
            radius = zone_attrs.get("radius")
            if latitude is None or longitude is None or not radius:
                continue
            if radius >= best_radius:
                continue

            if distance(
                fix.latitude, fix.longitude, latitude, longitude
            ) - radius < max(fix.accuracy, 1):
                best = Place(name=zone.name, radius=radius)
                best_radius = radius

        ours = recognise(
            self.places,
            latitude=fix.latitude,
            longitude=fix.longitude,
            geocode=geocode,
            tighter_than=best_radius,
        )
        return ours or best

    def gps_tracker(self, person_id: str) -> str | None:
        """The device_tracker carrying this person's phone GPS, if configured."""
        person = (self.people or {}).get(person_id)
        options = self.config_entry.options if self.config_entry else {}
        return gps_tracker_entity_id(
            options,
            person_id,
            person.attrs.homeassistant.gps_tracker if person else None,
        )

    def _place(self, data: PersonData, zones: dict[str, State]) -> None:
        """Map welkom's placement onto HA zones.

        Coordinates put the person on the map; `in_zones` is what a GPS tracker
        standing in that room would report, so zone counts, conditions and the
        person entity treat welkom's placement like any other. The room's zone
        is looked up by the full state string — a room name, or "Cabin: Kitchen"
        for another home, so same-named rooms in different homes don't collide —
        and the home's by its name, the main home being `zone.home`.
        """
        if data.home == self.home:
            home_zone = self.hass.states.get(ENTITY_ID_HOME)
        else:
            home_zone = zones.get(data.home.display_name.casefold())
        room_zone = (
            zones.get(data.state.casefold()) if data.room and data.state else None
        )

        in_zones: list[str] = []
        if room_zone:
            in_zones.append(room_zone.entity_id)
        if home_zone:
            in_zones.append(home_zone.entity_id)
            in_zones.extend(
                zone_id
                for zone_id in async_get_enclosing_zones(self.hass, home_zone.entity_id)
                if zone_id not in in_zones
            )
        data.in_zones = in_zones
        data.home_zone_id = home_zone.entity_id if home_zone else None

        if anchor := room_zone or home_zone:
            anchor_attrs = cast(dict[str, Any], anchor.attributes)
            data.latitude = anchor_attrs.get(ATTR_LATITUDE)
            data.longitude = anchor_attrs.get(ATTR_LONGITUDE)

    @property
    def _zones_by_name(self) -> dict[str, State]:
        """Available zones keyed by casefolded name; the first wins on duplicates."""
        hass = self.hass

        result: dict[str, State] = {}
        for zone_entity_id in hass.data.get(DATA_ZONE_ENTITY_IDS, ()):
            zone = hass.states.get(zone_entity_id)
            if not zone or zone.state == STATE_UNAVAILABLE:
                continue

            zone_attrs = cast(dict[str, Any], zone.attributes)
            if not zone_attrs.get(ATTR_LATITUDE) or not zone_attrs.get(ATTR_LONGITUDE):
                continue

            result.setdefault(zone.name.casefold(), zone)

        return result
