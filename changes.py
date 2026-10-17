"""Refreshing the moment welkom says a home's presence changed.

Polling alone left up to a minute between welkom being able to know about an
arrival or a roam and Home Assistant showing it: welkom's workers each cache
the network for 30 seconds, and the coordinator polls every 30 on top. Welkom
now streams a `changed` event per home (`GET /api/homes/{id}/changes`), and on
each one the coordinator refreshes with `fresh=true`, which makes welkom read
the network rather than its cache.

The 30-second poll stays as it was: this only makes changes arrive sooner, and
when the stream is down nothing is lost but the speed.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterable, AsyncIterator
import json
import logging
import time
from typing import TYPE_CHECKING, NamedTuple

import aiohttp

if TYPE_CHECKING:
    from .coordinator import WelkomCoordinator

_LOGGER = logging.getLogger(__name__)

RETRY_INITIAL = 5.0
RETRY_MAX = 300.0
# A welkom from before the stream answers 404; there's no point asking often.
RETRY_UNSUPPORTED = 3600.0
# How long a stream must stay up before a drop counts as a blip rather than a
# failure, and reconnects without waiting.
STABLE_AFTER = 60.0
# Welkom sends a keepalive after 15 seconds of quiet, so a read that waits much
# longer than that is a connection that died without saying so.
READ_TIMEOUT = 60.0


class Event(NamedTuple):
    name: str
    at: float | None = None
    """When welkom's read that noticed began (Unix time), for `changed`."""


async def events(lines: AsyncIterable[bytes]) -> AsyncIterator[Event]:
    """The server-sent events in a stream of lines.

    Comments (`: keepalive`) are just proof of life. Of `data:`, only `at` is
    used: refetching with `after=at` makes whichever welkom worker answers
    read at least as recently as the one that noticed the change.
    """
    name: str | None = None
    at: float | None = None
    async for raw in lines:
        line = raw.decode().rstrip("\r\n")
        if not line:
            if name:
                yield Event(name, at)
            name = at = None
        elif line.startswith("event:"):
            name = line.removeprefix("event:").strip()
        elif line.startswith("data:"):
            try:
                value = json.loads(line.removeprefix("data:")).get("at")
            except (ValueError, AttributeError):
                value = None
            at = float(value) if isinstance(value, int | float) else None


async def follow_changes(coordinator: WelkomCoordinator) -> None:
    """Follow every home's stream until the entry unloads."""
    home_ids = list(coordinator.homes or {})
    await asyncio.gather(*(_follow_home(coordinator, home_id) for home_id in home_ids))


async def _follow_home(coordinator: WelkomCoordinator, home_id: str) -> None:
    retry = RETRY_INITIAL
    warned = False
    while True:
        opened = time.monotonic()
        try:
            async for event in coordinator.client.changes(home_id, READ_TIMEOUT):
                # `ready` too: whatever changed while the stream was down
                # (or before it first opened) was never announced.
                if event.name in ("ready", "changed"):
                    await coordinator.async_refresh_fresh(after=event.at)
        except aiohttp.ClientResponseError as err:
            if err.status == 404:
                _LOGGER.info(
                    "Welkom has no change stream for %s; polling only", home_id
                )
                await asyncio.sleep(RETRY_UNSUPPORTED)
                continue
            # A 401/403 (the role lacks `home_people`) won't fix itself: say
            # so once where it'll be seen, and keep trying quietly.
            if not warned:
                _LOGGER.warning(
                    "Welkom refused the change stream for %s (%s); polling only "
                    "until it accepts",
                    home_id,
                    err.status,
                )
                warned = True
        except (aiohttp.ClientError, TimeoutError) as err:
            _LOGGER.debug("Change stream for %s dropped: %s", home_id, err)
        except Exception:
            # Anything else (a garbled line, say) must not end the follower:
            # it would leave the others running and this home polling only.
            _LOGGER.exception("Change stream for %s failed", home_id)

        # Only a stream that stayed up a while earns a quick reconnect. One
        # that dies straight after `ready` would otherwise reconnect every few
        # seconds, each time with a round of fresh reads.
        if time.monotonic() - opened >= STABLE_AFTER:
            retry = RETRY_INITIAL
        await asyncio.sleep(retry)
        retry = min(retry * 2, RETRY_MAX)
