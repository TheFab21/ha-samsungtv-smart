from __future__ import annotations

import importlib.util
import logging
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Déclaration des plateformes (switch ajouté si le fichier existe vraiment)
PLATFORMS: list[Platform] = [Platform.MEDIA_PLAYER]
try:
    if importlib.util.find_spec(f"{__name__}.switch") is not None:
        PLATFORMS.append(Platform.SWITCH)
except Exception:  # pragma: no cover
    pass


# ----- Hooks Home Assistant -----

async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up via YAML (non utilisé ici, mais requis par HA)."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up SamsungTV Smart from a ConfigEntry."""
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {}

    # Forward setup vers chaque plateforme disponible
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a ConfigEntry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return unload_ok


# ----- Ré-exports pour compat config_flow.py -----
# Le fichier config_flow.py importe :
#   from . import SamsungTVInfo, get_device_info, get_smartthings_api_key,
#              get_smartthings_entries, is_valid_ha_version
# On ré-exporte ces symboles depuis leurs modules réels.

try:
    from .websockets import SamsungTVInfo  # ajuste le nom du module si besoin
except Exception:  # pragma: no cover
    SamsungTVInfo = None  # sera détecté au runtime si réellement manquant

try:
    # adapte si ces fonctions vivent ailleurs (ex: .helpers, .rest, etc.)
    from .helpers import get_device_info, is_valid_ha_version
except Exception:  # pragma: no cover
    def get_device_info(*_args, **_kwargs):
        raise ImportError("get_device_info not available")
    def is_valid_ha_version() -> bool:
        return True

try:
    from .smartthings import get_smartthings_api_key, get_smartthings_entries
except Exception:  # pragma: no cover
    def get_smartthings_api_key(*_args, **_kwargs):
        return None
    def get_smartthings_entries(*_args, **_kwargs):
        return {}

__all__ = [
    "SamsungTVInfo",
    "get_device_info",
    "get_smartthings_api_key",
    "get_smartthings_entries",
    "is_valid_ha_version",
]
