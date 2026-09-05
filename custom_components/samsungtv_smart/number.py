"""Samsung TV Smart - Number entities.

Provides interactive Number entities (sliders in HA UI) for:
- IP Control picture backlight (0-50)
- Art Mode brightness (0-100)
- Art Mode color temperature (-5 to +5)

Art Mode numbers wrap the existing art_api methods and only appear on Frame TVs.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timedelta
import logging

from homeassistant.components.number import NumberEntity, NumberMode, RestoreNumber
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_ID, CONF_NAME, CONF_PORT, CONF_TOKEN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)

from .api.art import SamsungTVAsyncArt
from .api.ipcontrol import (
    SamsungIPControl,
    SamsungIPControlAuthError,
    SamsungIPControlError,
    SamsungIPControlModeLockedError,
    SamsungIPControlTransportError,
    SamsungIPControlUnsupportedError,
)
from .const import (
    CONF_ENABLE_IP_CONTROL,
    CONF_IP_CONTROL_TOKEN,
    CONF_IS_FRAME_TV,
    CONF_WS_NAME,
    DATA_ART_API,
    DATA_CFG,
    DEFAULT_PORT,
    DOMAIN,
    WS_PREFIX,
    ip_control_port,
)
from .token_notify import METHOD_IP_CONTROL, clear_token_problem, notify_token_problem

_LOGGER = logging.getLogger(__name__)


def _ip_control_active(entry: ConfigEntry) -> bool:
    """True when IP Control is paired AND enabled in the options."""
    return bool(entry.data.get(CONF_IP_CONTROL_TOKEN)) and entry.options.get(
        CONF_ENABLE_IP_CONTROL, True
    )


def _tv_normal_viewing(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """True when the TV is on for normal viewing (not off, not Art Mode).

    Guardrail for the picture-calibration controls: contrast/brightness/… only
    apply to normal viewing. In Art Mode the panel has its own Art Mode
    Brightness / Color Temperature, and the IP Control picture methods answer
    -32601 there; when off there is nothing to read. Rather than probe the TV
    and interpret errors, gate on the media_player's own state — it is fed by
    the WebSocket / SmartThings and is authoritative regardless of whether IP
    Control art-mode reads are enabled (they are off on some 2024 Frames).

    Scans every media_player of the entry and returns True if ANY is in normal
    viewing, so a stale/duplicate "unavailable" media_player entity can't veto a
    TV that is actually on.
    """
    try:
        ent_reg = er.async_get(hass)
        for ent in ent_reg.entities.get_entries_for_config_entry_id(entry.entry_id):
            if ent.domain != "media_player":
                continue
            state = hass.states.get(ent.entity_id)
            if state is None or state.state in ("off", "unavailable", "unknown"):
                continue
            if state.attributes.get("art_mode_status") == "on":
                continue
            return True
    except Exception:  # pylint: disable=broad-except
        return False
    return False


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Samsung TV number entities from config entry."""
    config = hass.data[DOMAIN][entry.entry_id][DATA_CFG]
    host = config[CONF_HOST]
    port = config.get(CONF_PORT, DEFAULT_PORT)
    token = config.get(CONF_TOKEN)
    ws_name = config.get(CONF_WS_NAME, "HomeAssistant")
    device_unique_id = config.get(CONF_ID, entry.entry_id)
    device_name = config.get(CONF_NAME) or entry.title or host

    if _ip_control_active(entry):
        # Backlight self-polls (single value, its own <field>Control getter that
        # works on all models); add it with update-before-add.
        async_add_entities(
            [
                SamsungTVIPControlBacklightNumber(
                    hass, entry, host, device_name, device_unique_id
                )
            ],
            True,
        )
        # The five expert picture sliders share one getVideoStates coordinator
        # so they never open concurrent connections to port 1516.
        video_coordinator = IPControlVideoCoordinator(hass, entry, host)
        async_add_entities(
            SamsungTVIPControlPictureNumber(
                video_coordinator, entry, host, device_name, device_unique_id, setting
            )
            for setting in IP_CONTROL_PICTURE_SETTINGS
        )
        hass.async_create_background_task(
            video_coordinator.async_request_refresh(),
            f"ip_control_video_initial_refresh_{entry.entry_id}",
        )
        _LOGGER.debug(
            "IP Control number entities created for %s (backlight + %d picture "
            "settings)",
            device_name,
            len(IP_CONTROL_PICTURE_SETTINGS),
        )

    session = async_get_clientsession(hass)

    # Reuse shared art_api if already created by sensor.py / select.py,
    # otherwise create one (same pattern as select.py).
    art_api = hass.data[DOMAIN][entry.entry_id].get(DATA_ART_API)
    if not art_api:
        art_api = SamsungTVAsyncArt(
            host=host,
            port=port,
            token=token,
            session=session,
            timeout=5,
            name=f"{WS_PREFIX} {ws_name} Art Number",
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

    if not is_frame_supported:
        _LOGGER.debug(
            "Not a Frame TV, skipping art number entities for %s", device_name
        )
        return

    entities: list[RestoreNumber] = [
        SamsungTVArtBrightnessNumber(
            hass, entry, art_api, device_name, device_unique_id
        ),
        SamsungTVArtColorTemperatureNumber(
            hass, entry, art_api, device_name, device_unique_id
        ),
    ]
    async_add_entities(entities)
    _LOGGER.debug(
        "Frame Art number entities (brightness, color temperature) created for %s",
        device_name,
    )


# ══════════════════════════════════════════════════════════════════════════
# IP Control Backlight (0-50, native TV scale)
# ══════════════════════════════════════════════════════════════════════════


class SamsungTVIPControlBacklightNumber(NumberEntity):
    """Number entity for the IP Control picture backlight value."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:brightness-6"
    _attr_mode = NumberMode.SLIDER
    _attr_native_min_value = 0
    _attr_native_max_value = 50
    _attr_native_step = 1

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        host: str,
        device_name: str,
        device_unique_id: str,
    ) -> None:
        """Initialize the IP Control backlight number."""
        self.hass = hass
        self._entry_id = entry.entry_id
        self._host = host
        self._device_name = device_name
        self._device_unique_id = device_unique_id
        self._ip_control: SamsungIPControl | None = None
        self._ip_control_token: str | None = None
        self._attr_unique_id = f"{device_unique_id}_ip_control_backlight"
        self._attr_name = "Backlight"
        self._attr_native_value: float | None = None
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
            self._ip_control = SamsungIPControl(
                self.hass,
                self._host,
                port=ip_control_port(entry.data),
                token=token,
            )
            self._ip_control_token = token
        return self._ip_control

    async def async_set_native_value(self, value: float) -> None:
        """Set picture backlight through IP Control."""
        backlight = int(value)
        if backlight < 0 or backlight > 50:
            raise HomeAssistantError("Backlight must be between 0 and 50.")

        client = self._get_ip_control()
        if client is None:
            raise HomeAssistantError(
                "IP Control is not paired or is disabled for this TV."
            )

        try:
            self._attr_native_value = await client.async_set_backlight(backlight)
            self._attr_available = True
        except SamsungIPControlAuthError as ex:
            self._mark_unavailable()
            notify_token_problem(
                self.hass, self._entry_id, METHOD_IP_CONTROL, self._device_title()
            )
            raise HomeAssistantError(
                f"IP Control token rejected while setting backlight: {ex}"
            ) from ex
        except SamsungIPControlError as ex:
            self._mark_unavailable()
            raise HomeAssistantError(
                f"Failed to set backlight via IP Control: {ex}"
            ) from ex

        clear_token_problem(self.hass, self._entry_id, METHOD_IP_CONTROL)
        self.async_write_ha_state()

    async def async_update(self) -> None:
        """Read current picture backlight from IP Control."""
        # Guardrail, same as the coordinator-backed picture sliders: backlight
        # applies to normal viewing only and answers -32601 in Art Mode, so a
        # read while the TV is off or showing art is pointless. It is also
        # expensive: on a TV that has left the network the connection attempt
        # costs CMD_TIMEOUT plus the weak-DH retry (~7.8 s measured), and this
        # entity polls independently of the video coordinator, taking the
        # per-host lock on its own. Two such entities per TV queued behind each
        # other is what pushed single updates past Home Assistant's 10 s
        # per-entity threshold — 1 626 core warnings in 21 h on a 13-TV install
        # (#248). The coordinator-backed sliders never produced one, because
        # the coordinator returns early on exactly this condition.
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if entry is None or not _tv_normal_viewing(self.hass, entry):
            self._mark_unavailable()
            return

        client = self._get_ip_control()
        if client is None:
            self._mark_unavailable()
            return

        try:
            self._attr_native_value = await client.async_get_backlight()
            self._attr_available = True
        except SamsungIPControlAuthError as ex:
            _LOGGER.warning(
                "IP Control backlight read for %s: token rejected (%s) — "
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
                "Could not refresh IP Control backlight for %s: %s", self._host, ex
            )
            self._mark_unavailable()
        else:
            clear_token_problem(self.hass, self._entry_id, METHOD_IP_CONTROL)

    def _mark_unavailable(self) -> None:
        """Expose no slider value until a live IP Control read succeeds."""
        self._attr_available = False
        self._attr_native_value = None


# ══════════════════════════════════════════════════════════════════════════
# IP Control expert picture settings (contrast / brightness / sharpness /
# color / tint) — writable via their <field>Control methods. Ranges verified
# empirically on Frame 2024 (QE55LS03D) / 2025 (GQ50LS03F); see
# notes/QN55LS03FAFXZA/IPCONTROL_DECOMPILED.md. The write is picture-mode
# gated (Dynamic/HDR-dynamic reject it with -32002 → clear HA error).
# ══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class IPControlPictureSetting:
    """Describes one <field>Control expert picture setting."""

    key: str
    field: str
    method: str
    name: str
    icon: str
    min_value: int
    max_value: int


IP_CONTROL_PICTURE_SETTINGS: tuple[IPControlPictureSetting, ...] = (
    IPControlPictureSetting(
        "contrast", "contrast", "contrastControl", "Contrast", "mdi:contrast-box", 0, 50
    ),
    IPControlPictureSetting(
        "brightness",
        "brightness",
        "brightnessControl",
        "Brightness",
        "mdi:brightness-6",
        -5,
        5,
    ),
    IPControlPictureSetting(
        "sharpness", "sharpness", "sharpnessControl", "Sharpness", "mdi:blur", 0, 20
    ),
    IPControlPictureSetting(
        "color", "color", "colorControl", "Color", "mdi:palette", 0, 50
    ),
    IPControlPictureSetting(
        "tint", "tint", "tintControl", "Tint", "mdi:invert-colors", -15, 15
    ),
)


# One shared getVideoStates poll feeds all five sliders (the TV resets
# overlapping connections, so per-field self-polling flapped their availability).
IP_CONTROL_VIDEO_SCAN_INTERVAL = timedelta(seconds=30)


class IPControlVideoCoordinator(DataUpdateCoordinator):
    """Single getVideoStates poll shared by all picture-setting sliders.

    getVideoStates returns contrast/brightness/sharpness/color/tint in ONE call
    and works on every Frame that speaks IP Control (unlike the per-field
    ``<field>Control`` getters, which some models reject with -32601). Reading
    through this coordinator gives each slider a stable value and availability
    without opening five concurrent connections to port 1516.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        host: str,
    ) -> None:
        """Initialize the video-state coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=f"IP Control video {entry.title}",
            update_interval=IP_CONTROL_VIDEO_SCAN_INTERVAL,
        )
        self._entry = entry
        self._host = host
        self._ip_control: SamsungIPControl | None = None
        self._ip_control_token: str | None = None
        # Which fields the TV last reported, so a change is logged once instead
        # of every poll. Without this the payload was never recorded anywhere,
        # and a slider sitting "unavailable" gave no way to tell whether the TV
        # omitted the field or returned it empty (#206).
        self._logged_fields: tuple[str, ...] | None = None

    def _device_title(self) -> str:
        entry = self.hass.config_entries.async_get_entry(self._entry.entry_id)
        return entry.title if entry else (self._host or "this Samsung TV")

    def get_ip_control(self) -> SamsungIPControl | None:
        """Return a live IP Control client if paired AND enabled (shared)."""
        entry = self.hass.config_entries.async_get_entry(self._entry.entry_id)
        if entry is None or not _ip_control_active(entry):
            self._ip_control = None
            self._ip_control_token = None
            return None
        token = entry.data.get(CONF_IP_CONTROL_TOKEN)
        if self._ip_control is None or self._ip_control_token != token:
            self._ip_control = SamsungIPControl(
                self.hass,
                self._host,
                port=ip_control_port(entry.data),
                token=token,
            )
            self._ip_control_token = token
        return self._ip_control

    async def _async_update_data(self) -> dict:
        """Fetch the getVideoStates snapshot over IP Control."""
        # Guardrail: picture calibration only applies to normal viewing. Skip
        # the poll entirely while the TV is off or in Art Mode (the getters
        # answer -32601 there) — the sliders go unavailable and recover when the
        # TV is on again, without probing the TV or interpreting errors.
        if not _tv_normal_viewing(self.hass, self._entry):
            return {}
        client = self.get_ip_control()
        if client is None:
            raise UpdateFailed("IP Control is not paired or is disabled")
        try:
            data = await client.async_get_video_states()
        except SamsungIPControlAuthError as ex:
            notify_token_problem(
                self.hass, self._entry.entry_id, METHOD_IP_CONTROL, self._device_title()
            )
            raise UpdateFailed(f"IP Control token rejected: {ex}") from ex
        except (SamsungIPControlTransportError, SamsungIPControlUnsupportedError):
            # Transport failure (TV dropped off the network) or a residual
            # -32601 (state changed mid-poll): expected, not a failure — go
            # unavailable without an ERROR and recover on the next cycle.
            return {}
        except SamsungIPControlError as ex:
            raise UpdateFailed(f"IP Control video read failed: {ex}") from ex

        # Full read succeeded → the token is good; clear any prior problem.
        clear_token_problem(self.hass, self._entry.entry_id, METHOD_IP_CONTROL)
        fields = tuple(sorted(data))
        if fields != self._logged_fields:
            missing = [
                setting.field
                for setting in IP_CONTROL_PICTURE_SETTINGS
                if setting.field not in data
            ]
            # A field can be present and still leave its slider unavailable:
            # the entity needs an int, and Samsung documents tint as an
            # "R15"/"G15" token on some generations rather than a number. That
            # case used to be indistinguishable from an absent field.
            unparseable = []
            for setting in IP_CONTROL_PICTURE_SETTINGS:
                if setting.field not in data:
                    continue
                try:
                    int(data[setting.field])
                except (TypeError, ValueError):
                    unparseable.append(f"{setting.field}={data[setting.field]!r}")
            _LOGGER.debug(
                "IP Control getVideoStates on %s returned %s "
                "(absent: %s | present but not an integer: %s)",
                self._host,
                data,
                ", ".join(missing) or "nothing",
                ", ".join(unparseable) or "nothing",
            )
            self._logged_fields = fields
        return data


class SamsungTVIPControlPictureNumber(CoordinatorEntity, NumberEntity):
    """Settable slider for one IP Control expert picture setting.

    Reads its value from the shared getVideoStates coordinator; writes through
    the per-field ``<field>Control`` setter. A TV that doesn't implement the
    setter (-32601) surfaces a clear "unsupported" error instead of flapping.
    """

    _attr_has_entity_name = True
    _attr_mode = NumberMode.SLIDER
    _attr_native_step = 1

    def __init__(
        self,
        coordinator: IPControlVideoCoordinator,
        entry: ConfigEntry,
        host: str,
        device_name: str,
        device_unique_id: str,
        setting: IPControlPictureSetting,
    ) -> None:
        """Initialize the expert picture setting number."""
        super().__init__(coordinator)
        self._entry_id = entry.entry_id
        self._host = host
        self._device_name = device_name
        self._device_unique_id = device_unique_id
        self._setting = setting
        self._attr_unique_id = f"{device_unique_id}_ip_control_{setting.key}"
        self._attr_name = setting.name
        self._attr_icon = setting.icon
        self._attr_native_min_value = setting.min_value
        self._attr_native_max_value = setting.max_value

    @property
    def device_info(self) -> DeviceInfo:
        """Link this entity to the TV device."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._device_unique_id)},
            name=self._device_name,
        )

    @property
    def native_value(self) -> float | None:
        """Return this setting's current value from the shared snapshot."""
        raw = (self.coordinator.data or {}).get(self._setting.field)
        try:
            return None if raw is None else int(raw)
        except (TypeError, ValueError):
            return None

    @property
    def available(self) -> bool:
        """Available while IP Control is active and a live value is present."""
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        return bool(
            entry
            and _ip_control_active(entry)
            and self.coordinator.last_update_success
            and self.native_value is not None
        )

    def _device_title(self) -> str:
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        return entry.title if entry else (self._device_name or "this Samsung TV")

    async def async_set_native_value(self, value: float) -> None:
        """Set the picture setting through IP Control."""
        setting = self._setting
        target = int(value)
        if target < setting.min_value or target > setting.max_value:
            raise HomeAssistantError(
                f"{setting.name} must be between "
                f"{setting.min_value} and {setting.max_value}."
            )
        # Guardrail: the picture methods only work in normal viewing. Don't even
        # hit the TV while it is off or in Art Mode — give a clear message.
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if entry is None or not _tv_normal_viewing(self.hass, entry):
            raise HomeAssistantError(
                f"Cannot set {setting.name}: the TV must be on and out of Art "
                "Mode. Picture settings apply to normal viewing only."
            )

        client = self.coordinator.get_ip_control()
        if client is None:
            raise HomeAssistantError(
                "IP Control is not paired or is disabled for this TV."
            )

        try:
            echoed = await client.async_set_video_setting(
                setting.method, setting.field, target
            )
        except SamsungIPControlModeLockedError as ex:
            # Rejected by the current picture mode; leave the slider where it is
            # and surface a clear, actionable message.
            raise HomeAssistantError(
                f"Cannot set {setting.name}: the TV's current picture mode "
                "blocks it. Switch to Standard, Movie or Filmmaker mode and "
                "retry."
            ) from ex
        except SamsungIPControlUnsupportedError as ex:
            # -32601 is ambiguous: the method may not exist on this model, OR it
            # exists but isn't available in the current state — most commonly
            # because the TV is in Art Mode, where picture calibration doesn't
            # apply. Do NOT latch it off (it may work once out of Art Mode);
            # just surface a clear, actionable message.
            raise HomeAssistantError(
                f"Cannot set {setting.name} right now: this control isn't "
                "available in the TV's current state (it applies to normal "
                "viewing, not Art Mode) or isn't supported on this model."
            ) from ex
        except SamsungIPControlAuthError as ex:
            notify_token_problem(
                self.hass, self._entry_id, METHOD_IP_CONTROL, self._device_title()
            )
            raise HomeAssistantError(
                f"IP Control token rejected while setting {setting.name}: {ex}"
            ) from ex
        except SamsungIPControlError as ex:
            raise HomeAssistantError(
                f"Failed to set {setting.name} via IP Control: {ex}"
            ) from ex

        clear_token_problem(self.hass, self._entry_id, METHOD_IP_CONTROL)
        # Reflect the accepted value on all sliders at once, without an extra TV
        # round-trip. This also resets the poll timer, so a rapid burst of sets
        # isn't swallowed by the request-refresh cooldown; the next scheduled
        # getVideoStates re-syncs from the TV.
        data = dict(self.coordinator.data or {})
        data[setting.field] = echoed
        self.coordinator.async_set_updated_data(data)


# ══════════════════════════════════════════════════════════════════════════
# Base class
# ══════════════════════════════════════════════════════════════════════════


class SamsungTVArtNumberBase(RestoreNumber):
    """Base class for Samsung Frame TV Art Mode number entities."""

    _attr_has_entity_name = True
    _attr_mode = NumberMode.SLIDER

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
        self._attr_native_value: float | None = None
        self._media_player_entity_id: str | None = None

    async def async_added_to_hass(self) -> None:
        """Restore the last known value after a restart.

        Art Mode brightness/color temperature can only be read while the TV is
        actually in Art Mode; in standby the read is skipped and the value would
        otherwise be "unknown" until the TV next enters Art Mode. Restoring the
        last stored value keeps the slider showing the most recent value across
        restarts; a live poll refreshes it once the TV is back in Art Mode.
        """
        await super().async_added_to_hass()
        last_data = await self.async_get_last_number_data()
        if last_data is not None and last_data.native_value is not None:
            self._attr_native_value = last_data.native_value

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._device_unique_id)},
            name=self._device_name,
        )

    @property
    def available(self) -> bool:
        """Only available while the panel is actually displaying Art Mode.

        Brightness / color temperature only apply to the art display; outside
        Art Mode the TV either can't answer or answers with bogus defaults
        (observed: a standby read returning brightness=10 right after a
        reload, showing a phantom 100 %). Going unavailable both blocks the
        dead slider in the UI and keeps the history honest.
        """
        return self._is_tv_in_art_mode()

    def _is_tv_in_art_mode(self) -> bool:
        """Return True only if the TV is actually displaying Art Mode.

        Checks the HA state of the media_player entity for this config entry
        (avoids hitting the WebSocket Art API when the TV is off/standby),
        CROSS-CHECKED against the Frame Art sensor: right after a reload the
        media_player can briefly restore a stale art_mode_status='on' while
        the art coordinator already knows the TV is powered off — that stale
        window let a bogus standby read (brightness=10 → phantom 100 %)
        through. The sensor state must not contradict the attribute.
        """
        if self._media_player_entity_id is None:
            entity_registry = er.async_get(self.hass)
            for entity in entity_registry.entities.values():
                if (
                    entity.config_entry_id == self._entry.entry_id
                    and entity.domain == "media_player"
                ):
                    self._media_player_entity_id = entity.entity_id
                    break

        if not self._media_player_entity_id:
            return False

        state = self.hass.states.get(self._media_player_entity_id)
        if state is None:
            return False

        # Art Mode brightness/color temp are only meaningful (and readable)
        # when the TV is actually in Art Mode. In this integration a Frame TV
        # displaying art reports media_player state "off" with the
        # "art_mode_status" attribute set to "on" (HA convention) — so the
        # attribute is the authoritative signal, checked BEFORE the state.
        if state.attributes.get("art_mode_status") == "on":
            return not self._frame_art_sensor_says_off()

        if state.state in ("off", "unavailable", "unknown"):
            return False

        # TV is on (normal viewing) — art values are not readable, but allow
        # polling when the attribute is absent entirely (e.g. older firmware
        # or attribute not yet populated) so we don't go permanently blind.
        return "art_mode_status" not in state.attributes

    def _frame_art_sensor_says_off(self) -> bool:
        """True when the Frame Art sensor explicitly reports art NOT displaying.

        Used only as a veto: when the sensor is missing or unavailable it
        returns False (no veto), so TVs without the sensor keep the
        media_player-based behaviour.
        """
        registry = er.async_get(self.hass)
        sensor_id = registry.async_get_entity_id(
            "sensor", DOMAIN, f"{self._entry.entry_id}_frame_art"
        )
        if not sensor_id:
            return False
        state = self.hass.states.get(sensor_id)
        if state is None or state.state in ("unavailable", "unknown"):
            return False
        return state.state != "on"


# ══════════════════════════════════════════════════════════════════════════
# Brightness (0-100, mapped to TV's 1-10 scale)
# ══════════════════════════════════════════════════════════════════════════


class SamsungTVArtBrightnessNumber(SamsungTVArtNumberBase):
    """Number entity for Art Mode brightness (0-100, mapped to TV 1-10)."""

    _attr_icon = "mdi:brightness-6"
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 10
    _attr_native_unit_of_measurement = "%"

    def __init__(self, hass, entry, art_api, device_name, device_unique_id):
        super().__init__(hass, entry, art_api, device_name, device_unique_id)
        self._attr_unique_id = f"{device_unique_id}_art_brightness"
        self._attr_name = "Art Mode Brightness"

    async def async_set_native_value(self, value: float) -> None:
        """Set brightness on the TV.

        Accepts 0-100 from UI, converts to TV's 1-10 scale.
        """
        ui_brightness = int(value)
        if ui_brightness == 0:
            tv_brightness = 0
        else:
            tv_brightness = max(1, min(10, round(ui_brightness / 10)))
        try:
            _LOGGER.debug(
                "Frame Art: Setting brightness UI=%d -> TV=%d",
                ui_brightness,
                tv_brightness,
            )
            await self._art_api.set_brightness(tv_brightness)
            self._attr_native_value = ui_brightness
            self.async_write_ha_state()
        except Exception as ex:
            _LOGGER.error("Error setting art mode brightness: %s", ex)

    async def async_update(self) -> None:
        """Read current brightness from TV (TV scale 1-10) and convert to 0-100."""
        if not self._is_tv_in_art_mode():
            return
        try:
            async with asyncio.timeout(5):
                result = await self._art_api.get_brightness()
            if result is not None:
                # API may return a dict {"value": N} or a raw int depending on path
                tv_value = result.get("value") if isinstance(result, dict) else result
                if tv_value is not None:
                    self._attr_native_value = int(tv_value) * 10
        except Exception as ex:
            _LOGGER.debug("Could not refresh art mode brightness: %s", ex)


# ══════════════════════════════════════════════════════════════════════════
# Color Temperature (-5 to +5, native TV scale)
# ══════════════════════════════════════════════════════════════════════════


class SamsungTVArtColorTemperatureNumber(SamsungTVArtNumberBase):
    """Number entity for Art Mode color temperature (-5 = warm to +5 = cool)."""

    _attr_icon = "mdi:thermometer"
    _attr_native_min_value = -5
    _attr_native_max_value = 5
    _attr_native_step = 1

    def __init__(self, hass, entry, art_api, device_name, device_unique_id):
        super().__init__(hass, entry, art_api, device_name, device_unique_id)
        self._attr_unique_id = f"{device_unique_id}_art_color_temperature"
        self._attr_name = "Art Mode Color Temperature"

    async def async_set_native_value(self, value: float) -> None:
        """Set color temperature on the TV (-5 to +5)."""
        ct_value = int(value)
        try:
            _LOGGER.debug("Frame Art: Setting color temperature to %d", ct_value)
            await self._art_api.set_color_temperature(ct_value)
            self._attr_native_value = ct_value
            self.async_write_ha_state()
        except Exception as ex:
            _LOGGER.error("Error setting art mode color temperature: %s", ex)

    async def async_update(self) -> None:
        """Read current color temperature from TV."""
        if not self._is_tv_in_art_mode():
            return
        try:
            async with asyncio.timeout(5):
                result = await self._art_api.get_color_temperature()
            if result is not None:
                tv_value = result.get("value") if isinstance(result, dict) else result
                if tv_value is not None:
                    self._attr_native_value = int(tv_value)
        except Exception as ex:
            _LOGGER.debug("Could not refresh art mode color temperature: %s", ex)
