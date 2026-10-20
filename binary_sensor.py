"""Device tracker platform for Welkom."""

from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .client import WelkomClient
from .const import DOMAIN
from .coordinator import WelkomConfigEntry, WelkomCoordinator
from .heard import HeardWhileHome


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
        self._last_presence: str | None = None
        self._heard = HeardWhileHome()

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
        if self._attr_is_on and data:
            self._last_presence = data.presence
        last_heard = self._heard.update(
            self._attr_is_on, data.last_heard if data else None
        )

        # Who this is, to anything that has to turn a handle back into a
        # person: a Schlage code, a WhatsApp id. One entity per person, every
        # person welkom knows, so a template can find somebody by any of
        # their handles without a table of its own -- see `handles` on the
        # person in welkom.yml.
        person = (self.coordinator.people or {}).get(self.coordinator_context)
        hints = person.attrs.homeassistant if person else None
        self._attr_extra_state_attributes = {
            "person_id": self.coordinator_context,
            "person": person.display_name if person else None,
            "handles": dict(hints.handles) if hints else {},
            "duties": list(hints.duties) if hints else [],
            # The person's trip sensor, when they have one, so nothing has to
            # build its entity id out of their name.
            "trip": self._trip_entity_id(),
            # What being home rests on while it is on. `door` is a credential
            # at the front door with no device behind it yet: it expires on a
            # clock, so it going off is not evidence that anybody left.
            "presence": data.presence if data else None,
            # And what it rested on the last time it was on, kept once it is
            # off: by the time anything reacts to somebody going, `presence`
            # is already gone, and a door sighting running out must not read
            # as them leaving. None until they've been home since a restart.
            "last_presence": self._last_presence,
            # When any of their devices was last heard at the home, kept once
            # it is off the same way: the off lands five to nine minutes after
            # that (welkom's online window plus its presence hold), so a
            # departure dated from here is minutes closer to the door. None
            # until they've been home since a restart. See `heard.py`.
            "last_heard": last_heard.isoformat() if last_heard else None,
        }

    def _trip_entity_id(self) -> str | None:
        if self.hass is None:
            return None
        return er.async_get(self.hass).async_get_entity_id(
            "sensor", DOMAIN, f"{self.entity_description.device_id}_trip"
        )

    async def async_added_to_hass(self) -> None:
        """Fill in what needed Home Assistant to be there, e.g. the trip."""
        await super().async_added_to_hass()
        self._async_update_attrs()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""

        self._async_update_attrs()
        self.async_write_ha_state()
