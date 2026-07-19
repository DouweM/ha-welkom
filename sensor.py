"""Device tracker platform for Welkom."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

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
    DeviceData,
    HomeData,
    RoomData,
    WelkomConfigEntry,
    WelkomCoordinator,
    WelkomData,
)
from .models import Activity

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
                    value_fn=lambda data, key: (
                        data.homes.get(key, HomeData()).people_count
                    ),
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
                    value_fn=lambda data, key: (
                        data.homes.get(key, HomeData()).known_people_count
                    ),
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
                    value_fn=lambda data, key: (
                        data.homes.get(key, HomeData()).unknown_people_count
                    ),
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
                    value_fn=lambda data, key: (
                        data.rooms.get(key, RoomData()).people_count
                    ),
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
                    value_fn=lambda data, key: (
                        data.rooms.get(key, RoomData()).known_people_count
                    ),
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
                    value_fn=lambda data, key: (
                        data.rooms.get(key, RoomData()).unknown_people_count
                    ),
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

    # Per-person "current device" sensors, added as people appear.
    known_person_ids: set[str] = set()

    @callback
    def _add_new_people() -> None:
        people = coordinator.people or {}
        new_ids = [
            person_id for person_id in people if person_id not in known_person_ids
        ]
        if not new_ids:
            return

        known_person_ids.update(new_ids)
        async_add_entities(
            WelkomCurrentDeviceSensor(
                coordinator,
                entity_description=WelkomPersonSensorDescription(
                    key="current_device",
                    name="Current device",
                    client=client,
                    context=person_id,
                    device_id=people[person_id].unique_id,
                    device_name=people[person_id].display_name,
                ),
            )
            for person_id in new_ids
        )

    _add_new_people()
    config_entry.async_on_unload(coordinator.async_add_listener(_add_new_people))

    # Per-device connection sensors, added as devices appear.
    known_device_keys: set[str] = set()

    @callback
    def _add_new_devices() -> None:
        devices = coordinator.data.devices if coordinator.data else {}
        new_keys = [key for key in devices if key not in known_device_keys]
        if not new_keys:
            return

        known_device_keys.update(new_keys)
        async_add_entities(
            WelkomConnectionSensor(
                coordinator,
                entity_description=WelkomDeviceSensorDescription(
                    key="connection",
                    name="Connection",
                    client=client,
                    context=key,
                    device_id=f"device_{key}",
                    device_name=devices[key].device.display_name,
                    icon=devices[key].device.icon,
                ),
            )
            for key in new_keys
        )

    _add_new_devices()
    config_entry.async_on_unload(coordinator.async_add_listener(_add_new_devices))


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


@dataclass(frozen=True, kw_only=True)
class WelkomPersonSensorDescription(SensorEntityDescription):
    """A class that describes person sensor entities."""

    client: WelkomClient
    context: str

    has_entity_name: bool = True
    name: str | None = None

    device_name: str
    device_id: str

    @property
    def unique_id(self) -> str:
        """The unique id of the entity."""
        return f"{self.device_id}_{self.key}"

    @property
    def device_info(self) -> DeviceInfo:
        """The device info of the person."""
        return DeviceInfo(
            name=self.device_name,
            identifiers={(DOMAIN, self.device_id)},
            via_device=(DOMAIN, self.client.unique_id),
        )


class WelkomCurrentDeviceSensor(CoordinatorEntity[WelkomCoordinator], SensorEntity):
    """The device a person is currently using to access a tracked service."""

    _attr_device_info: DeviceInfo | None = None

    entity_description: WelkomPersonSensorDescription

    def __init__(
        self,
        coordinator: WelkomCoordinator,
        entity_description: WelkomPersonSensorDescription,
    ):
        """Initialize the sensor."""

        self.entity_description = entity_description

        super().__init__(coordinator, context=entity_description.context)

        self._attr_unique_id = entity_description.unique_id
        self._attr_device_info = entity_description.device_info

        self._async_update_attrs()

    @property
    def activity(self) -> Activity | None:
        """The person's current activity."""
        data = self.coordinator.data.people.get(self.coordinator_context)
        return data.activity if data else None

    @callback
    def _async_update_attrs(self) -> None:
        """Update the attributes of the entity."""

        activity = self.activity
        if not activity:
            self._attr_native_value = None
            self._attr_icon = "mdi:cellphone-off"
            self._attr_extra_state_attributes = {}
            return

        self._attr_native_value = activity.device

        device_type = activity.device_type
        self._attr_icon = "mdi:" + (
            device_type.mdi_icon if device_type else "cellphone"
        )

        attrs: dict[str, Any] = {
            "device_type": device_type.value if device_type else None,
            "network_id": activity.network_id,
            "role_id": activity.role_id,
            "host": activity.host,
            "last_seen_at": activity.last_seen_at,
            "connection_summary": activity.summary,
        }

        if room_id := activity.room_id:
            attrs["room_id"] = room_id
            if room := (self.coordinator.rooms or {}).get(room_id):
                attrs["room"] = room.display_name

        # The resolver's freshness fields duplicate last_seen_at here (metadata
        # is snapshotted at activity time); they live on the connection sensor.
        attrs.update(
            activity.metadata.model_dump(
                exclude_unset=True, exclude={"last_seen", "last_roamed_at"}
            )
        )

        self._attr_extra_state_attributes = attrs

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""

        self._async_update_attrs()
        self.async_write_ha_state()


@dataclass(frozen=True, kw_only=True)
class WelkomDeviceSensorDescription(SensorEntityDescription):
    """A class that describes device sensor entities."""

    client: WelkomClient
    context: str

    has_entity_name: bool = True
    name: str | None = None

    device_name: str
    device_id: str

    @property
    def unique_id(self) -> str:
        """The unique id of the entity."""
        return f"{self.device_id}_{self.key}"

    @property
    def device_info(self) -> DeviceInfo:
        """The device info of the device."""
        return DeviceInfo(
            name=self.device_name,
            identifiers={(DOMAIN, self.device_id)},
            via_device=(DOMAIN, self.client.unique_id),
        )


class WelkomConnectionSensor(CoordinatorEntity[WelkomCoordinator], SensorEntity):
    """How a device is currently connected: the network id, or unknown when offline."""

    _attr_device_info: DeviceInfo | None = None

    entity_description: WelkomDeviceSensorDescription

    def __init__(
        self,
        coordinator: WelkomCoordinator,
        entity_description: WelkomDeviceSensorDescription,
    ):
        """Initialize the sensor."""

        self.entity_description = entity_description

        super().__init__(coordinator, context=entity_description.context)

        self._attr_unique_id = entity_description.unique_id
        self._attr_device_info = entity_description.device_info

        self._async_update_attrs()

    @property
    def data(self) -> DeviceData | None:
        """The data of the device."""
        return self.coordinator.data.devices.get(self.coordinator_context)

    @callback
    def _async_update_attrs(self) -> None:
        """Update the attributes of the entity."""

        data = self.data
        conn = data.connection if data else None
        if not data or not conn:
            self._attr_native_value = None
            self._attr_extra_state_attributes = {}
            return

        self._attr_native_value = data.state

        attrs: dict[str, Any] = {
            "network": conn.network.display_name,
            "role_id": conn.role.id,
        }

        if person := conn.person:
            attrs["person_id"] = person.id
            attrs["person"] = person.display_name

        if home := conn.home:
            attrs["home_id"] = home.id
            attrs["home"] = home.display_name

        if room := conn.room:
            attrs["room_id"] = room.id
            attrs["room"] = room.display_name

        attrs.update(conn.metadata.model_dump(exclude_unset=True))

        if len(data.connections) > 1:
            attrs["networks"] = [c.network.id for c in data.connections]

        self._attr_extra_state_attributes = attrs

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""

        self._async_update_attrs()
        self.async_write_ha_state()
