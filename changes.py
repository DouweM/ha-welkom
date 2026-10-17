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
import logging
from typing import TYPE_CHECKING

import aiohttp

if TYPE_CHECKING:
    from .coordinator import WelkomCoordinator

_LOGGER = logging.getLogger(__name__)

RETRY_INITIAL = 5.0
RETRY_MAX = 300.0
# A welkom from before the stream answers 404; there's no point asking often.
RETRY_UNSUPPORTED = 3600.0
# Welkom sends a keepalive after 15 seconds of quiet, so a read that waits much
# longer than that is a connection that died without saying so.
READ_TIMEOUT = 60.0


async def events(lines: AsyncIterable[bytes]) -> AsyncIterator[str]:
    """The names of the server-sent events in a stream of lines.

    Only the `event:` field matters here; `data:` carries nothing the
    coordinator needs, and comments (`: keepalive`) are just proof of life.
    """
    name: str | None = None
    async for raw in lines:
        line = raw.decode().rstrip("\r\n")
        if not line:
            if name:
                yield name
            name = None
        elif line.startswith("event:"):
            name = line.removeprefix("event:").strip()


async def follow_changes(coordinator: WelkomCoordinator) -> None:
    """Follow every home's stream until the entry unloads."""
    home_ids = list(coordinator.homes or {})
    await asyncio.gather(*(_follow_home(coordinator, home_id) for home_id in home_ids))


async def _follow_home(coordinator: WelkomCoordinator, home_id: str) -> None:
    retry = RETRY_INITIAL
    while True:
        try:
            async for event in coordinator.client.changes(home_id, READ_TIMEOUT):
                retry = RETRY_INITIAL
                # `ready` too: whatever changed while the stream was down
                # (or before it first opened) was never announced.
                if event in ("ready", "changed"):
                    await coordinator.async_refresh_fresh()
        except aiohttp.ClientResponseError as err:
            if err.status == 404:
                _LOGGER.info(
                    "Welkom has no change stream for %s; polling only", home_id
                )
                await asyncio.sleep(RETRY_UNSUPPORTED)
                continue
            _LOGGER.debug("Change stream for %s failed: %s", home_id, err)
        except (aiohttp.ClientError, TimeoutError) as err:
            _LOGGER.debug("Change stream for %s dropped: %s", home_id, err)

        await asyncio.sleep(retry)
        retry = min(retry * 2, RETRY_MAX)
