"""Device tracker platform for Welkom."""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Callable

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.components.sensor.const import SensorStateClass
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .client import WelkomClient
from .const import DOMAIN
from .coordinator import (
    HomeData,
    RoomData,
    WelkomConfigEntry,
    WelkomCoordinator,
    WelkomData,
)

NUMBER_PARAMS = {
    "state_class": SensorStateClass.MEASUREMENT,
    "suggested_display_precision": 0,
}


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: WelkomConfigEntry,
    async_add_entities: AddEntitiesCallback,
):
    """Set up entry."""

    coordinator = config_entry.runtime_data
    client = coordinator.client
    homes = coordinator.homes or {}
    rooms = coordinator.rooms or {}

    entity_descriptions: list[WelkomAreaSensorDescription] = []
    for home in homes.values():
        device_params: dict[str, Any] = {
            "client": client,
            "device_id": home.unique_id,
            "device_name": home.display_name,
            "context": home.id,
        }
        entity_descriptions.extend(
            [
                WelkomAreaSensorDescription(
                    icon=home.icon,
                    key="people_count",
                    **NUMBER_PARAMS,
                    value_fn=lambda data, key: data.homes.get(
                        key, HomeData()
                    ).people_count,
                    **device_params,
                ),
                WelkomAreaSensorDescription(
                    key="people",
                    name="People",
                    icon="mdi:home-account",
                    value_fn=lambda data, key: ", ".join(
                        data.homes.get(key, HomeData()).people
                    ),
                    **device_params,
                ),
                WelkomAreaSensorDescription(
                    key="known_people_count",
                    name="Known people count",
                    icon="mdi:account-check",
                    **NUMBER_PARAMS,
                    value_fn=lambda data, key: data.homes.get(
                        key, HomeData()
                    ).known_people_count,
                    **device_params,
                ),
                WelkomAreaSensorDescription(
                    **device_params,
                    key="known_people",
                    name="Known people",
                    icon="mdi:account-check",
                    value_fn=lambda data, key: ", ".join(
                        data.homes.get(key, HomeData()).known_people
                    ),
                ),
                WelkomAreaSensorDescription(
                    key="unknown_people_count",
                    name="Unknown people count",
                    icon="mdi:account-question",
                    **NUMBER_PARAMS,
                    value_fn=lambda data, key: data.homes.get(
                        key, HomeData()
                    ).unknown_people_count,
                    **device_params,
                ),
                WelkomAreaSensorDescription(
                    key="unknown_people",
                    name="Unknown people",
                    icon="mdi:account-question",
                    value_fn=lambda data, key: ", ".join(
                        data.homes.get(key, HomeData()).unknown_people
                    ),
                    **device_params,
                ),
            ]
        )

    for room in rooms.values():
        device_params: dict[str, Any] = {
            "client": client,
            "device_id": room.unique_id,
            "device_name": room.display_name,
            "context": room.id,
        }
        entity_descriptions.extend(
            [
                WelkomAreaSensorDescription(
                    key="people_count",
                    icon=room.icon,
                    **NUMBER_PARAMS,
                    value_fn=lambda data, key: data.rooms.get(
                        key, RoomData()
                    ).people_count,
                    **device_params,
                ),
                WelkomAreaSensorDescription(
                    key="people",
                    name="People",
                    icon="mdi:home-account",
                    value_fn=lambda data, key: ", ".join(
                        data.rooms.get(key, RoomData()).people
                    ),
                    **device_params,
                ),
                WelkomAreaSensorDescription(
                    key="known_people_count",
                    name="Known people count",
                    icon="mdi:account-check",
                    **NUMBER_PARAMS,
                    value_fn=lambda data, key: data.rooms.get(
                        key, RoomData()
                    ).known_people_count,
                    **device_params,
                ),
                WelkomAreaSensorDescription(
                    key="known_people",
                    name="Known people",
                    icon="mdi:account-check",
                    value_fn=lambda data, key: ", ".join(
                        data.rooms.get(key, RoomData()).known_people
                    ),
                    **device_params,
                ),
                WelkomAreaSensorDescription(
                    key="unknown_people_count",
                    name="Unknown people count",
                    icon="mdi:account-question",
                    **NUMBER_PARAMS,
                    value_fn=lambda data, key: data.rooms.get(
                        key, RoomData()
                    ).unknown_people_count,
                    **device_params,
                ),
                WelkomAreaSensorDescription(
                    key="unknown_people",
                    name="Unknown people",
                    icon="mdi:account-question",
                    value_fn=lambda data, key: ", ".join(
                        data.rooms.get(key, RoomData()).unknown_people
                    ),
                    **device_params,
                ),
            ]
        )

    async_add_entities(
        [
            WelkomAreaSensor(
                coordinator,
                entity_description=entity_description,
            )
            for entity_description in entity_descriptions
        ]
    )


@dataclass(frozen=True, kw_only=True)
class WelkomAreaSensorDescription(SensorEntityDescription):
    """A class that describes tracker entities."""

    client: WelkomClient
    context: str

    has_entity_name: bool = True
    name: str | None = None

    device_name: str
    device_id: str
    entity_picture: str | None = None
    suggested_area: str | None = None

    value_fn: Callable[[WelkomData, str], StateType | date | datetime | Decimal] = (
        lambda _, __: None
    )

    @property
    def unique_id(self) -> str:
        """The unique id of the entity."""
        return f"{self.device_id}_{self.key}"

    @property
    def device_info(self) -> DeviceInfo:
        """The device info of the home."""
        return DeviceInfo(
            name=self.device_name,
            identifiers={(DOMAIN, self.device_id)},
            via_device=(DOMAIN, self.client.unique_id),
            suggested_area=self.device_name,
        )


class WelkomAreaSensor(CoordinatorEntity[WelkomCoordinator], SensorEntity):
    """Representation of a Welkom home tracker."""

    _attr_device_info: DeviceInfo | None = None

    entity_description: WelkomAreaSensorDescription

    def __init__(
        self,
        coordinator: WelkomCoordinator,
        entity_description: WelkomAreaSensorDescription,
    ):
        """Initialize the  sensor."""

        self.entity_description = entity_description

        super().__init__(coordinator, context=entity_description.context)

        self._attr_unique_id = entity_description.unique_id
        self._attr_device_info = entity_description.device_info
        self._attr_entity_picture = entity_description.entity_picture

        self._async_update_attrs()

    @callback
    def _async_update_attrs(self) -> None:
        """Update the attributes of the entity."""

        self._attr_native_value = self.entity_description.value_fn(
            self.coordinator.data, self.coordinator_context
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""

        self._async_update_attrs()
        self.async_write_ha_state()
