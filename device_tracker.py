"""Device tracker platform for Welkom."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.device_tracker import TrackerEntity
from homeassistant.components.device_tracker.config_entry import (
    TrackerEntityDescription,  # pyright: ignore[reportAttributeAccessIssue]  # lazy HA __getattr__ export
)
from homeassistant.const import (
    ATTR_GPS_ACCURACY,
    ATTR_LATITUDE,
    ATTR_LONGITUDE,
    STATE_NOT_HOME,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    EntityCategory,
)
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.typing import StateType
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .client import WelkomClient
from .const import DOMAIN, GPS_MAX_AGE
from .coordinator import PersonData, WelkomConfigEntry, WelkomCoordinator, WelkomData
from .location import Circle, Fix, placement_holds
from .models import Person


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: WelkomConfigEntry,
    async_add_entities: AddEntitiesCallback,
):
    """Set up entry."""

    coordinator = config_entry.runtime_data
    client = coordinator.client

    def get_person_data(data: WelkomData, id: str) -> PersonData | None:
        return data.people.get(id)

    def get_unknown_person_data(data: WelkomData, idx: int) -> PersonData | None:
        try:
            return data.unknown_people[idx]
        except IndexError:
            return None

    # Fixed slots for unknown people, added once.
    async_add_entities(
        WelkomTracker(
            coordinator,
            entity_description=WelkomTrackerDescription(
                key="tracker",
                client=client,
                context=idx,
                device_id=f"unknown_person_{idx + 1}",
                device_name=f"Unknown Person {idx + 1}",
                icon="mdi:account-question",
                data_fn=get_unknown_person_data,
            ),
        )
        for idx in range(10)
    )

    known_ids: set[str] = set()

    @callback
    def _add_new_people() -> None:
        people = coordinator.people or {}
        new_ids = [person_id for person_id in people if person_id not in known_ids]
        if not new_ids:
            return

        known_ids.update(new_ids)
        async_add_entities(
            WelkomTracker(
                coordinator,
                entity_description=WelkomTrackerDescription(
                    key="tracker",
                    key_in_unique_id=False,
                    client=client,
                    context=person_id,
                    device_id=people[person_id].unique_id,
                    device_name=people[person_id].display_name,
                    icon=people[person_id].icon,
                    entity_picture=people[person_id].avatar_url,
                    data_fn=get_person_data,
                ),
                gps_entity_id=coordinator.gps_tracker(person_id),
            )
            for person_id in new_ids
        )

    _add_new_people()
    config_entry.async_on_unload(coordinator.async_add_listener(_add_new_people))


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
    """Where a welkom person is.

    Welkom places people by the devices it sees on the network: a room of the
    main home, another home, or nothing at all. With a phone tracker configured
    for the person (the integration's options, or ``attrs.homeassistant.gps_tracker``
    on the person in welkom), the phone's GPS fills in the rest — their
    position while they're out — and gets the final word at the edges: a fresh
    fix clearly outside the home overrules a placement welkom is still holding
    from a lingering WiFi association (see ``location.placement_holds``). That
    makes this the one tracker to link to ``person.<name>``, instead of a
    welkom tracker and a phone tracker racing each other in the person
    entity's "latest write wins" merge.
    """

    _attr_device_info: DeviceInfo | None = None
    _attr_entity_category: EntityCategory | None = None

    entity_description: WelkomTrackerDescription

    def __init__(
        self,
        coordinator: WelkomCoordinator,
        entity_description: WelkomTrackerDescription,
        gps_entity_id: str | None = None,
    ):
        """Initialize the device tracker."""

        self.entity_description = entity_description

        super().__init__(coordinator, context=self.entity_description.context)

        self._attr_unique_id = entity_description.unique_id
        self._attr_device_info = entity_description.device_info
        self._attr_entity_picture = entity_description.entity_picture

        self._gps_entity_id = gps_entity_id
        # Which evidence the current state rests on: "welkom" or "gps" (only
        # meaningful when a phone is configured), None when there is none.
        self._source: str | None = None

        self._attr_location_accuracy = 0
        self._async_update_attrs()

    @property
    def force_update(self) -> bool:
        """Only write real changes.

        TrackerEntity forces a write on every update because pushed trackers
        may legitimately repeat a position. Welkom polls every 30 seconds, and
        a forced rewrite of an unchanged placement bumps ``last_updated`` —
        which is what the person entity ranks trackers by — so it let this
        tracker out-shout a phone with a fresher fix, twice a minute.
        """
        return False

    @property
    def available(self) -> bool:
        """Follow the coordinator, unless a phone can carry the entity through.

        Without a phone, an unreachable welkom reads as unavailable, and
        ``person.*`` holds its last state rather than everyone leaving at once.
        With a phone the entity can do better on its own: the phone keeps
        reporting, and welkom's last placement (the coordinator keeps its last
        good data) is still the best room-level guess while the fix stays
        within the home.
        """
        return self._gps_entity_id is not None or super().available

    async def async_added_to_hass(self) -> None:
        """Follow the phone tracker, once there is a state machine to read."""
        await super().async_added_to_hass()
        if self._gps_entity_id is None:
            return
        self.async_on_remove(
            async_track_state_change_event(
                self.hass, [self._gps_entity_id], self._async_gps_changed
            )
        )
        self._async_update_attrs()

    @callback
    def _async_gps_changed(self, event: Event[EventStateChangedData]) -> None:
        self._async_update_attrs()
        self.async_write_ha_state()

    @property
    def data(self) -> PersonData | None:
        """Welkom's data for the person: their placement, or None when unseen."""
        return self.entity_description.data_fn(
            self.coordinator.data, self.coordinator_context
        )

    @property
    def person(self) -> Person | None:
        """The person, whether or not welkom currently sees them."""
        if (data := self.data) and data.person:
            return data.person
        if isinstance(self.coordinator_context, str):
            return (self.coordinator.people or {}).get(self.coordinator_context)
        return None

    def _fix(self) -> Fix | None:
        """The phone's current fix, if the tracker has one to give."""
        if self._gps_entity_id is None or self.hass is None:
            return None
        state = self.hass.states.get(self._gps_entity_id)
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return None
        latitude = state.attributes.get(ATTR_LATITUDE)
        longitude = state.attributes.get(ATTR_LONGITUDE)
        if latitude is None or longitude is None:
            return None
        return Fix(
            latitude=latitude,
            longitude=longitude,
            accuracy=state.attributes.get(ATTR_GPS_ACCURACY) or 0,
            age=dt_util.utcnow() - state.last_updated,
        )

    def _home_circle(self, data: PersonData) -> Circle | None:
        """The footprint of the zone standing for the home welkom placed them in."""
        if data.home_zone_id is None or self.hass is None:
            return None
        zone = self.hass.states.get(data.home_zone_id)
        if zone is None:
            return None
        return Circle(
            latitude=zone.attributes[ATTR_LATITUDE],
            longitude=zone.attributes[ATTR_LONGITUDE],
            radius=zone.attributes["radius"],
        )

    @callback
    def _async_update_attrs(self) -> None:
        """Update the attributes of the entity."""

        if person := self.person:
            self._attr_icon = person.icon
            self._attr_entity_picture = person.avatar_url

        data = self.data
        fix = self._fix()

        if data and placement_holds(self._home_circle(data), fix, GPS_MAX_AGE):
            self._source = "welkom"
            self._attr_state = data.state
            self._attr_latitude = data.latitude
            self._attr_longitude = data.longitude
            self._attr_location_accuracy = 0
            self._attr_in_zones = data.in_zones
        elif fix:
            # Position from the phone; zones and state follow from it, the way
            # they do for any GPS tracker.
            self._source = "gps"
            self._attr_state = None
            self._attr_latitude = fix.latitude
            self._attr_longitude = fix.longitude
            self._attr_location_accuracy = fix.accuracy
            self._attr_in_zones = None
        else:
            self._source = None
            self._attr_state = STATE_NOT_HOME
            self._attr_latitude = None
            self._attr_longitude = None
            self._attr_location_accuracy = 0
            self._attr_in_zones = []

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""

        self._async_update_attrs()
        self.async_write_ha_state()

    @property
    def state(self) -> StateType:
        """Welkom's placement by name; otherwise whatever the position implies."""
        if self._attr_state is not None:
            return self._attr_state
        return super().state

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the device state attributes."""
        attr: dict[str, Any] = {}

        if person := self.person:
            attr["known"] = person.known
            attr["person_name"] = person.display_name

        if self._gps_entity_id is not None:
            attr["gps_tracker"] = self._gps_entity_id
            attr["source"] = self._source

        # Welkom's network view only describes the state while it is the state.
        if self._source == "welkom" and (data := self.data):
            if device := data.device:
                attr["device_name"] = device.display_name

            attr["home_id"] = data.home.id
            attr["home"] = data.home.display_name

            if room := data.room:
                attr["room_id"] = room.id
                attr["room"] = room.display_name

        return attr
