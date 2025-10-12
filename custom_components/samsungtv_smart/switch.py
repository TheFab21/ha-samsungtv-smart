from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Noms d’options (doivent correspondre à ceux exposés dans config_flow)
OPT_FRAME_ART_ENABLED = "frame_art_enabled"
OPT_FRAME_ART_SOURCE_OFF = "frame_art_source_off"
OPT_FRAME_ART_APP_ID = "frame_art_app_id"
OPT_FRAME_ART_SELECT_DELAY = "frame_art_select_delay"
OPT_FRAME_ART_RETRIES = "frame_art_retries"
OPT_FRAME_ART_RETRY_SLEEP = "frame_art_retry_sleep"

# Valeurs par défaut
DEF_FRAME_ART_ENABLED = True
DEF_FRAME_ART_APP_ID = "TV/HDMI"
DEF_FRAME_ART_SELECT_DELAY = 0.6
DEF_FRAME_ART_RETRIES = 6
DEF_FRAME_ART_RETRY_SLEEP = 0.35


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the Frame HDMI switch from a config entry."""
    async_add_entities([FrameHdmiSwitch(hass, entry)], update_before_add=False)


class FrameHdmiSwitch(SwitchEntity):
    """Switch qui, à l'extinction, force l'HDMI/app voulue (mode Frame)."""

    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry

        self._attr_unique_id = f"{entry.entry_id}_frame_hdmi"
        self._attr_name = f"{entry.title} – Frame HDMI"
        self._state = False  # Off = actionnera HDMI/app
        self._media_entity_id = None  # défini au premier update

    @property
    def is_on(self) -> bool:
        return self._state

    @property
    def available(self) -> bool:
        # Disponible si l'entité media_player de la TV existe
        return self._resolve_media_entity_id() is not None

    def _opt(self, key: str, default: Any) -> Any:
        return self.entry.options.get(key, default)

    def _resolve_media_entity_id(self) -> str | None:
        if self._media_entity_id:
            return self._media_entity_id
        # Entité media_player créée par la même entry
        # Pattern standard: media_player.<slug du nom>
        # Utilisons la registry pour être robustes
        ent_reg = self.hass.helpers.entity_registry.async_get(self.hass)
        for ent in ent_reg.entities.values():
            if ent.config_entry_id == self.entry.entry_id and ent.domain == "media_player":
                self._media_entity_id = ent.entity_id
                break
        return self._media_entity_id

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._state = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Quand on met le switch à Off: lancer app_id puis source (optionnel)."""
        self._state = False
        self.async_write_ha_state()

        if not self._opt(OPT_FRAME_ART_ENABLED, DEF_FRAME_ART_ENABLED):
            _LOGGER.debug("Frame HDMI désactivé dans les options; rien à faire.")
            return

        media = self._resolve_media_entity_id()
        if not media:
            _LOGGER.warning("Impossible de trouver l'entité media_player liée.")
            return

        app_id = self._opt(OPT_FRAME_ART_APP_ID, DEF_FRAME_ART_APP_ID)
        source_off = (self._opt(OPT_FRAME_ART_SOURCE_OFF, "") or "").strip()
        delay = float(self._opt(OPT_FRAME_ART_SELECT_DELAY, DEF_FRAME_ART_SELECT_DELAY))
        retries = int(self._opt(OPT_FRAME_ART_RETRIES, DEF_FRAME_ART_RETRIES))
        sleep = float(self._opt(OPT_FRAME_ART_RETRY_SLEEP, DEF_FRAME_ART_RETRY_SLEEP))

        # 1) Lancer l'app (TV/HDMI) si présent
        if app_id:
            try:
                await self.hass.services.async_call(
                    "media_player",
                    "play_media",
                    {
                        "entity_id": media,
                        "media_content_type": "app",
                        "media_content_id": app_id,
                    },
                    blocking=True,
                )
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("play_media(app_id=%s) a échoué: %s", app_id, err)

        # 2) Puis sélectionner la source si fournie
        if source_off:
            await asyncio.sleep(max(0.0, delay))

            # Tentatives tolérantes : certaines TV n’acceptent la source que quand l’app a fini de se lancer.
            for attempt in range(1, max(1, retries) + 1):
                try:
                    await self.hass.services.async_call(
                        "media_player",
                        "select_source",
                        {"entity_id": media, "source": source_off},
                        blocking=True,
                    )
                    break
                except Exception as err:  # noqa: BLE001
                    if attempt >= retries:
                        _LOGGER.error(
                            "select_source('%s') a échoué après %s tentatives: %s",
                            source_off,
                            retries,
                            err,
                        )
                        break
                    await asyncio.sleep(max(0.0, sleep))
