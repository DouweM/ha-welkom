from enum import Enum
from typing import Any
import urllib.parse

from pydantic import BaseModel, ConfigDict, computed_field


class Network(BaseModel):
    id: str
    display_name: str

    class Attrs(BaseModel):
        mdi_icon: str | None = None

    attrs: Attrs = Attrs()

    @computed_field
    @property
    def icon(self) -> str | None:
        return "mdi:" + self.attrs.mdi_icon if self.attrs.mdi_icon else None


class DeviceType(str, Enum):
    phone = "phone"
    wearable = "wearable"
    handheld = "handheld"

    laptop = "laptop"
    tablet = "tablet"
    desktop = "desktop"

    other = "other"

    # @computed_field
    # @property
    # def icon(self) -> str | None:
    #     match self:
    #         case DeviceType.phone | DeviceType.handheld:
    #             return "iphone"
    #         case DeviceType.wearable:
    #             return "applewatch"

    #         case DeviceType.tablet:
    #             return "ipad"
    #         case DeviceType.desktop:
    #             return "desktopcomputer"
    #         case DeviceType.laptop:
    #             return "laptopcomputer"

    #         case _:
    #             return None


class Device(BaseModel):
    known: bool
    ids: list[str]
    display_name: str

    attrs: dict[str, Any] = {}

    type: DeviceType | None
    tracker: bool
    personal: bool

    # @computed_field
    # @property
    # def icon(self) -> str | None:
    #     return self.type.icon if self.type else None


class Person(BaseModel):
    known: bool
    id: str
    display_name: str

    avatar_url: str | None

    class Attrs(BaseModel):
        phone: str | None = None
        email: str | None = None
        door_code: str | int | None = None

        mdi_icon: str | None = None

    attrs: Attrs = Attrs()

    @computed_field
    @property
    def icon(self) -> str:
        return "mdi:" + (self.attrs.mdi_icon or "account")

    @computed_field
    @property
    def unique_id(self) -> str:
        """The unique ID of the person."""
        return f"person_{self.id}"


class Role(BaseModel):
    id: str
    display_name: str

    class Attrs(BaseModel):
        mdi_icon: str | None = None

    attrs: Attrs = Attrs()

    @computed_field
    @property
    def icon(self) -> str | None:
        return "mdi:" + self.attrs.mdi_icon if self.attrs.mdi_icon else None


class Area(BaseModel):
    id: str
    display_name: str

    @computed_field
    @property
    def unique_id(self) -> str:
        """The unique ID of the room."""

        raise NotImplementedError

    @computed_field
    @property
    def icon(self) -> str | None:
        raise NotImplementedError

    def __hash__(self):
        return hash(self.id)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Room):
            return self.id == other.id
        return False


class Room(Area):
    class Attrs(BaseModel):
        mdi_icon: str | None = None

    attrs: Attrs = Attrs()

    @computed_field
    @property
    def icon(self) -> str | None:
        return "mdi:" + (self.attrs.mdi_icon or "home-map-marker")

    @computed_field
    @property
    def unique_id(self) -> str:
        """The unique ID of the room."""

        return f"room_{self.id}"


class Home(Area):
    connected: bool | None = None

    rooms: list[Room] = []

    class Attrs(BaseModel):
        class Wifi(BaseModel):
            ssid: str | None = None
            password: str | None = None

        class Address(BaseModel):
            street: str | None = None
            neighborhood: str | None = None
            postal_code: str | int | None = None
            city: str | None = None
            state: str | None = None
            country: str | None = None

            @computed_field
            @property
            def google_maps_url(self) -> str | None:
                parts = [
                    part
                    for part in [
                        self.street,
                        self.neighborhood,
                        str(self.postal_code),
                        self.city,
                        self.state,
                        self.country,
                    ]
                    if part
                ]
                query = ", ".join(parts)
                return f"https://www.google.com/maps/search/?api=1&query={urllib.parse.quote_plus(query)}"

        class Link(BaseModel):
            label: str
            url: str

            class Attrs(BaseModel):
                mdi_icon: str | None = None
                roles: list[str] | None = None

            attrs: Attrs = Attrs()

            @computed_field
            @property
            def icon(self) -> str | None:
                return "mdi:" + self.attrs.mdi_icon if self.attrs.mdi_icon else None

            @computed_field
            @property
            def roles(self) -> list[str] | None:
                return self.attrs.roles

        links: list[Link] = []

        address: Address | None = None
        wifi: Wifi | None = None

        class DoorCode(BaseModel):
            prefix: str | None = None
            code: str | int | None = None

        door_code: DoorCode | None = None

        avatar_url: str | None = None

        mdi_icon: str | None = None

    attrs: Attrs = Attrs()

    @computed_field
    @property
    def icon(self) -> str | None:
        return "mdi:" + (self.attrs.mdi_icon or "home")

    def door_code(self, person: Person | None = None) -> str | None:
        door_code = self.attrs.door_code
        if not door_code:
            return None

        code = door_code.code or (person and person.attrs.door_code)

        if not code:
            return None

        code = str(code)
        if prefix := door_code.prefix:
            code = prefix + code

        return code

    @computed_field
    @property
    def avatar_url(self) -> str | None:
        return self.attrs.avatar_url

    def __hash__(self):
        return hash(self.id)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Home):
            return self.id == other.id
        return False

    @computed_field
    @property
    def unique_id(self) -> str:
        """The unique ID of the home."""
        return f"home_{self.id}"


class Metadata(BaseModel):
    model_config = ConfigDict(extra="allow")

    ip: str | None = None
    mac: str | None = None
    mac_is_private: bool = False
    wifi_ssid: str | None = None

    # country: CountryAlpha2 | None = None


class Connection(BaseModel):
    summary: str

    known: bool

    active_ids: list[str]
    known_active_ids: list[str]

    network: Network

    device: Device
    person: Person | None = None

    role: Role

    home: Home | None = None
    room: Room | None = None

    metadata: Metadata

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Connection):
            return (
                self.network.id == other.network.id
                and self.active_ids == other.active_ids
            )
        return False


class ConnectedPerson(BaseModel):
    known: bool

    person: Person

    home: Home | None = None
    room: Room | None = None

    role: Role

    connection: Connection
