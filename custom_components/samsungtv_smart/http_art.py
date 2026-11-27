"""Serve a TV's private Frame Art files to dashboards (#472).

See art_files: the files live outside ``www``, and a request needs either
this run's token for that TV (``?token=…``, for plain ``<img>`` tags) or a
normal Home Assistant login.
"""

from __future__ import annotations

import hmac
import mimetypes
import os
import re

from aiohttp import web

from homeassistant.components.http import KEY_AUTHENTICATED, HomeAssistantView
from homeassistant.core import HomeAssistant

from .art_files import ART_URL_PREFIX, art_dir, art_token

# Config entry ids are ULIDs; anything else is not one of ours.
_ENTRY_ID = re.compile(r"^[0-9A-Za-z]{8,40}$")


class SamsungTVArtView(HomeAssistantView):
    """``GET /api/samsungtv_smart/art/<entry_id>/<path>``."""

    url = ART_URL_PREFIX + "/{entry_id}/{path:.+}"
    name = "api:samsungtv_smart:art"
    # Checked here instead: a plain <img> request carries no login, only the
    # token. A logged-in request is accepted as well.
    requires_auth = False

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the view."""
        self._hass = hass

    async def get(
        self, request: web.Request, entry_id: str, path: str
    ) -> web.StreamResponse:
        """Return the file, or 404 for anything not allowed or not there."""
        if not _ENTRY_ID.match(entry_id):
            return web.Response(status=404)
        if not request.get(KEY_AUTHENTICATED, False):
            token = request.query.get("token", "")
            if not token or not hmac.compare_digest(
                token, art_token(self._hass, entry_id)
            ):
                # 404 rather than 401: do not confirm that the file exists.
                return web.Response(status=404)
        file_path = await self._hass.async_add_executor_job(
            self._resolve, entry_id, path
        )
        if file_path is None:
            return web.Response(status=404)
        content_type = mimetypes.guess_type(file_path)[0] or "application/octet-stream"
        return web.FileResponse(
            file_path,
            headers={
                "Content-Type": content_type,
                # The URL changes with the file (?v=mtime): safe to cache, but
                # only in the viewer's browser.
                "Cache-Control": "private, max-age=86400",
            },
        )

    def _resolve(self, entry_id: str, path: str) -> str | None:
        """The file under the TV's private folder, never outside it."""
        base = os.path.realpath(art_dir(self._hass, entry_id))
        candidate = os.path.realpath(os.path.join(base, path))
        if not candidate.startswith(base + os.sep):
            return None
        if not os.path.isfile(candidate):
            return None
        return candidate
