from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers import entity_registry as er

from samsungtvws import SamsungTVWS

_LOGGER = logging.getLogger(__name__)

# --- Options keys stored in entry.options (namespace 'frame_art_')
OPT_ENABLED = "frame_art_enabled"
OPT_SOURCE_OFF = "frame_art_source_off"
OPT_APP_ID = "frame_art_app_id"
OPT_SELECT_DELAY = "frame_art_select_delay"
OPT_RETRIES = "frame_art_retries"
OPT_RETRY_SLEEP = "frame_art_retry_sleep"

DEF_ENABLED = True
DEF_APP_ID = "TV/HDMI"
DEF_SELECT_DELAY = 0.6
DEF_RETRIES = 6
DEF_RETRY_SLEEP = 0.35

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create a Frame Art switch per SamsungTV entry."""
    opts = entry.options or {}
    if opts.get(OPT_ENABLED, DEF_ENABLED) is False:
        _LOGGER.debug("[%s] Frame Art disabled in options, skipping switch", entry.entry_id)
        return

    # Determine the media_player entity for this config entry
    ent_reg = er.async_get(hass)
    mp_entity_id: Optional[str] = None
    for ent in ent_reg.entities.values():
        if ent.domain == "media_player" and ent.platform == "samsungtv_smart" and ent.config_entry_id == entry.entry_id:
            mp_entity_id = ent.entity_id
            break

    # Get host from entry data with best-effort keys used by the integration
    host = entry.data.get("host") or entry.data.get("ip_address") or entry.data.get("ip") or entry.data.get("device_ip")
    name = entry.title or "Samsung The Frame"

    if not host:
        _LOGGER.warning("[%s] No host/ip in entry.data; cannot create Frame Art switch", entry.entry_id)
        return

    async_add_entities([
        FrameArtSwitch(
            hass=hass,
            unique_id=f"{entry.entry_id}-frame_art",
            name=f"{name} Art",
            host=host,
            media_player=mp_entity_id,
            options=opts,
        )
    ])


class FrameArtSwitch(SwitchEntity):
    """Switch that toggles Art Mode and hops to HDMI/app using samsungtv_smart services."""

    _attr_should_poll = True

    def __init__(
        self,
        hass: HomeAssistant,
        unique_id: str,
        name: str,
        host: str,
        media_player: Optional[str],
        options: dict[str, Any],
    ) -> None:
        self.hass = hass
        self._attr_unique_id = unique_id
        self._attr_name = name
        self._host = host
        self._mp = media_player
        self._tv = SamsungTVWS(self._host, timeout=5.0)
        self._attr_is_on = False

        # Options
        self._enabled = options.get(OPT_ENABLED, DEF_ENABLED)
        self._source_off = options.get(OPT_SOURCE_OFF)  # e.g. "Home cinéma"
        self._app_id = options.get(OPT_APP_ID, DEF_APP_ID)  # e.g. "TV/HDMI"
        self._delay = max(0.0, float(options.get(OPT_SELECT_DELAY, DEF_SELECT_DELAY)))
        self._retries = max(0, int(options.get(OPT_RETRIES, DEF_RETRIES)))
        self._retry_sleep = max(0.05, float(options.get(OPT_RETRY_SLEEP, DEF_RETRY_SLEEP)))

    @property
    def device_info(self):
        return {
            "identifiers": {("samsungtv_smart", self._host)},
            "manufacturer": "Samsung",
            "name": self._attr_name,
            "model": "The Frame",
        }

    # ------------- helpers

    def _get_attr(self, key: str):
        st = self.hass.states.get(self._mp) if self._mp else None
        return None if st is None else st.attributes.get(key)

    async def _wait_attr(self, key: str, expected: str) -> bool:
        for _ in range(self._retries + 1):
            if self._get_attr(key) == expected:
                return True
            await asyncio.sleep(self._retry_sleep)
        return False

    async def _call_select_source(self, label: str) -> bool:
        if not self._mp:
            return False
        try:
            await self.hass.services.async_call(
                "media_player",
                "select_source",
                {"entity_id": self._mp, "source": label},
                blocking=True,
            )
            _LOGGER.debug("[%s] media_player.select_source('%s') sent", self.entity_id, label)
            return True
        except Exception as err:
            _LOGGER.warning("[%s] select_source('%s') failed: %s", self.entity_id, label, err)
            return False

    async def _call_select_app(self, app_id: str) -> bool:
        if not self._mp:
            return False
        # prefer samsungtv_smart.select_app
        try:
            await self.hass.services.async_call(
                "samsungtv_smart",
                "select_app",
                {"entity_id": self._mp, "app": app_id},
                blocking=True,
            )
            _LOGGER.debug("[%s] samsungtv_smart.select_app(app='%s') sent", self.entity_id, app_id)
            return True
        except Exception:
            # fallback param name
            try:
                await self.hass.services.async_call(
                    "samsungtv_smart",
                    "select_app",
                    {"entity_id": self._mp, "app_id": app_id},
                    blocking=True,
                )
                _LOGGER.debug("[%s] samsungtv_smart.select_app(app_id='%s') sent", self.entity_id, app_id)
                return True
            except Exception as err2:
                _LOGGER.debug("[%s] select_app fallback failed: %s", self.entity_id, err2)
        # last resort: treat as source label
        return await self._call_select_source(app_id)

    async def _sequence_after_art_off(self) -> None:
        if not self._enabled:
            return
        # 1) source first (e.g. "Home cinéma")
        if self._source_off:
            if self._delay > 0:
                await asyncio.sleep(self._delay)
            if await self._call_select_source(self._source_off):
                await self._wait_attr("source", self._source_off)
        # 2) then app/context (e.g. "TV/HDMI")
        if self._app_id:
            if self._delay > 0:
                await asyncio.sleep(self._delay)
            if await self._call_select_app(self._app_id):
                await self._wait_attr("app_id", self._app_id)

    # ------------- Switch API

    async def async_turn_on(self, **kwargs: Any) -> None:
        try:
            await self.hass.async_add_executor_job(lambda: self._tv.art().set_artmode("on"))
            self._attr_is_on = True
            self._attr_available = True
            self.async_write_ha_state()
        except Exception as err:
            self._attr_available = False
            _LOGGER.warning("[%s] Art Mode ON failed: %s", self.entity_id, err)

    async def async_turn_off(self, **kwargs: Any) -> None:
        ok = False
        try:
            await self.hass.async_add_executor_job(lambda: self._tv.art().set_artmode("off"))
            ok = True
            self._attr_is_on = False
            self._attr_available = True
            self.async_write_ha_state()
        except Exception as err:
            self._attr_available = False
            _LOGGER.warning("[%s] Art Mode OFF failed: %s", self.entity_id, err)
        if ok:
            await self._sequence_after_art_off()

    async def async_update(self) -> None:
        try:
            status = await self.hass.async_add_executor_job(lambda: self._tv.art().get_artmode())
            self._attr_is_on = (status == "on")
            self._attr_available = True
        except Exception as err:
            self._attr_available = False
            _LOGGER.debug("[%s] Art Mode status error: %s", self.entity_id, err)
