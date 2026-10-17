"""Standalone checks for following welkom's change stream.

Run with ``uv run python tests/check_changes.py``. Loaded by path, like the
other checks: ``changes.py`` needs nothing from Home Assistant at runtime.

What matters: every `changed` (and the `ready` that opens a stream) becomes
exactly one fresh refresh; keepalive comments and data lines don't; a welkom
from before the stream (404) is left alone instead of being retried every few
seconds; and a dropped stream is reconnected.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
import importlib.util
import logging
import pathlib
import sys
import types

import aiohttp

_ROOT = pathlib.Path(__file__).resolve().parent.parent

_pkg = types.ModuleType("welkom")
_pkg.__path__ = [str(_ROOT)]
sys.modules["welkom"] = _pkg


def _load(name: str):
    spec = importlib.util.spec_from_file_location(
        f"welkom.{name}", _ROOT / f"{name}.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[f"welkom.{name}"] = module
    spec.loader.exec_module(module)
    return module


changes = _load("changes")


def check(label: str, got: object, want: object) -> None:
    status = "ok  " if got == want else "FAIL"
    print(f"{status} {label}: got {got!r}, want {want!r}")
    if got != want:
        sys.exit(1)


STREAM = (
    b'event: ready\ndata: {"home": "oasis"}\n\n'
    b": keepalive\n\n"
    b'event: changed\r\ndata: {"home": "oasis", "at": 1792206516.5}\r\n\r\n'
    b'data: {"orphan": true}\n\n'
    b'event: changed\ndata: {"home": "oasis"}\n\n'
    b"event: changed\ndata: not json\n\n"
)


async def lines(blob: bytes) -> AsyncIterator[bytes]:
    for line in blob.splitlines(keepends=True):
        yield line


async def parsed(blob: bytes) -> list[tuple[str, float | None]]:
    return [(e.name, e.at) async for e in changes.events(lines(blob))]


@contextmanager
def capture_warnings() -> Iterator[list[logging.LogRecord]]:
    records: list[logging.LogRecord] = []

    class Collect(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            if record.levelno == logging.WARNING:
                records.append(record)

    handler = Collect()
    changes._LOGGER.addHandler(handler)
    try:
        yield records
    finally:
        changes._LOGGER.removeHandler(handler)


class FakeClient:
    """Plays one scripted outcome per connection attempt."""

    def __init__(self, attempts: list[object]) -> None:
        self.attempts = attempts
        self.connects = 0

    async def changes(self, home_id: str, read_timeout: float) -> AsyncIterator[str]:
        self.connects += 1
        outcome = self.attempts.pop(0) if self.attempts else asyncio.Event()
        if isinstance(outcome, BaseException):
            raise outcome
        if isinstance(outcome, asyncio.Event):
            await outcome.wait()  # a stream that stays open and quiet
            return
        for event in outcome:  # type: ignore[union-attr]
            yield event if isinstance(event, changes.Event) else changes.Event(event)


class FakeCoordinator:
    def __init__(self, client: FakeClient) -> None:
        self.client = client
        self.homes = {"oasis": object()}
        self.fresh_refreshes = 0
        self.afters: list[float | None] = []

    async def async_refresh_fresh(self, after: float | None = None) -> None:
        self.fresh_refreshes += 1
        self.afters.append(after)


async def run(coordinator: FakeCoordinator, sleeps: list[float]) -> None:
    """Run the follower until it settles into an idle stream."""

    real_sleep = changes.asyncio.sleep

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        await real_sleep(0)

    changes.asyncio.sleep = fake_sleep  # type: ignore[assignment]
    try:
        task = asyncio.ensure_future(changes.follow_changes(coordinator))
        for _ in range(50):
            await real_sleep(0)
        task.cancel()
    finally:
        changes.asyncio.sleep = real_sleep


async def main() -> None:
    check(
        "events: ready and changed with their `at`, not keepalives or bare data",
        await parsed(STREAM),
        [
            ("ready", None),
            ("changed", 1792206516.5),
            ("changed", None),
            ("changed", None),
        ],
    )

    client = FakeClient([["ready", "changed", "changed"]])
    coordinator = FakeCoordinator(client)
    sleeps: list[float] = []
    await run(coordinator, sleeps)
    check("each ready/changed is one fresh refresh", coordinator.fresh_refreshes, 3)

    client = FakeClient([[changes.Event("changed", 1792206516.5)]])
    coordinator = FakeCoordinator(client)
    await run(coordinator, [])
    check("a change's `at` is passed on as `after`", coordinator.afters, [1792206516.5])

    not_found = aiohttp.ClientResponseError(
        request_info=None,  # type: ignore[arg-type]
        history=(),
        status=404,
    )
    client = FakeClient([not_found])
    coordinator = FakeCoordinator(client)
    sleeps = []
    await run(coordinator, sleeps)
    check(
        "an old welkom (404) is retried hourly, not every few seconds",
        sleeps[0],
        changes.RETRY_UNSUPPORTED,
    )

    client = FakeClient([aiohttp.ClientConnectionError("gone"), ["ready"]])
    coordinator = FakeCoordinator(client)
    sleeps = []
    await run(coordinator, sleeps)
    check("a dropped stream reconnects", client.connects >= 2, True)
    check("and refreshes when it does", coordinator.fresh_refreshes, 1)
    check("after a short wait", sleeps[0], changes.RETRY_INITIAL)

    client = FakeClient([["ready"], ["ready"], ["ready"]])
    coordinator = FakeCoordinator(client)
    sleeps = []
    await run(coordinator, sleeps)
    check(
        "a stream that dies straight after ready backs off",
        sleeps[:3],
        [changes.RETRY_INITIAL, changes.RETRY_INITIAL * 2, changes.RETRY_INITIAL * 4],
    )

    client = FakeClient([UnicodeDecodeError("utf-8", b"", 0, 1, "bad"), ["ready"]])
    coordinator = FakeCoordinator(client)
    sleeps = []
    await run(coordinator, sleeps)
    check("an unexpected error doesn't end the follower", client.connects >= 2, True)

    forbidden = aiohttp.ClientResponseError(
        request_info=None,  # type: ignore[arg-type]
        history=(),
        status=403,
    )
    client = FakeClient([forbidden, forbidden, forbidden])
    coordinator = FakeCoordinator(client)
    sleeps = []
    with capture_warnings() as warnings:
        await run(coordinator, sleeps)
    check("a refusal is warned about once, not every retry", len(warnings), 1)


asyncio.run(main())
