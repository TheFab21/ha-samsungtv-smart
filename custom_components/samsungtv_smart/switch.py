# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import device_registry as dr

_LOGGER = logging.getLogger(__name__)

# Options keys (mappés à config_flow)
OPT_FRAME_ART_ENABLED = "frame_art_enabled"
OPT_FRAME_ART_SOURCE_OFF = "frame_art_source_off"
OPT_FRAME_ART_APP_ID = "frame_art_app_id"
OPT_FRAME_ART_SELECT_DELAY = "frame_art_select_delay"
OPT_FRAME_ART_RETRIES = "frame_art_retries"
OPT_FRAME_ART_RETRY_SLEEP = "frame_art_retry_sleep"

# Defaults
DEF_FRAME_ART_ENABLED = True
DEF_FRAME_ART_APP_ID = "TV/HDMI"
DEF_FRAME_ART_SELECT_DELAY = 0.6
DEF_FRAME_ART_RETRIES = 6
DEF_FRAME_ART_RETRY_SLEEP = 0.35


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Register the switch for this config entry (auto for all existing TVs)."""
    async_add_entities([FrameHdmiSwitch(hass, entry)], update_before_add=False)


class FrameHdmiSwitch(SwitchEntity):
    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self._attr_unique_id = f"{entry.entry_id}_frame_hdmi"
        self._attr_name = f"{entry.title} - Frame HDMI"
        self._is_on = False
        self._media_entity_id: Optional[str] = None

    # ---- base props
    @property
    def is_on(self) -> bool:
        return self._is_on

    @property
    def available(self) -> bool:
        ent_id = self._resolve_media_entity_id(force_refresh=True)
        return ent_id is not None and self.hass.states.get(ent_id) is not None

    # ---- helpers
    def _opt(self, key: str, default: Any) -> Any:
        return self.entry.options.get(key, default)

    def _resolve_media_entity_id(self, force_refresh: bool = False) -> Optional[str]:
        """
        Reprend l'entité media_player EXISTANTE correspondant à cette TV :
        1) par config_entry_id (cas nominal)
        2) sinon par device_id commun (device registry)
        On filtre: domain=media_player, platform=samsungtv_smart, non désactivée,
        réellement présente dans hass.states. On retente si l'ID en cache est orphelin.
        """
        if not force_refresh and self._media_entity_id:
            if self.hass.states.get(self._media_entity_id) is not None:
                return self._media_entity_id
            self._media_entity_id = None

        ent_reg = er.async_get(self.hass)
        dev_reg = dr.async_get(self.hass)

        def _is_valid(ent: er.RegistryEntry) -> bool:
            return (
                ent.domain == "media_player"
                and ent.platform == "samsungtv_smart"
                and ent.disabled_by is None
            )

        # (1) par config_entry_id
        candidates = [
            e.entity_id
            for e in ent_reg.entities.values()
            if e.config_entry_id == self.entry.entry_id and _is_valid(e)
        ]

        # (2) si rien, par device_id
        if not candidates:
            # trouve un device_id lié à CETTE entry (n'importe quelle entité)
            device_ids = {
                e.device_id
                for e in ent_reg.entities.values()
                if e.config_entry_id == self.entry.entry_id and e.device_id
            }
            # pour chaque device, cherche un media_player samsungtv_smart actif
            for device_id in device_ids:
                for e in ent_reg.entities.values():
                    if e.device_id == device_id and _is_valid(e):
                        candidates.append(e.entity_id)

        # garde seulement celles présentes dans les states
        candidates = [e for e in candidates if self.hass.states.get(e) is not None]

        chosen: Optional[str] = None
        if len(candidates) == 1:
            chosen = candidates[0]
        elif len(candidates) > 1:
            # heuristique: préférer un entity_id qui contient un slug du titre
            title_slug = (self.entry.title or "").lower().replace(" ", "_")
            for e in candidates:
                if title_slug and title_slug in e:
                    chosen = e
                    break
            if not chosen:
                chosen = candidates[0]

        self._media_entity_id = chosen
        if not chosen:
            _LOGGER.debug(
                "Aucune media_player active trouvée pour entry_id=%s (entities=%d, devices=%d)",
                self.entry.entry_id,
                len(ent_reg.entities),
                len(dev_reg.devices),
            )
        else:
            _LOGGER.debug("media_player résolue: %s", chosen)
        return chosen

    async def _select_app(self, media_entity: str, app: str) -> bool:
        # Prefer samsungtv_smart.select_app; fallback to media_player.play_media
        try:
            await self.hass.services.async_call(
                "samsungtv_smart", "select_app", {"entity_id": media_entity, "app": app}, blocking=True
            )
            _LOGGER.debug("samsungtv_smart.select_app(app='%s') sent", app)
            return True
        exce
