from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from time import monotonic
from typing import TYPE_CHECKING, Any
from urllib.parse import quote, urlsplit

import aiohttp
from pydantic import BaseModel

from .const import IMAGE_URL_PREFIX
from .models import ConnectedPerson, Connection, Home, Person, Role, Uplink

if TYPE_CHECKING:
    from .changes import Event


FLOOR_TTL = 60.0
"""Seconds a refresh's `after` keeps applying to later reads: twice welkom's
30-second cache, so each worker has refilled it since."""


class WelkomRefused(Exception):
    """welkom answered a write with an error status."""

    def __init__(self, status: int, detail: str):
        super().__init__(f"welkom refused ({status}): {detail}")
        self.status = status
        self.detail = detail


class WelkomClient(BaseModel):
    """A Welkom client."""

    id: str
    url: str
    home_id: str | None = None  # TODO: Move elsewhere? More a coordinator property?

    _session: aiohttp.ClientSession | None = None
    # While above zero, presence reads skip welkom's 30-second cache: set
    # around a refresh that a `changed` event triggered (see changes.py).
    _fresh: int = 0
    # Every open fresh scope's `after`: scopes from different homes' streams
    # overlap and end in any order, so the newest is taken from all of them.
    _fresh_afters: list[float] = []
    # The newest `after` a refresh has used, and when (monotonic). For a
    # while after it, every read asks for at least that recent too: welkom's
    # 17 workers each cache their own, so a plain poll landing on one whose
    # read predates a change would hand back the state from before it.
    # 2026-10-17 18:32: Douwe home at :19, gone at :29 on a plain poll, home
    # again at :50. Only for a while, because welkom reuses any read that
    # started after `after`, however old: past FLOOR_TTL every worker's own
    # 30-second cache has been refilled since, and asking for it would only
    # stop a worker that nobody else is waking from ever reading again.
    _floor: tuple[float, float] | None = None

    _connection: Connection | None = None
    _homes: dict[str, Home] | None = None
    _people: dict[str, Person] | None = None
    _roles: list[Role] | None = None
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
            homes = [
                self._proxy_images(Home.model_validate(home)) for home in raw_homes
            ]
            self._homes = {home.id: home for home in homes}
        return self._homes

    @property
    async def people(self) -> dict[str, Person]:
        if self._people is None:
            return await self.fetch_people()
        return self._people

    async def fetch_people(self) -> dict[str, Person]:
        """Fetch the people list from the API, bypassing the cache."""
        raw_people = await self.request(f"{self.url}/api/people")
        people = [
            self._proxy_images(Person.model_validate(person)) for person in raw_people
        ]
        self._people = {person.id: person for person in people}
        return self._people

    def proxied_url(self, url: str | None) -> str | None:
        """A picture URL as the browser should load it.

        Pictures welkom serves itself come back as absolute URLs on welkom's
        host, which browsers can't reach the way this integration can (see
        images.py); those are rewritten to the integration's same-origin
        proxy. Anything hosted elsewhere is left alone.
        """
        if not url:
            return url
        ours, theirs = urlsplit(self.url), urlsplit(url)
        if theirs.netloc.lower() != ours.netloc.lower():
            return url
        if not theirs.path.startswith("/api/"):
            return url
        proxied = f"{IMAGE_URL_PREFIX}{theirs.path.removeprefix('/api')}"
        return f"{proxied}?{theirs.query}" if theirs.query else proxied

    def _proxy_images[T: Person | Home](self, model: T) -> T:
        model.avatar_url = self.proxied_url(model.avatar_url)
        if isinstance(model, Home):
            for image in model.images.values():
                image.url = self.proxied_url(image.url) or image.url
        return model

    # @property
    # async def devices(self) -> dict[str, Device]:
    #     if self._devices is None:
    #         raw_devices = await self.request(f"{self.url}/api/devices")
    #         devices = [Device.model_validate(device) for device in raw_devices]
    #         # TODO: What ID to use?
    #         self._devices = {device.id: device for device in devices}
    #     return self._devices

    @property
    async def roles(self) -> list[Role]:
        """The configured roles, ordered from least to most privileged."""
        if self._roles is None:
            raw_roles = await self.request(f"{self.url}/api/roles")
            self._roles = [Role.model_validate(role) for role in raw_roles]
        return self._roles

    # Scoped to one home rather than the `/api/homes/...` aggregates: welkom
    # answers 503 when a home's network resolver can't see its network, and the
    # aggregate returns a flat list with no way to say "all of it except that
    # one home". A travel router with a broken API would take the whole house's
    # presence down with it. Per home, each failure stays where it belongs.
    @contextmanager
    def fresh_reads(self, after: float | None = None) -> Iterator[None]:
        """Within this, presence reads skip welkom's 30-second cache; with
        `after`, they're from a read at least that recent (see changes.py)."""
        self._fresh += 1
        if after is not None:
            self._fresh_afters.append(after)
        try:
            yield
        finally:
            self._fresh -= 1
            if after is not None:
                self._fresh_afters.remove(after)
                floor = self._current_floor()
                if floor is None or after >= floor:
                    self._floor = (after, monotonic())

    def _current_floor(self) -> float | None:
        if self._floor is None:
            return None
        after, set_at = self._floor
        return after if monotonic() - set_at < FLOOR_TTL else None

    def _presence_url(self, home_id: str, path: str) -> str:
        url = f"{self.home_url(home_id)}/{path}"
        floor = self._current_floor()
        afters = [*self._fresh_afters, *([floor] if floor is not None else [])]
        if afters:
            # `fresh` too, for a welkom that doesn't know `after`.
            return f"{url}?fresh=true&after={max(afters)}"
        return f"{url}?fresh=true" if self._fresh else url

    async def changes(
        self, home_id: str, read_timeout: float
    ) -> AsyncIterator["Event"]:
        """The events welkom streams for a home; see changes.py."""
        from .changes import events

        async with self.session.get(
            f"{self.home_url(home_id)}/changes",
            timeout=aiohttp.ClientTimeout(total=None, sock_read=read_timeout),
        ) as response:
            async for event in events(response.content):
                yield event

    async def connections(self, home_id: str) -> list[Connection]:
        raw_connections = await self.request(self._presence_url(home_id, "connections"))
        return [Connection.model_validate(connection) for connection in raw_connections]

    async def connected_people(self, home_id: str) -> list[ConnectedPerson]:
        raw_connected_people = await self.request(self._presence_url(home_id, "people"))
        connected_people = [
            ConnectedPerson.model_validate(connected_person)
            for connected_person in raw_connected_people
        ]
        for connected_person in connected_people:
            self._proxy_images(connected_person.person)
            if connected_person.home:
                self._proxy_images(connected_person.home)
        return connected_people

    async def uplink(self, home_id: str) -> Uplink | None:
        """What the home's router is plugged into, or None if welkom can't say.

        A welkom from before the endpoint answers 404, which is "can't say"
        too. A 503 is the router not answering, and is raised: to a roaming
        home that is news, not a gap.
        """
        try:
            raw = await self.request(f"{self.home_url(home_id)}/uplink")
        except aiohttp.ClientResponseError as err:
            if err.status == 404:
                return None
            raise
        return Uplink.model_validate(raw) if raw else None

    def home_url(self, home_id: str) -> str:
        return f"{self.url}/api/homes/{quote(home_id, safe='')}"

    @property
    async def suspended_devices(self) -> set[str]:
        """Names of devices barred from claiming/sustaining the current-device slot."""
        raw = await self.request(f"{self.url}/api/devices/suspended")
        return {str(device) for device in raw}

    async def set_device_suspended(self, device: str, suspended: bool) -> None:
        """Suspend or unsuspend a device's current-device activity."""
        await self.session.put(
            f"{self.url}/api/devices/{quote(device, safe='')}/suspended",
            json={"suspended": suspended},
        )

    async def report_sighting(
        self, home_id: str, person: str, room: str, at: float, source: str
    ) -> dict[str, Any]:
        """Tell welkom somebody was seen in a room of a home, with no device.

        A credential at the front door names a person minutes before their
        phone joins the WiFi, if it ever does. welkom holds them in that room
        until a device of theirs shows up or the hold runs out. Raises on a
        refusal, with welkom's reason in the message."""
        response = await self.session.post(
            f"{self.home_url(home_id)}/sightings",
            json={"person": person, "room": room, "at": at, "source": source},
            raise_for_status=False,
        )
        if response.status >= 400:
            detail = await response.text()
            raise WelkomRefused(response.status, detail)
        return await response.json()

    @property
    def unique_id(self) -> str:
        """The unique ID of the client."""
        return f"client_{self.id}"
