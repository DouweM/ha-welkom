from typing import Any

import aiohttp
from pydantic import BaseModel

from .models import ConnectedPerson, Connection, Home, Person, Room


class WelkomClient(BaseModel):
    """A Welkom client."""

    id: str
    url: str
    home_id: str | None = None  # TODO: Move elsewhere? More a coordinator property?

    _session: aiohttp.ClientSession | None = None

    _connection: Connection | None = None
    _homes: dict[str, Home] | None = None
    _home: Home | None = None
    _rooms: dict[str, Room] | None = None
    _people: dict[str, Person] | None = None
    # _devices: dict[str, Device] | None = None

    @property
    def session(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession(raise_for_status=True)

        return self._session

    async def request(self, url: str) -> list[dict[str, Any]] | dict[str, Any]:
        response = await self.session.get(url)
        return await response.json()

    @property
    async def connection(self) -> Connection:
        if self._connection is None:
            raw_connection = await self.request(f"{self.url}/api/me")
            self._connection = Connection.model_validate(raw_connection)

        return self._connection

    @property
    async def homes(self) -> dict[str, Home]:
        if self._homes is None:
            raw_homes = await self.request(f"{self.url}/api/homes")
            homes = [Home.model_validate(home) for home in raw_homes]
            self._homes = {home.id: home for home in homes}
        return self._homes

    @property
    async def people(self) -> dict[str, Person]:
        if self._people is None:
            raw_people = await self.request(f"{self.url}/api/people")
            people = [Person.model_validate(person) for person in raw_people]
            self._people = {person.id: person for person in people}
        return self._people

    # @property
    # async def devices(self) -> dict[str, Device]:
    #     if self._devices is None:
    #         raw_devices = await self.request(f"{self.url}/api/devices")
    #         devices = [Device.model_validate(device) for device in raw_devices]
    #         # TODO: What ID to use?
    #         self._devices = {device.id: device for device in devices}
    #     return self._devices

    @property
    async def home(self) -> Home:
        if self._home is None:
            homes = await self.homes

            home = None
            if self.home_id:
                if self.home_id not in homes:
                    raise ValueError(
                        f"Home '{self.home_id}' not found. Available homes: {', '.join(homes.keys())}"
                    )

                home = homes[self.home_id]
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
    async def rooms(self) -> dict[str, Room]:
        if self._rooms is None:
            home = await self.home
            self._rooms = {room.id: room for room in home.rooms} if home else {}

        return self._rooms

    @property
    async def connected_people(self) -> list[ConnectedPerson]:
        raw_connected_people = await self.request(f"{self.url}/api/homes/people")
        return [
            ConnectedPerson.model_validate(connected_person)
            for connected_person in raw_connected_people
        ]

    @property
    def unique_id(self) -> str:
        """The unique ID of the client."""
        return f"client_{self.id}"
