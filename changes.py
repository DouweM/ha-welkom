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
import time
from typing import TYPE_CHECKING

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
    warned = False
    while True:
        opened = time.monotonic()
        try:
            async for event in coordinator.client.changes(home_id, READ_TIMEOUT):
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
