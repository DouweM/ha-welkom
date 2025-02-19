"""Device tracker platform for Welkom."""

from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .client import WelkomClient
from .const import DOMAIN
from .coordinator import WelkomConfigEntry, WelkomCoordinator
from .models import Person


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: WelkomConfigEntry,
    async_add_entities: AddEntitiesCallback,
):
    """Set up entry."""

    coordinator = config_entry.runtime_data
    client = coordinator.client

    people = await client.people

    entity_descriptions = [
        WelkomPersonSensorDescription(
            key=person_id,
            client=client,
            person=person,
        )
        for person_id, person in people.items()
    ]

    async_add_entities(
        [
            WelkomPersonSensor(
                coordinator,
                entity_description=entity_description,
            )
            for entity_description in entity_descriptions
        ]
    )


@dataclass(frozen=True, kw_only=True)
class WelkomPersonSensorDescription(BinarySensorEntityDescription):
    """A class that describes tracker entities."""

    client: WelkomClient
    person: Person

    has_entity_name: bool = True
    name: str | None = None

    device_class: BinarySensorDeviceClass = BinarySensorDeviceClass.PRESENCE

    @property
    def unique_id(self) -> str:
        """The unique ID of the person."""
        return self.person.unique_id

    @property
    def device_info(self) -> DeviceInfo:
        """The device info of the person."""
        return DeviceInfo(
            name=self.person.display_name,
            identifiers={(DOMAIN, self.unique_id)},
            via_device=(DOMAIN, self.client.unique_id),
        )


class WelkomPersonSensor(CoordinatorEntity[WelkomCoordinator], BinarySensorEntity):
    """Representation of a Welkom person tracker."""

    _attr_device_info: DeviceInfo | None = None

    def __init__(
        self,
        coordinator: WelkomCoordinator,
        entity_description: WelkomPersonSensorDescription,
    ):
        """Initialize the binary sensor."""

        self.entity_description = entity_description

        person = entity_description.person
        self.person_id = person.id

        super().__init__(coordinator, context=self.person_id)

        self._attr_unique_id = entity_description.unique_id
        self._attr_device_info = entity_description.device_info

        self._attr_icon = person.icon
        self._attr_entity_picture = person.avatar_url

        self._async_update_attrs()

    @callback
    def _async_update_attrs(self) -> None:
        """Update the attributes of the entity."""

        data = self.coordinator.data.people.get(self.person_id)

        self._attr_is_on = bool(
            data and data.home_id == self.coordinator.client.home_id
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""

        self._async_update_attrs()
        self.async_write_ha_state()
