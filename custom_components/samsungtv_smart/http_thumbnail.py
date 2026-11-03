"""On-demand resized-thumbnail HTTP view for the folder gallery card.

A folder of full-size originals (several MB each, many of them) made the gallery
download and decode the whole set at full resolution just to show a grid. The
card now points its grid `<img>` tags at this endpoint, which returns a small
cached JPEG instead, so only kilobytes per tile travel to the browser.
"""

from __future__ import annotations

import hashlib
import logging
import os

from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

THUMBNAIL_URL = "/api/samsungtv_smart/thumbnail"
_CACHE_SUBDIR = ("frame_art", ".thumb_cache")
_DEFAULT_W = 400
# The endpoint is unauthenticated and caches one file per (image, width): with
# any width from 64 to 1024 accepted, anyone who can reach Home Assistant could
# make it encode and store ~960 variants of every image under www. Widths are
# snapped UP to these sizes, so each image has at most this many cached copies
# and a configured thumbnail_width still gets at least what it asked for.
WIDTH_BUCKETS = (128, 256, 400, 640, 1024)


def snap_width(raw) -> int:
    """Return the smallest bucket >= the requested width (default 400)."""
    try:
        width = int(raw)
    except (TypeError, ValueError):
        return _DEFAULT_W
    for bucket in WIDTH_BUCKETS:
        if width <= bucket:
            return bucket
    return WIDTH_BUCKETS[-1]


class SamsungTVThumbnailView(HomeAssistantView):
    """Serve a downscaled JPEG thumbnail of an image stored under ``<config>/www``.

    Unauthenticated on purpose: plain ``<img>`` requests carry no HA auth, and
    this only ever reads files under ``<config>/www`` — which Home Assistant
    already serves publicly at ``/local/`` — so it exposes nothing new. The
    requested path is strictly validated to stay within that directory
    (realpath containment check) to prevent traversal.
    """

    url = THUMBNAIL_URL
    name = "api:samsungtv_smart:thumbnail"
    requires_auth = False

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the view."""
        self._hass = hass
        self._www = os.path.realpath(hass.config.path("www"))
        self._cache_dir = os.path.join(hass.config.path("www"), *_CACHE_SUBDIR)

    async def get(self, request: web.Request) -> web.StreamResponse:
        """Return a resized JPEG for ``?path=<url>&w=<width>``."""
        raw_path = request.query.get("path")
        if not raw_path:
            return web.Response(status=400, text="missing path")

        width = snap_width(request.query.get("w", _DEFAULT_W))

        # realpath / isfile touch the disk: keep them off the event loop.
        src = await self._hass.async_add_executor_job(self._resolve, raw_path)
        if src is None:
            return web.Response(status=404, text="not found")

        try:
            data = await self._hass.async_add_executor_job(
                self._build_thumbnail, src, width
            )
        except FileNotFoundError:
            return web.Response(status=404, text="not found")
        except Exception as ex:  # noqa: BLE001 - never 500 the whole grid
            _LOGGER.debug("Thumbnail build failed for %s: %s", src, ex)
            # Fall back to the original so the tile still shows something.
            raise web.HTTPFound(self._local_url(src)) from None

        if data is None:
            # Pillow unavailable → serve the original (un-resized) instead.
            raise web.HTTPFound(self._local_url(src))

        return web.Response(
            body=data,
            content_type="image/jpeg",
            headers={"Cache-Control": "max-age=86400"},
        )

    def _resolve(self, raw_path: str) -> str | None:
        """Map a ``/local/...`` URL (or www-relative path) to a file under www."""
        path = raw_path.split("?", 1)[0]
        if path.startswith("/local/"):
            rel = path[len("/local/") :]
        elif path.startswith("/config/www/"):
            rel = path[len("/config/www/") :]
        else:
            rel = path.lstrip("/")

        candidate = os.path.realpath(os.path.join(self._www, rel))
        # Strict containment: candidate must be the www dir or below it.
        if candidate != self._www and not candidate.startswith(self._www + os.sep):
            return None
        if not os.path.isfile(candidate):
            return None
        return candidate

    def _local_url(self, abs_path: str) -> str:
        """Build the public ``/local/...`` URL for an absolute www path."""
        rel = os.path.relpath(abs_path, self._www).replace(os.sep, "/")
        return f"/local/{rel}"

    def _build_thumbnail(self, src: str, width: int) -> bytes | None:
        """Return resized JPEG bytes (runs in executor). None if Pillow missing.

        Results are cached on disk keyed by (path, width, mtime), so a file is
        only re-encoded when it actually changes.
        """
        try:
            from PIL import Image, ImageOps  # noqa: PLC0415 - optional, lazy
        except ImportError:
            return None

        mtime = int(os.path.getmtime(src))
        key = hashlib.sha1(f"{src}|{width}|{mtime}".encode()).hexdigest()
        os.makedirs(self._cache_dir, exist_ok=True)
        cache_file = os.path.join(self._cache_dir, f"{key}.jpg")

        if os.path.isfile(cache_file):
            with open(cache_file, "rb") as fh:
                return fh.read()

        with Image.open(src) as image:
            # Honour EXIF orientation; cap to width (allow tall portraits).
            image = ImageOps.exif_transpose(image)
            image.thumbnail((width, width * 4))
            if image.mode not in ("RGB", "L"):
                image = image.convert("RGB")
            tmp_file = f"{cache_file}.tmp"
            image.save(tmp_file, format="JPEG", quality=78, optimize=True)
        os.replace(tmp_file, cache_file)

        with open(cache_file, "rb") as fh:
            return fh.read()
