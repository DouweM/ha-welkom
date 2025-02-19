"""Device tracker platform for Welkom."""

from dataclasses import dataclass
from typing import Any, Callable

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.components.sensor.const import SensorStateClass
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .client import WelkomClient
from .const import DOMAIN
from .coordinator import WelkomConfigEntry, WelkomCoordinator, WelkomData


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: WelkomConfigEntry,
    async_add_entities: AddEntitiesCallback,
):
    """Set up entry."""

    coordinator = config_entry.runtime_data
    client = coordinator.client

    homes = await client.homes
    rooms = await client.rooms

    entity_descriptions = [
        *(
            WelkomAreaSensorDescription(
                key=home_id,
                client=client,
                device_id=home.unique_id,
                sensor_id="people_count",
                device_name=home.display_name,
                icon=home.icon,
                state_class=SensorStateClass.MEASUREMENT,
                suggested_display_precision=0,
                value_fn=lambda data, key: home_data.people_count
                if (home_data := data.homes.get(key))
                else 0,
            )
            for home_id, home in homes.items()
        ),
        *(
            WelkomAreaSensorDescription(
                key=home_id,
                client=client,
                device_id=home.unique_id,
                sensor_id="people",
                device_name=home.display_name,
                name="People",
                icon="mdi:home-account",
                value_fn=lambda data, key: ", ".join(home_data.people)
                if (home_data := data.homes.get(key))
                else "",
            )
            for home_id, home in homes.items()
        ),
        *(
            WelkomAreaSensorDescription(
                key=home_id,
                client=client,
                device_id=home.unique_id,
                sensor_id="known_people_count",
                device_name=home.display_name,
                name="Known people count",
                icon="mdi:account-check",
                state_class=SensorStateClass.MEASUREMENT,
                suggested_display_precision=0,
                value_fn=lambda data, key: home_data.known_people_count
                if (home_data := data.homes.get(key))
                else 0,
            )
            for home_id, home in homes.items()
        ),
        *(
            WelkomAreaSensorDescription(
                key=home_id,
                client=client,
                device_id=home.unique_id,
                sensor_id="known_people",
                device_name=home.display_name,
                name="Known people",
                icon="mdi:account-check",
                value_fn=lambda data, key: ", ".join(home_data.known_people)
                if (home_data := data.homes.get(key))
                else "",
            )
            for home_id, home in homes.items()
        ),
        *(
            WelkomAreaSensorDescription(
                key=home_id,
                client=client,
                device_id=home.unique_id,
                sensor_id="unknown_people_count",
                device_name=home.display_name,
                name="Unknown people count",
                icon="mdi:account-off",
                state_class=SensorStateClass.MEASUREMENT,
                suggested_display_precision=0,
                value_fn=lambda data, key: home_data.unknown_people_count
                if (home_data := data.homes.get(key))
                else 0,
            )
            for home_id, home in homes.items()
        ),
        *(
            WelkomAreaSensorDescription(
                key=home_id,
                client=client,
                device_id=home.unique_id,
                sensor_id="unknown_people",
                device_name=home.display_name,
                name="Unknown people",
                icon="mdi:account-off",
                value_fn=lambda data, key: ", ".join(home_data.unknown_people)
                if (home_data := data.homes.get(key))
                else "",
            )
            for home_id, home in homes.items()
        ),
        *(
            WelkomAreaSensorDescription(
                key=room_id,
                client=client,
                device_id=room.unique_id,
                sensor_id="people_count",
                device_name=room.display_name,
                icon=room.icon,
                state_class=SensorStateClass.MEASUREMENT,
                suggested_display_precision=0,
                value_fn=lambda data, key: room_data.people_count
                if (room_data := data.rooms.get(key))
                else 0,
            )
            for room_id, room in rooms.items()
        ),
        *(
            WelkomAreaSensorDescription(
                key=room_id,
                client=client,
                device_id=room.unique_id,
                sensor_id="people",
                device_name=room.display_name,
                name="People",
                icon="mdi:home-account",
                value_fn=lambda data, key: ", ".join(room_data.people)
                if (room_data := data.rooms.get(key))
                else "",
            )
            for room_id, room in rooms.items()
        ),
        *(
            WelkomAreaSensorDescription(
                key=room_id,
                client=client,
                device_id=room.unique_id,
                sensor_id="known_people_count",
                device_name=room.display_name,
                name="Known people count",
                icon="mdi:account-check",
                state_class=SensorStateClass.MEASUREMENT,
                suggested_display_precision=0,
                value_fn=lambda data, key: room_data.known_people_count
                if (room_data := data.rooms.get(key))
                else 0,
            )
            for room_id, room in rooms.items()
        ),
        *(
            WelkomAreaSensorDescription(
                key=room_id,
                client=client,
                device_id=room.unique_id,
                sensor_id="known_people",
                device_name=room.display_name,
                name="Known people",
                icon="mdi:account-check",
                value_fn=lambda data, key: ", ".join(room_data.known_people)
                if (room_data := data.rooms.get(key))
                else "",
            )
            for room_id, room in rooms.items()
        ),
        *(
            WelkomAreaSensorDescription(
                key=room_id,
                client=client,
                device_id=room.unique_id,
                sensor_id="unknown_people_count",
                device_name=room.display_name,
                name="Unknown people count",
                icon="mdi:account-off",
                state_class=SensorStateClass.MEASUREMENT,
                suggested_display_precision=0,
                value_fn=lambda data, key: room_data.unknown_people_count
                if (room_data := data.rooms.get(key))
                else 0,
            )
            for room_id, room in rooms.items()
        ),
        *(
            WelkomAreaSensorDescription(
                key=room_id,
                client=client,
                device_id=room.unique_id,
                sensor_id="unknown_people",
                device_name=room.display_name,
                name="Unknown people",
                icon="mdi:account-off",
                value_fn=lambda data, key: ", ".join(room_data.unknown_people)
                if (room_data := data.rooms.get(key))
                else "",
            )
            for room_id, room in rooms.items()
        ),
    ]

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

    has_entity_name: bool = True
    name: str | None = None

    device_name: str
    device_id: str
    sensor_id: str | None = None
    entity_picture: str | None = None
    suggested_area: str | None = None

    value_fn: Callable[[WelkomData, str], Any] = lambda _, __: None

    @property
    def unique_id(self) -> str:
        """The unique ID of the entity."""
        id = self.device_id
        if self.sensor_id:
            id += "_" + self.sensor_id

        return id

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

        super().__init__(coordinator, context=self.entity_description.key)

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
