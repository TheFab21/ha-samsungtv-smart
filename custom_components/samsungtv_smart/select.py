"""Samsung TV Smart - Select entities."""

from __future__ import annotations

import asyncio
import json
import logging
import time

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_HOST,
    CONF_ID,
    CONF_NAME,
    CONF_PORT,
    CONF_TOKEN,
    STATE_OFF,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api.art import SamsungTVAsyncArt
from .api.ipcontrol import (
    COLOR_TONE_OPTIONS,
    SamsungIPControl,
    SamsungIPControlAuthError,
    SamsungIPControlError,
    SamsungIPControlModeLockedError,
)
from .const import (
    AUTH_METHOD_OAUTH,
    CONF_API_KEY,
    CONF_AUTH_METHOD,
    CONF_DEVICE_ID,
    CONF_ENABLE_IP_CONTROL,
    CONF_IP_CONTROL_TOKEN,
    CONF_IS_FRAME_TV,
    CONF_OAUTH_TOKEN,
    CONF_WS_NAME,
    DATA_ART_API,
    DATA_CFG,
    DEFAULT_PORT,
    DOMAIN,
    WS_PREFIX,
)
from .token_notify import METHOD_IP_CONTROL, clear_token_problem, notify_token_problem

_LOGGER = logging.getLogger(__name__)

# Retry settings when TV is off at startup
_RETRY_INTERVAL = 30  # seconds between retries
_MAX_RETRIES = 10  # give up after 5 minutes

# SmartThings REST API
_API_DEVICES = "https://api.smartthings.com/v1/devices"


def _ip_control_active(entry: ConfigEntry) -> bool:
    """True when IP Control is paired AND enabled in the options."""
    return bool(entry.data.get(CONF_IP_CONTROL_TOKEN)) and entry.options.get(
        CONF_ENABLE_IP_CONTROL, True
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Samsung TV select entities from config entry."""
    config = hass.data[DOMAIN][entry.entry_id][DATA_CFG]
    host = config[CONF_HOST]
    port = config.get(CONF_PORT, DEFAULT_PORT)
    token = config.get(CONF_TOKEN)
    ws_name = config.get(CONF_WS_NAME, "HomeAssistant")
    device_unique_id = config.get(CONF_ID, entry.entry_id)
    device_name = config.get(CONF_NAME) or entry.title or host

    if _ip_control_active(entry):
        async_add_entities(
            [
                SamsungTVIPControlColorToneSelect(
                    hass, entry, host, device_name, device_unique_id
                )
            ],
            True,
        )
        _LOGGER.debug("IP Control color tone select created for %s", device_name)

    session = async_get_clientsession(hass)

    entities = []

    # ── Frame TV Matte selects ────────────────────────────────────────────
    # Reuse shared art_api if already created by sensor.py, otherwise create one
    art_api = hass.data[DOMAIN][entry.entry_id].get(DATA_ART_API)
    if not art_api:
        art_api = SamsungTVAsyncArt(
            host=host,
            port=port,
            token=token,
            session=session,
            timeout=5,
            name=f"{WS_PREFIX} {ws_name} Art Select",
        )

    # Use persisted flag if available, otherwise probe live
    is_frame_tv_cached = entry.data.get(CONF_IS_FRAME_TV, False)
    if is_frame_tv_cached:
        is_frame_supported = True
    else:
        try:
            async with asyncio.timeout(5):
                is_frame_supported = await art_api.supported()
        except Exception:
            is_frame_supported = False

    matte_type_select = None
    matte_color_select = None
    if is_frame_supported:
        matte_type_select = SamsungTVMatteTypeSelect(
            hass, entry, art_api, device_name, device_unique_id
        )
        matte_color_select = SamsungTVMatteColorSelect(
            hass, entry, art_api, device_name, device_unique_id
        )
        entities.extend([matte_type_select, matte_color_select])

    # ── Picture Mode select (SmartThings) ─────────────────────────────────
    api_key = config.get(CONF_API_KEY)
    device_id = config.get(CONF_DEVICE_ID)
    auth_method = config.get(CONF_AUTH_METHOD)
    if auth_method == AUTH_METHOD_OAUTH and not api_key:
        oauth_token = config.get(CONF_OAUTH_TOKEN)
        if oauth_token and isinstance(oauth_token, dict):
            api_key = oauth_token.get("access_token")

    if api_key and device_id:
        picture_mode_select = SamsungTVPictureModeSelect(
            hass=hass,
            entry=entry,
            device_name=device_name,
            device_unique_id=device_unique_id,
            api_key=api_key,
            device_id=device_id,
            session=session,
        )
        entities.append(picture_mode_select)
        _LOGGER.debug("Picture Mode select entity created for %s", device_name)
    else:
        picture_mode_select = None
        _LOGGER.debug(
            "SmartThings not configured for %s, skipping Picture Mode select",
            device_name,
        )

    if entities:
        async_add_entities(entities)

    # ── Background tasks ──────────────────────────────────────────────────
    if matte_type_select and matte_color_select:
        hass.async_create_background_task(
            _load_matte_options(hass, art_api, matte_type_select, matte_color_select),
            f"samsungtv_matte_options_{entry.entry_id}",
        )

    if picture_mode_select:
        hass.async_create_background_task(
            _load_picture_mode_options(hass, picture_mode_select),
            f"samsungtv_picture_mode_options_{entry.entry_id}",
        )

    if is_frame_supported:
        hass.async_create_background_task(
            _load_motion_options(
                hass, art_api, device_name, device_unique_id, async_add_entities, entry
            ),
            f"samsungtv_motion_options_{entry.entry_id}",
        )


# ══════════════════════════════════════════════════════════════════════════
# Background loaders
# ══════════════════════════════════════════════════════════════════════════


async def _load_matte_options(
    hass: HomeAssistant,
    art_api: SamsungTVAsyncArt,
    type_select: "SamsungTVMatteTypeSelect",
    color_select: "SamsungTVMatteColorSelect",
) -> None:
    """Fetch matte list from TV and populate select options, with retries."""
    for attempt in range(_MAX_RETRIES):
        try:
            async with asyncio.timeout(10):
                matte_types, matte_colors = await art_api.get_matte_list(
                    include_color=True
                )

            type_options = [
                m["matte_type"] if isinstance(m, dict) else str(m) for m in matte_types
            ]
            color_options = [
                m["color"] if isinstance(m, dict) else str(m) for m in matte_colors
            ]

            if type_options:
                type_select.set_options(type_options)
            if color_options:
                color_select.set_options(color_options)

            _LOGGER.info(
                "Matte selects populated — types: %s | colors: %s",
                type_options,
                color_options,
            )

            # Now that the option lists are known, re-read the TV's current
            # matte so the selects reflect the real state. The initial refresh
            # in async_added_to_hass can run before these options are loaded,
            # in which case _parse_matte_id cannot match the actual matte and
            # the selects stay on their default ("none"/first colour). Leaving
            # them wrong is not just cosmetic: an automation that re-applies the
            # selects' value would push that bogus "none" back to the TV and
            # wipe the real matte on every restart.
            await type_select.async_refresh_current()
            await color_select.async_refresh_current()
            return

        except asyncio.TimeoutError:
            _LOGGER.debug(
                "Timeout fetching matte list (attempt %d/%d), retrying in %ds",
                attempt + 1,
                _MAX_RETRIES,
                _RETRY_INTERVAL,
            )
        except Exception as ex:
            _LOGGER.debug(
                "Error fetching matte list (attempt %d/%d): %s",
                attempt + 1,
                _MAX_RETRIES,
                ex,
            )

        await asyncio.sleep(_RETRY_INTERVAL)

    _LOGGER.warning("Could not populate matte options after %d attempts", _MAX_RETRIES)


async def _load_picture_mode_options(
    hass: HomeAssistant,
    select_entity: "SamsungTVPictureModeSelect",
) -> None:
    """Fetch picture mode list from SmartThings, with retries."""
    for attempt in range(_MAX_RETRIES):
        try:
            async with asyncio.timeout(10):
                await select_entity.async_fetch_picture_modes()

            if select_entity.options:
                _LOGGER.info(
                    "Picture mode select populated — modes: %s",
                    select_entity.options,
                )
                return

        except asyncio.TimeoutError:
            _LOGGER.debug(
                "Timeout fetching picture modes (attempt %d/%d), retrying in %ds",
                attempt + 1,
                _MAX_RETRIES,
                _RETRY_INTERVAL,
            )
        except Exception as ex:
            _LOGGER.debug(
                "Error fetching picture modes (attempt %d/%d): %s",
                attempt + 1,
                _MAX_RETRIES,
                ex,
            )

        await asyncio.sleep(_RETRY_INTERVAL)

    _LOGGER.warning(
        "Could not populate picture mode options after %d attempts", _MAX_RETRIES
    )


def _parse_artmode_setting_options(item: dict) -> list[str]:
    """Build the option list for a get_artmode_settings item.

    `valid_values` is reported by the TV as a JSON-encoded string (e.g.
    '["off","60","120","180","240"]'), not an actual list — feeding it
    straight into list() explodes it into one option per character.
    Settings without valid_values (e.g. motion_sensitivity) instead report
    a numeric min/max range, so fall back to that.
    """
    valid_values = item.get("valid_values")
    if isinstance(valid_values, str):
        try:
            parsed = json.loads(valid_values)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, list):
            return [str(v) for v in parsed]
    elif isinstance(valid_values, list):
        return [str(v) for v in valid_values]

    item_min, item_max = item.get("min"), item.get("max")
    if item_min is not None and item_max is not None:
        try:
            return [str(v) for v in range(int(item_min), int(item_max) + 1)]
        except (TypeError, ValueError):
            pass

    current = item.get("value")
    return [str(current)] if current is not None else []


async def _load_motion_options(
    hass: HomeAssistant,
    art_api: SamsungTVAsyncArt,
    device_name: str,
    device_unique_id: str,
    async_add_entities: AddEntitiesCallback,
    entry: ConfigEntry,
) -> None:
    """Probe Art Mode motion sensor settings and create selects only if present.

    Not every Frame model has a motion sensor, and there is no dedicated
    "supported" flag for it — the only way to know is to ask
    get_artmode_settings and see whether the TV reports the item.
    """
    for attempt in range(_MAX_RETRIES):
        try:
            async with asyncio.timeout(10):
                sensitivity_item = await art_api.get_artmode_settings(
                    "motion_sensitivity"
                )
                timer_item = await art_api.get_artmode_settings("motion_timer")
                brightness_sensor_item = await art_api.get_artmode_settings(
                    "brightness_sensor_setting"
                )

            entities = []

            if isinstance(sensitivity_item, dict) and sensitivity_item.get("value"):
                options = _parse_artmode_setting_options(sensitivity_item)
                entities.append(
                    SamsungTVArtMotionSensitivitySelect(
                        hass,
                        entry,
                        art_api,
                        device_name,
                        device_unique_id,
                        options,
                        sensitivity_item.get("value"),
                    )
                )

            if isinstance(timer_item, dict) and timer_item.get("value"):
                options = _parse_artmode_setting_options(timer_item)
                entities.append(
                    SamsungTVArtMotionTimerSelect(
                        hass,
                        entry,
                        art_api,
                        device_name,
                        device_unique_id,
                        options,
                        timer_item.get("value"),
                    )
                )

            # Brightness sensor is on/off; the TV reports a value but no
            # valid_values, so the option list is fixed (the only accepted
            # values per the firmware).
            if (
                isinstance(brightness_sensor_item, dict)
                and brightness_sensor_item.get("value") is not None
            ):
                entities.append(
                    SamsungTVArtBrightnessSensorSelect(
                        hass,
                        entry,
                        art_api,
                        device_name,
                        device_unique_id,
                        ["off", "on"],
                        str(brightness_sensor_item.get("value")),
                    )
                )

            if entities:
                async_add_entities(entities)
                _LOGGER.info(
                    "Art Mode motion selects created for %s: %s",
                    device_name,
                    [e._setting_name for e in entities],
                )
            else:
                _LOGGER.info(
                    "TV %s did not report any Art Mode motion/brightness sensor "
                    "settings (motion_sensitivity, motion_timer, "
                    "brightness_sensor_setting) — this model has no such sensor, "
                    "so the Motion Sensitivity, Motion Timer and Brightness "
                    "Sensor controls are intentionally not created. This is "
                    "expected on Frames without the motion/ambient-light sensor "
                    "(e.g. some 2020/2021 models); it is not an error.",
                    device_name,
                )
            return

        except asyncio.TimeoutError:
            _LOGGER.debug(
                "Timeout fetching motion settings (attempt %d/%d), retrying in %ds",
                attempt + 1,
                _MAX_RETRIES,
                _RETRY_INTERVAL,
            )
        except Exception as ex:
            _LOGGER.debug(
                "Error fetching motion settings (attempt %d/%d): %s",
                attempt + 1,
                _MAX_RETRIES,
                ex,
            )

        await asyncio.sleep(_RETRY_INTERVAL)

    _LOGGER.debug(
        "Could not probe Art Mode motion settings after %d attempts "
        "(TV likely does not support a motion sensor)",
        _MAX_RETRIES,
    )


# ══════════════════════════════════════════════════════════════════════════
# IP Control Color Tone
# ══════════════════════════════════════════════════════════════════════════


class SamsungTVIPControlColorToneSelect(SelectEntity):
    """Select entity for the IP Control picture color tone."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:palette-swatch"
    _attr_should_poll = True

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        host: str,
        device_name: str,
        device_unique_id: str,
    ) -> None:
        """Initialize the IP Control color tone select."""
        self.hass = hass
        self._entry_id = entry.entry_id
        self._host = host
        self._device_name = device_name
        self._device_unique_id = device_unique_id
        self._ip_control: SamsungIPControl | None = None
        self._ip_control_token: str | None = None
        self._attr_unique_id = f"{device_unique_id}_ip_control_color_tone"
        self._attr_name = "Color Tone"
        self._attr_options = list(COLOR_TONE_OPTIONS)
        self._attr_current_option: str | None = None
        self._attr_available = False

    @property
    def device_info(self) -> DeviceInfo:
        """Link this entity to the TV device."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._device_unique_id)},
            name=self._device_name,
        )

    @property
    def available(self) -> bool:
        """Available only while IP Control is paired, enabled, and reachable."""
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        return bool(entry and _ip_control_active(entry) and self._attr_available)

    def _device_title(self) -> str:
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        return entry.title if entry else (self._device_name or "this Samsung TV")

    def _get_ip_control(self) -> SamsungIPControl | None:
        """Return a live IP Control client if paired AND enabled."""
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if entry is None or not _ip_control_active(entry):
            self._ip_control = None
            self._ip_control_token = None
            return None
        token = entry.data.get(CONF_IP_CONTROL_TOKEN)
        if self._ip_control is None or self._ip_control_token != token:
            self._ip_control = SamsungIPControl(self.hass, self._host, token=token)
            self._ip_control_token = token
        return self._ip_control

    async def async_select_option(self, option: str) -> None:
        """Set picture color tone through IP Control."""
        if option not in COLOR_TONE_OPTIONS:
            raise HomeAssistantError(
                f"Color tone must be one of {', '.join(COLOR_TONE_OPTIONS)}."
            )

        client = self._get_ip_control()
        if client is None:
            raise HomeAssistantError(
                "IP Control is not paired or is disabled for this TV."
            )

        try:
            self._attr_current_option = await client.async_set_color_tone(option)
            self._attr_available = True
        except SamsungIPControlAuthError as ex:
            self._mark_unavailable()
            notify_token_problem(
                self.hass, self._entry_id, METHOD_IP_CONTROL, self._device_title()
            )
            raise HomeAssistantError(
                f"IP Control token rejected while setting color tone: {ex}"
            ) from ex
        except SamsungIPControlModeLockedError as ex:
            # Reversible TV-side state, not a pairing or capability problem —
            # leave the entity available so the next attempt (after switching
            # picture mode) can succeed without re-pairing.
            raise HomeAssistantError(
                f"Color tone can't be changed right now: {ex}"
            ) from ex
        except SamsungIPControlError as ex:
            self._mark_unavailable()
            raise HomeAssistantError(
                f"Failed to set color tone via IP Control: {ex}"
            ) from ex

        clear_token_problem(self.hass, self._entry_id, METHOD_IP_CONTROL)
        self.async_write_ha_state()

    async def async_update(self) -> None:
        """Read current picture color tone from IP Control."""
        client = self._get_ip_control()
        if client is None:
            self._mark_unavailable()
            return

        try:
            self._attr_current_option = await client.async_get_color_tone()
            self._attr_available = True
        except SamsungIPControlAuthError as ex:
            _LOGGER.warning(
                "IP Control color tone read for %s: token rejected (%s) — "
                "re-pair via the integration options",
                self._host,
                ex,
            )
            self._mark_unavailable()
            notify_token_problem(
                self.hass, self._entry_id, METHOD_IP_CONTROL, self._device_title()
            )
        except SamsungIPControlError as ex:
            _LOGGER.debug(
                "Could not refresh IP Control color tone for %s: %s", self._host, ex
            )
            self._mark_unavailable()
        else:
            clear_token_problem(self.hass, self._entry_id, METHOD_IP_CONTROL)

    def _mark_unavailable(self) -> None:
        """Expose no selected color tone until a live IP Control read succeeds."""
        self._attr_available = False
        self._attr_current_option = None


# ══════════════════════════════════════════════════════════════════════════
# Matte select entities (Frame TV only)
# ══════════════════════════════════════════════════════════════════════════


class SamsungTVMatteSelectBase(SelectEntity):
    """Base class for Samsung Frame TV matte select entities."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        art_api: SamsungTVAsyncArt,
        device_name: str,
        device_unique_id: str,
    ) -> None:
        self.hass = hass
        self._entry = entry
        self._art_api = art_api
        self._device_name = device_name
        self._device_unique_id = device_unique_id
        self._attr_options: list[str] = []
        self._attr_current_option: str | None = None
        self._attr_should_poll = False

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._device_unique_id)},
            name=self._device_name,
        )

    def set_options(self, options: list[str]) -> None:
        """Update available options and refresh HA state."""
        self._attr_options = options
        if self._attr_current_option not in options:
            self._attr_current_option = options[0] if options else None
        if self.platform is not None:
            self.async_write_ha_state()

    async def async_refresh_current(self) -> None:
        """Read current artwork matte from TV and update state."""
        try:
            async with asyncio.timeout(5):
                current = await self._art_api.get_current()
            if current:
                matte_id: str = current.get("matte_id", "")
                self._parse_matte_id(matte_id)
                self.async_write_ha_state()
        except Exception as ex:
            _LOGGER.debug("Could not refresh current matte: %s", ex)

    def _parse_matte_id(self, matte_id: str) -> None:
        """To be implemented by subclass."""
        raise NotImplementedError


class SamsungTVMatteTypeSelect(SamsungTVMatteSelectBase):
    """Select entity for matte frame type (e.g. modern, shadowbox, triptych...)."""

    _attr_icon = "mdi:border-style"
    _attr_has_entity_name = True

    def __init__(self, hass, entry, art_api, device_name, device_unique_id):
        super().__init__(hass, entry, art_api, device_name, device_unique_id)
        self._attr_unique_id = f"{device_unique_id}_matte_type"
        self._attr_name = "Matte Type"
        self._attr_options = ["none"]
        self._attr_current_option = "none"

    def _parse_matte_id(self, matte_id: str) -> None:
        """Extract type part from matte_id (format: type_color or just type)."""
        if "_" in matte_id:
            matte_type = matte_id.rsplit("_", 1)[0]
        else:
            matte_type = matte_id or "none"
        # The TV reports some matte_ids upper-cased (e.g. SHADOWBOX_POLAR) while
        # the option list is lower-cased; match case-insensitively.
        matte_type = matte_type.lower()
        if matte_type in self._attr_options:
            self._attr_current_option = matte_type

    async def async_select_option(self, option: str) -> None:
        """Called when user picks a new matte type."""
        try:
            current = await self._art_api.get_current()
            if not current:
                _LOGGER.warning("Cannot change matte type: no current artwork")
                return

            content_id: str = current.get("content_id", "")
            existing_matte: str = current.get("matte_id", "none_polar")

            if "_" in existing_matte:
                color_part = existing_matte.rsplit("_", 1)[1]
            else:
                color_part = "polar"

            new_matte_id = f"{option}_{color_part}" if option != "none" else "none"

            await self._art_api.change_matte(
                content_id=content_id, matte_id=new_matte_id
            )
            self._attr_current_option = option
            self.async_write_ha_state()
            _LOGGER.debug(
                "Matte type changed to %s (full id: %s)", option, new_matte_id
            )

        except Exception as ex:
            _LOGGER.error("Error changing matte type: %s", ex)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        await self.async_refresh_current()


class SamsungTVMatteColorSelect(SamsungTVMatteSelectBase):
    """Select entity for matte color (e.g. polar, black, apricot...)."""

    _attr_icon = "mdi:palette"
    _attr_has_entity_name = True

    def __init__(self, hass, entry, art_api, device_name, device_unique_id):
        super().__init__(hass, entry, art_api, device_name, device_unique_id)
        self._attr_unique_id = f"{device_unique_id}_matte_color"
        self._attr_name = "Matte Color"
        self._attr_options = ["polar"]
        self._attr_current_option = "polar"

    def _parse_matte_id(self, matte_id: str) -> None:
        """Extract color part from matte_id (format: type_color)."""
        if "_" in matte_id:
            # Match case-insensitively: the TV may report e.g. SHADOWBOX_POLAR
            # while the option list is lower-cased.
            color = matte_id.rsplit("_", 1)[1].lower()
            if color in self._attr_options:
                self._attr_current_option = color

    async def async_select_option(self, option: str) -> None:
        """Called when user picks a new matte color."""
        try:
            current = await self._art_api.get_current()
            if not current:
                _LOGGER.warning("Cannot change matte color: no current artwork")
                return

            content_id: str = current.get("content_id", "")
            existing_matte: str = current.get("matte_id", "none")

            if "_" in existing_matte:
                type_part = existing_matte.rsplit("_", 1)[0]
            else:
                type_part = existing_matte or "shadowbox"

            new_matte_id = f"{type_part}_{option}"

            await self._art_api.change_matte(
                content_id=content_id, matte_id=new_matte_id
            )
            self._attr_current_option = option
            self.async_write_ha_state()
            _LOGGER.debug(
                "Matte color changed to %s (full id: %s)", option, new_matte_id
            )

        except Exception as ex:
            _LOGGER.error("Error changing matte color: %s", ex)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        await self.async_refresh_current()


# ══════════════════════════════════════════════════════════════════════════
# Art Mode motion sensor selects (Frame models with a motion sensor)
# ══════════════════════════════════════════════════════════════════════════


class SamsungTVArtMotionSelectBase(SelectEntity):
    """Base class for Art Mode motion-sensor select entities.

    Only created when get_artmode_settings reports the corresponding item
    for this TV (motion_sensitivity / motion_timer), since the underlying
    motion sensor is not present on every Frame model. The option list is
    read from the item's valid_values rather than hard-coded, so it follows
    whatever this firmware actually supports.
    """

    _attr_has_entity_name = True
    _attr_should_poll = True
    _setting_name = ""  # overridden by subclass

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        art_api: SamsungTVAsyncArt,
        device_name: str,
        device_unique_id: str,
        options: list[str],
        current: str | None,
    ) -> None:
        self.hass = hass
        self._entry = entry
        self._art_api = art_api
        self._device_name = device_name
        self._device_unique_id = device_unique_id
        self._attr_options = options
        self._attr_current_option = current

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._device_unique_id)},
            name=self._device_name,
        )

    def _tv_powered_off(self) -> bool:
        """True when the TV is off (and not in Art Mode) — skip art polling.

        Polling ``get_artmode_settings`` over the art WebSocket while the TV is
        powered off keeps poking the (sleeping) art-app for nothing and can wedge
        it overnight. Mirror the switch: rely on the media_player's already
        published state + ``art_mode_status`` attribute instead of issuing our
        own TV request, so this never adds traffic to a TV that's off.
        """
        ent_reg = er.async_get(self.hass)
        mp_id = None
        for entity in er.async_entries_for_config_entry(ent_reg, self._entry.entry_id):
            if entity.domain == "media_player":
                mp_id = entity.entity_id
                break
        if not mp_id:
            return False  # unknown → don't suppress
        state = self.hass.states.get(mp_id)
        if state is None:
            return False
        if state.state not in (STATE_OFF, "unavailable"):
            return False  # TV is on
        # media_player off/unavailable → TV off UNLESS Art Mode is active
        return state.attributes.get("art_mode_status") != "on"

    async def _async_set(self, option: str) -> None:
        """To be implemented by subclass: call the matching art_api setter."""
        raise NotImplementedError

    async def async_select_option(self, option: str) -> None:
        try:
            await self._async_set(option)
            self._attr_current_option = option
            self.async_write_ha_state()
        except Exception as ex:
            raise HomeAssistantError(
                f"Failed to set {self._setting_name}: {ex}"
            ) from ex

    async def async_update(self) -> None:
        # Don't touch the art channel while the TV is off: it's pointless (the
        # art-app is asleep) and the constant overnight polling can wedge the
        # art-app by morning. Keep the last known value instead.
        if self._tv_powered_off():
            return
        try:
            item = await self._art_api.get_artmode_settings(self._setting_name)
            if item and isinstance(item, dict):
                value = item.get("value")
                if isinstance(value, str) and value in self._attr_options:
                    self._attr_current_option = value
        except Exception as ex:
            _LOGGER.debug("Could not refresh %s: %s", self._setting_name, ex)


class SamsungTVArtMotionSensitivitySelect(SamsungTVArtMotionSelectBase):
    """Select entity for Art Mode motion sensitivity."""

    _attr_icon = "mdi:motion-sensor"
    _setting_name = "motion_sensitivity"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._attr_unique_id = f"{self._device_unique_id}_art_motion_sensitivity"
        self._attr_name = "Motion Sensitivity"

    async def _async_set(self, option: str) -> None:
        await self._art_api.set_motion_sensitivity(option)


class SamsungTVArtMotionTimerSelect(SamsungTVArtMotionSelectBase):
    """Select entity for Art Mode motion timer."""

    _attr_icon = "mdi:timer-outline"
    _setting_name = "motion_timer"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._attr_unique_id = f"{self._device_unique_id}_art_motion_timer"
        self._attr_name = "Motion Timer"

    async def _async_set(self, option: str) -> None:
        await self._art_api.set_motion_timer(option)


class SamsungTVArtBrightnessSensorSelect(SamsungTVArtMotionSelectBase):
    """Select entity for the Art Mode ambient brightness sensor (on/off).

    The TV reports this setting's current value but no valid_values, so the
    options are fixed to off/on (per the firmware, the only accepted values).
    """

    _attr_icon = "mdi:brightness-auto"
    _setting_name = "brightness_sensor_setting"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._attr_unique_id = f"{self._device_unique_id}_art_brightness_sensor"
        self._attr_name = "Brightness Sensor"

    async def _async_set(self, option: str) -> None:
        await self._art_api.set_brightness_sensor_setting(option)


# ══════════════════════════════════════════════════════════════════════════
# Picture Mode select entity (SmartThings)
# ══════════════════════════════════════════════════════════════════════════


class SamsungTVPictureModeSelect(SelectEntity):
    """Select entity for Samsung TV picture mode (Standard, Movie, Dynamic, etc.)."""

    _attr_icon = "mdi:image-filter-hdr"
    _attr_has_entity_name = True
    _attr_should_poll = True

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        device_name: str,
        device_unique_id: str,
        api_key: str,
        device_id: str,
        session,
    ) -> None:
        self.hass = hass
        self._entry = entry
        self._device_name = device_name
        self._device_unique_id = device_unique_id
        self._api_key = api_key
        self._device_id = device_id
        self._session = session

        self._attr_unique_id = f"{device_unique_id}_picture_mode"
        self._attr_name = "Picture Mode"
        self._attr_options: list[str] = []
        self._attr_current_option: str | None = None

        # Map display name -> API id (e.g. "Standard" -> "modeStandard")
        self._mode_map: dict[str, str] = {}
        # Which capability the TV uses
        self._capability: str | None = None
        # Cooldown: skip polls briefly after setting a mode
        self._skip_poll_until: float = 0
        # After a set, remember the desired mode and hold it until the cloud
        # confirms it — SmartThings lags ~30-45s, so a plain short skip lets a
        # stale "old mode" poll revert the selection (issue #116).
        self._pending_mode: str | None = None
        self._pending_until: float = 0

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._device_unique_id)},
            name=self._device_name,
        )

    @property
    def available(self) -> bool:
        """Only available when we have options loaded."""
        return len(self._attr_options) > 0

    def _get_api_key(self) -> str:
        """Get current API key, refreshing from entry data for OAuth.

        The oauth_token is preferred whenever present, regardless of the
        entry's auth_method label: OAuth-created entries can be labeled
        "pat" (the access token doubles as the API key), and after a token
        refresh the oauth_token always holds the current access token.
        """
        entry = self.hass.config_entries.async_get_entry(self._entry.entry_id)
        if entry:
            oauth_token = entry.data.get(CONF_OAUTH_TOKEN)
            if isinstance(oauth_token, dict):
                new_key = oauth_token.get("access_token")
                if new_key:
                    self._api_key = new_key
                    return new_key
            api_key = entry.data.get(CONF_API_KEY)
            if api_key:
                self._api_key = api_key
        return self._api_key

    async def _rest_get_capability_status(self, capability: str) -> dict | None:
        """Fetch capability status via SmartThings REST API."""
        api_key = self._get_api_key()
        url = (
            f"{_API_DEVICES}/{self._device_id}"
            f"/components/main/capabilities/{capability}/status"
        )
        try:
            async with self._session.get(
                url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Accept": "application/json",
                },
            ) as resp:
                if resp.status != 200:
                    return None
                return await resp.json()
        except Exception as ex:
            _LOGGER.debug("Error fetching picture mode capability: %s", ex)
            return None

    async def async_fetch_picture_modes(self) -> None:
        """Fetch picture mode list and current mode from SmartThings REST API."""
        for cap_name in ("samsungvd.pictureMode", "custom.picturemode"):
            data = await self._rest_get_capability_status(cap_name)
            if not data:
                continue

            # Parse supported modes map
            raw_map = data.get("supportedPictureModesMap", {}).get("value")
            if raw_map:
                new_map: dict[str, str] = {}
                for entry in raw_map:
                    if isinstance(entry, dict):
                        m_id = entry.get("id", "")
                        m_name = entry.get("name", m_id)
                    elif isinstance(entry, str):
                        m_id = m_name = entry
                    else:
                        continue
                    if m_id:
                        new_map[m_name] = m_id
                if new_map:
                    self._mode_map = new_map
                    self._attr_options = list(new_map.keys())
                    self._capability = cap_name

            # Fallback: supportedPictureModes (names only)
            if not self._attr_options:
                raw_list = data.get("supportedPictureModes", {}).get("value")
                if raw_list and isinstance(raw_list, list):
                    self._attr_options = raw_list
                    self._capability = cap_name

            # Read current mode
            raw_mode = data.get("pictureMode", {}).get("value")
            if raw_mode and self._attr_options:
                if self._mode_map:
                    reverse = {v: k for k, v in self._mode_map.items()}
                    self._attr_current_option = reverse.get(raw_mode, raw_mode)
                else:
                    self._attr_current_option = raw_mode

                if self.platform is not None:
                    self.async_write_ha_state()

                _LOGGER.debug(
                    "Picture mode: current=%s, options=%s (cap=%s)",
                    self._attr_current_option,
                    self._attr_options,
                    cap_name,
                )
                return

        _LOGGER.debug("No picture mode capability found on this TV")

    async def async_select_option(self, option: str) -> None:
        """Called when user picks a new picture mode.

        Delegates to the samsungtv_smart.select_picture_mode service which
        uses the SmartThingsTV instance (with active OAuth token refresh).
        Direct REST calls from select.py used a potentially stale token.
        """
        # Find the media_player entity for this device
        entity_id = self._get_media_player_entity_id()
        if not entity_id:
            _LOGGER.error(
                "Picture mode: no media_player entity found for %s",
                self._device_name,
            )
            return

        try:
            await self.hass.services.async_call(
                DOMAIN,
                "select_picture_mode",
                {"entity_id": entity_id, "picture_mode": option},
                blocking=True,
            )
            _LOGGER.debug("Picture mode set to %s via service", option)

            self._attr_current_option = option
            self.async_write_ha_state()
            # Short blind skip to let the TV apply the change, then hold the
            # desired value until SmartThings actually reports it (cloud lag
            # can be 30-45s). Without the hold, a poll in between reads the OLD
            # mode and reverts the selection (issue #116).
            now = time.time()
            self._skip_poll_until = now + 5
            self._pending_mode = option
            self._pending_until = now + 60

        except Exception as ex:
            _LOGGER.error("Error setting picture mode to %s: %s", option, ex)

    def _get_media_player_entity_id(self) -> str | None:
        """Find the media_player entity for this TV."""
        from homeassistant.helpers import entity_registry as er

        registry = er.async_get(self.hass)
        for entity in registry.entities.get_entries_for_config_entry_id(
            self._entry.entry_id
        ):
            if entity.domain == "media_player":
                return entity.entity_id
        return None

    async def async_update(self) -> None:
        """Poll current picture mode from SmartThings."""
        if not self._capability:
            return

        # Skip polling briefly after a mode change to let the TV apply it
        if time.time() < self._skip_poll_until:
            return

        data = await self._rest_get_capability_status(self._capability)
        if not data:
            return

        raw_mode = data.get("pictureMode", {}).get("value")
        if not raw_mode:
            return

        if self._mode_map:
            reverse = {v: k for k, v in self._mode_map.items()}
            new_mode = reverse.get(raw_mode, raw_mode)
        else:
            new_mode = raw_mode

        # If the TV reports a mode we don't know about (e.g. Filmmaker),
        # add it dynamically to options and map
        if new_mode not in self._attr_options:
            _LOGGER.info(
                "Picture mode: discovered new mode '%s' (raw: %s), adding to options",
                new_mode,
                raw_mode,
            )
            self._attr_options = [*self._attr_options, new_mode]
            if raw_mode != new_mode:
                self._mode_map[new_mode] = raw_mode

        # Hold the just-set mode until the cloud confirms it. While a set is
        # pending: if the cloud now reports our desired mode, it's confirmed;
        # if it still reports the old value (cloud lag), keep our selection
        # instead of reverting. After the grace window we accept whatever the
        # cloud says (covers a genuine change made on the TV itself).
        if self._pending_mode is not None:
            if new_mode == self._pending_mode:
                self._pending_mode = None
                self._pending_until = 0
            elif time.time() < self._pending_until:
                _LOGGER.debug(
                    "Picture mode: cloud still reports %s, holding pending %s",
                    new_mode,
                    self._pending_mode,
                )
                return
            else:
                # Grace expired without confirmation — give up on the pending
                # value and accept the cloud's reading below.
                self._pending_mode = None
                self._pending_until = 0

        if new_mode != self._attr_current_option:
            _LOGGER.debug(
                "Picture mode updated: %s -> %s", self._attr_current_option, new_mode
            )
            self._attr_current_option = new_mode
