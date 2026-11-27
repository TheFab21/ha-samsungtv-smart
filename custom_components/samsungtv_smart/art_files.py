"""Where Frame Art thumbnails live, and how dashboards reach them (#472).

They used to be written under ``<config>/www/frame_art/<entry_id>/``. Home
Assistant serves that whole folder tree at ``/local/`` without asking anyone to
log in, so the artwork shown on the TV and, worse, the user's own uploaded
photos (``personal/MY_F….jpg``) could be downloaded by anyone who reached Home
Assistant and knew the entry id. That id is not a secret: it is in the Frame
Art sensor's ``current_thumbnail_url``, in browser history, in screenshots. The
content ids that follow it are sequential. Spook 5.12 started flagging it.

They now live under ``<config>/.storage/samsungtv_smart/frame_art/<entry_id>/``,
which nothing serves, and reach dashboards through the integration's own view
at ``/api/samsungtv_smart/art/<entry_id>/<file>?token=<token>``. The token is
random, one per TV, and made anew each time Home Assistant starts, the way the
media player and camera image proxies work: a plain ``<img>`` tag can load the
picture, and a URL that leaks stops working at the next restart. The view also
accepts a request authenticated the usual way.

Existing files are moved out of ``www`` on setup (migrate_legacy_art).
"""

from __future__ import annotations

import logging
import os
import secrets
import shutil
from urllib.parse import quote

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import STORAGE_DIR

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

ART_URL_PREFIX = f"/api/{DOMAIN}/art"
_TOKENS = f"{DOMAIN}_art_tokens"


def art_root(hass: HomeAssistant) -> str:
    """The private folder that holds every TV's Frame Art files."""
    return hass.config.path(STORAGE_DIR, DOMAIN, "frame_art")


def art_dir(hass: HomeAssistant, entry_id: str, *parts: str) -> str:
    """A TV's private Frame Art folder, or a path inside it."""
    return os.path.join(art_root(hass), entry_id, *parts)


def art_token(hass: HomeAssistant, entry_id: str) -> str:
    """This run's access token for a TV's Frame Art files."""
    tokens: dict[str, str] = hass.data.setdefault(_TOKENS, {})
    if entry_id not in tokens:
        tokens[entry_id] = secrets.token_urlsafe(32)
    return tokens[entry_id]


def art_url(
    hass: HomeAssistant, entry_id: str, rel: str, *, version: int | None = None
) -> str:
    """The URL a dashboard uses for ``rel`` (e.g. ``current.jpg``).

    ``version`` (the file's mtime) changes the URL when the picture changes,
    so a browser does not keep showing the previous one.
    """
    url = (
        f"{ART_URL_PREFIX}/{entry_id}/{quote(rel.replace(os.sep, '/'))}"
        f"?token={art_token(hass, entry_id)}"
    )
    if version is not None:
        url += f"&v={version}"
    return url


def art_file_url(hass: HomeAssistant, entry_id: str, abs_path: str) -> str | None:
    """The URL of a file inside a TV's folder, versioned by its mtime.

    None when the file is missing or outside that folder. Touches the disk.
    """
    base = os.path.realpath(art_dir(hass, entry_id))
    path = os.path.realpath(abs_path)
    if not path.startswith(base + os.sep):
        return None
    try:
        mtime = int(os.path.getmtime(path))
    except OSError:
        return None
    return art_url(hass, entry_id, os.path.relpath(path, base), version=mtime)


def legacy_art_dir(hass: HomeAssistant, *parts: str) -> str:
    """The old, publicly served location under ``www``."""
    return hass.config.path("www", "frame_art", *parts)


def migrate_legacy_art(hass: HomeAssistant, entry_id: str) -> int:
    """Move a TV's files out of ``www`` into the private folder.

    Blocking: run it in the executor. A file already present at the
    destination is kept (it is the newer copy) and the old one is dropped. The
    gallery card's resize cache under ``www/frame_art/.thumb_cache`` is
    removed: it is rebuilt in private storage on demand. ``www/frame_art`` is
    removed once empty. Returns the number of files moved.
    """
    old = legacy_art_dir(hass, entry_id)
    moved = 0
    if os.path.isdir(old):
        new = art_dir(hass, entry_id)
        for dirpath, _dirnames, filenames in os.walk(old):
            target_dir = os.path.join(new, os.path.relpath(dirpath, old))
            os.makedirs(target_dir, exist_ok=True)
            for name in filenames:
                src = os.path.join(dirpath, name)
                dst = os.path.join(target_dir, name)
                if os.path.exists(dst):
                    os.remove(src)
                else:
                    shutil.move(src, dst)
                    moved += 1
        shutil.rmtree(old, ignore_errors=True)
    cache = legacy_art_dir(hass, ".thumb_cache")
    if os.path.isdir(cache):
        shutil.rmtree(cache, ignore_errors=True)
    parent = legacy_art_dir(hass)
    try:
        os.rmdir(parent)  # only succeeds when empty
    except OSError:
        pass
    if moved:
        _LOGGER.info(
            "Moved %d Frame Art file(s) of %s out of www (where Home Assistant "
            "serves them without login) into %s",
            moved,
            entry_id,
            art_dir(hass, entry_id),
        )
    return moved
