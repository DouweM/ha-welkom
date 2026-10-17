"""Standalone checks for reporting a door sighting to welkom.

Run with ``uv run python tests/check_sighting.py``. Not a pytest module, for
the same reason as ``check_changes.py``. A real aiohttp server stands in for
welkom, so what's checked is what goes over the wire and what a refusal turns
into: an automation calling ``welkom.report_sighting`` must see welkom's reason
in its trace rather than a bare status.
"""

from __future__ import annotations

import asyncio
import importlib.util
import pathlib
import sys
import types
from typing import Any

from aiohttp import web

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


_load("const")
_load("models")
client_module = _load("client")


def check(label: str, got: object, want: object) -> None:
    status = "ok  " if got == want else "FAIL"
    print(f"{status} {label}: got {got!r}, want {want!r}")
    if got != want:
        sys.exit(1)


async def main() -> None:
    received: list[tuple[str, dict[str, Any]]] = []

    async def sightings(request: web.Request) -> web.Response:
        body = await request.json()
        received.append((request.match_info["home"], body))
        if body["person"] == "nobody":
            return web.json_response({"detail": "Unknown person"}, status=404)
        return web.json_response(body, status=201)

    app = web.Application()
    app.router.add_post("/api/homes/{home}/sightings", sightings)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]

    client = client_module.WelkomClient(id="x", url=f"http://127.0.0.1:{port}")
    try:
        got = await client.report_sighting(
            "oasis", "araceli", "garage", 1792261932.0, "door"
        )
        check(
            "the sighting goes to the home's endpoint as given",
            received[-1],
            (
                "oasis",
                {
                    "person": "araceli",
                    "room": "garage",
                    "at": 1792261932.0,
                    "source": "door",
                },
            ),
        )
        check("and welkom's answer comes back", got["person"], "araceli")

        try:
            await client.report_sighting("oasis", "nobody", "garage", 1.0, "door")
        except client_module.WelkomRefused as e:
            check("a refusal keeps welkom's status", e.status, 404)
            check("and its reason", "Unknown person" in str(e), True)
        else:
            check("a refusal raises", False, True)
    finally:
        await client.session.close()
        await runner.cleanup()

    print("all sighting checks passed")


asyncio.run(main())
