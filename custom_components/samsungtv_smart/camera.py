"""Camera showing the artwork currently displayed on a Frame TV.

Since the Frame Art files left ``www`` (#472), a camera set up by hand on
``/config/www/frame_art/<entry_id>/current.jpg`` (Local File) or on
``/local/frame_art/<entry_id>/current.jpg`` (Generic Camera) finds nothing. This
one reads the same picture from the private folder, so picture cards,
notifications and HomeKit keep a camera to use, behind Home Assistant's own
camera proxy and its access tokens.
"""

from __future__ import annotations

import asyncio
import logging

from homeassistant.components.camera import Camera
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ID
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .art_files import art_dir
from .const import CONF_IS_FRAME_TV, DATA_ART_API, DATA_CFG, DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add the current-artwork camera for a Frame TV."""
    data = hass.data[DOMAIN][entry.entry_id]
    art_api = data.get(DATA_ART_API)
    is_frame_tv = bool(entry.data.get(CONF_IS_FRAME_TV))
    if art_api is not None and not is_frame_tv:
        # Same probe as the other art entities when the flag is not persisted
        # yet (first setup).
        try:
            async with asyncio.timeout(5):
                is_frame_tv = await art_api.supported()
        except Exception:  # noqa: BLE001
            is_frame_tv = False
    if not is_frame_tv:
        return
    device_unique_id = data[DATA_CFG].get(CONF_ID, entry.entry_id)
    async_add_entities([FrameArtCurrentCamera(hass, entry, device_unique_id)])


def _read(path: str) -> bytes | None:
    try:
        with open(path, "rb") as fh:
            return fh.read()
    except OSError:
        return None


class FrameArtCurrentCamera(Camera):
    """The picture the TV shows in Art Mode (its private current.jpg)."""

    _attr_has_entity_name = True
    _attr_translation_key = "art_current"
    _attr_icon = "mdi:image-frame"

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, device_unique_id: str
    ) -> None:
        """Initialize the camera."""
        super().__init__()
        self.hass = hass
        self._entry_id = entry.entry_id
        self._attr_unique_id = f"{entry.entry_id}_art_current"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, device_unique_id)})
        self.content_type = "image/jpeg"

    @property
    def _path(self) -> str:
        return art_dir(self.hass, self._entry_id, "current.jpg")

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return the current artwork, or None before the first download.

        Read on each request: the coordinator rewrites the file when the
        artwork changes, and cards refresh camera pictures on their own.
        """
        return await self.hass.async_add_executor_job(_read, self._path)
