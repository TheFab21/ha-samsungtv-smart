"""Samsung Frame TV Art Mode sensor entity."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timedelta
import logging
import time
from typing import Any

from pysmartthings import Attribute, Capability

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_HOST,
    CONF_ID,
    CONF_NAME,
    CONF_PORT,
    CONF_TOKEN,
    LIGHT_LUX,
    EntityCategory,
    UnitOfEnergy,
    UnitOfPower,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)

from . import async_get_samsungtv_api_key
from .api.art import SamsungTVAsyncArt, _DeviceLoggerAdapter
from .api.ipcontrol import (
    SamsungIPControl,
    SamsungIPControlAuthError,
    SamsungIPControlError,
    SamsungIPControlModeLockedError,
    SamsungIPControlTransportError,
    SamsungIPControlUnsupportedError,
)
from .const import (
    ART_IDENTIFY_DEBOUNCE,
    CONF_API_KEY,
    CONF_ART_IDENTIFY_ENABLE,
    CONF_DEVICE_ID,
    CONF_ENABLE_IP_CONTROL,
    CONF_IP_CONTROL_POLL_INTERVAL,
    CONF_IP_CONTROL_TOKEN,
    CONF_IS_FRAME_TV,
    CONF_OAUTH_TOKEN,
    CONF_SLIDESHOW_API,
    CONF_ST_POLL_ON_INTERVAL,
    CONF_WS_NAME,
    DATA_ART_API,
    DATA_CFG,
    DATA_IP_CONTROL_STATE_COORDINATOR,
    DEFAULT_IP_CONTROL_POLL_INTERVAL,
    DEFAULT_PORT,
    DEFAULT_ST_POLL_ON_INTERVAL,
    DOMAIN,
    ST_POLL_OFF_INTERVAL,
    TUNER_INPUT_SOURCES,
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


def _ip_control_poll_interval(entry: ConfigEntry) -> int:
    """Configured IP Control poll cadence (seconds) for the state sensors."""
    return entry.options.get(
        CONF_IP_CONTROL_POLL_INTERVAL, DEFAULT_IP_CONTROL_POLL_INTERVAL
    )


def _st_poll_on_interval(entry: ConfigEntry) -> int:
    """Configured SmartThings poll cadence (seconds) while the TV is ON."""
    return entry.options.get(CONF_ST_POLL_ON_INTERVAL, DEFAULT_ST_POLL_ON_INTERVAL)


def _tv_powered_off(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """True when the TV (media_player) is off and NOT showing Art Mode.

    Used to skip SmartThings cloud polls while the TV is off — the local
    WebSocket is the primary power source, so there is nothing to fetch from
    the cloud during standby. A Frame displaying Art also reports state "off"
    (HA convention: off + ``art_mode_status`` attribute), so keep polling in
    that case: an art-displaying Frame still draws power and can change picture
    settings. "unknown"/"unavailable" are NOT treated as off (WS still booting
    or a transient media_player error) to avoid freezing sensors at startup.
    """
    from homeassistant.helpers import entity_registry as er

    try:
        ent_reg = er.async_get(hass)
        for ent in ent_reg.entities.get_entries_for_config_entry_id(entry.entry_id):
            if ent.domain != "media_player":
                continue
            state = hass.states.get(ent.entity_id)
            if state is None:
                return False
            if state.state != "off":
                return False
            return state.attributes.get("art_mode_status") != "on"
    except Exception:  # pylint: disable=broad-except
        return False
    return False


def _tv_in_art_mode(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """True when the TV (media_player) is off but displaying Art Mode.

    A Frame showing Art reports state "off" with ``art_mode_status == "on"``.
    It still draws power, so power/energy stay worth polling — but only slowly,
    since the draw barely changes. Callers use this to fall back to a fixed slow
    cadence instead of the (possibly fast) "when on" interval.
    """
    from homeassistant.helpers import entity_registry as er

    try:
        ent_reg = er.async_get(hass)
        for ent in ent_reg.entities.get_entries_for_config_entry_id(entry.entry_id):
            if ent.domain != "media_player":
                continue
            state = hass.states.get(ent.entity_id)
            if state is None:
                return False
            return (
                state.state == "off" and state.attributes.get("art_mode_status") == "on"
            )
    except Exception:  # pylint: disable=broad-except
        return False
    return False


def _st_child_gate(entity) -> bool:
    """Return True if a per-child ST sensor (illuminance/brightness) may poll now.

    The Frame's ambient LIGHT sensor is active independently of TV power — it
    drives the panel's art auto-dimming and is genuinely useful for room-light
    automations — and SmartThings keeps publishing it while the TV is off. So
    do NOT stop polling in standby (that froze the light sensor at its last
    value / left it unavailable after a restart while the TV was off); instead
    fall back to the slow OFF keepalive cadence. When the TV is on, throttle to
    the configured ST cadence rather than the 15 s module scan interval.
    ``entity`` must expose ``hass``, ``_entry`` and a mutable ``_st_last_poll``.
    """
    if _tv_powered_off(entity.hass, entity._entry):
        interval = ST_POLL_OFF_INTERVAL
    else:
        interval = _st_poll_on_interval(entity._entry)
    now = time.monotonic()
    if now - getattr(entity, "_st_last_poll", 0.0) < interval:
        return False
    entity._st_last_poll = now
    return True


# How long to wait before re-fetching the thumbnail of an Art Store (SAM-*)
# artwork the TV has not cached locally yet. The TV materializes the thumbnail
# a little while after the content is displayed/favorited; this bounded retry
# recovers within seconds of that happening without hammering the art channel
# on every poll.
STORE_THUMBNAIL_RETRY_INTERVAL = 30  # seconds


@dataclass(frozen=True, kw_only=True)
class SamsungIPControlSensorDescription(SensorEntityDescription):
    """Describes an IP Control read-only state sensor.

    ``source`` selects which JSON-RPC snapshot the value is read from:
    ``"tv"`` for ``getTVStates`` or ``"video"`` for ``getVideoStates``.
    """

    source: str = "tv"


# getTVStates fields exposed as diagnostic sensors. Setter availability for
# matching IP Control fields is model-dependent; these remain read-only sensor
# entities even when a corresponding local setter (such as inputSourceControl)
# is available. The getVideoStates fields (contrast/brightness/sharpness/color/
# tint) are NOT here — they are settable `number` sliders (see number.py),
# written via their <field>Control methods when the picture mode allows it.
IP_CONTROL_STATE_SENSORS: tuple[SamsungIPControlSensorDescription, ...] = (
    # getTVStates
    SamsungIPControlSensorDescription(
        key="inputSource",
        source="tv",
        name="Input Source",
        icon="mdi:import",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SamsungIPControlSensorDescription(
        key="pictureMode",
        source="tv",
        name="Picture Mode",
        icon="mdi:image-multiple-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SamsungIPControlSensorDescription(
        key="soundMode",
        source="tv",
        name="Sound Mode",
        icon="mdi:music-note",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SamsungIPControlSensorDescription(
        key="pictureSize",
        source="tv",
        name="Picture Size",
        icon="mdi:aspect-ratio",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    # NOTE: speakerSelect is NOT exposed here — it is a settable `select`
    # entity (SamsungTVIPControlSpeakerSelect / SamsungTVSTMediaOutputSelect in
    # select.py), switched via speakerSelectControl / externalSpeakerControl.
    SamsungIPControlSensorDescription(
        key="mute",
        source="tv",
        name="Mute",
        icon="mdi:volume-mute",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SamsungIPControlSensorDescription(
        key="volume",
        source="tv",
        name="Volume",
        icon="mdi:volume-high",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    # NOTE: the getVideoStates fields (contrast, brightness, sharpness, color,
    # tint) are NOT exposed here as read-only sensors — they are settable
    # `number` sliders in number.py (SamsungTVIPControlPictureNumber), written
    # via their <field>Control methods when the picture mode allows it.
)

# Default scan interval for the plain SmartThings child sensors
# (illuminance / brightness — they self-throttle to the ST cadence anyway).
SCAN_INTERVAL = timedelta(seconds=15)

# The Frame Art coordinator polls the (cheap, local) art WebSocket for the
# current artwork; keep it snappy so a manual/gallery art change shows up fast.
# The heavier get_content_list call is throttled separately
# (CONF_CONTENT_LIST_INTERVAL), so this short cadence stays cheap.
FRAME_ART_SCAN_INTERVAL = timedelta(seconds=5)


async def async_setup_entry(  # noqa: C901
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Samsung Frame Art sensor from config entry."""
    config = hass.data[DOMAIN][entry.entry_id][DATA_CFG]
    host = config[CONF_HOST]
    port = config.get(CONF_PORT, DEFAULT_PORT)
    token = config.get(CONF_TOKEN)
    ws_name = config.get(CONF_WS_NAME, "HomeAssistant")

    # Get device unique ID - must match entity.py logic for device grouping
    device_unique_id = config.get(CONF_ID, entry.entry_id)

    # Get device name from config or entry title, fallback to host
    device_name = config.get(CONF_NAME) or entry.title or host

    session = async_get_clientsession(hass)

    # Clean up registry leftovers of the read-only IP Control sensors that were
    # replaced by settable entities (contrast/brightness/sharpness/color/tint →
    # number sliders in 8.3.0b8; speakerSelect → select in 8.3.0b13). A removed
    # entity otherwise lingers in the registry forever as a "restored" /
    # unavailable ghost next to its working replacement.
    from homeassistant.helpers import entity_registry as er

    _replaced_sensor_keys = (
        "contrast",
        "brightness",
        "sharpness",
        "color",
        "tint",
        "speakerSelect",
    )
    ent_reg = er.async_get(hass)
    for _key in _replaced_sensor_keys:
        _stale_unique_id = f"{device_unique_id}_ip_control_{_key}"
        if _stale_id := ent_reg.async_get_entity_id("sensor", DOMAIN, _stale_unique_id):
            ent_reg.async_remove(_stale_id)
            _LOGGER.debug(
                "Removed stale registry entry %s (replaced by a settable entity)",
                _stale_id,
            )

    entities = []

    # Reuse the shared Art API instance (created in __init__.py) so all
    # platforms talk over a single art-app WebSocket; the TV misbehaves with
    # multiple clients on that channel. Create one only as a fallback.
    art_api = hass.data[DOMAIN][entry.entry_id].get(DATA_ART_API)
    if not art_api:
        art_api = SamsungTVAsyncArt(
            host=host,
            port=port,
            token=token,
            session=session,
            timeout=5,
            name=f"{WS_PREFIX} {ws_name} Art",
        )

    # Check Frame TV support:
    # If already confirmed as a Frame TV (persisted flag), skip the live check.
    # This prevents Art Mode entities from disappearing after a reload when
    # the TV is off or in Art Mode (and thus unreachable via WebSocket).
    is_frame_tv_cached = entry.data.get(CONF_IS_FRAME_TV, False)

    if is_frame_tv_cached:
        _LOGGER.debug(
            "Frame TV flag found in entry data for %s, skipping live check", host
        )
        is_supported = True
    else:
        # First time: probe the TV live to detect Frame capability
        try:
            async with asyncio.timeout(5):
                is_supported = await art_api.supported()
        except asyncio.TimeoutError:
            _LOGGER.debug("Timeout checking Frame TV support for %s", host)
            is_supported = False
        except Exception as ex:
            _LOGGER.debug("Frame TV support check failed: %s", ex)
            is_supported = False

        if is_supported:
            # Persist the flag so future reloads skip this live check
            hass.config_entries.async_update_entry(
                entry, data={**entry.data, CONF_IS_FRAME_TV: True}
            )
            _LOGGER.info(
                "Frame TV confirmed for %s, persisting flag in entry data", host
            )

    if is_supported:
        # Create www/frame_art/{entry_id} directory if it doesn't exist
        import os

        www_path = hass.config.path("www", "frame_art", entry.entry_id)
        try:
            os.makedirs(www_path, exist_ok=True)
            _LOGGER.debug("Frame Art directory ready: %s", www_path)
        except Exception as ex:
            _LOGGER.warning("Could not create frame_art directory: %s", ex)

        # Store art_api in hass.data for sharing with media_player
        hass.data[DOMAIN][entry.entry_id][DATA_ART_API] = art_api

        # Create the coordinator
        coordinator = FrameArtCoordinator(hass, art_api, entry)

        # Refresh immediately on artwork/matte/slideshow/favorite/rotation
        # broadcasts instead of waiting up to the scan interval for the change
        # to be picked up by the next poll. The broadcast is a definitive change,
        # so let that refresh trust a new content_id right away (skip the
        # two-poll confirmation that only exists to filter spurious glitches).
        def _on_art_content() -> None:
            coordinator._trust_next_content_id = True
            hass.async_create_task(coordinator.async_request_refresh())

        art_api.register_art_content_callback(_on_art_content)

        # Add Frame Art sensor
        entities.append(
            FrameArtSensor(coordinator, entry, art_api, device_name, device_unique_id)
        )

        # Art metadata sensor (opt-in): auto-identifies each artwork.
        if entry.data.get(CONF_ART_IDENTIFY_ENABLE):
            entities.append(
                FrameArtMetadataSensor(entry, device_name, device_unique_id)
            )
            _LOGGER.debug("Art metadata sensor created for %s", device_name)

        # Add folder sensors for each thumbnail subdirectory.
        # These mirror HA's platform:folder format (file_list, path, bytes…)
        # with a stable unique_id so entity_ids never shuffle on restart.
        for subdir in ("personal", "store", "other"):
            entities.append(
                FrameArtFolderSensor(hass, entry, subdir, device_name, device_unique_id)
            )

        # Schedule first refresh in background (non-blocking)
        hass.async_create_background_task(
            coordinator.async_request_refresh(),
            f"frame_art_initial_refresh_{entry.entry_id}",
        )
    else:
        _LOGGER.info("Frame TV art mode not supported on %s", host)

    # Add SmartThings sensors if SmartThings is configured.
    # Resolve the API key through the shared helper instead of the cached
    # config snapshot: at startup the stored OAuth access token may already
    # be expired (e.g. HA was down for >24h), and this setup runs exactly
    # once — a stale token here means get_device() fails with an auth error
    # and the SmartThings sensors silently never get created. The helper
    # refreshes the token (or waits for an in-flight refresh) first.
    api_key = await async_get_samsungtv_api_key(hass, entry)
    device_id = config.get(CONF_DEVICE_ID)

    if api_key and device_id:
        try:
            # Create SmartThings client for initial setup
            from pysmartthings import SmartThings

            st_client = SmartThings(session=session)
            st_client.authenticate(api_key)

            # Get the main TV device info
            main_device = await st_client.get_device(device_id)
            main_location_id = main_device.location_id
            main_room_id = main_device.room_id

            _LOGGER.debug(
                "Main TV device: %s (location: %s, room: %s)",
                main_device.label,
                main_location_id,
                main_room_id,
            )

            # Power/energy consumption sensors on the TV device itself, when the
            # TV reports the powerConsumptionReport capability over SmartThings.
            try:
                main_status = await st_client.get_device_status(device_id)
                if "powerConsumptionReport" in main_status.get("main", {}):
                    _LOGGER.info("Adding power consumption sensors for %s", device_name)
                    # One shared coordinator = one get_device_status per cycle
                    # for all five fields (was 5× redundant calls), throttled to
                    # the ST cadence and skipped while the TV is off.
                    power_coordinator = SmartThingsPowerCoordinator(
                        hass, entry, session, device_id, device_name
                    )
                    for measure in (
                        "power",
                        "energy",
                        "deltaEnergy",
                        "powerEnergy",
                        "energySaved",
                    ):
                        entities.append(
                            SmartThingsPowerConsumptionSensor(
                                coordinator=power_coordinator,
                                parent_device_id=device_unique_id,
                                measure=measure,
                            )
                        )
                    hass.async_create_background_task(
                        power_coordinator.async_request_refresh(),
                        f"st_power_initial_refresh_{entry.entry_id}",
                    )
            except Exception as ex:
                _LOGGER.debug("Could not check power consumption capability: %s", ex)

            # Get ALL devices in the location
            all_devices = await st_client.get_devices()

            # Find child devices (same location + parent_device_id or same room)
            related_devices = []
            for device in all_devices:
                # Skip the main TV device
                if device.device_id == device_id:
                    continue

                # Decide whether this device belongs to THIS TV.
                # Prefer an explicit parent link: a device is a child only if
                # its parent_device_id points at this TV. Fall back to the
                # room/label heuristic ONLY for devices that have no parent
                # link at all — otherwise, with multiple Frames in the same
                # room, every TV entry would claim every "light sensor" in the
                # room and collide on unique IDs (e.g. two TVs both registering
                # <id>_illuminance for each other's light sensor).
                if device.parent_device_id == device_id:
                    is_child = True
                elif (
                    not device.parent_device_id
                    and device.location_id == main_location_id
                    and main_room_id
                    and device.room_id == main_room_id
                    and "light sensor" in device.label.lower()
                ):
                    is_child = True
                else:
                    is_child = False

                if is_child:
                    _LOGGER.debug(
                        "Found related device: %s (parent: %s, room: %s)",
                        device.label,
                        device.parent_device_id,
                        device.room_id,
                    )
                    related_devices.append(device)

            # Add sensors for related devices with light sensor capabilities
            for device in related_devices:
                try:
                    components = await st_client.get_device_status(device.device_id)

                    # Check for illuminance sensor
                    if (
                        "main" in components
                        and Capability.ILLUMINANCE_MEASUREMENT in components["main"]
                        and Attribute.ILLUMINANCE
                        in components["main"][Capability.ILLUMINANCE_MEASUREMENT]
                    ):
                        _LOGGER.info(
                            "Adding illuminance sensor for %s (child of %s)",
                            device.label,
                            device_name,
                        )
                        entities.append(
                            SmartThingsIlluminanceSensor(
                                hass=hass,
                                entry=entry,
                                session=session,
                                device_id=device.device_id,
                                device_name=device.label,
                                parent_device_id=device_unique_id,
                            )
                        )

                    # Check for brightness intensity sensor
                    if (
                        "main" in components
                        and Capability.RELATIVE_BRIGHTNESS in components["main"]
                        and Attribute.BRIGHTNESS_INTENSITY
                        in components["main"][Capability.RELATIVE_BRIGHTNESS]
                    ):
                        _LOGGER.info(
                            "Adding brightness intensity sensor for %s (child of %s)",
                            device.label,
                            device_name,
                        )
                        entities.append(
                            SmartThingsBrightnessIntensitySensor(
                                hass=hass,
                                entry=entry,
                                session=session,
                                device_id=device.device_id,
                                device_name=device.label,
                                parent_device_id=device_unique_id,
                            )
                        )

                    if "main" not in components or (
                        Capability.ILLUMINANCE_MEASUREMENT not in components["main"]
                        and Capability.RELATIVE_BRIGHTNESS not in components["main"]
                    ):
                        _LOGGER.debug(
                            "Device %s does not have light sensor capabilities",
                            device.label,
                        )
                except Exception as ex:
                    _LOGGER.warning("Error checking device %s: %s", device.label, ex)

            if not related_devices:
                _LOGGER.debug(
                    "No child devices found for %s (device_id: %s)",
                    device_name,
                    device_id,
                )
        except Exception as ex:
            _LOGGER.warning("Could not setup SmartThings sensors: %s", ex)

    # Read-only IP Control state sensors (getTVStates / getVideoStates).
    # Gated behind IP Control being paired AND enabled, sharing a single
    # coordinator so each poll issues just two JSON-RPC calls for all 12.
    if _ip_control_active(entry):
        state_coordinator = IPControlStateCoordinator(hass, entry, host)
        hass.data[DOMAIN][entry.entry_id][
            DATA_IP_CONTROL_STATE_COORDINATOR
        ] = state_coordinator
        entities.extend(
            IPControlStateSensor(
                state_coordinator, entry, description, device_name, device_unique_id
            )
            for description in IP_CONTROL_STATE_SENSORS
        )
        hass.async_create_background_task(
            state_coordinator.async_request_refresh(),
            f"ip_control_state_initial_refresh_{entry.entry_id}",
        )
        _LOGGER.debug(
            "IP Control state sensors created for %s (%d entities)",
            device_name,
            len(IP_CONTROL_STATE_SENSORS),
        )
    else:
        hass.data[DOMAIN][entry.entry_id].pop(DATA_IP_CONTROL_STATE_COORDINATOR, None)

    if entities:
        async_add_entities(entities)


class FrameArtCoordinator(DataUpdateCoordinator):
    """Coordinator for Frame Art data updates."""

    def __init__(
        self,
        hass: HomeAssistant,
        art_api: SamsungTVAsyncArt,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the coordinator."""
        # Mirrors api.art's per-host log prefix so Frame Art lines can be told
        # apart the same way the Art API and media_player logs already are.
        # Passed to super() too so the base coordinator's own messages
        # ("Finished fetching …", "Manually updated …") are prefixed as well.
        self._log = _DeviceLoggerAdapter(_LOGGER, {"host": entry.data.get(CONF_HOST)})
        super().__init__(
            hass,
            self._log,
            name=f"Frame Art {entry.title}",
            update_interval=FRAME_ART_SCAN_INTERVAL,
        )
        self._art_api = art_api
        self._entry = entry
        self._hass = hass
        self._last_content_id: str | None = None
        # Set by the art content-event callback (image_selected / matte / etc.):
        # a WS broadcast is a definitive change, so the next poll may trust a new
        # content_id immediately instead of waiting for the two-poll confirmation
        # that guards against spurious get_current_artwork glitches.
        self._trust_next_content_id = False
        # content_id that the saved current.jpg actually represents. Tracked
        # separately from "does current.jpg exist on disk" so a stale file from
        # a previous artwork cannot masquerade as the current content's
        # thumbnail (which used to block re-fetching for minutes — see
        # _has_thumbnail_for).
        self._current_thumbnail_content_id: str | None = None
        # Art Store (SAM-*) content the TV has not cached yet: remember which
        # content_id is pending and the earliest time to retry, so we recover
        # within seconds of the TV materializing it without re-fetching on
        # every single poll.
        self._store_retry_content_id: str | None = None
        self._store_retry_at: float | None = None
        # Debounce for transient content_id glitches: the TV occasionally
        # reports a spurious one-off content_id from get_current_artwork during
        # matte/select operations (a single poll returning an unrelated id,
        # corrected on the next poll). _confirmed_content_id is the trusted
        # value currently exposed in HA; _pending_content_id is a changed id
        # seen only once, held until a second consecutive poll confirms it.
        self._confirmed_content_id: str | None = None
        self._pending_content_id: str | None = None
        # Enabled by default - thumbnails are fetched for current artwork
        self._thumbnail_fetch_enabled = True
        self._thumbnail_failures = 0
        # Temporary backoff for thumbnail fetch (re-enables automatically)
        self._thumbnail_backoff_until: float | None = None

        # Connection failure tracking to prevent infinite reconnection loops
        self._connection_failures = 0
        self._max_connection_failures = 5
        self._backoff_until: float | None = None

        self._log.info(
            "Frame Art Coordinator initialized with thumbnail fetching enabled"
        )

    async def _async_update_data(self) -> dict[str, Any]:  # noqa: C901
        """Fetch data from the Frame TV."""
        # Check if we're in backoff period (after multiple connection failures)
        if self._backoff_until is not None:
            if time.time() < self._backoff_until:
                # Still in backoff period, skip update. Raise UpdateFailed so
                # last_update_success goes False and the sensor reports HA's
                # real "unavailable" semantics (the entity keeps its previous
                # data), instead of publishing the literal string
                # "unavailable" as a state while claiming to be available.
                remaining = self._backoff_until - time.time()
                self._log.debug(
                    "Frame Art: Skipping update due to connection backoff (%.0fs remaining)",
                    remaining,
                )
                raise UpdateFailed(
                    f"Art channel in connection backoff ({remaining:.0f}s remaining)"
                )
            else:
                # Backoff period expired, reset and try again
                self._log.info("Frame Art: Backoff period expired, resuming updates")
                self._backoff_until = None
                self._connection_failures = 0

        data = {
            "art_mode": None,
            "current_artwork": None,
            "artwork_count": None,
            "slideshow_status": None,
            "api_version": None,
            "current_thumbnail_url": None,
            "tv_powered_off": False,
        }

        # FIRST: Check if TV is powered off
        if self._is_tv_powered_off():
            self._log.debug("Frame Art: TV is powered off, returning minimal data")
            data["tv_powered_off"] = True
            data["art_mode"] = "off"
            # Keep current_thumbnail_url from last known state (for Lovelace display)
            if self._has_current_thumbnail():
                data["current_thumbnail_url"] = (
                    f"/local/frame_art/{self._entry.entry_id}/current.jpg"
                )
            # Keep artwork_count from previous data if available
            if self.data and self.data.get("artwork_count") is not None:
                data["artwork_count"] = self.data["artwork_count"]
            # Keep current_artwork (current_content_id) from previous data for Lovelace
            if self.data and self.data.get("current_artwork"):
                data["current_artwork"] = self.data["current_artwork"]
            # Return immediately with minimal data when TV is off
            return data

        try:
            # First, try to get art_mode from media_player state (more reliable)
            # This avoids double API calls and keeps sensor in sync with media_player
            media_player_art_mode = self._get_media_player_art_mode()
            if media_player_art_mode is not None:
                data["art_mode"] = media_player_art_mode
                self._log.debug(
                    "Frame Art: Using media_player art_mode_status: %s",
                    media_player_art_mode,
                )
            else:
                # Fallback to direct API call if media_player state not available
                try:
                    async with asyncio.timeout(8):
                        art_mode = await self._art_api.get_artmode()
                        data["art_mode"] = art_mode
                        self._log.debug("Frame Art: Direct API art_mode: %s", art_mode)
                except asyncio.TimeoutError:
                    self._log.debug("Timeout getting art mode status")
                except Exception as ex:
                    self._log.debug("Error getting art mode: %s", ex)

            # Get current artwork with timeout
            content_id = None
            try:
                async with asyncio.timeout(8):
                    current = await self._art_api.get_current()
                    if current:
                        raw_content_id = current.get("content_id")
                        content_id = self._confirm_content_id(raw_content_id)
                        if content_id is not None and content_id == raw_content_id:
                            # Trusted reading: expose it.
                            data["current_artwork"] = {
                                "content_id": content_id,
                                "category_id": current.get("category_id"),
                                "matte_id": current.get("matte_id"),
                            }
                        else:
                            # Unconfirmed/transient reading: keep the previously
                            # exposed artwork (already copied into data above) and
                            # don't let this id drive thumbnail logic this cycle.
                            if raw_content_id is not None:
                                self._log.debug(
                                    "Frame Art: ignoring unconfirmed content_id "
                                    "%s this cycle",
                                    raw_content_id,
                                )
                            content_id = None
            except asyncio.TimeoutError:
                self._log.debug("Timeout getting current artwork")
            except Exception as ex:
                self._log.debug("Error getting current artwork: %s", ex)

            # Only fetch thumbnail if:
            # - Thumbnail fetching is not in backoff period
            # - We have a content_id
            # - Content has changed OR we don't have a thumbnail yet
            thumbnail_in_backoff = (
                self._thumbnail_backoff_until is not None
                and time.time() < self._thumbnail_backoff_until
            )
            if thumbnail_in_backoff:
                # Check if backoff expired — reset and re-enable
                pass
            elif self._thumbnail_backoff_until is not None:
                # Backoff expired — reset state and allow fetch
                self._log.info(
                    "Frame Art: Thumbnail fetch backoff expired, resuming automatic fetch"
                )
                self._thumbnail_backoff_until = None
                self._thumbnail_failures = 0

            if not self._thumbnail_fetch_enabled:
                # Automatic fetching disabled via the enable_thumbnail_fetch
                # service — honor the flag (it is also surfaced in the
                # sensor's thumbnail_auto_fetch attribute).
                if content_id:
                    self._log.debug(
                        "Frame Art: Thumbnail auto-fetch disabled, skipping %s",
                        content_id,
                    )
            # Re-fetch when the content changed OR we don't actually hold this
            # content's thumbnail. The check is content-aware (not just "does
            # current.jpg exist"): a stale file from a previous artwork must not
            # block fetching the current one. For Art Store content the TV has
            # not cached yet, a short cooldown throttles the retry so we recover
            # within seconds of it becoming available without polling-hammering.
            need_thumbnail = (
                content_id != self._last_content_id
                or not self._has_thumbnail_for(content_id)
            )
            store_retry_waiting = (
                self._store_retry_content_id == content_id
                and self._store_retry_at is not None
                and time.time() < self._store_retry_at
            )
            if (
                content_id
                and not thumbnail_in_backoff
                and need_thumbnail
                and not store_retry_waiting
            ):
                self._log.info(
                    "Frame Art: Triggering thumbnail fetch for %s (changed: %s, has_thumbnail: %s)",
                    content_id,
                    content_id != self._last_content_id,
                    self._has_thumbnail_for(content_id),
                )
                # Schedule thumbnail fetch as background task (non-blocking)
                self._hass.async_create_background_task(
                    self._fetch_and_save_thumbnail(content_id),
                    f"frame_art_thumbnail_{content_id}",
                )
                self._last_content_id = content_id
            elif content_id and thumbnail_in_backoff:
                self._log.debug(
                    "Frame Art: Thumbnail fetch in backoff (%.0fs remaining), skipping %s",
                    self._thumbnail_backoff_until - time.time(),
                    content_id,
                )
            elif content_id and store_retry_waiting:
                self._log.debug(
                    "Frame Art: Art Store thumbnail %s not cached yet, next "
                    "retry in %.0fs",
                    content_id,
                    self._store_retry_at - time.time(),
                )
            elif content_id:
                self._log.debug(
                    "Frame Art: Skipping thumbnail fetch - same content_id %s, has_thumbnail: %s",
                    content_id,
                    self._has_thumbnail_for(content_id),
                )

            # If we have a saved thumbnail, use it
            if self._has_current_thumbnail():
                data["current_thumbnail_url"] = (
                    f"/local/frame_art/{self._entry.entry_id}/current.jpg"
                )

            # Get artwork count (less frequently, only if art_mode is on)
            if data["art_mode"] == "on":
                try:
                    async with asyncio.timeout(15):
                        artwork_list = await self._art_api.available()
                        data["artwork_count"] = len(artwork_list) if artwork_list else 0
                except asyncio.TimeoutError:
                    self._log.debug("Timeout getting artwork list")
                except Exception as ex:
                    self._log.debug("Error getting artwork list: %s", ex)

            # Get slideshow / auto-rotation status (routed via persisted API).
            # Samsung Frame TVs split this feature across two parallel APIs
            # depending on firmware: some models respond to
            # ``slideshow_status``, others only to ``auto_rotation_status``.
            # We detect once and persist the choice in entry.data so all
            # subsequent reads/writes use the right one.
            active_api = self._entry.data.get(CONF_SLIDESHOW_API)
            if not active_api:
                try:
                    async with asyncio.timeout(8):
                        detected = await self._art_api.detect_slideshow_api()
                    if detected is not None:
                        self._hass.config_entries.async_update_entry(
                            self._entry,
                            data={
                                **self._entry.data,
                                CONF_SLIDESHOW_API: detected,
                            },
                        )
                        active_api = detected
                        self._log.info(
                            "Frame Art: slideshow API detected as %r, "
                            "persisted in entry data",
                            detected,
                        )
                    else:
                        self._log.debug(
                            "Frame Art: slideshow API detection inconclusive "
                            "(neither endpoint responded); will retry next cycle"
                        )
                except asyncio.TimeoutError:
                    self._log.debug("Frame Art: timeout during slideshow API detection")
                except Exception as ex:  # noqa: BLE001
                    self._log.debug(
                        "Frame Art: error during slideshow API detection: %s",
                        ex,
                    )

            try:
                async with asyncio.timeout(8):
                    if active_api == "auto_rotation":
                        slideshow = await self._art_api.get_auto_rotation_status()
                    else:
                        slideshow = await self._art_api.get_slideshow_status()
                    if slideshow:
                        data["slideshow_status"] = slideshow.get("value", "off")
            except asyncio.TimeoutError:
                self._log.debug("Timeout getting slideshow status")
            except Exception as ex:
                self._log.debug("Error getting slideshow status: %s", ex)

        except Exception as ex:
            # Track connection failures to prevent infinite reconnection loops
            error_msg = str(ex).lower()
            is_connection_error = any(
                keyword in error_msg
                for keyword in ["connect", "timeout", "closed", "transport"]
            )

            if is_connection_error:
                self._connection_failures += 1
                self._log.warning(
                    "Frame Art: Connection error (%d/%d): %s",
                    self._connection_failures,
                    self._max_connection_failures,
                    ex,
                )

                # If too many consecutive failures, enter backoff period
                if self._connection_failures >= self._max_connection_failures:
                    # Exponential backoff: 5 minutes, then 15 minutes, then 30 minutes
                    backoff_minutes = min(
                        5
                        * (
                            2
                            ** (
                                self._connection_failures
                                - self._max_connection_failures
                            )
                        ),
                        30,
                    )
                    self._backoff_until = time.time() + (backoff_minutes * 60)
                    self._log.warning(
                        "Frame Art: Too many connection failures (%d), "
                        "entering %d minute backoff period. "
                        "Frame Art sensor will pause updates until backoff expires.",
                        self._connection_failures,
                        backoff_minutes,
                    )
            else:
                self._log.warning("Frame Art: Error updating data: %s", ex)

            # Don't raise just return partial data
        else:
            # Update successful, reset failure counters
            if self._connection_failures > 0:
                self._log.info(
                    "Frame Art: Update successful, resetting failure counter"
                )
                self._connection_failures = 0
            # Also reset thumbnail failures on a successful update cycle
            # (TV is reachable again, timeouts were transient)
            if self._thumbnail_failures > 0 and self._thumbnail_backoff_until is None:
                self._thumbnail_failures = 0

        return data

    def _is_tv_powered_off(self) -> bool:
        """Check if the TV (media_player) is powered off.

        "unknown" is NOT treated as powered off — it means the WebSocket is
        not yet established (TV is booting). Treating it as off would cause
        the coordinator to short-circuit and return art_mode="off" during
        startup, leaving the sensor stuck in an incorrect state until the
        next polling cycle.
        """
        try:
            # Find media_player entity for this config entry
            from homeassistant.helpers import entity_registry as er

            entity_registry = er.async_get(self._hass)

            for entity in entity_registry.entities.values():
                if (
                    entity.config_entry_id == self._entry.entry_id
                    and entity.domain == "media_player"
                ):
                    state = self._hass.states.get(entity.entity_id)
                    if state:
                        # "unknown" = WebSocket not yet connected (booting up).
                        # "unavailable" = the media_player update failed (e.g.
                        # SmartThings cloud error) — not a power statement;
                        # the direct art API fallback will determine the
                        # actual state. Only a real "off" is powered off.
                        if state.state != "off":
                            return False
                        # A Frame TV displaying art ALSO reports state "off"
                        # (HA convention: off + art_mode_status attribute).
                        # Only treat it as powered off when art mode is not
                        # active, mirroring FrameArtModeSwitch._is_tv_on.
                        return state.attributes.get("art_mode_status") != "on"
                    break
        except Exception as ex:
            self._log.debug("Could not check media_player power state: %s", ex)
        return False

    def _get_media_player_art_mode(self) -> str | None:
        """Get art_mode_status from the linked media_player entity."""
        try:
            # Find media_player entity for this config entry
            from homeassistant.helpers import entity_registry as er

            entity_registry = er.async_get(self._hass)

            for entity in entity_registry.entities.values():
                if (
                    entity.config_entry_id == self._entry.entry_id
                    and entity.domain == "media_player"
                ):
                    state = self._hass.states.get(entity.entity_id)
                    if state and state.attributes:
                        art_mode_status = state.attributes.get("art_mode_status")
                        if art_mode_status:
                            return art_mode_status
                    break
        except Exception as ex:
            self._log.debug("Could not get media_player art_mode_status: %s", ex)
        return None

    def _has_current_thumbnail(self) -> bool:
        """Check if current thumbnail file exists."""
        import os

        www_path = self._hass.config.path(
            "www", "frame_art", self._entry.entry_id, "current.jpg"
        )
        return os.path.isfile(www_path)

    def _clear_store_retry(self, content_id: str) -> None:
        """Clear the Art Store retry cooldown once ``content_id`` is resolved."""
        if self._store_retry_content_id == content_id:
            self._store_retry_content_id = None
            self._store_retry_at = None

    def _has_thumbnail_for(self, content_id: str) -> bool:
        """Whether current.jpg exists AND actually represents ``content_id``.

        ``_has_current_thumbnail`` only checks the file on disk, so a leftover
        current.jpg from a previously displayed artwork would otherwise look
        like a valid thumbnail for the new content and suppress the fetch. Gate
        on the tracked content_id so we keep (re)fetching until we genuinely
        hold this artwork's thumbnail.
        """
        return (
            self._current_thumbnail_content_id == content_id
            and self._has_current_thumbnail()
        )

    def _confirm_content_id(self, raw_content_id: str | None) -> str | None:
        """Debounce transient content_id glitches from the TV.

        The Frame occasionally reports a spurious one-off content_id from
        get_current_artwork during matte/select operations (observed: a single
        poll returning an unrelated id such as SAM-F0222 while a different
        artwork is actually displayed, corrected on the very next poll). Require
        a *changed* id to be seen on two consecutive polls before trusting it,
        so one bad reading never flashes in HA or triggers a wasted thumbnail
        fetch.

        Returns the trusted content_id. It equals ``raw_content_id`` only when
        the reading is accepted; otherwise it returns the previously trusted id
        (or None) so callers can tell an accepted reading from a held one.
        """
        if raw_content_id is None:
            # Failed/empty poll: don't disturb the trusted value or pending state.
            return None
        if self._trust_next_content_id:
            # This poll was triggered by a definitive WS art broadcast — trust
            # the reported id immediately, no two-poll confirmation needed.
            self._trust_next_content_id = False
            self._confirmed_content_id = raw_content_id
            self._pending_content_id = None
            return raw_content_id
        if raw_content_id == self._confirmed_content_id:
            # Steady state: clear any half-seen pending change.
            self._pending_content_id = None
            return raw_content_id
        if self._confirmed_content_id is None:
            # First reading after startup: nothing to protect, trust immediately.
            self._confirmed_content_id = raw_content_id
            self._pending_content_id = None
            return raw_content_id
        if raw_content_id == self._pending_content_id:
            # Same new id seen twice in a row: it's real now.
            self._confirmed_content_id = raw_content_id
            self._pending_content_id = None
            return raw_content_id
        # First sighting of a new id: hold it tentatively, keep the trusted one.
        self._log.debug(
            "Frame Art: content_id %s seen once (current trusted: %s); waiting "
            "for confirmation before exposing it",
            raw_content_id,
            self._confirmed_content_id,
        )
        self._pending_content_id = raw_content_id
        return self._confirmed_content_id

    def _create_error_placeholder(self) -> bytes:
        """Create a generic download error placeholder image.

        Thumbnails are never DRM-protected — a 0-byte response is always a
        transient transport failure (TV busy, WebSocket lag, etc.).
        Uses PIL if available for better quality, otherwise creates a basic PNG.
        """
        try:
            from PIL import Image, ImageDraw, ImageFont

            # Create image with PIL
            width, height = 480, 320
            img = Image.new("RGB", (width, height), color=(30, 30, 35))
            draw = ImageDraw.Draw(img)

            # Draw border
            draw.rectangle(
                [10, 10, width - 10, height - 10], outline=(60, 60, 70), width=2
            )

            # Draw warning triangle icon
            icon_x, icon_y = width // 2, height // 2 - 35
            triangle = [
                (icon_x, icon_y - 25),
                (icon_x - 28, icon_y + 22),
                (icon_x + 28, icon_y + 22),
            ]
            draw.polygon(triangle, fill=(80, 80, 90), outline=(180, 160, 90))
            draw.rectangle(
                [icon_x - 2, icon_y - 10, icon_x + 2, icon_y + 8],
                fill=(220, 200, 120),
            )
            draw.rectangle(
                [icon_x - 2, icon_y + 13, icon_x + 2, icon_y + 17],
                fill=(220, 200, 120),
            )

            # Try to use a font, fall back to default
            try:
                font_large = ImageFont.truetype(
                    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 22
                )
                font_small = ImageFont.truetype(
                    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 14
                )
            except Exception:
                font_large = ImageFont.load_default()
                font_small = ImageFont.load_default()

            # Draw text
            text1 = "Oops!"
            text2 = "Something went wrong"
            text3 = "downloading thumbnail"

            # Center text
            bbox1 = draw.textbbox((0, 0), text1, font=font_large)
            bbox2 = draw.textbbox((0, 0), text2, font=font_small)
            bbox3 = draw.textbbox((0, 0), text3, font=font_small)

            x1 = (width - (bbox1[2] - bbox1[0])) // 2
            x2 = (width - (bbox2[2] - bbox2[0])) // 2
            x3 = (width - (bbox3[2] - bbox3[0])) // 2

            draw.text((x1, icon_y + 50), text1, fill=(220, 200, 120), font=font_large)
            draw.text((x2, icon_y + 85), text2, fill=(200, 200, 210), font=font_small)
            draw.text((x3, icon_y + 105), text3, fill=(200, 200, 210), font=font_small)

            # Save to bytes
            import io

            buffer = io.BytesIO()
            img.save(buffer, format="JPEG", quality=85)
            return buffer.getvalue()

        except ImportError:
            # PIL not available, create a simple PNG
            import struct
            import zlib

            width, height = 400, 300

            # PNG signature
            signature = b"\x89PNG\r\n\x1a\n"

            # IHDR chunk
            ihdr_data = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
            ihdr_crc = zlib.crc32(b"IHDR" + ihdr_data) & 0xFFFFFFFF
            ihdr = (
                struct.pack(">I", 13)
                + b"IHDR"
                + ihdr_data
                + struct.pack(">I", ihdr_crc)
            )

            # IDAT chunk - create simple dark image with lighter center
            raw_data = b""
            for y in range(height):
                raw_data += b"\x00"  # filter byte
                for x in range(width):
                    # Dark background
                    gray = 35
                    # Lighter rectangle in center (where text would be)
                    if 80 < x < 320 and 100 < y < 200:
                        gray = 50
                    # Border
                    if x < 5 or x > width - 5 or y < 5 or y > height - 5:
                        gray = 60
                    raw_data += bytes([gray, gray, gray + 5])

            compressed = zlib.compress(raw_data, 9)
            idat_crc = zlib.crc32(b"IDAT" + compressed) & 0xFFFFFFFF
            idat = (
                struct.pack(">I", len(compressed))
                + b"IDAT"
                + compressed
                + struct.pack(">I", idat_crc)
            )

            # IEND chunk
            iend_crc = zlib.crc32(b"IEND") & 0xFFFFFFFF
            iend = struct.pack(">I", 0) + b"IEND" + struct.pack(">I", iend_crc)

            return signature + ihdr + idat + iend

    async def _save_error_placeholder(self, content_id: str) -> None:
        """Save a generic error placeholder when thumbnail fetch fails.

        Thumbnails are never DRM-protected; a failure is always a transient
        transport issue (TV busy, WebSocket lag, Art API returning empty data).
        """
        try:
            import os

            www_path = self._hass.config.path("www", "frame_art", self._entry.entry_id)

            def _write_placeholder():
                os.makedirs(www_path, exist_ok=True)

                placeholder_data = self._create_error_placeholder()

                # Save as current.jpg
                file_path = os.path.join(www_path, "current.jpg")
                with open(file_path, "wb") as f:
                    f.write(placeholder_data)

                # Save error marker file
                error_marker = os.path.join(www_path, "current_error.txt")
                with open(error_marker, "w") as f:
                    f.write(f"Thumbnail download failed: {content_id}\n")

                # Clean up legacy DRM marker from previous versions
                legacy_marker = os.path.join(www_path, "current_drm.txt")
                if os.path.exists(legacy_marker):
                    try:
                        os.remove(legacy_marker)
                    except OSError:
                        pass

                return file_path

            await self._hass.async_add_executor_job(_write_placeholder)
            self._log.debug("Saved error placeholder for %s", content_id)

        except Exception as ex:
            self._log.debug("Error saving error placeholder: %s", ex)

    async def _fetch_and_save_thumbnail(self, content_id: str) -> None:
        """Fetch and save thumbnail in background (non-blocking).

        For personal photos (MY_F*) a 0-byte / failed response is usually a
        transient transport issue (TV busy, slow WebSocket), so retries help.

        For Art Store content (SAM-S*) it is different: the TV only keeps a
        thumbnail in its local cache (``data/download_thumbnail_contents/``)
        once the item has actually been downloaded — i.e. displayed, favorited
        or otherwise materialized. Until then ``get_thumbnail`` returns
        ``SYSTEM_FAIL`` / 0 bytes because there is genuinely nothing to serve.
        That is structural, not transient, so hammering it with retries + an
        error placeholder + a global backoff (the previous behavior) just
        spammed the log for content that was never going to resolve until the
        TV materialized it. Such content is fetched on a single attempt and, on
        failure, left quietly for the next ``image_added`` / ``image_selected``
        broadcast to retrigger.
        """
        import os

        # Fast path: if this artwork's thumbnail was already downloaded in a
        # previous cycle (personal/store/other), promote that local copy to
        # current.jpg straight away and skip the live TV fetch. Downloaded
        # thumbnails don't change, and the live fetch is flaky (SYSTEM_FAIL on
        # some Frame models), so the local copy is both faster and more reliable.
        if await self._restore_current_from_cache(content_id):
            return

        self._log.info("Frame Art: Starting thumbnail fetch for %s", content_id)

        # Art Store (SAM-S*) thumbnails only exist once the TV has materialized
        # the content locally; retrying before that is pointless. Personal
        # photos can fail transiently, so they keep the multi-attempt retry.
        is_store_content = content_id.startswith("SAM-S")
        if is_store_content:
            max_retries = 1
            retry_delays: list[int] = []
        else:
            max_retries = 3
            retry_delays = [1, 2, 5]  # seconds
        thumbnail_data = None
        last_error = None

        for attempt in range(max_retries):
            try:
                async with asyncio.timeout(15):
                    self._log.debug(
                        "Frame Art: get_thumbnail attempt %d/%d for %s",
                        attempt + 1,
                        max_retries,
                        content_id,
                    )
                    data = await self._art_api.get_thumbnail(content_id)
                    received = len(data) if data else 0

                    if data and received > 1:
                        thumbnail_data = data
                        self._log.info(
                            "Frame Art: get_thumbnail returned %d bytes for %s "
                            "(attempt %d/%d)",
                            received,
                            content_id,
                            attempt + 1,
                            max_retries,
                        )
                        break

                    last_error = f"got {received} bytes"
                    self._log.debug(
                        "Frame Art: Empty thumbnail data for %s on attempt %d/%d (%s)",
                        content_id,
                        attempt + 1,
                        max_retries,
                        last_error,
                    )

            except asyncio.TimeoutError:
                last_error = "timeout (15s)"
                self._log.debug(
                    "Frame Art: Timeout on attempt %d/%d for %s",
                    attempt + 1,
                    max_retries,
                    content_id,
                )
            except Exception as ex:
                last_error = str(ex)
                self._log.debug(
                    "Frame Art: Error on attempt %d/%d for %s: %s",
                    attempt + 1,
                    max_retries,
                    content_id,
                    ex,
                )

            if attempt < max_retries - 1:
                await asyncio.sleep(retry_delays[attempt])

        if thumbnail_data:
            www_path = self._hass.config.path("www", "frame_art", self._entry.entry_id)

            def _write_thumbnails():
                os.makedirs(www_path, exist_ok=True)

                # Remove any stale error/DRM markers
                for marker_name in ("current_error.txt", "current_drm.txt"):
                    marker_path = os.path.join(www_path, marker_name)
                    if os.path.exists(marker_path):
                        try:
                            os.remove(marker_path)
                        except OSError:
                            pass

                # Save as current.jpg
                file_path = os.path.join(www_path, "current.jpg")
                with open(file_path, "wb") as f:
                    f.write(thumbnail_data)

                # Also save with content_id name in the appropriate subfolder
                if content_id.startswith("MY_F"):
                    subdir = "personal"
                elif content_id.startswith("SAM-"):
                    subdir = "store"
                else:
                    subdir = "other"
                content_dir = os.path.join(www_path, subdir)
                os.makedirs(content_dir, exist_ok=True)
                content_file = f"{content_id.replace(':', '_')}.jpg"
                content_path = os.path.join(content_dir, content_file)
                with open(content_path, "wb") as f:
                    f.write(thumbnail_data)

                self._log.info("Frame Art: Written thumbnail to %s", file_path)
                return file_path, content_path

            await self._hass.async_add_executor_job(_write_thumbnails)

            self._log.info("Frame Art: Successfully saved thumbnail for %s", content_id)
            self._thumbnail_failures = 0
            self._current_thumbnail_content_id = content_id
            self._clear_store_retry(content_id)
            self.async_set_updated_data(self.data)
            return

        # All retries failed. Before showing an error placeholder, try to
        # reuse a copy of this artwork that a previous cycle already saved
        # under personal/store/other. The live thumbnail fetch is flaky on
        # some Frame models, but once an image has been downloaded it stays
        # valid — so an earlier copy is a much better fallback than a generic
        # "image unavailable" placeholder.
        # (Fallback approach contributed by @PrestonMcAfee.)
        if await self._restore_current_from_cache(content_id, after_failure=True):
            return

        # Art Store content the TV hasn't materialized yet: the failure is
        # structural (no local thumbnail exists), not a transport problem.
        # Log it calmly and leave the current image — do NOT write an error
        # placeholder or trip the global backoff. The TV caches the thumbnail a
        # little while after the content is displayed/favorited; favoriting Art
        # Store content does NOT emit an image_added broadcast (only personal
        # uploads do), so we cannot rely on an event to retrigger the fetch.
        # Instead arm a short retry cooldown so the next poll after it elapses
        # re-attempts and picks the thumbnail up within seconds of it becoming
        # available, rather than stalling until the artwork happens to change.
        if is_store_content:
            self._store_retry_content_id = content_id
            self._store_retry_at = time.time() + STORE_THUMBNAIL_RETRY_INTERVAL
            self._log.debug(
                "Frame Art: No thumbnail for Art Store content %s yet "
                "(last error: %s) — the TV only caches it once it has been "
                "displayed/favorited. Will retry in %ds.",
                content_id,
                last_error,
                STORE_THUMBNAIL_RETRY_INTERVAL,
            )
            return

        # No cached copy available — save generic error placeholder.
        # Personal photos are never DRM-protected; a failure here is a
        # transient transport issue (TV busy, WebSocket lag).
        self._thumbnail_failures += 1
        self._log.warning(
            "Frame Art: Could not download thumbnail for %s after %d attempts "
            "(last error: %s), and no cached copy was found. "
            "Transient transport failure.",
            content_id,
            max_retries,
            last_error,
        )
        await self._save_error_placeholder(content_id)

        if self._thumbnail_failures >= 3:
            self._thumbnail_backoff_until = time.time() + 300  # 5 minutes
            self._log.warning(
                "Frame Art: Too many thumbnail failures (%d), pausing automatic "
                "fetch for 5 minutes. Will retry automatically.",
                self._thumbnail_failures,
            )

    @staticmethod
    def _subdir_for_content(content_id: str) -> str:
        """Classify an artwork content_id into its thumbnail subfolder.

        Mirrors the classification used when thumbnails are first saved:
        personal (MY_F*), store (SAM-*), or other.
        """
        if content_id.startswith("MY_F"):
            return "personal"
        if content_id.startswith("SAM-"):
            return "store"
        return "other"

    async def _restore_current_from_cache(
        self, content_id: str, after_failure: bool = False
    ) -> bool:
        """Reuse a previously-downloaded thumbnail as current.jpg.

        Used both as the fast path (before any live fetch — a downloaded copy is
        authoritative and doesn't change) and as the fallback when a live
        thumbnail fetch fails, instead of showing an error placeholder. The
        copy lives under personal/store/other from an earlier successful
        download. ``after_failure`` only tunes the log wording.

        Returns True if a cached copy was found and promoted to current.jpg.
        """
        import os
        import shutil

        www_path = self._hass.config.path("www", "frame_art", self._entry.entry_id)
        subdir = self._subdir_for_content(content_id)
        content_file = f"{content_id.replace(':', '_')}.jpg"
        cached_path = os.path.join(www_path, subdir, content_file)

        def _promote() -> bool:
            if not os.path.isfile(cached_path):
                return False
            os.makedirs(www_path, exist_ok=True)
            current_path = os.path.join(www_path, "current.jpg")
            shutil.copyfile(cached_path, current_path)
            # Clear any stale error/DRM markers from previous failures.
            for marker_name in ("current_error.txt", "current_drm.txt"):
                marker_path = os.path.join(www_path, marker_name)
                if os.path.exists(marker_path):
                    try:
                        os.remove(marker_path)
                    except OSError:
                        pass
            return True

        try:
            promoted = await self._hass.async_add_executor_job(_promote)
        except Exception as ex:  # noqa: BLE001
            self._log.debug(
                "Frame Art: error restoring cached thumbnail for %s: %s",
                content_id,
                ex,
            )
            return False

        if promoted:
            if after_failure:
                self._log.info(
                    "Frame Art: live thumbnail fetch failed for %s; reused cached "
                    "copy from %s/ as current.jpg",
                    content_id,
                    subdir,
                )
            else:
                self._log.debug(
                    "Frame Art: used already-downloaded copy of %s from %s/ as "
                    "current.jpg (skipped live fetch)",
                    content_id,
                    subdir,
                )
            self._thumbnail_failures = 0
            self._current_thumbnail_content_id = content_id
            self._clear_store_retry(content_id)
            self.async_set_updated_data(self.data)

        return promoted


class FrameArtMetadataSensor(SensorEntity):
    """Auto-identify the artwork on screen and expose its metadata (opt-in).

    Watches the Frame Art sensor's ``current_content_id`` and, on a (debounced)
    change, runs the reverse-search + LLM pipeline via the shared entry point.
    State = artwork title (or "Unidentified"); the artist, date, biography,
    confidence, source and content_id land in the attributes (no 255-char
    limit). No polling — it is driven purely by the Frame Art sensor's state.
    """

    _attr_has_entity_name = True
    _attr_icon = "mdi:image-frame"
    _attr_should_poll = False

    def __init__(
        self, entry: ConfigEntry, device_name: str, device_unique_id: str
    ) -> None:
        """Initialize the art metadata sensor."""
        self._entry_id = entry.entry_id
        self._device_name = device_name
        self._device_unique_id = device_unique_id
        self._attr_unique_id = f"{entry.entry_id}_art_metadata"
        self._attr_name = "Art Metadata"
        self._attr_native_value: str | None = None
        self._attr_extra_state_attributes: dict[str, Any] = {}
        self._frame_art_entity_id: str | None = None
        # content_id already acted on (any outcome) — dedup guard so unrelated
        # Frame-Art attribute churn can't re-trigger the same identification.
        self._handled_content_id: str | None = None
        # one-shot retry flag for the startup thumbnail-not-ready race.
        self._retried_content_id: str | None = None
        self._pending_content_id: str | None = None
        self._debounce_unsub = None

    @property
    def device_info(self) -> DeviceInfo:
        """Link this entity to the TV device."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._device_unique_id)},
            name=self._device_name,
        )

    def _find_frame_art_entity(self) -> str | None:
        registry = er.async_get(self.hass)
        return registry.async_get_entity_id(
            "sensor", DOMAIN, f"{self._entry_id}_frame_art"
        )

    async def async_added_to_hass(self) -> None:
        """Subscribe to the Frame Art sensor and kick an initial identification."""
        await super().async_added_to_hass()
        self._frame_art_entity_id = self._find_frame_art_entity()
        if not self._frame_art_entity_id:
            return
        self.async_on_remove(
            async_track_state_change_event(
                self.hass, [self._frame_art_entity_id], self._async_frame_art_changed
            )
        )
        state = self.hass.states.get(self._frame_art_entity_id)
        content_id = state.attributes.get("current_content_id") if state else None
        if content_id:
            self._schedule_identify(content_id)

    async def async_will_remove_from_hass(self) -> None:
        """Cancel any pending debounced identification."""
        if self._debounce_unsub is not None:
            self._debounce_unsub()
            self._debounce_unsub = None

    @callback
    def _async_frame_art_changed(self, event) -> None:
        new_state = event.data.get("new_state")
        content_id = (
            new_state.attributes.get("current_content_id") if new_state else None
        )
        if not content_id or content_id == self._handled_content_id:
            return
        _LOGGER.debug(
            "Art metadata: artwork changed to %s (was %s) — scheduling identify",
            content_id,
            self._handled_content_id,
        )
        self._schedule_identify(content_id)

    @callback
    def _schedule_identify(self, content_id: str) -> None:
        """Debounce: coalesce rapid slideshow flips into one identification."""
        self._pending_content_id = content_id
        if self._debounce_unsub is not None:
            self._debounce_unsub()
        self._debounce_unsub = async_call_later(
            self.hass, ART_IDENTIFY_DEBOUNCE, self._async_debounce_fire
        )
        _LOGGER.debug(
            "Art metadata: identify for %s debounced %ss",
            content_id,
            ART_IDENTIFY_DEBOUNCE,
        )

    @callback
    def _async_debounce_fire(self, _now) -> None:
        self._debounce_unsub = None
        content_id = self._pending_content_id
        if content_id:
            self.hass.async_create_task(self._async_run_identify(content_id))

    async def _async_run_identify(self, content_id: str) -> None:
        from .art_identify import async_identify_for_entry, get_shared_cache

        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if entry is None:
            return
        # Mark handled BEFORE the call so a persistent error (bad key, quota)
        # can't be retried on every unrelated Frame-Art attribute change — that
        # would hammer the API. Recovery paths: the artwork changing, or the
        # manual art_identify service with force: true.
        self._handled_content_id = content_id
        cache = get_shared_cache(self.hass, self._entry_id)
        result = await async_identify_for_entry(
            self.hass, entry, content_id, cache=cache
        )
        if result.get("skipped"):
            _LOGGER.debug("Art metadata skipped: %s", result.get("skipped"))
            return
        if result.get("error"):
            _LOGGER.debug("Art metadata error: %s", result.get("error"))
            # Startup race: current.jpg not downloaded yet — allow ONE delayed
            # retry for this artwork (no storm: guarded per content_id).
            if (
                "thumbnail not available" in result["error"]
                and self._retried_content_id != content_id
            ):
                self._retried_content_id = content_id
                self._handled_content_id = None
                self._schedule_identify(content_id)
            return
        identified = bool(result.get("identified"))
        translations = result.get("translations") or {}
        # Convenience copy in the HA instance language (fallback English) so a
        # plain Markdown card works; full 'translations' is exposed too so a
        # frontend card can pick the viewer's own browser/user language.
        lang = (self.hass.config.language or "en").split("-")[0]
        local = translations.get(lang) or translations.get("en") or {}
        _LOGGER.debug(
            "Art metadata updated: %s -> identified=%s title=%s source=%s langs=%s",
            content_id,
            identified,
            local.get("title"),
            result.get("source"),
            list(translations),
        )
        self._attr_native_value = local.get("title") if identified else "Unidentified"
        self._attr_extra_state_attributes = {
            "identified": identified,
            "confidence": result.get("confidence"),
            "artist": result.get("artist"),
            "date": result.get("date"),
            "artist_biography": local.get("artist_biography"),
            "artwork_description": local.get("artwork_description"),
            "visual_description": local.get("visual_description"),
            "matched_candidate": result.get("matched_candidate"),
            "suggested_search_query": result.get("suggested_search_query"),
            "source": result.get("source"),
            "content_id": content_id,
            "translations": translations,
        }
        self.async_write_ha_state()


class FrameArtFolderSensor(SensorEntity):
    """Sensor exposing the thumbnail file list for one frame_art subdirectory.

    Mirrors the HA platform:folder sensor format (state = total size in MB,
    attributes: path, filter, number_of_files, bytes, file_list) so that
    folder-gallery-card can use it directly via folder_sensor.

    The unique_id is stable across restarts, so entity_ids never shuffle.
    """

    _attr_icon = "mdi:folder-image"
    _attr_native_unit_of_measurement = "MB"
    _attr_should_poll = True
    # file_list grows with the number of thumbnails and can exceed the
    # recorder's 16 KiB per-state attribute limit on TVs with many artworks.
    # Keep it live for folder-gallery-card but out of the database history.
    _unrecorded_attributes = frozenset({"file_list"})

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        subdir: str,
        device_name: str,
        device_unique_id: str,
    ) -> None:
        """Initialize the folder sensor."""
        self.hass = hass
        self._entry = entry
        self._subdir = subdir  # "personal", "store", or "other"
        self._attr_unique_id = f"{entry.entry_id}_folder_{subdir}"
        self._attr_name = f"{device_name} {subdir}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_unique_id)},
        )
        self._files: list[str] = []
        self._total_bytes: int = 0

    @property
    def scan_interval(self):
        """Scan every 5 minutes."""
        from datetime import timedelta

        return timedelta(minutes=5)

    @property
    def native_value(self) -> float:
        """Return total size in MB (mirrors platform:folder behaviour)."""
        return round(self._total_bytes / (1024 * 1024), 2)

    @property
    def extra_state_attributes(self) -> dict:
        """Return file list and metadata compatible with platform:folder."""
        www_path = self.hass.config.path(
            "www", "frame_art", self._entry.entry_id, self._subdir
        )
        return {
            "path": f"{www_path}/",
            "filter": "*.jpg",
            "number_of_files": len(self._files),
            "bytes": self._total_bytes,
            "file_list": self._files,
        }

    async def async_update(self) -> None:
        """Scan the subdirectory and refresh file list + total size."""
        import os

        www_path = self.hass.config.path(
            "www", "frame_art", self._entry.entry_id, self._subdir
        )

        def _scan() -> tuple[list[str], int]:
            files: list[str] = []
            total: int = 0
            if not os.path.isdir(www_path):
                return files, total
            for fname in sorted(os.listdir(www_path)):
                if fname.lower().endswith(".jpg"):
                    fpath = os.path.join(www_path, fname)
                    files.append(fpath)
                    try:
                        total += os.path.getsize(fpath)
                    except OSError:
                        pass
            return files, total

        self._files, self._total_bytes = await self.hass.async_add_executor_job(_scan)


class FrameArtSensor(CoordinatorEntity, SensorEntity):
    """Sensor entity for Samsung Frame TV Art Mode."""

    _attr_icon = "mdi:image-frame"
    # Keep the (potentially large) last service result out of the recorder.
    # Service calls such as get_content_list or get_thumbnails_batch can store
    # the full TV artwork list here, which easily exceeds the recorder's
    # 16 KiB per-state attribute limit and bloats the database. The attribute
    # is still exposed live for cards/automations; it just isn't recorded.
    _unrecorded_attributes = frozenset({"last_service_result"})

    def __init__(
        self,
        coordinator: FrameArtCoordinator,
        entry: ConfigEntry,
        art_api: SamsungTVAsyncArt,
        device_name: str,
        device_unique_id: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._entry = entry
        self._art_api = art_api
        self._attr_unique_id = f"{entry.entry_id}_frame_art"
        # Use explicit name instead of has_entity_name to avoid "None" prefix
        self._attr_name = f"{device_name} Frame Art"
        self._last_service_result: dict[str, Any] | None = None

        # Device info to link with the main TV entity
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_unique_id)},
        )

    @property
    def native_value(self) -> str | None:
        """Return the current art mode status."""
        if self.coordinator.data:
            return self.coordinator.data.get("art_mode")
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes."""
        attrs = {}

        if self.coordinator.data:
            data = self.coordinator.data

            # If TV is powered off, return minimal attributes
            if data.get("tv_powered_off", False):
                attrs["art_mode_status"] = "off"
                # Keep artwork_count if we have it (from last time TV was on)
                if data.get("artwork_count") is not None:
                    attrs["artwork_count"] = data["artwork_count"]
                # Keep current_thumbnail_url for Lovelace display
                if data.get("current_thumbnail_url"):
                    attrs["current_thumbnail_url"] = data["current_thumbnail_url"]
                # Keep current_content_id for Lovelace display
                if data.get("current_artwork"):
                    current = data["current_artwork"]
                    content_id = current.get("content_id")
                    if content_id:
                        attrs["current_content_id"] = content_id
                # Add indicator that TV is off
                attrs["tv_power_state"] = "off"
                # Thumbnail auto-fetch status
                attrs["thumbnail_auto_fetch"] = (
                    self.coordinator._thumbnail_fetch_enabled
                )
                return attrs

            # TV is on - return full attributes
            attrs["tv_power_state"] = "on"

            # Art mode status
            if data.get("art_mode") is not None:
                attrs["art_mode_status"] = data["art_mode"]

            # Current artwork details (only when TV is on)
            if data.get("current_artwork"):
                current = data["current_artwork"]
                content_id = current.get("content_id")
                attrs["current_content_id"] = content_id
                attrs["current_category_id"] = current.get("category_id")
                attrs["current_matte_id"] = current.get("matte_id")

                # Check if current image is DRM protected (SAM-S* = Art Store)
                if content_id:
                    is_drm = content_id.startswith("SAM-S")
                    attrs["current_is_drm_protected"] = is_drm
                    if is_drm:
                        attrs["current_content_type"] = "Art Store (DRM)"
                    elif content_id.startswith("MY_F"):
                        attrs["current_content_type"] = "My Photos"
                    elif content_id.startswith("SAM-"):
                        attrs["current_content_type"] = "Samsung Collection"
                    else:
                        attrs["current_content_type"] = "Unknown"

            # Current thumbnail URL (for Lovelace)
            if data.get("current_thumbnail_url"):
                attrs["current_thumbnail_url"] = data["current_thumbnail_url"]

            # Artwork count
            if data.get("artwork_count") is not None:
                attrs["artwork_count"] = data["artwork_count"]

            # Slideshow status
            if data.get("slideshow_status") is not None:
                attrs["slideshow_status"] = data["slideshow_status"]

            # API version
            if data.get("api_version") is not None:
                attrs["api_version"] = data["api_version"]

        # Thumbnail auto-fetch status
        attrs["thumbnail_auto_fetch"] = self.coordinator._thumbnail_fetch_enabled

        # Last service result (for debugging/monitoring service calls)
        if self._last_service_result:
            attrs["last_service_result"] = self._last_service_result

        return attrs

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self.coordinator.last_update_success

    async def async_set_artmode(self, enabled: bool) -> dict:
        """Enable or disable Art Mode."""
        try:
            await self._art_api.set_artmode(enabled)
            result = {"service": "set_artmode", "success": True, "enabled": enabled}
        except Exception as ex:
            result = {"service": "set_artmode", "error": str(ex)}
        self._last_service_result = result
        await self.coordinator.async_request_refresh()
        return result

    async def async_select_image(
        self,
        content_id: str,
        category_id: str | None = None,
        show: bool = True,
    ) -> dict:
        """Select and display artwork."""
        try:
            await self._art_api.select_image(content_id, category_id, show)
            result = {
                "service": "select_image",
                "success": True,
                "content_id": content_id,
            }
        except Exception as ex:
            result = {"service": "select_image", "error": str(ex)}
        self._last_service_result = result
        await self.coordinator.async_request_refresh()
        return result

    async def async_get_available(self, category_id: str | None = None) -> dict:
        """Get list of available artwork."""
        try:
            artwork_list = await self._art_api.available(category_id)
            result = {
                "service": "get_available",
                "success": True,
                "count": len(artwork_list) if artwork_list else 0,
                "artwork": artwork_list,
            }
        except Exception as ex:
            result = {"service": "get_available", "error": str(ex)}
        self._last_service_result = result
        self.async_write_ha_state()
        return result

    async def async_upload_image(
        self,
        file_path: str,
        matte_id: str | None = None,
        file_type: str = "png",
    ) -> dict:
        """Upload an image to the TV."""
        try:
            content_id = await self._art_api.upload(
                file_path,
                matte=matte_id or "shadowbox_polar",
                file_type=file_type,
            )
            result = {
                "service": "upload_image",
                "success": content_id is not None,
                "content_id": content_id,
            }
        except Exception as ex:
            result = {"service": "upload_image", "error": str(ex)}
        self._last_service_result = result
        await self.coordinator.async_request_refresh()
        return result

    async def async_delete_image(self, content_id: str) -> dict:
        """Delete an uploaded image."""
        try:
            success = await self._art_api.delete(content_id)
            result = {
                "service": "delete_image",
                "success": success,
                "content_id": content_id,
            }
        except Exception as ex:
            result = {"service": "delete_image", "error": str(ex)}
        self._last_service_result = result
        await self.coordinator.async_request_refresh()
        return result

    async def async_set_slideshow(
        self,
        duration: int = 0,
        shuffle: bool = True,
        category_id: int = 2,
    ) -> dict:
        """Configure slideshow settings."""
        try:
            success = await self._art_api.set_slideshow_status(
                duration=duration,
                shuffle=shuffle,
                category=category_id,
            )
            result = {
                "service": "set_slideshow",
                "success": success,
                "duration": duration,
                "shuffle": shuffle,
            }
        except Exception as ex:
            result = {"service": "set_slideshow", "error": str(ex)}
        self._last_service_result = result
        await self.coordinator.async_request_refresh()
        return result

    async def async_change_matte(
        self,
        content_id: str,
        matte_id: str,
    ) -> dict:
        """Change the matte/frame style for artwork."""
        try:
            success = await self._art_api.change_matte(content_id, matte_id)
            result = {
                "service": "change_matte",
                "success": success,
                "content_id": content_id,
                "matte_id": matte_id,
            }
        except Exception as ex:
            result = {"service": "change_matte", "error": str(ex)}
        self._last_service_result = result
        self.async_write_ha_state()
        return result

    async def async_set_photo_filter(
        self,
        content_id: str,
        filter_id: str,
    ) -> dict:
        """Apply a photo filter to artwork."""
        try:
            success = await self._art_api.set_photo_filter(content_id, filter_id)
            result = {
                "service": "set_photo_filter",
                "success": success,
                "content_id": content_id,
                "filter_id": filter_id,
            }
        except Exception as ex:
            result = {"service": "set_photo_filter", "error": str(ex)}
        self._last_service_result = result
        self.async_write_ha_state()
        return result

    async def async_set_favourite(
        self,
        content_id: str,
        status: str = "on",
    ) -> dict:
        """Add or remove artwork from favorites."""
        try:
            success = await self._art_api.set_favourite(content_id, status)
            result = {
                "service": "set_favourite",
                "success": success,
                "content_id": content_id,
                "status": status,
            }
        except Exception as ex:
            result = {"service": "set_favourite", "error": str(ex)}
        self._last_service_result = result
        self.async_write_ha_state()
        return result

    async def async_enable_thumbnail_fetch(self, enabled: bool = True) -> dict:
        """Enable or disable automatic thumbnail fetching.

        If thumbnail fetching times out repeatedly, it is automatically disabled.
        Use this method to re-enable it.
        """
        self.coordinator._thumbnail_fetch_enabled = enabled
        self.coordinator._thumbnail_failures = 0
        result = {
            "service": "enable_thumbnail_fetch",
            "success": True,
            "enabled": enabled,
        }
        self._last_service_result = result
        self.async_write_ha_state()

        # If enabling, trigger an immediate refresh
        if enabled:
            await self.coordinator.async_request_refresh()

        return result

    async def async_get_thumbnail(self, content_id: str) -> dict:
        """Manually fetch and save a thumbnail for a specific artwork."""
        try:
            thumbnail_data = await self._art_api.get_thumbnail(content_id, timeout=30)
            if thumbnail_data:
                import os

                www_path = self.hass.config.path(
                    "www", "frame_art", self._entry.entry_id
                )

                def _write_thumbnail():
                    if content_id.startswith("MY_F"):
                        subdir = "personal"
                    elif content_id.startswith("SAM-"):
                        subdir = "store"
                    else:
                        subdir = "other"
                    subdir_path = os.path.join(www_path, subdir)
                    os.makedirs(subdir_path, exist_ok=True)
                    file_name = f"{content_id.replace(':', '_')}.jpg"
                    file_path = os.path.join(subdir_path, file_name)
                    with open(file_path, "wb") as f:
                        f.write(thumbnail_data)
                    return subdir, file_name

                subdir, file_name = await self.hass.async_add_executor_job(
                    _write_thumbnail
                )

                result = {
                    "service": "get_thumbnail",
                    "success": True,
                    "content_id": content_id,
                    "thumbnail_url": (
                        f"/local/frame_art/{self._entry.entry_id}"
                        f"/{subdir}/{file_name}"
                    ),
                    "size": len(thumbnail_data),
                }
            else:
                result = {
                    "service": "get_thumbnail",
                    "success": False,
                    "content_id": content_id,
                    "error": "No thumbnail data received",
                }
        except Exception as ex:
            result = {
                "service": "get_thumbnail",
                "success": False,
                "content_id": content_id,
                "error": str(ex),
            }
        self._last_service_result = result
        self.async_write_ha_state()
        return result


class SmartThingsIlluminanceSensor(SensorEntity):
    """Samsung Frame TV light sensor via SmartThings."""

    _attr_device_class = SensorDeviceClass.ILLUMINANCE
    _attr_native_unit_of_measurement = LIGHT_LUX
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_has_entity_name = True
    _attr_translation_key = "illuminance"

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        session,  # aiohttp session
        device_id: str,
        device_name: str,
        parent_device_id: str,
    ) -> None:
        """Initialize the sensor."""
        self.hass = hass
        self._entry = entry
        self._session = session
        self._device_id = device_id
        self._device_name = device_name
        self._parent_device_id = parent_device_id
        self._attr_unique_id = f"{device_id}_illuminance"
        self._attr_native_value = None
        self._st_last_poll = 0.0

    async def _get_st_client(self):
        """Get SmartThings client with current token from config entry."""
        from pysmartthings import SmartThings

        api_key = await async_get_samsungtv_api_key(self.hass, self._entry)

        if not api_key:
            config = self.hass.data[DOMAIN][self._entry.entry_id][DATA_CFG]
            api_key = config.get(CONF_API_KEY)
            if not api_key:
                oauth_token = config.get(CONF_OAUTH_TOKEN)
                if oauth_token and isinstance(oauth_token, dict):
                    api_key = oauth_token.get("access_token")

        if not api_key:
            return None

        st_client = SmartThings(session=self._session)
        st_client.authenticate(api_key)
        return st_client

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info - link to parent TV device."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._parent_device_id)},
            name=self._device_name,
            manufacturer="Samsung",
            model="Frame TV Light Sensor",
        )

    @property
    def name(self) -> str:
        """Return the name of the sensor."""
        return "Light Level"

    async def async_update(self) -> None:
        """Update the sensor value from SmartThings."""
        # Local WS is primary: skip while the TV is off and throttle to the
        # configured ST cadence (instead of the 15 s module scan interval).
        if not _st_child_gate(self):
            return
        try:
            st_client = await self._get_st_client()
            if not st_client:
                return

            components = await st_client.get_device_status(self._device_id)

            if (
                "main" in components
                and Capability.ILLUMINANCE_MEASUREMENT in components["main"]
                and Attribute.ILLUMINANCE
                in components["main"][Capability.ILLUMINANCE_MEASUREMENT]
            ):
                illuminance_status = components["main"][
                    Capability.ILLUMINANCE_MEASUREMENT
                ][Attribute.ILLUMINANCE]
                self._attr_native_value = illuminance_status.value
                _LOGGER.debug(
                    "Updated illuminance sensor for %s: %s lux",
                    self._device_name,
                    self._attr_native_value,
                )
            else:
                _LOGGER.debug(
                    "Illuminance data not available for %s", self._device_name
                )
        except Exception as ex:
            _LOGGER.warning("Error updating illuminance sensor: %s", ex)


class SmartThingsBrightnessIntensitySensor(SensorEntity):
    """Samsung Frame TV brightness intensity sensor via SmartThings."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_has_entity_name = True
    _attr_translation_key = "brightness_intensity"

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        session,  # aiohttp session
        device_id: str,
        device_name: str,
        parent_device_id: str,
    ) -> None:
        """Initialize the sensor."""
        self.hass = hass
        self._entry = entry
        self._session = session
        self._device_id = device_id
        self._device_name = device_name
        self._parent_device_id = parent_device_id
        self._attr_unique_id = f"{device_id}_brightness_intensity"
        self._attr_native_value = None
        self._st_last_poll = 0.0

    async def _get_st_client(self):
        """Get SmartThings client with current token from config entry."""
        from pysmartthings import SmartThings

        api_key = await async_get_samsungtv_api_key(self.hass, self._entry)

        if not api_key:
            config = self.hass.data[DOMAIN][self._entry.entry_id][DATA_CFG]
            api_key = config.get(CONF_API_KEY)
            if not api_key:
                oauth_token = config.get(CONF_OAUTH_TOKEN)
                if oauth_token and isinstance(oauth_token, dict):
                    api_key = oauth_token.get("access_token")

        if not api_key:
            return None

        st_client = SmartThings(session=self._session)
        st_client.authenticate(api_key)
        return st_client

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info - link to parent TV device."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._parent_device_id)},
            name=self._device_name,
            manufacturer="Samsung",
            model="Frame TV Light Sensor",
        )

    @property
    def name(self) -> str:
        """Return the name of the sensor."""
        return "Brightness Intensity"

    async def async_update(self) -> None:
        """Update the sensor value from SmartThings."""
        # Local WS is primary: skip while the TV is off and throttle to the
        # configured ST cadence (instead of the 15 s module scan interval).
        if not _st_child_gate(self):
            return
        try:
            st_client = await self._get_st_client()
            if not st_client:
                return

            components = await st_client.get_device_status(self._device_id)

            if (
                "main" in components
                and Capability.RELATIVE_BRIGHTNESS in components["main"]
                and Attribute.BRIGHTNESS_INTENSITY
                in components["main"][Capability.RELATIVE_BRIGHTNESS]
            ):
                brightness_status = components["main"][Capability.RELATIVE_BRIGHTNESS][
                    Attribute.BRIGHTNESS_INTENSITY
                ]
                self._attr_native_value = brightness_status.value
                _LOGGER.debug(
                    "Updated brightness intensity sensor for %s: %s",
                    self._device_name,
                    self._attr_native_value,
                )
            else:
                _LOGGER.debug(
                    "Brightness intensity data not available for %s", self._device_name
                )
        except Exception as ex:
            _LOGGER.warning("Error updating brightness intensity sensor: %s", ex)


class SmartThingsPowerCoordinator(DataUpdateCoordinator):
    """One SmartThings ``get_device_status`` per cycle for all power sensors.

    Previously each of the five power/energy sensors polled ``get_device_status``
    on the SAME TV device independently (5× redundant cloud calls every 15 s).
    This shares a single call, throttles it to the configured ST cadence, and
    skips it entirely while the TV is off (the local WebSocket is the primary
    power source), so an idle Frame makes no power calls at all. A Frame showing
    Art still reports power draw, so ``_tv_powered_off`` keeps polling then.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        session,  # aiohttp session
        device_id: str,
        device_name: str,
    ) -> None:
        """Initialize the power-consumption coordinator."""
        self._entry = entry
        self._session = session
        self.device_id = device_id
        self._device_name = device_name
        self._st_last_poll = 0.0
        super().__init__(
            hass,
            _LOGGER,
            name=f"SmartThings power {entry.title}",
            update_interval=timedelta(seconds=_st_poll_on_interval(entry)),
        )

    async def _get_st_client(self):
        """Get SmartThings client with current token from config entry."""
        from pysmartthings import SmartThings

        api_key = await async_get_samsungtv_api_key(self.hass, self._entry)
        if not api_key:
            config = self.hass.data[DOMAIN][self._entry.entry_id][DATA_CFG]
            api_key = config.get(CONF_API_KEY)
            if not api_key:
                oauth_token = config.get(CONF_OAUTH_TOKEN)
                if oauth_token and isinstance(oauth_token, dict):
                    api_key = oauth_token.get("access_token")
        if not api_key:
            return None
        st_client = SmartThings(session=self._session)
        st_client.authenticate(api_key)
        return st_client

    # Fields whose value should NOT be frozen while the TV is off: the
    # instantaneous power draw (W) is ~0 in standby, unlike the cumulative
    # energy counters (TOTAL_INCREASING) which must keep their last value.
    _INSTANTANEOUS_FIELDS = ("power",)

    async def _async_update_data(self) -> dict:
        """Fetch the powerConsumption dict once, or keep last values."""
        # Local WS is primary: while the TV is truly off (not showing Art),
        # skip the cloud call and keep the last reported values — except the
        # instantaneous power draw, which drops to ~0 in standby (don't leave
        # the last ON wattage frozen on the sensor).
        if _tv_powered_off(self.hass, self._entry):
            self.logger.debug(
                "Power sensors: TV off, skipping SmartThings poll for %s",
                self._device_name,
            )
            data = dict(self.data or {})
            for field in self._INSTANTANEOUS_FIELDS:
                if field in data:
                    data[field] = 0
            return data
        # In Art Mode the Frame draws power but the draw barely changes, so poll
        # at a fixed slow keepalive (ST_POLL_OFF_INTERVAL) rather than the — often
        # much faster — "when on" cadence used for responsive channel/picture-mode
        # updates. This decouples the power sensor from the comfort interval.
        if _tv_in_art_mode(self.hass, self._entry):
            now = time.monotonic()
            if now - self._st_last_poll < ST_POLL_OFF_INTERVAL:
                self.logger.debug(
                    "Power sensors: Art Mode, throttling SmartThings poll for %s",
                    self._device_name,
                )
                return self.data or {}
        st_client = await self._get_st_client()
        if not st_client:
            return self.data or {}
        self._st_last_poll = time.monotonic()
        try:
            components = await st_client.get_device_status(self.device_id)
        except Exception as ex:  # pylint: disable=broad-except
            # Keep last values instead of marking every energy sensor
            # unavailable on a transient cloud hiccup.
            self.logger.debug("Power consumption read failed: %s", ex)
            return self.data or {}
        main = components.get("main", {})
        # String capability/attribute names: the dict is keyed that way and it
        # avoids depending on enum names across pysmartthings versions.
        report = main.get("powerConsumptionReport", {}).get("powerConsumption")
        value = getattr(report, "value", None)
        if isinstance(value, dict):
            return value
        return self.data or {}


class SmartThingsPowerConsumptionSensor(CoordinatorEntity, SensorEntity):
    """TV power/energy consumption via SmartThings ``powerConsumptionReport``.

    SmartThings exposes a single ``powerConsumption`` attribute whose value is a
    dict (``power`` in W, ``energy``/``deltaEnergy`` in Wh, etc.). One instance
    surfaces one field of that dict, all fed by a shared coordinator.
    """

    _attr_has_entity_name = True

    # measure -> (suffix, friendly name, device_class, state_class, unit, divisor)
    _MEASURES = {
        "power": (
            "power",
            "Power",
            SensorDeviceClass.POWER,
            SensorStateClass.MEASUREMENT,
            UnitOfPower.WATT,
            1,
        ),
        "energy": (
            "energy",
            "Energy",
            SensorDeviceClass.ENERGY,
            SensorStateClass.TOTAL_INCREASING,
            UnitOfEnergy.KILO_WATT_HOUR,
            1000,  # SmartThings reports Wh; HA wants kWh
        ),
        "deltaEnergy": (
            "energy_difference",
            "Energy difference",
            SensorDeviceClass.ENERGY,
            SensorStateClass.TOTAL_INCREASING,
            UnitOfEnergy.KILO_WATT_HOUR,
            1000,
        ),
        "powerEnergy": (
            "power_energy",
            "Power energy",
            SensorDeviceClass.ENERGY,
            SensorStateClass.TOTAL_INCREASING,
            UnitOfEnergy.KILO_WATT_HOUR,
            1000,
        ),
        "energySaved": (
            "energy_saved",
            "Energy saved",
            SensorDeviceClass.ENERGY,
            SensorStateClass.TOTAL_INCREASING,
            UnitOfEnergy.KILO_WATT_HOUR,
            1000,
        ),
    }

    def __init__(
        self,
        coordinator: SmartThingsPowerCoordinator,
        parent_device_id: str,
        measure: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._parent_device_id = parent_device_id
        suffix, friendly, dev_class, state_class, unit, divisor = self._MEASURES[
            measure
        ]
        # The SmartThings powerConsumption dict is keyed by the measure name
        # (power, energy, deltaEnergy, powerEnergy, energySaved); suffix is only
        # used for the entity's unique_id / friendly name.
        self._field = measure
        self._friendly = friendly
        self._divisor = divisor
        self._attr_device_class = dev_class
        self._attr_state_class = state_class
        self._attr_native_unit_of_measurement = unit
        self._attr_unique_id = f"{coordinator.device_id}_{suffix}"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info - link to the TV device."""
        return DeviceInfo(identifiers={(DOMAIN, self._parent_device_id)})

    @property
    def name(self) -> str:
        """Return the name of the sensor."""
        return self._friendly

    @property
    def native_value(self):
        """Return this field's value from the shared coordinator data."""
        raw = (self.coordinator.data or {}).get(self._field)
        if raw is None:
            return None
        try:
            return round(raw / self._divisor, 3)
        except (TypeError, ValueError):
            return None


# Consecutive -32002 answers tolerated on a state READ before it is reported as
# a coordinator failure. The TV returns this for a read it momentarily refuses;
# our own message calls it "usually transient", and it is — measured as isolated
# single occurrences a few times a day on healthy TVs, each recovering on the
# next cycle. Raising UpdateFailed on the first one logs an ERROR for a normal
# condition, the same mistake #248 fixed for the sleeping-TV overrun. A run of
# them is different: that is a TV stuck in a state it will not read from, and
# worth an ERROR.
IP_CONTROL_READ_TRANSIENT_TOLERANCE = 3


class IPControlStateCoordinator(DataUpdateCoordinator):
    """Polls getTVStates over IP Control for the read-only state sensors.

    A single coordinator feeds all the getTVStates sensors, so each cycle shares
    one getTVStates request (plus a cheap power-state check) regardless of how
    many sensors are enabled. When a non-Frame TV is on a tuner input, one
    optional directChannelControl request may also fetch local channel metadata.
    The TV is skipped while it is powered off, both to
    avoid pointless traffic and because the state getters return stale values in
    standby. (The getVideoStates picture fields moved to settable number
    sliders with their own coordinator — see number.py.)
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        host: str,
    ) -> None:
        """Initialize the IP Control state coordinator."""
        self._log = _DeviceLoggerAdapter(_LOGGER, {"host": host})
        super().__init__(
            hass,
            self._log,
            name=f"IP Control state {entry.title}",
            update_interval=timedelta(seconds=_ip_control_poll_interval(entry)),
        )
        self._entry = entry
        self._host = host
        self._ip_control: SamsungIPControl | None = None
        self._ip_control_token: str | None = None
        self._channel_control_supported: bool | None = None
        # Consecutive -32002 answers on getTVStates; see the constant above.
        self._transient_read_failures = 0

    def _device_title(self) -> str:
        entry = self.hass.config_entries.async_get_entry(self._entry.entry_id)
        return entry.title if entry else (self._host or "this Samsung TV")

    def _get_ip_control(self) -> SamsungIPControl | None:
        """Return a live IP Control client if paired AND enabled."""
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

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch the getTVStates snapshot over IP Control."""
        client = self._get_ip_control()
        if client is None:
            raise UpdateFailed("IP Control is not paired or is disabled")

        try:
            # Skip the snapshots while the TV is off: the getters would return
            # stale values, and this avoids needless traffic to a sleeping TV.
            # A powered-off TV is an expected, recurring condition — NOT an
            # update failure — so do not raise UpdateFailed here. Doing so makes
            # HA log an ERROR on the first cycle of every off streak (i.e. once
            # per off->on transition), spamming the log. Instead return a
            # "powered_off" snapshot and let the sensors report unavailable via
            # that flag, keeping last_update_success True (no ERROR).
            if await client.async_get_power_state() == "powerOff":
                self._log.debug(
                    "IP Control state: TV is powered off, skipping state poll"
                )
                return {"tv": {}, "powered_off": True}

            # getTVStates remains the primary shared snapshot. When the tuner is
            # the active input, also query optional local channel metadata.
            # Frame TVs have a tuner too and answer the getter (measured on a
            # QN55LS03FAFXZA), so they are not excluded; a panel that does not
            # implement directChannelControl answers -32601 and is then never
            # asked again for the life of the coordinator.
            tv_states = await client.async_get_tv_states()

            channel_states: dict[str, Any] = {}
            input_source = tv_states.get("inputSource")
            tuner_active = (
                isinstance(input_source, str)
                and input_source.casefold() in TUNER_INPUT_SOURCES
            )
            # pictureMode is "Ambient" exactly while art is on the panel — the
            # free panel signal async_get_art_mode cross-checks against, with no
            # extra request here. A Frame in art mode still reports inputSource
            # "TV" (measured), so without this the tuner would look active and
            # be polled for a channel number that means nothing while art is
            # displayed.
            art_mode_on = tv_states.get("pictureMode") == "Ambient"

            if (
                tuner_active
                and not art_mode_on
                and self._channel_control_supported is not False
            ):
                try:
                    channel_states = await client.async_get_channel()
                    self._channel_control_supported = True
                except SamsungIPControlUnsupportedError:
                    if art_mode_on:
                        # The decompiled server dispatches directChannelControl
                        # from a map that is not active in ambient/art mode, so
                        # a TV that does support it can still answer "method not
                        # found" while art is displayed. Latching on that would
                        # disable the channel for the life of the coordinator
                        # over a temporary display state, so retry later instead.
                        self._log.debug(
                            "IP Control channel state unavailable while art mode "
                            "is on — not treating it as unsupported"
                        )
                    else:
                        self._channel_control_supported = False
                        self._log.debug(
                            "IP Control channel state is not supported on this TV"
                        )
                except SamsungIPControlAuthError:
                    raise
                except SamsungIPControlError as ex:
                    # Channel metadata is optional. A transient failure must not
                    # invalidate the normal getTVStates snapshot.
                    self._log.debug("IP Control channel state read failed: %s", ex)

        except SamsungIPControlAuthError as ex:
            notify_token_problem(
                self.hass,
                self._entry.entry_id,
                METHOD_IP_CONTROL,
                self._device_title(),
            )
            raise UpdateFailed(f"IP Control token rejected: {ex}") from ex
        except SamsungIPControlTransportError as ex:
            # A network-layer failure (timeout, host unreachable, connection
            # refused) is indistinguishable from "the TV is simply off" on the
            # Frames that drop off the network when powered down — and the
            # power-state probe above raises this very error when it can't
            # reach the TV to learn it's off in the first place. Treat it like
            # a powered-off snapshot (sensors go unavailable, no ERROR) instead
            # of UpdateFailed, which would log an ERROR on every off streak for
            # a perfectly normal condition. Genuine application errors still
            # fall through to the UpdateFailed branch below.
            self._log.debug(
                "IP Control state: transport failure (TV likely off): %s", ex
            )
            return {"tv": {}, "powered_off": True}
        except SamsungIPControlModeLockedError as ex:
            # -32002 on a READ: the TV refused it in its current state. Isolated
            # occurrences are normal and recover on the next cycle, so hold the
            # previous snapshot instead of failing the coordinator. Only a run
            # of them means something is actually wrong.
            self._transient_read_failures += 1
            if self._transient_read_failures <= IP_CONTROL_READ_TRANSIENT_TOLERANCE:
                self._log.debug(
                    "IP Control state read refused (%s) — attempt %d of %d "
                    "tolerated, keeping the previous snapshot",
                    ex,
                    self._transient_read_failures,
                    IP_CONTROL_READ_TRANSIENT_TOLERANCE,
                )
                if self.data is not None:
                    return self.data
                return {"tv": {}, "channel": {}, "powered_off": False}
            raise UpdateFailed(
                f"IP Control state read refused {self._transient_read_failures} "
                f"times in a row: {ex}"
            ) from ex
        except SamsungIPControlError as ex:
            raise UpdateFailed(f"IP Control state read failed: {ex}") from ex

        self._transient_read_failures = 0
        clear_token_problem(self.hass, self._entry.entry_id, METHOD_IP_CONTROL)
        return {
            "tv": tv_states,
            "channel": channel_states,
            "powered_off": False,
        }


class IPControlStateSensor(CoordinatorEntity, SensorEntity):
    """Read-only sensor for one getTVStates / getVideoStates field."""

    entity_description: SamsungIPControlSensorDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: IPControlStateCoordinator,
        entry: ConfigEntry,
        description: SamsungIPControlSensorDescription,
        device_name: str,
        device_unique_id: str,
    ) -> None:
        """Initialize the IP Control state sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._entry_id = entry.entry_id
        self._device_name = device_name
        self._device_unique_id = device_unique_id
        self._attr_unique_id = f"{device_unique_id}_ip_control_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_unique_id)},
            name=device_name,
        )

    @property
    def native_value(self) -> Any | None:
        """Return the value for this field from the coordinator snapshot."""
        if not self.coordinator.data:
            return None
        snapshot = self.coordinator.data.get(self.entity_description.source, {})
        return snapshot.get(self.entity_description.key)

    @property
    def available(self) -> bool:
        """Available only while IP Control is active and the TV is on.

        A powered-off TV is reported through the coordinator data (not as an
        update failure), so honor that flag here to mark the sensor
        unavailable while the TV is off without logging an ERROR each cycle.
        """
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        data = self.coordinator.data or {}
        return bool(
            entry
            and _ip_control_active(entry)
            and self.coordinator.last_update_success
            and not data.get("powered_off")
        )
