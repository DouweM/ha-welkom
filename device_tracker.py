"""Device tracker platform for Welkom."""

from dataclasses import dataclass
from typing import Any, Callable

from homeassistant.components.device_tracker import TrackerEntity
from homeassistant.components.device_tracker.config_entry import (
    TrackerEntityDescription,
)
from homeassistant.const import STATE_NOT_HOME, EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .client import WelkomClient
from .const import DOMAIN
from .coordinator import PersonData, WelkomConfigEntry, WelkomCoordinator, WelkomData


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: WelkomConfigEntry,
    async_add_entities: AddEntitiesCallback,
):
    """Set up entry."""

    coordinator = config_entry.runtime_data
    client = coordinator.client

    people = await client.people

    entity_descriptions: list[WelkomTrackerDescription] = []

    def get_person_data(data: WelkomData, id: str) -> PersonData | None:
        return data.people.get(id)

    def get_unknown_person_data(data: WelkomData, idx: int) -> PersonData | None:
        try:
            return data.unknown_people[idx]
        except IndexError:
            return None

    for person_id, person in people.items():
        entity_descriptions.append(
            WelkomTrackerDescription(
                key="tracker",
                key_in_unique_id=False,
                client=client,
                context=person_id,
                device_id=person.unique_id,
                device_name=person.display_name,
                icon=person.icon,
                entity_picture=person.avatar_url,
                data_fn=get_person_data,
            )
        )

    entity_descriptions.extend(
        WelkomTrackerDescription(
            key="tracker",
            client=client,
            context=idx,
            device_id=f"unknown_person_{idx + 1}",
            device_name=f"Unknown Person {idx + 1}",
            icon="mdi:account-question",
            data_fn=get_unknown_person_data,
        )
        for idx in range(10)
    )

    async_add_entities(
        [
            WelkomTracker(
                coordinator,
                entity_description=entity_description,
            )
            for entity_description in entity_descriptions
        ]
    )


@dataclass(frozen=True, kw_only=True)
class WelkomTrackerDescription(TrackerEntityDescription):
    """A class that describes tracker entities."""

    client: WelkomClient
    context: str | int

    has_entity_name: bool = True
    name: str | None = None

    key_in_unique_id: bool = True

    device_name: str
    device_id: str
    entity_picture: str | None = None

    data_fn: Callable[[WelkomData, Any], PersonData | None] = lambda _, __: None

    @property
    def unique_id(self) -> str:
        """The unique id of the entity."""
        if self.key_in_unique_id:
            return f"{self.device_id}_{self.key}"

        return self.device_id

    @property
    def device_info(self) -> DeviceInfo | None:
        """The device info of the home."""
        return DeviceInfo(
            name=self.device_name,
            identifiers={(DOMAIN, self.device_id)},
            via_device=(DOMAIN, self.client.unique_id),
        )


class WelkomTracker(CoordinatorEntity[WelkomCoordinator], TrackerEntity):
    """Representation of a Welkom person tracker."""

    _attr_device_info: DeviceInfo | None = None
    _attr_entity_category: EntityCategory | None = None

    entity_description: WelkomTrackerDescription

    def __init__(
        self,
        coordinator: WelkomCoordinator,
        entity_description: WelkomTrackerDescription,
    ):
        """Initialize the device tracker."""

        self.entity_description = entity_description

        super().__init__(coordinator, context=self.entity_description.context)

        self._attr_unique_id = entity_description.unique_id
        self._attr_device_info = entity_description.device_info
        self._attr_entity_picture = entity_description.entity_picture

        self._attr_location_accuracy = 0
        self._async_update_attrs()

    @property
    def data(self) -> PersonData | None:
        """The data of the person."""
        return self.entity_description.data_fn(
            self.coordinator.data, self.coordinator_context
        )

    @callback
    def _async_update_attrs(self) -> None:
        """Update the attributes of the entity."""

        if data := self.data:
            if person := data.person:
                self._attr_icon = person.icon
                self._attr_entity_picture = person.avatar_url

            self._attr_state = data.state
            self._attr_latitude = data.latitude
            self._attr_longitude = data.longitude
        else:
            self._attr_state = STATE_NOT_HOME
            self._attr_latitude = None
            self._attr_longitude = None

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""

        self._async_update_attrs()
        self.async_write_ha_state()

    @property
    def state(self) -> str | None:
        """Get the state of the entity."""
        return self._attr_state

    @property
    def state_attributes(self) -> dict[str, StateType]:
        """Return the device state attributes."""
        attr: dict[str, StateType] = {}
        attr.update(super().state_attributes)

        if data := self.data:
            if person := data.person:
                attr["known"] = person.known
                attr["person_name"] = person.display_name

            if device := data.device:
                attr["device_name"] = device.display_name

            if home := data.home:
                attr["home_id"] = home.id
                attr["home"] = home.display_name

            if room := data.room:
                attr["room_id"] = room.id
                attr["room"] = room.display_name

        return attr
