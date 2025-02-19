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
from .models import ConnectedPerson

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

    state: str | None = None

    home_id: str | None = None
    room_id: str | None = None

    latitude: float | None = None
    longitude: float | None = None

    # Do we need an eq method here for always_update=False to work?


class WelkomData(BaseModel):
    """Data for the Welkom component."""

    homes: dict[str, HomeData] = {}
    rooms: dict[str, RoomData] = {}
    people: dict[str, PersonData] = {}


class WelkomCoordinator(DataUpdateCoordinator[WelkomData]):
    """My custom coordinator."""

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
            await asyncio.gather(
                # self.client.connection,
                self.client.homes,  # TODO: Should this be stored here, not cached on client?
                self.client.people,
            )

    def _update_area_data(self, area_data: AreaData, conn: ConnectedPerson):
        area_data.people_count += 1  # TODO: Use list length instead
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

        zone_lat_longs = self._zone_lat_longs

        for conn in conns:
            main_home = await self.client.home

            home = conn.home or main_home
            room = conn.room

            state: str | None = None
            if home == main_home:
                state = room.display_name if room else STATE_HOME
            else:
                state = home.display_name
                if room:
                    state += f": {room.display_name}"

            lat, lon = zone_lat_longs.get(state.casefold(), (None, None))

            people[conn.person.id] = PersonData(
                home_id=home.id,
                room_id=room.id if room else None,
                state=state,
                latitude=lat,
                longitude=lon,
            )

            if home:
                self._update_area_data(homes[home.id], conn)

            if home == main_home and room:
                self._update_area_data(rooms[room.id], conn)

        return WelkomData(
            homes=homes,
            rooms=rooms,
            people=people,
        )

    @property
    def _zone_lat_longs(self) -> dict[str, tuple[float | None, float | None]]:
        hass = self.hass

        result = {}
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
