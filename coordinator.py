import asyncio
from collections import defaultdict
from datetime import timedelta
import logging
from typing import Any, cast

from pydantic import BaseModel

from homeassistant.components.zone import ZONE_ENTITY_IDS
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_LATITUDE,
    ATTR_LONGITUDE,
    STATE_HOME,
    STATE_UNAVAILABLE,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .client import WelkomClient
from .models import ConnectedPerson, Device, Home, Person, Room

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

    latitude: float | None = None
    longitude: float | None = None

    # Do we need an eq method here for always_update=False to work?


class WelkomData(BaseModel):
    """Data for the Welkom component."""

    homes: dict[str, HomeData] = {}
    rooms: dict[str, RoomData] = {}
    people: dict[str, PersonData] = {}
    unknown_people: list[PersonData] = []


class WelkomCoordinator(DataUpdateCoordinator[WelkomData]):
    """My custom coordinator."""

    homes: dict[str, Home] | None = None
    people: dict[str, Person] | None = None

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

    async def _async_setup(self):
        async with asyncio.timeout(10):
            self.homes, self.people = await asyncio.gather(
                # self.client.connection,
                self.client.homes,
                self.client.people,
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

    async def _async_update_data(self):
        conns = await self.client.connected_people

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

        zone_lat_longs = self._zone_lat_longs
        for state, state_people in people_by_state.items():
            try:
                lat, lon = zone_lat_longs[state.casefold()]

                # TODO: This affects locations on the map, should only be used for those actually shown?
                # distance_between_people = 0.00003
                # offset = (len(state_people) - 1) * (distance_between_people / 2) * -1

                for person in state_people:
                    person.latitude = lat
                    person.longitude = lon

                    # person.longitude += offset
                    # offset += distance_between_people
            except KeyError:
                continue

        return WelkomData(
            homes=homes,
            rooms=rooms,
            people=people,
            unknown_people=unknown_people,
        )

    @property
    def _zone_lat_longs(self) -> dict[str, tuple[float, float]]:
        hass = self.hass

        result: dict[str, tuple[float, float]] = {}
        for zone_entity_id in hass.data.get(ZONE_ENTITY_IDS, ()):
            zone = hass.states.get(zone_entity_id)
            if not zone or zone.state == STATE_UNAVAILABLE:
                continue

            zone_attrs = cast(dict[str, Any], zone.attributes)
            lat = zone_attrs.get(ATTR_LATITUDE)
            lon = zone_attrs.get(ATTR_LONGITUDE)
            if not lat or not lon:
                continue

            zone_name = STATE_HOME if zone.entity_id == STATE_HOME else zone.name
            result[zone_name.casefold()] = (lat, lon)

        return result
