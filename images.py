"""Same-origin proxy for the pictures welkom serves: avatars and map overlays.

Entity pictures are loaded by the browser, straight from the URL on the
entity, with no auth header and from whatever host the dashboard was opened on
(the LAN name, a public one, the companion app). A welkom URL would need the
browser to reach welkom directly and to be identified there — a cookie the
proxy's forward auth only ever set for the *dashboard's* host. Serving the
pictures from this integration instead keeps them on the same origin as the
dashboard: the integration fetches them from welkom the way it fetches
everything else, and the browser only ever talks to Home Assistant.

``/welkom/people/{id}/avatar``, ``/welkom/homes/{id}/avatar`` and
``/welkom/homes/{id}/images/{name}`` mirror welkom's ``/api/...`` paths;
``/welkom/homes/{id}/images/{name}.json`` answers with the image's proxied URL
and map bounds, for the bundled map-card plugin.
"""

from __future__ import annotations

from collections import OrderedDict
import logging
from typing import ClassVar

import aiohttp
from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .const import DOMAIN, IMAGE_URL_PREFIX
from .coordinator import WelkomCoordinator

_LOGGER = logging.getLogger(__name__)

# Pictures are small and few (one per person and home), and their URLs carry a
# version that changes with the file, so a cached copy never goes stale — it
# only ever stops being asked for.
_CACHE_SIZE = 256
_MANIFEST_SUFFIX = ".json"


class WelkomImageView(HomeAssistantView):
    """Serves welkom's pictures from Home Assistant's own origin."""

    url = f"{IMAGE_URL_PREFIX}/people/{{person_id}}/avatar"
    extra_urls: ClassVar[list[str]] = [
        f"{IMAGE_URL_PREFIX}/homes/{{home_id}}/avatar",
        f"{IMAGE_URL_PREFIX}/homes/{{home_id}}/images/{{name}}",
    ]
    name = f"api:{DOMAIN}:images"
    # Loaded by <img> tags and the map plugin's fetch, neither of which can
    # carry HA's auth header — like HA's own /api/image/serve. Welkom itself
    # still decides who sees what: a role that can't see the attr gets a 403
    # there, which is passed on here.
    requires_auth = False

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the view."""
        self.hass = hass
        self._cache: OrderedDict[str, tuple[bytes, str]] = OrderedDict()

    async def get(
        self,
        request: web.Request,
        person_id: str | None = None,
        home_id: str | None = None,
        name: str | None = None,
    ) -> web.Response:
        """Serve one picture, or the manifest of one home image."""
        if name is not None and name.endswith(_MANIFEST_SUFFIX):
            assert home_id is not None
            return self._manifest(home_id, name.removesuffix(_MANIFEST_SUFFIX))

        coordinator = self._coordinator_for(person_id=person_id, home_id=home_id)
        if coordinator is None:
            return web.Response(status=404, text="unknown person or home")

        # The path after our prefix is welkom's path after /api; the query
        # (the file version) goes along unchanged.
        path = request.path.removeprefix(IMAGE_URL_PREFIX)
        url = f"{coordinator.client.url}/api{path}"
        if request.query_string:
            url = f"{url}?{request.query_string}"

        cached = self._cache.get(url)
        if cached is None:
            try:
                response = await coordinator.client.session.get(url)
                content = await response.read()
                content_type = response.content_type
            except aiohttp.ClientResponseError as err:
                # 404 (no picture) and 403 (welkom won't show it to us) are
                # answers; anything else is welkom being unreachable or broken.
                status = err.status if err.status in (403, 404) else 502
                return web.Response(status=status, text=err.message)
            except aiohttp.ClientError as err:
                return web.Response(status=502, text=str(err))

            cached = (content, content_type)
            # Only a versioned URL is safe to keep: without `v` a swapped file
            # would keep serving the old bytes until a restart.
            if "v" in request.query:
                self._cache[url] = cached
                while len(self._cache) > _CACHE_SIZE:
                    self._cache.popitem(last=False)

        content, content_type = cached
        return web.Response(
            body=content,
            content_type=content_type,
            headers={
                "Cache-Control": "public, max-age=86400"
                if "v" in request.query
                else "no-cache"
            },
        )

    def _manifest(self, home_id: str, name: str) -> web.Response:
        coordinator = self._coordinator_for(home_id=home_id)
        home = (coordinator.homes or {}).get(home_id) if coordinator else None
        image = home.images.get(name) if home else None
        if image is None:
            return web.Response(status=404, text="no such image")
        return self.json(
            {"url": image.url, "bounds": image.bounds},
            headers={"Cache-Control": "no-cache"},
        )

    def _coordinator_for(
        self, person_id: str | None = None, home_id: str | None = None
    ) -> WelkomCoordinator | None:
        """The first configured welkom that knows this person or home."""
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            coordinator = getattr(entry, "runtime_data", None)
            if not isinstance(coordinator, WelkomCoordinator):
                continue
            if person_id is not None and person_id in (coordinator.people or {}):
                return coordinator
            if home_id is not None and home_id in (coordinator.homes or {}):
                return coordinator
        return None
