"""Device tracker platform for Welkom."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
import math
from typing import Any

import voluptuous as vol

from homeassistant.components.device_tracker import (
    TrackerEntity,
    TrackerEntityDescription,
)
from homeassistant.const import (
    ATTR_GPS_ACCURACY,
    ATTR_LATITUDE,
    ATTR_LONGITUDE,
    STATE_HOME,
    STATE_NOT_HOME,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    EntityCategory,
)
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import (
    config_validation as cv,
    entity_platform,
    entity_registry as er,
)
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
)
from homeassistant.helpers.restore_state import (
    ExtraStoredData,
    RestoredExtraData,
    RestoreEntity,
)
from homeassistant.helpers.typing import StateType
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .client import WelkomClient
from .const import (
    AREA_LINGER,
    DOMAIN,
    GAZETTEER_RADIUS_MAX,
    GAZETTEER_RADIUS_MIN,
    GEOCODE_SLACK,
    GEOCODE_WAIT,
    GPS_MAX_AGE,
    WELKOM_HOLD,
)
from .coordinator import PersonData, WelkomConfigEntry, WelkomCoordinator, WelkomData
from .gazetteer import search_radius
from .gazetteer_store import async_get_gazetteer
from .location import (
    Circle,
    Fix,
    fix_at_home,
    fix_in_circle,
    fix_places_within_home,
    placement_holds,
    placement_lingers,
)
from .models import Home, Person
from .places import (
    Geocode,
    GeocodedArea,
    lingering_area,
    recognise,
    tighter,
    tracker_place,
)
from .roaming import Position, roaming_as_dict, roaming_from_dict
from .trip import Place


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

    async_add_entities(
        WelkomRoamingTracker(coordinator, home)
        for home in (coordinator.homes or {}).values()
        if coordinator.is_roaming(home.id)
    )
    entity_platform.async_get_current_platform().async_register_entity_service(
        "locate",
        {
            vol.Required("latitude"): cv.latitude,
            vol.Required("longitude"): cv.longitude,
            vol.Optional("accuracy", default=30): vol.Coerce(float),
        },
        "async_locate",
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
    """Where a welkom person is.

    Welkom places people by the devices it sees on the network: a room of the
    main home, another home, or nothing at all. With a phone tracker configured
    for the person (the integration's options, or ``attrs.homeassistant.gps_tracker``
    on the person in welkom), the phone's GPS fills in the rest — their
    position while they're out — and gets the final word at the edges: a fresh
    fix clearly outside the home overrules a placement welkom is still holding
    from a lingering WiFi association, and once it has, welkom only gets them
    back on a fix clearly *inside* the home (see ``location.placement_holds``). That
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
        # The phone's reverse-geocode sensor, found beside the tracker on its
        # device once there is a registry to ask — see `async_added_to_hass`.
        self._geocode_entity_id: str | None = None
        # Which evidence the current state rests on: "welkom" or "gps" (only
        # meaningful when a phone is configured), None when there is none.
        self._source: str | None = None
        # The last placement welkom confirmed, and when: kept for a while
        # after welkom loses the person, see _async_update_attrs.
        self._held: tuple[PersonData, datetime] | None = None
        self._holding = False
        # The last neighbourhood the phone's geocode named, for the fixes it
        # doesn't geocode; and the fix whose geocode is being waited for.
        self._area: GeocodedArea | None = None
        # The fix a wait last ran out on, which is named without waiting again.
        self._geocode_waited: object = None
        self._geocode_wait_cancel: Callable[[], None] | None = None
        # The geocode the current name came from: the fix's own, or the
        # nearest one the gazetteer remembers. Its colonia and alcaldía are
        # attributes of their own.
        self._named_by: Geocode | None = None

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
        # Somebody on a travel router stands wherever it does, and it moves
        # between polls, whenever anybody's phone on it reports.
        self.async_on_remove(
            self.coordinator.add_roaming_listener(self._handle_coordinator_update)
        )
        if self._gps_entity_id is None:
            return
        # The companion app puts the phone's geocoded-location sensor on the
        # same device as its tracker, with a unique id that ends in
        # `_geocoded_location`. Found rather than configured: the household
        # already said which phone, and the sensor follows from that.
        registry = er.async_get(self.hass)
        if (tracker := registry.async_get(self._gps_entity_id)) and tracker.device_id:
            for entry in er.async_entries_for_device(registry, tracker.device_id):
                if entry.domain == "sensor" and (entry.unique_id or "").endswith(
                    "_geocoded_location"
                ):
                    self._geocode_entity_id = entry.entity_id
                    break
        watched = [self._gps_entity_id]
        if self._geocode_entity_id is not None:
            # A geocode landing after its fix can change the place's name, so
            # it is a reason to look again.
            watched.append(self._geocode_entity_id)
        self.async_on_remove(
            async_track_state_change_event(self.hass, watched, self._async_gps_changed)
        )
        self.async_on_remove(self._cancel_geocode_wait)
        self._async_update_attrs()

    async def async_locate(self, **_: Any) -> None:
        """`welkom.locate` is for roaming homes; a person is placed by their phone."""
        raise ServiceValidationError(
            f"{self.entity_id} is a person; welkom.locate places a roaming home"
        )

    @callback
    def _async_gps_changed(self, event: Event[EventStateChangedData]) -> None:
        self._async_update_attrs()
        self.async_write_ha_state()

    def _geocode_is_stale(self, fix: Fix) -> bool:
        """Whether the phone has a geocode, but of somewhere else than `fix`."""
        if self._geocode_entity_id is None or self.hass is None:
            return False
        state = self.hass.states.get(self._geocode_entity_id)
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return False
        geocode = Geocode.from_attributes(state.attributes)
        return not geocode.describes(
            fix.latitude, fix.longitude, max(GEOCODE_SLACK, fix.accuracy)
        )

    @staticmethod
    def _fix_key(fix: Fix) -> object:
        return fix.at or (fix.latitude, fix.longitude)

    def _await_geocode(self, fix: Fix) -> bool:
        """Whether to keep the current name while this fix's geocode is on its way.

        One deadline at a time: anything that looks again inside it -- a
        welkom poll, the next fix -- keeps waiting rather than naming the fix
        without its geocode, and a run of fixes can't push it back, so a name
        is never held past GEOCODE_WAIT. Once it passes, the fix it ended on
        is named as it is."""
        if self.hass is None or not self._geocode_is_stale(fix):
            return False
        if self._geocode_wait_cancel is not None:
            return True
        if self._fix_key(fix) == self._geocode_waited:
            return False
        self._geocode_wait_cancel = async_call_later(
            self.hass, GEOCODE_WAIT, self._async_geocode_waited
        )
        return True

    @callback
    def _async_geocode_waited(self, _now: datetime) -> None:
        self._geocode_wait_cancel = None
        if fix := self._fix():
            self._geocode_waited = self._fix_key(fix)
        self._async_update_attrs()
        self.async_write_ha_state()

    def _geocoded_area(self, fix: Fix, geocode: Geocode) -> GeocodedArea | None:
        """The place this geocode names, if the geocode is why.

        A household place, or failing one the geocode's own colonia or
        alcaldía (see `places.tracker_place`): a colonia named a block ago is
        as much better than the city as a household neighbourhood is, and
        `lingering_area` keeps either no further than AREA_LINGER.
        """
        latitude, longitude = self._geocode_position(fix, geocode)
        places = self.coordinator.places
        drawn = recognise(places, latitude=latitude, longitude=longitude, geocode=None)
        named = tracker_place(
            named=recognise(
                places, latitude=latitude, longitude=longitude, geocode=geocode
            ),
            drawn=drawn,
            geocode=geocode,
        )
        if named is None or (drawn is not None and drawn.name == named.name):
            return None
        return GeocodedArea(place=named, latitude=latitude, longitude=longitude)

    @staticmethod
    def _geocode_position(fix: Fix, geocode: Geocode) -> tuple[float, float]:
        """Where the geocode is about: its own Location, else the fix's."""
        if geocode.latitude is None or geocode.longitude is None:
            return fix.latitude, fix.longitude
        return geocode.latitude, geocode.longitude

    def _name(self, fix: Fix, geocode: Geocode | None) -> Place | None:
        """What the tracker calls the fix, by this geocode: a household place,
        else the tighter of the zones and the geocode's own colonia or
        alcaldía. Trips keep `place_at`'s answer; this is the badge's."""
        named = self.coordinator.place_at(fix, geocode, coarse=math.inf)
        if geocode is None:
            return named
        drawn = self.coordinator.place_at(fix, None, coarse=math.inf)
        return tracker_place(named=named, drawn=drawn, geocode=geocode)

    def _cancel_geocode_wait(self) -> None:
        if self._geocode_wait_cancel is not None:
            self._geocode_wait_cancel()
            self._geocode_wait_cancel = None

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
            at=state.last_updated,
        )

    def _geocode(self, fix: Fix) -> Geocode | None:
        """What the phone's reverse geocoder says about this fix, if it is about it.

        The sensor is a separate state from the tracker and can be a poll
        behind, so its own `Location` has to agree with the fix before its
        words are used: a geocode still describing the last street must not
        name the neighbourhood the phone just left.
        """
        if self._geocode_entity_id is None or self.hass is None:
            return None
        state = self.hass.states.get(self._geocode_entity_id)
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return None
        geocode = Geocode.from_attributes(state.attributes)
        if not geocode.describes(
            fix.latitude, fix.longitude, max(GEOCODE_SLACK, fix.accuracy)
        ):
            return None
        return geocode

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

    def _base(self, data: PersonData) -> Circle | None:
        """Where a trip from here would leave from, if not the main home.

        A travel router's position while it has one, drawn as big as the main
        home's zone: a hotel is a building too, and the same footprint keeps a
        walk to the lobby from counting as going out. None for any fixed home,
        and for a router with no position yet.
        """
        roaming = self.coordinator.roaming(data.home.id)
        if roaming is None or (position := roaming.position) is None:
            return None
        main = self.coordinator.home_circle
        return Circle(
            latitude=position.latitude,
            longitude=position.longitude,
            radius=main.radius if main else 0,
        )

    @callback
    def _async_update_attrs(self) -> None:
        """Update the attributes of the entity."""

        if person := self.person:
            self._attr_icon = person.icon
            self._attr_entity_picture = person.avatar_url

        data = self.data
        fix = self._fix()
        geocode = self._geocode(fix) if fix else None
        now = dt_util.utcnow()
        # Which side spoke last, so the handover needs real evidence in both
        # directions instead of flipping on whichever fix landed most recently.
        gps_in_charge = self._source == "gps"

        self._holding = False
        use_welkom = data is not None and placement_holds(
            self._home_circle(data), fix, GPS_MAX_AGE, gps_in_charge=gps_in_charge
        )
        if (
            use_welkom
            and data is not None
            and fix
            and fix.age <= GPS_MAX_AGE
            and isinstance(self.coordinator_context, str)
            and self.coordinator.is_roaming(data.home.id)
            and not self.coordinator.phone_on(data.home.id, self.coordinator_context)
            and (base := self._base(data)) is not None
            and not fix_in_circle(fix, base)
        ):
            # On a travel router by some other device, while the phone is
            # speaking from off it and away from it: a laptop left in the hotel
            # room says nothing about where its owner went to dinner. Only the
            # phone keeps them there, the same rule that places the router
            # itself. A phone that has merely dozed off the WiFi is still in
            # the room, and its fix says so.
            use_welkom = False
        if data is not None and use_welkom:
            self._held = (data, now)
        elif (
            data is None
            # A room may only be resurrected if the person never left in the
            # meantime. Once the phone has taken charge they demonstrably did,
            # and welkom's last room is a place nobody was ever seen.
            and not gps_in_charge
            and self._held
            and placement_lingers(
                self._home_circle(self._held[0]), fix, now - self._held[1], WELKOM_HOLD
            )
        ):
            # Welkom lost the person but the phone hasn't left the home: keep
            # the room rather than dropping to a bare "home".
            data = self._held[0]
            self._holding = use_welkom = True

        # Tell the coordinator which evidence won, so it can follow the trip:
        # a journey starts when the phone takes over and ends when welkom gets
        # them back, and this is the only place that knows which.
        if self._gps_entity_id is not None and isinstance(
            self.coordinator_context, str
        ):
            if data and use_welkom:
                # welkom has them: they are home and any trip is over.
                self.coordinator.observe(
                    self.coordinator_context,
                    None,
                    now,
                    geocode,
                    base=self._base(data),
                    roaming=self.coordinator.is_roaming(data.home.id),
                )
            elif fix:
                self.coordinator.observe(self.coordinator_context, fix, now, geocode)
            # And when NEITHER speaks, nothing is said at all. Silence from
            # both sources is not evidence of being home, and it used to be
            # read as exactly that: `None if (data and use_welkom) else fix`
            # hands `None` over when the phone entity blinks unavailable, so a
            # two-second gap ended the journey. On 2026-09-20 at 05:56:55
            # Gaby's phone went quiet for two seconds while she was 7 km away
            # and her evening's itinerary was declared over. `stale` is what
            # covers a phone that never comes back.

        if data and use_welkom:
            self._source = "welkom"
            # A neighbourhood from before welkom had them isn't where they are.
            self._area = None
            self._attr_state = data.state
            self._attr_latitude = data.latitude
            self._attr_longitude = data.longitude
            self._attr_location_accuracy = 0
            self._attr_in_zones = data.in_zones
            if (
                fix
                # Anchored on the home itself: no room, or a room without a
                # zone of its own (`_place` puts both at the home's center).
                and data.in_zones[:1] == [data.home_zone_id]
                and fix_places_within_home(self._home_circle(data), fix, GPS_MAX_AGE)
            ):
                # The phone knows where in the house better than the home
                # zone's center does. State and zones still come from welkom.
                self._attr_latitude = fix.latitude
                self._attr_longitude = fix.longitude
                self._attr_location_accuracy = fix.accuracy
            if (roaming := self.coordinator.roaming(data.home.id)) is not None:
                # On a travel router: a fresh fix from this phone is a fix on
                # the router, and the router's position is theirs.
                if (
                    fix
                    and fix.age <= GPS_MAX_AGE
                    and isinstance(self.coordinator_context, str)
                ):
                    self.coordinator.locate(data.home.id, self.coordinator_context, fix)
                    roaming = self.coordinator.roaming(data.home.id) or roaming
                if position := roaming.position:
                    self._attr_latitude = position.latitude
                    self._attr_longitude = position.longitude
                    self._attr_location_accuracy = position.accuracy
        elif fix:
            # Position from the phone; zones and state follow from it, the way
            # they do for any GPS tracker.
            self._source = "gps"
            # Nothing from before the walk describes where they are now.
            self._held = None
            # Named by the tightest place that holds: a real circle, one of
            # the household's polygons, or a neighbourhood the phone's
            # geocoder put them in -- the same answer a trip would give for
            # this fix, so the badge, the stop and the card never disagree.
            # `person.*` copies this state, which is how "Roma" reaches the
            # dashboard without a zone being drawn. `place_at` looks at every
            # active zone but `zone.home`, the city included, so nothing
            # recognised is `not_home` -- never left to Home Assistant's own
            # zone logic, whose "might be in it" rule would call a blurry fix
            # from the street `home`.
            #
            # Home is decided here instead. It isn't a place to name a trip
            # by, so `place_at` skips it, and a fix inside it used to read as
            # the neighbourhood the house is in for the minute or two between
            # the phone walking in the door and welkom seeing it on the WiFi.
            # That is `home`, just without a room. Hysteretic on what this
            # tracker last said (see `location.fix_at_home`): coming in takes
            # a fix entirely inside the zone, staying takes one that may be.
            # The source stays "gps"; the room comes when welkom has them.
            was_home = not gps_in_charge or self._attr_state == STATE_HOME
            gazetteer = async_get_gazetteer(self.hass) if self.hass else None
            if fix_at_home(self.coordinator.home_circle, fix, was_home=was_home):
                self._attr_state = STATE_HOME
                self._area = None
                self._named_by = geocode
            elif geocode is not None:
                place = self._name(fix, geocode)
                self._attr_state = place.name if place else STATE_NOT_HOME
                self._cancel_geocode_wait()
                self._named_by = geocode
                # What this geocode alone calls the spot, zones aside, so the
                # fixes iOS doesn't geocode can keep it (see `lingering_area`).
                # Every geocode replaces it, including one that names nothing:
                # the newest geocode is the one to believe.
                self._area = self._geocoded_area(fix, geocode)
                if gazetteer is not None:
                    # And for anybody's fix here that iOS won't geocode. The
                    # same fix seen again (every welkom poll) is no news.
                    latitude, longitude = self._geocode_position(fix, geocode)
                    gazetteer.add(latitude, longitude, geocode, fix.at or now)
            elif (
                gps_in_charge
                and self._attr_state not in (None, STATE_HOME, STATE_NOT_HOME)
                and self._await_geocode(fix)
            ):
                # Its geocode is usually a second behind: keep the name the
                # last fix had until it lands, rather than calling every new
                # fix "CDMX" for a moment (2026-10-17, a walk to Chapu II).
                pass
            else:
                bare = self.coordinator.place_at(fix, None, coarse=math.inf)
                # What somebody's phone said nearest here, if iOS didn't
                # geocode this fix: driving, it rarely does (2026-10-17 21:07,
                # Gaby 2.8 km from her last geocode, read as "CDMX"). Nearer
                # evidence than where this phone last geocoded, so it goes
                # first; and like that, only when it says more than `bare`.
                # Never into `_area`: it is a borrowed name, not this phone's.
                nearby = (
                    gazetteer.nearest(
                        fix.latitude,
                        fix.longitude,
                        search_radius(
                            fix.accuracy,
                            minimum=GAZETTEER_RADIUS_MIN,
                            maximum=GAZETTEER_RADIUS_MAX,
                        ),
                    )
                    if gazetteer is not None
                    else None
                )
                self._named_by = nearby
                borrowed = self._name(fix, nearby) if nearby is not None else None
                place = (
                    (borrowed if borrowed and tighter(borrowed, than=bare) else None)
                    or lingering_area(
                        self._area,
                        latitude=fix.latitude,
                        longitude=fix.longitude,
                        bare=bare,
                        max_distance=AREA_LINGER,
                    )
                    or bare
                )
                self._attr_state = place.name if place else STATE_NOT_HOME
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
            attr["held"] = self._holding

        if self._source == "gps":
            # What the geocoder calls the spot, whether or not that is the
            # state: the map and the cards show both.
            named_by = self._named_by
            attr["colonia"] = named_by.first("sub_locality") if named_by else None
            attr["alcaldia"] = named_by.first("locality") if named_by else None

        # Welkom's network view only describes the state while it is the state.
        data = self._held[0] if self._holding and self._held else self.data
        if self._source == "welkom" and data:
            if device := data.device:
                attr["device_name"] = device.display_name

            attr["home_id"] = data.home.id
            attr["home"] = data.home.display_name
            attr["presence"] = data.presence
            if self.coordinator.is_roaming(data.home.id):
                attr["roaming"] = True

            if room := data.room:
                attr["room_id"] = room.id
                attr["room"] = room.display_name

        return attr


class WelkomRoamingTracker(
    CoordinatorEntity[WelkomCoordinator], RestoreEntity, TrackerEntity
):
    """Where a roaming home is: a travel router, placed by the phones on it.

    The position is the newest fresh fix from a phone welkom sees on the
    router, held for as long as the router stays plugged into the same thing
    and dropped the moment it isn't — see `roaming.py`, which holds the whole
    decision. Without one the state is `unknown` and the router is off the
    map, which is the truth while it is in a bag or on a plane nobody's phone
    has reported from.

    Its state names where it is the way a person's tracker does: the tightest
    zone or configured place that holds the position, else Home Assistant's
    own `not_home`. Naming a town needs a geocoder, which a phone has and a
    router does not.
    """

    _attr_has_entity_name = True
    _attr_name = None
    _attr_device_info: DeviceInfo | None = None
    _attr_entity_category: EntityCategory | None = None

    def __init__(self, coordinator: WelkomCoordinator, home: Home):
        """Initialize the tracker."""
        super().__init__(coordinator, context=home.id)
        self._home_id = home.id
        self._attr_unique_id = f"{home.unique_id}_location"
        self._attr_icon = home.icon
        self._attr_entity_picture = home.avatar_url
        # The home's own device, beside its people-count sensors.
        self._attr_device_info = DeviceInfo(
            name=home.display_name,
            identifiers={(DOMAIN, home.unique_id)},
            via_device=(DOMAIN, coordinator.client.unique_id),
            suggested_area=home.display_name,
        )

    @property
    def _position(self) -> Position | None:
        roaming = self.coordinator.roaming(self._home_id)
        return roaming.position if roaming else None

    @property
    def force_update(self) -> bool:
        """Only write real changes; see `WelkomTracker.force_update`."""
        return False

    @property
    def available(self) -> bool:
        """Always: what it knows outlives welkom being briefly unreachable."""
        return True

    @property
    def extra_restore_state_data(self) -> ExtraStoredData | None:
        """The whereabouts, whole, so a restart does not forget the hotel."""
        roaming = self.coordinator.roaming(self._home_id)
        return RestoredExtraData(roaming_as_dict(roaming)) if roaming else None

    async def async_added_to_hass(self) -> None:
        """Listen for the router moving, and hand back what outlived a restart."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self.coordinator.add_roaming_listener(self._handle_coordinator_update)
        )
        last = await self.async_get_last_extra_data()
        if last is not None and (remembered := roaming_from_dict(last.as_dict())):
            self.coordinator.restore_roaming(self._home_id, remembered)

    async def async_locate(
        self, latitude: float, longitude: float, accuracy: float = 30
    ) -> None:
        """Place the router by hand: `welkom.locate`.

        Held by the same rule as a phone's fix, so it lasts exactly as long as
        the uplink it was set on. Which is why there has to be one: a router
        that has not answered yet, or is dark, has nowhere to put it.
        """
        placed = self.coordinator.place(
            self._home_id,
            Position(
                latitude=latitude,
                longitude=longitude,
                accuracy=accuracy,
                at=dt_util.utcnow(),
                by="manual",
            ),
        )
        if not placed:
            raise HomeAssistantError(
                f"{self.name or self._home_id} isn't answering, so there is no "
                "uplink to hold a position on"
            )

    @property
    def latitude(self) -> float | None:
        return position.latitude if (position := self._position) else None

    @property
    def longitude(self) -> float | None:
        return position.longitude if (position := self._position) else None

    @property
    def location_accuracy(self) -> float:
        return position.accuracy if (position := self._position) else 0

    @property
    def state(self) -> StateType:
        """The tightest place that holds the router, like a person's tracker."""
        if (position := self._position) is None:
            return None
        place = self.coordinator.place_at(
            Fix(
                latitude=position.latitude,
                longitude=position.longitude,
                accuracy=position.accuracy,
            ),
            coarse=math.inf,
        )
        return place.name if place else super().state

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """What it is plugged into, who placed it, and who is on it."""
        roaming = self.coordinator.roaming(self._home_id)
        attr: dict[str, Any] = {}
        if roaming is None:
            return attr

        interface, _, ssid = (roaming.uplink or "").partition(":")
        attr["uplink"] = interface or None
        attr["ssid"] = ssid or None
        attr["uplink_since"] = roaming.uplink_since
        attr["dark_since"] = roaming.dark_since

        position = roaming.position
        attr["located_at"] = position.at if position else None
        located_by = None
        if position:
            person = (self.coordinator.people or {}).get(position.by)
            located_by = person.display_name if person else position.by
        attr["located_by"] = located_by

        data = self.coordinator.data
        home = data.homes.get(self._home_id) if data else None
        attr["people"] = home.people if home else []
        return attr
