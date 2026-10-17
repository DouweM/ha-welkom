"""The integration's one gazetteer: loaded, saved, and seeded from history.

`gazetteer.py` holds the geocodes; this keeps them across restarts in Home
Assistant's storage and, the first time there is nothing stored, fills them
from the recorder's history of every phone's geocoded-location sensor -- ten
days of the household's driving, on Oasis's `purge_keep_days`.

None of it may hold up the event loop: the UniFi integration once stalled it
for seconds at a time and every automation in the house with it. Reading and
writing the store, building the index and querying the recorder all happen in
an executor, and setup never waits for any of them -- until the load lands, the
gazetteer is merely empty, which is how it started anyway.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta
import logging
from typing import Any

from homeassistant.components.recorder import get_instance, history
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, State, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN,
    GAZETTEER_MAX_ENTRIES,
    GAZETTEER_SAVE_DELAY,
    GAZETTEER_STORAGE_KEY,
    GAZETTEER_STORAGE_VERSION,
)
from .gazetteer import Entry, Gazetteer
from .places import Geocode

_LOGGER = logging.getLogger(__name__)

DATA_GAZETTEER = "gazetteer"

# What the companion app's geocoded-location sensors end in, as entity and as
# unique id: the same test the tracker uses to find a phone's own.
_GEOCODED_SUFFIX = "_geocoded_location"

# Further back than any recorder keeps; `get_significant_states` simply starts
# at the oldest row there is.
_SEED_SINCE = timedelta(days=365)


class GazetteerStore:
    """The gazetteer, and the store it outlives restarts in."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.gazetteer = Gazetteer(GAZETTEER_MAX_ENTRIES)
        # Serialised in an executor: `_snapshot` hands it an immutable copy.
        self._store: Store[dict[str, Any]] = Store(
            hass,
            GAZETTEER_STORAGE_VERSION,
            GAZETTEER_STORAGE_KEY,
            serialize_in_event_loop=False,
        )

    def nearest(
        self, latitude: float, longitude: float, max_distance: float
    ) -> Geocode | None:
        return self.gazetteer.nearest(latitude, longitude, max_distance)

    @callback
    def add(
        self, latitude: float, longitude: float, geocode: Geocode, at: datetime
    ) -> None:
        """Remember what the geocoder said here; saved within GAZETTEER_SAVE_DELAY."""
        entry = Entry(
            latitude=latitude,
            longitude=longitude,
            values=geocode.values,
            at=at.timestamp(),
        )
        if self.gazetteer.add(entry):
            self._async_schedule_save()

    @callback
    def _async_schedule_save(self) -> None:
        # The snapshot is taken here, in the loop, so the writer thread never
        # reads the live index while a geocode is being added to it. A tuple
        # of twenty thousand references costs well under a millisecond, and a
        # later add replaces it before the write happens.
        snapshot = self.gazetteer.entries()
        self._store.async_delay_save(
            lambda: Gazetteer.entries_as_dict(snapshot), GAZETTEER_SAVE_DELAY
        )

    async def async_start(self) -> None:
        """Load what was stored, or seed from history when nothing was."""
        try:
            stored = await self._store.async_load()
        except Exception:  # a corrupt store must not cost the integration
            _LOGGER.exception("Couldn't read the stored gazetteer; starting empty")
            stored = None
        if stored:
            loaded = await self.hass.async_add_executor_job(
                Gazetteer.from_dict, stored, GAZETTEER_MAX_ENTRIES
            )
            if len(loaded):
                self.gazetteer.absorb(loaded)
                _LOGGER.debug("Loaded %d gazetteer entries", len(self.gazetteer))
                return
        await self._async_seed()

    async def _async_seed(self) -> None:
        """Fill an empty gazetteer from the recorder, once; carry on empty if not."""
        entity_ids = self._geocoded_entity_ids()
        if not entity_ids or "recorder" not in self.hass.config.components:
            return
        try:
            instance = get_instance(self.hass)
            if not await instance.async_db_ready:
                return
            seeded = await instance.async_add_executor_job(
                _seed, self.hass, entity_ids, dt_util.utcnow() - _SEED_SINCE
            )
        except Exception:  # seeding is a nicety
            _LOGGER.exception("Couldn't seed the gazetteer from history")
            return
        self.gazetteer.absorb(seeded)
        _LOGGER.info(
            "Seeded the gazetteer with %d geocodes from %d sensors",
            len(self.gazetteer),
            len(entity_ids),
        )
        if len(self.gazetteer):
            self._async_schedule_save()

    def _geocoded_entity_ids(self) -> list[str]:
        """Every phone's geocoded-location sensor: all of them are household phones.

        From the registry, so a phone whose sensor is unavailable just now
        still counts, and from the state machine for one without a unique id.
        """
        found = {
            entry.entity_id
            for entry in er.async_get(self.hass).entities.values()
            if entry.domain == "sensor"
            and (entry.unique_id or "").endswith(_GEOCODED_SUFFIX)
        }
        found.update(
            entity_id
            for entity_id in self.hass.states.async_entity_ids("sensor")
            if entity_id.endswith(_GEOCODED_SUFFIX)
        )
        return sorted(found)


def _seed(hass: HomeAssistant, entity_ids: list[str], since: datetime) -> Gazetteer:
    """Every recorded geocode of these sensors, as a gazetteer. In the recorder's
    executor: this is a database query and a few thousand rows of parsing.

    Every row, not only the significant ones: a geocode sensor's state is the
    address line, and the attributes can change under one that doesn't.
    """
    states = history.get_significant_states(
        hass,
        since,
        entity_ids=entity_ids,
        include_start_time_state=False,
        significant_changes_only=False,
        minimal_response=False,
        no_attributes=False,
    )
    rows: Iterable[State | dict[str, Any]] = (
        row for found in states.values() for row in found
    )
    return Gazetteer.from_entries(_entries(rows), GAZETTEER_MAX_ENTRIES)


def _entries(rows: Iterable[State | dict[str, Any]]) -> Iterable[Entry]:
    """The rows that say something, somewhere: a Location and a value."""
    for row in rows:
        if not isinstance(row, State) or row.state in (
            STATE_UNAVAILABLE,
            STATE_UNKNOWN,
        ):
            continue
        geocode = Geocode.from_attributes(row.attributes)
        if geocode.latitude is None or geocode.longitude is None:
            continue
        if not geocode.values:
            continue
        yield Entry(
            latitude=geocode.latitude,
            longitude=geocode.longitude,
            values=geocode.values,
            at=row.last_updated.timestamp(),
        )


@callback
def async_get_gazetteer(hass: HomeAssistant) -> GazetteerStore | None:
    """The integration's gazetteer, once `async_setup_gazetteer` has made it."""
    return hass.data.get(DOMAIN, {}).get(DATA_GAZETTEER)


@callback
def async_setup_gazetteer(hass: HomeAssistant) -> None:
    """Make the gazetteer once across entries, and load it in the background."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    if DATA_GAZETTEER in domain_data:
        return
    store = domain_data[DATA_GAZETTEER] = GazetteerStore(hass)
    hass.async_create_background_task(store.async_start(), f"{DOMAIN} gazetteer")
