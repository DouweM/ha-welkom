"""When a person's devices were last heard at the home.

welkom lets go of a phone well after it has gone: its "online" window is two
minutes on the provider's `last_seen`, and the presence hold keeps a quiet
device present for five more, so `binary_sensor.<person>` turns off five to
nine minutes after the person walked out. The device's own `last_seen` stops
at the last frame the access point heard, and welkom keeps serving it through
the hold. Measured against the doors on 2026-10-19 it lands 0 to 5 minutes
after the walk-out (median about 1.5), where the off lands 6 to 11.

So the binary sensor carries the moment as `last_heard`, kept once it is off
the way `last_presence` is, and whatever stamps a departure can date it from
there. It is not a signal that anybody left: phones drop off and come back all
day, and only welkom's own off says the absence held.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime

from .models import Connection


def last_heard(connections: Iterable[Connection], home_id: str) -> dict[str, datetime]:
    """The newest `last_seen` of each known person's connections at the home.

    Every connection counts, not just the person's primary one: a watch can
    outlast the phone, and the primary connection is the online one first,
    which by the end of the hold none of them are.
    """

    heard: dict[str, datetime] = {}
    for conn in connections:
        if conn.person is None or conn.home is None or conn.home.id != home_id:
            continue
        seen = conn.metadata.last_seen
        if seen is None:
            continue
        if seen.tzinfo is None:
            seen = seen.replace(tzinfo=UTC)
        pid = conn.person.id
        if pid not in heard or seen > heard[pid]:
            heard[pid] = seen
    return heard


class HeardWhileHome:
    """The latest a person was heard during their current (or last) stay.

    The newest reading is kept for as long as they are home, never moving
    backwards: a device that drops out of the list before the person does
    must not take the time with it. A new stay starts again from nothing, so
    an off always carries a time from the stay it ends. None until they have
    been home since a restart.
    """

    def __init__(self) -> None:
        self.value: datetime | None = None
        self._home = False

    def update(self, home: bool, heard: datetime | None) -> datetime | None:
        if home:
            if not self._home:
                self.value = None
            if heard is not None and (self.value is None or heard > self.value):
                self.value = heard
        self._home = home
        return self.value
