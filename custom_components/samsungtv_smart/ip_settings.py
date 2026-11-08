"""IP Control picture/eco settings the integration did not expose yet.

Every setting below was accepted, read and written, in normal viewing on a
QN55LS03FAFXZA (2025 Frame, firmware T-PTMFAKUC-0090-1296.8) - see
notes/QN55LS03FAFXZA/IPCONTROL_DECOMPILED.md, "Expert Picture Methods" - or on a
TQ65S95HFTXXC (2026 S95H, Tizen 10) - see notes/TQ65S95HFTXXC/IPCONTROL_LIVE.md.
Values a TV rejected are not offered. Like the other IP Control picture
entities they answer only in normal viewing (not in Art Mode or while off), so
they poll only then and are unavailable otherwise.

Left out on purpose: pictureSizeControl (its getter works but every write was
refused with -32002), gammaModeControl (only BT.1886 is accepted in SDR, so a
selector has one usable value), WB2Point/WB20Point white balance (six gains
with no known ranges - writing guesses would wreck a calibration) and
firstScreenAppControl (lists app names only, and its launch was refused).
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
import time

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_OFF
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo

from .api.ipcontrol import (
    SamsungIPControl,
    SamsungIPControlAuthError,
    SamsungIPControlError,
    SamsungIPControlModeLockedError,
)
from .const import (
    CONF_ENABLE_IP_CONTROL,
    CONF_IP_CONTROL_TOKEN,
    DOMAIN,
    ip_control_port,
)
from .token_notify import METHOD_IP_CONTROL, clear_token_problem, notify_token_problem

_LOGGER = logging.getLogger(__name__)

# Seconds between two reads of one setting. Home Assistant polls a platform at
# its own SCAN_INTERVAL, and this integration's switch platform uses 5 s for the
# Art Mode switch, so without this every expert switch read the TV every 5 s.
# The values rarely change; a write reads its echo, so 30 s loses nothing.
IP_SETTING_POLL_INTERVAL = 30.0


@dataclass(frozen=True)
class IPSetting:
    """One ``<x>Control`` JSON-RPC setting and how it is shown."""

    key: str  # translation key and unique_id suffix
    method: str
    param: str
    icon: str
    # select: option key (translated, lowercase) -> the TV's value
    options: dict[str, str] | None = None
    # number
    min_value: int | None = None
    max_value: int | None = None


IP_SELECTS = (
    IPSetting(
        "ip_auto_motion_plus",
        "autoMotionPlusControl",
        "autoMotionPlus",
        "mdi:motion-play-outline",
        options={"off": "Off", "auto": "Auto", "custom": "Custom"},
    ),
    IPSetting(
        "ip_apply_picture_settings",
        "applyPictureSettingsControl",
        "applyPictureSettings",
        "mdi:television-shimmer",
        options={"current_source": "CurrentSource", "all_sources": "AllSources"},
    ),
    # The TV menu's three values. The 2025 Frame answered -32002 to the getter,
    # so this select is simply unavailable there; the S95H accepted all three.
    IPSetting(
        "ip_peak_brightness",
        "peakBrightnessControl",
        "peakBrightness",
        "mdi:brightness-7",
        options={"off": "Off", "medium": "Medium", "high": "High"},
    ),
    # Off / Low / High on both sets; "Medium" is refused.
    IPSetting(
        "ip_contrast_enhancer",
        "contrastEnhancerControl",
        "contrastEnhancer",
        "mdi:contrast-box",
        options={"off": "Off", "low": "Low", "high": "High"},
    ),
)

# Off/On settings, shown as switches.
IP_SWITCHES = (
    IPSetting(
        "ip_auto_hdr_remastering",
        "autoHDRRemasteringControl",
        "autoHDRRemastering",
        "mdi:hdr",
    ),
    IPSetting(
        "ip_brightness_optimization",
        "brightnessOptimizationControl",
        "brightnessOptimization",
        "mdi:brightness-auto",
    ),
    IPSetting(
        "ip_motion_lighting",
        "motionLightingControl",
        "motionLighting",
        "mdi:motion-sensor",
    ),
    IPSetting(
        "ip_auto_power_saving",
        "autoPowerSavingControl",
        "autoPowerSaving",
        "mdi:leaf",
    ),
    # The quick-settings "Energy Saving Solution" tile. While it is On the TV
    # caps the backlight and refuses backlightControl writes with -32002, so
    # anyone driving the backlight from HA wants to see and clear this one;
    # the quick-setting select (qs_energy_saving_solution) covers the same
    # tile over the local mobilebff channel on TVs that have it.
    IPSetting(
        "ip_energy_saving_solution",
        "energySavingSolutionControl",
        "energySavingSolution",
        "mdi:leaf-circle-outline",
    ),
)

IP_NUMBERS = (
    IPSetting(
        "ip_gamma_bt1886",
        "gamma.BT1886Control",
        "gamma.BT1886",
        "mdi:gamma",
        min_value=-3,
        max_value=3,
    ),
)


def ip_control_active(entry: ConfigEntry) -> bool:
    """True when IP Control is paired AND enabled in the options."""
    return bool(entry.data.get(CONF_IP_CONTROL_TOKEN)) and entry.options.get(
        CONF_ENABLE_IP_CONTROL, True
    )


def tv_normal_viewing(hass: HomeAssistant, entry_id: str) -> bool:
    """True when the TV is on for normal viewing (not off, not Art Mode).

    These methods answer -32601 outside normal viewing, and polling a TV that
    has left the network costs ~7.8 s a call (#248), so they only poll then.
    """
    registry = er.async_get(hass)
    for entity in registry.entities.get_entries_for_config_entry_id(entry_id):
        if entity.domain != "media_player":
            continue
        state = hass.states.get(entity.entity_id)
        if state is None or state.state in (STATE_OFF, "unavailable", "unknown"):
            return False
        return state.attributes.get("art_mode_status") != "on"
    return False


class IPSettingEntity:
    """Shared plumbing: client, availability, read and write with error mapping.

    Subclasses implement ``_apply(value)`` to store a value read from or
    written to the TV in the entity's own state.
    """

    _attr_has_entity_name = True
    _attr_should_poll = True
    _attr_entity_registry_enabled_default = False

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        host: str,
        device_name: str,
        device_unique_id: str,
        setting: IPSetting,
    ) -> None:
        self.hass = hass
        self._entry_id = entry.entry_id
        self._host = host
        self._device_name = device_name
        self._device_unique_id = device_unique_id
        self._setting = setting
        self._ip_control: SamsungIPControl | None = None
        self._ip_control_token: str | None = None
        self._live = False
        self._last_read: float | None = None
        self._attr_unique_id = f"{device_unique_id}_{setting.key}"
        self._attr_translation_key = setting.key
        self._attr_icon = setting.icon

    @property
    def device_info(self) -> DeviceInfo:
        """Link this entity to the TV device."""
        return DeviceInfo(identifiers={(DOMAIN, self._device_unique_id)})

    @property
    def available(self) -> bool:
        """Available only while IP Control is active and the last read worked."""
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        return bool(entry and ip_control_active(entry) and self._live)

    def _title(self) -> str:
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        return entry.title if entry else (self._device_name or "this Samsung TV")

    def _client(self) -> SamsungIPControl | None:
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if entry is None or not ip_control_active(entry):
            self._ip_control = None
            self._ip_control_token = None
            return None
        token = entry.data.get(CONF_IP_CONTROL_TOKEN)
        if self._ip_control is None or self._ip_control_token != token:
            self._ip_control = SamsungIPControl(
                self.hass, self._host, port=ip_control_port(entry.data), token=token
            )
            self._ip_control_token = token
        return self._ip_control

    def _apply(self, value) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    def _clear(self) -> None:
        self._live = False
        self._apply(None)

    async def async_update(self) -> None:
        """Read the setting, only in normal viewing, at most every 30 s."""
        if not tv_normal_viewing(self.hass, self._entry_id):
            # Read again as soon as normal viewing is back.
            self._last_read = None
            self._clear()
            return
        client = self._client()
        if client is None:
            self._last_read = None
            self._clear()
            return
        now = time.monotonic()
        if (
            self._last_read is not None
            and 0 <= now - self._last_read < IP_SETTING_POLL_INTERVAL
        ):
            return
        self._last_read = now
        try:
            value = await client.async_get_setting(
                self._setting.method, self._setting.param
            )
        except SamsungIPControlAuthError as ex:
            _LOGGER.warning(
                "IP Control %s read for %s: token rejected (%s) - re-pair via "
                "the integration options",
                self._setting.method,
                self._host,
                ex,
            )
            self._clear()
            notify_token_problem(
                self.hass, self._entry_id, METHOD_IP_CONTROL, self._title()
            )
        except SamsungIPControlError as ex:
            _LOGGER.debug(
                "Could not read IP Control %s for %s: %s",
                self._setting.method,
                self._host,
                ex,
            )
            self._clear()
        else:
            self._live = True
            self._apply(value)
            clear_token_problem(self.hass, self._entry_id, METHOD_IP_CONTROL)

    async def _async_write(self, value) -> None:
        """Write the setting and keep what the TV echoes."""
        client = self._client()
        if client is None:
            raise HomeAssistantError(
                "IP Control is not paired or is disabled for this TV."
            )
        try:
            echoed = await client.async_set_setting(
                self._setting.method, self._setting.param, value
            )
        except SamsungIPControlAuthError as ex:
            self._clear()
            notify_token_problem(
                self.hass, self._entry_id, METHOD_IP_CONTROL, self._title()
            )
            raise HomeAssistantError(f"IP Control token rejected: {ex}") from ex
        except SamsungIPControlModeLockedError as ex:
            # TV-side state (picture mode, input, content) - not a pairing
            # problem, so the entity stays available for the next attempt.
            raise HomeAssistantError(f"The TV refuses this right now: {ex}") from ex
        except SamsungIPControlError as ex:
            self._clear()
            raise HomeAssistantError(f"IP Control write failed: {ex}") from ex
        self._live = True
        self._last_read = time.monotonic()
        self._apply(echoed)
        clear_token_problem(self.hass, self._entry_id, METHOD_IP_CONTROL)
        self.async_write_ha_state()
