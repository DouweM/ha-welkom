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


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: WelkomConfigEntry,
    async_add_entities: AddEntitiesCallback,
):
    """Set up entry."""

    coordinator = config_entry.runtime_data
    client = coordinator.client

    known_ids: set[str] = set()

    @callback
    def _add_new_people() -> None:
        people = coordinator.people or {}
        new_ids = [person_id for person_id in people if person_id not in known_ids]
        if not new_ids:
            return

        known_ids.update(new_ids)
        async_add_entities(
            WelkomPresenceSensor(
                coordinator,
                entity_description=WelkomPresenceSensorDescription(
                    key="presence",
                    key_in_unique_id=False,
                    client=client,
                    context=person_id,
                    device_id=people[person_id].unique_id,
                    device_name=people[person_id].display_name,
                    icon=people[person_id].icon,
                    entity_picture=people[person_id].avatar_url,
                ),
            )
            for person_id in new_ids
        )

    _add_new_people()
    config_entry.async_on_unload(coordinator.async_add_listener(_add_new_people))


@dataclass(frozen=True, kw_only=True)
class WelkomPresenceSensorDescription(BinarySensorEntityDescription):
    """A class that describes tracker entities."""

    client: WelkomClient
    context: str

    has_entity_name: bool = True
    name: str | None = None

    key_in_unique_id: bool = True

    device_name: str
    device_id: str
    entity_picture: str | None = None
    device_class: BinarySensorDeviceClass = BinarySensorDeviceClass.PRESENCE

    @property
    def unique_id(self) -> str:
        """The unique ID of the person."""
        if self.key_in_unique_id:
            return f"{self.device_id}_{self.key}"

        return self.device_id

    @property
    def device_info(self) -> DeviceInfo:
        """The device info of the person."""
        return DeviceInfo(
            name=self.device_name,
            identifiers={(DOMAIN, self.device_id)},
            via_device=(DOMAIN, self.client.unique_id),
        )


class WelkomPresenceSensor(CoordinatorEntity[WelkomCoordinator], BinarySensorEntity):
    """Representation of a Welkom person tracker."""

    _attr_device_info: DeviceInfo | None = None

    entity_description: WelkomPresenceSensorDescription

    def __init__(
        self,
        coordinator: WelkomCoordinator,
        entity_description: WelkomPresenceSensorDescription,
    ):
        """Initialize the binary sensor."""

        self.entity_description = entity_description

        super().__init__(coordinator, context=entity_description.context)

        self._attr_unique_id = entity_description.unique_id
        self._attr_device_info = entity_description.device_info
        self._attr_entity_picture = entity_description.entity_picture

        self._async_update_attrs()

    @callback
    def _async_update_attrs(self) -> None:
        """Update the attributes of the entity."""

        data = self.coordinator.data.people.get(self.coordinator_context)

        main_home = self.coordinator.home
        self._attr_is_on = bool(data and data.home == main_home)

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""

        self._async_update_attrs()
        self.async_write_ha_state()
