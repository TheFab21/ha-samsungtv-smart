"""Support for interface with an Samsung TV."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import timedelta
from enum import Enum
import logging
import os
from socket import error as socketError
import time
from typing import Any
from urllib.parse import parse_qs, urlparse

from aiohttp import ClientConnectionError, ClientResponseError, ClientSession
import async_timeout
import voluptuous as vol
from wakeonlan import send_magic_packet
from websocket import WebSocketTimeoutException

from homeassistant.components import media_source
from homeassistant.components.media_player import (
    ATTR_MEDIA_ENQUEUE,
    MediaPlayerDeviceClass,
    MediaPlayerEnqueue,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
)
from homeassistant.components.media_player.browse_media import (
    async_process_play_media_url,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_API_KEY,
    CONF_BROADCAST_ADDRESS,
    CONF_DEVICE_ID,
    CONF_HOST,
    CONF_PORT,
    CONF_SERVICE,
    CONF_SERVICE_DATA,
    CONF_TIMEOUT,
    CONF_TOKEN,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
)
from homeassistant.core import DOMAIN as HA_DOMAIN
from homeassistant.core import HomeAssistant, SupportsResponse, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_platform
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.service import CONF_SERVICE_ENTITY_ID, async_call_from_config
from homeassistant.helpers.storage import STORAGE_DIR
from homeassistant.util import Throttle
from homeassistant.util import dt as dt_util
from homeassistant.util.async_ import run_callback_threadsafe

from . import (
    get_oauth_refresh_lock,
    get_smartthings_api_key,
    is_invalid_grant_error,
    is_oauth_token_invalid,
    set_oauth_refresh_in_progress,
    set_oauth_token_invalid,
    start_oauth_reauth,
    update_shared_oauth_token,
)
from .api.art import SamsungTVAsyncArt
from .api.ipcontrol import (
    SamsungIPControl,
    SamsungIPControlAuthError,
    SamsungIPControlError,
    SamsungIPControlUnsupportedError,
)
from .api.samsungcast import SamsungCastTube
from .api.samsungws import ArtModeStatus, SamsungTVAsyncRest, SamsungTVWS
from .api.smartthings import SmartThingsCapabilityUnsupported, SmartThingsTV, STStatus
from .api.upnp import SamsungUPnP
from .art_mode_guard import ArtModeWriteSuppressed, guard_for
from .const import (
    ATTR_BRIGHTNESS,
    ATTR_CATEGORY_ID,
    ATTR_COLOR_TEMPERATURE,
    ATTR_CONTENT_ID,
    ATTR_DURATION,
    ATTR_ENABLED,
    ATTR_FILE_PATH,
    ATTR_FILE_TYPE,
    ATTR_FILTER_ID,
    ATTR_MATTE_ID,
    ATTR_SCREEN_RESOLUTION,
    ATTR_SHOW,
    ATTR_SHUFFLE,
    ATTR_STATUS,
    ATTR_TEXT,
    AUTH_METHOD_OAUTH,
    AUTH_METHOD_ST_ENTRY,
    CONF_APP_LAUNCH_METHOD,
    CONF_APP_LIST,
    CONF_APP_LOAD_METHOD,
    CONF_AUTH_METHOD,
    CONF_CHANNEL_LIST,
    CONF_DUMP_APPS,
    CONF_ENABLE_IP_CONTROL,
    CONF_EXT_POWER_ENTITY,
    CONF_IP_CONTROL_ART_MODE,
    CONF_IP_CONTROL_FW_VERSION,
    CONF_IP_CONTROL_MODEL_ID,
    CONF_IP_CONTROL_TOKEN,
    CONF_LOGO_OPTION,
    CONF_OAUTH_TOKEN,
    CONF_PING_PORT,
    CONF_POWER_ON_METHOD,
    CONF_REST_PORT,
    CONF_SHOW_CHANNEL_NR,
    CONF_SLIDESHOW_API,
    CONF_SOURCE_LIST,
    CONF_ST_ENTRY_UNIQUE_ID,
    CONF_ST_PICTURE_MODE_CAPABILITY,
    CONF_ST_POLL_ON_INTERVAL,
    CONF_SUPPORTS_GET_BRIGHTNESS,
    CONF_SUPPORTS_GET_COLOR_TEMPERATURE,
    CONF_SYNC_TURN_OFF,
    CONF_SYNC_TURN_ON,
    CONF_TOGGLE_ART_MODE,
    CONF_USE_LOCAL_LOGO,
    CONF_USE_MUTE_CHECK,
    CONF_USE_ST_CHANNEL_INFO,
    CONF_USE_ST_STATUS_INFO,
    CONF_WOL_REPEAT,
    CONF_WS_NAME,
    DATA_ART_API,
    DATA_CFG,
    DATA_IP_CONTROL_STATE_COORDINATOR,
    DATA_OPTIONS,
    DEFAULT_APP,
    DEFAULT_PORT,
    DEFAULT_SOURCE_LIST,
    DEFAULT_ST_POLL_ON_INTERVAL,
    DEFAULT_TIMEOUT,
    DOMAIN,
    LOCAL_LOGO_PATH,
    MAX_WOL_REPEAT,
    SERVICE_ART_AVAILABLE,
    SERVICE_ART_CHANGE_MATTE,
    SERVICE_ART_DELETE,
    SERVICE_ART_GET_ARTMODE,
    SERVICE_ART_GET_BRIGHTNESS,
    SERVICE_ART_GET_COLOR_TEMPERATURE,
    SERVICE_ART_GET_CURRENT,
    SERVICE_ART_GET_MATTE_LIST,
    SERVICE_ART_GET_PHOTO_FILTER_LIST,
    SERVICE_ART_GET_THUMBNAIL,
    SERVICE_ART_GET_THUMBNAILS_BATCH,
    SERVICE_ART_IDENTIFY,
    SERVICE_ART_SELECT_IMAGE,
    SERVICE_ART_SET_ARTMODE,
    SERVICE_ART_SET_AUTO_ROTATION,
    SERVICE_ART_SET_BRIGHTNESS,
    SERVICE_ART_SET_COLOR_TEMPERATURE,
    SERVICE_ART_SET_FAVOURITE,
    SERVICE_ART_SET_PHOTO_FILTER,
    SERVICE_ART_SET_SLIDESHOW,
    SERVICE_ART_UPLOAD,
    SERVICE_ART_UPLOAD_BATCH,
    SERVICE_SELECT_PICTURE_MODE,
    SERVICE_SEND_TEXT,
    SERVICE_START_HUE_SYNC,
    SERVICE_STOP_HUE_SYNC,
    SIGNAL_CONFIG_ENTITY,
    ST_POLL_OFF_INTERVAL,
    STD_APP_LIST,
    TUNER_INPUT_SOURCES,
    WS_PREFIX,
    AppLaunchMethod,
    AppLoadMethod,
    PowerOnMethod,
    ip_control_port,
)
from .entity import SamsungTVEntity
from .logo import LOGO_OPTION_DEFAULT, LocalImageUrl, Logo, LogoOption
from .picture_mode_keys import picture_mode_ws_key
from .token_notify import (
    METHOD_IP_CONTROL,
    METHOD_LOCAL,
    METHOD_SMARTTHINGS,
    clear_token_problem,
    notify_token_problem,
)

ATTR_ART_MODE_STATUS = "art_mode_status"

# Media title shown when the Frame is displaying Art Mode. SmartThings reports
# the "running app" as "art" in that case; we surface a friendly title and use
# the currently displayed artwork (current.jpg) as the media image.
ART_MODE_MEDIA_TITLE = "Art Mode"
ATTR_IP_ADDRESS = "ip_address"
ATTR_PICTURE_MODE = "picture_mode"
ATTR_PICTURE_MODE_LIST = "picture_mode_list"
ATTR_CONFIG_ENTRY_ID = "config_entry_id"


class _SkipPowerOn(Exception):
    """Internal: leave the power-on block without sending the power key."""


CMD_OPEN_BROWSER = "open_browser"
CMD_RUN_APP = "run_app"
CMD_RUN_APP_REMOTE = "run_app_remote"
CMD_RUN_APP_REST = "run_app_rest"
CMD_SEND_KEY = "send_key"
CMD_SEND_TEXT = "send_text"

DELAYED_SOURCE_TIMEOUT = 80
# Timeout for the periodic power probe of a TV we ALREADY believe is off.
# DEFAULT_TIMEOUT (6 s) is longer than SCAN_INTERVAL (5 s), so a TV that has
# left the network cannot possibly be polled within its own interval: every
# single poll overran, and both this integration and Home Assistant core
# logged a warning for it (#248 measured ~5 000 lines/day per sleeping TV).
# A probe that fails in 3 s finishes inside the interval, which removes the
# cause rather than hiding the symptom. The generous timeout is kept for a
# TV believed to be ON, where a slow answer is worth waiting for.
POWER_PROBE_OFF_TIMEOUT = 3.0
# Minimum gap between two slow-update warnings for one entity, and the
# suppressed count is reported with the next one.
SLOW_UPDATE_WARN_INTERVAL = 300.0
KEYHOLD_MAX_DELAY = 5.0
KEYPRESS_DEFAULT_DELAY = 0.5
KEYPRESS_MAX_DELAY = 2.0
KEYPRESS_MIN_DELAY = 0.2
# Delay after KEY_HOME when waking the panel from Art Mode before a source
# switch. 2024 Frames take noticeably longer than the standard key delay to
# leave Art Mode and render the home UI; sending the source key too early makes
# the TV reject it with an on-screen "not available" error (reported by Preston,
# #40). Mirrors the manual "home, wait, source" workaround.
SOURCE_WAKE_DELAY = 1.5
MAX_ST_ERROR_COUNT = 4
# Consecutive authorization failures from SmartThings before we treat the
# device as gone rather than the cloud as flaky, and slow the poll right down.
MAX_ST_AUTH_ERROR_COUNT = 3
# Poll interval (seconds) once SmartThings has refused the device id. A 403 is
# not transient -- the id is wrong until the user reconfigures -- so retrying
# every 5s only burns API quota and stretches the update cycle (#: a repaired
# TV produced 1591 refusals in two hours). Long enough to be harmless, short
# enough that a fix is noticed without a restart.
ST_POLL_AUTH_ERROR_INTERVAL = 300
MEDIA_TYPE_BROWSER = "browser"
MEDIA_TYPE_KEY = "send_key"
MEDIA_TYPE_TEXT = "send_text"
POWER_OFF_DELAY = 20
ST_APP_SEPARATOR = "/"
ST_UPDATE_TIMEOUT = 5

YT_APP_IDS = ("111299001912", "9Ur5IzDKqV.TizenYouTube")
YT_VIDEO_QS = "v"
YT_SVIDEO = "/shorts/"

MAX_CONTROLLED_ENTITY = 4

SUPPORT_SAMSUNGTV_SMART = (
    MediaPlayerEntityFeature.PAUSE
    | MediaPlayerEntityFeature.VOLUME_SET
    | MediaPlayerEntityFeature.VOLUME_STEP
    | MediaPlayerEntityFeature.VOLUME_MUTE
    | MediaPlayerEntityFeature.PREVIOUS_TRACK
    | MediaPlayerEntityFeature.NEXT_TRACK
    | MediaPlayerEntityFeature.SELECT_SOURCE
    | MediaPlayerEntityFeature.TURN_OFF
    | MediaPlayerEntityFeature.TURN_ON
    | MediaPlayerEntityFeature.PLAY
    | MediaPlayerEntityFeature.PLAY_MEDIA
    | MediaPlayerEntityFeature.STOP
)

ST_API_KEY_UPDATE_INTERVAL = timedelta(minutes=30)
OAUTH_TOKEN_REFRESH_BUFFER = 300  # Refresh OAuth token 5 minutes before expiration
SCAN_INTERVAL = timedelta(seconds=5)
# Phase 2: background poll period for the authoritative Art Mode state via
# IP Control. Cheap LAN call (~50–100 ms over HTTPS:1516); matches the
# SmartThings polling cadence so the art mode switch feels just as
# responsive.
IP_ART_MODE_REFRESH_INTERVAL = timedelta(seconds=5)
# Consecutive IP Control transport failures tolerated before the cached
# art-mode value is considered stale and cleared (TV likely in deep standby
# with port 1516 closed). 3 × 5s = 15s worst-case staleness.
IP_ART_MODE_MAX_FAILURES = 3
# Device identity (model / firmware) is fetched via IP Control once at startup
# and then re-checked on this cadence, so a firmware upgrade is picked up
# without re-pairing. It is a get-only LAN call and the value rarely changes,
# so a daily refresh is plenty.
IP_DEVICE_INFO_REFRESH_INTERVAL = timedelta(hours=24)

_LOGGER = logging.getLogger(__name__)


class _DeviceLoggerAdapter(logging.LoggerAdapter):
    """Prefix every log line with the TV's host so multi-TV logs can be told apart."""

    def process(self, msg, kwargs):
        return f"[{self.extra['host']}] {msg}", kwargs


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> None:
    """Set up the Samsung TV from a config entry."""

    # session used by aiohttp
    session = async_get_clientsession(hass)
    local_logo_path = hass.data[DOMAIN].get(LOCAL_LOGO_PATH)
    config = hass.data[DOMAIN][entry.entry_id][DATA_CFG]

    logo_file = hass.config.path(STORAGE_DIR, f"{DOMAIN}_logo_paths")

    def update_token_func(token: str, token_key: str) -> None:
        """Update config entry with the new token."""
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, token_key: token}
        )

    async_add_entities(
        [
            SamsungTVDevice(
                config,
                entry.entry_id,
                hass.data[DOMAIN][entry.entry_id],
                session,
                update_token_func,
                logo_file,
                local_logo_path,
            )
        ],
        True,
    )

    # register services
    platform = entity_platform.current_platform.get()
    platform.async_register_entity_service(
        SERVICE_SELECT_PICTURE_MODE,
        {vol.Required(ATTR_PICTURE_MODE): cv.string},
        "async_select_picture_mode",
    )
    platform.async_register_entity_service(
        SERVICE_START_HUE_SYNC,
        {},
        "async_start_hue_sync",
    )
    platform.async_register_entity_service(
        SERVICE_STOP_HUE_SYNC,
        {},
        "async_stop_hue_sync",
    )

    # Frame Art Extended Services
    platform.async_register_entity_service(
        SERVICE_ART_GET_ARTMODE,
        {},
        "async_art_get_artmode",
        supports_response=SupportsResponse.OPTIONAL,
    )
    platform.async_register_entity_service(
        SERVICE_ART_SET_ARTMODE,
        {vol.Required(ATTR_ENABLED): cv.boolean},
        "async_art_set_artmode",
        supports_response=SupportsResponse.OPTIONAL,
    )
    platform.async_register_entity_service(
        SERVICE_ART_AVAILABLE,
        {vol.Optional(ATTR_CATEGORY_ID): cv.string},
        "async_art_available",
        supports_response=SupportsResponse.OPTIONAL,
    )
    platform.async_register_entity_service(
        SERVICE_ART_GET_CURRENT,
        {},
        "async_art_get_current",
        supports_response=SupportsResponse.OPTIONAL,
    )
    platform.async_register_entity_service(
        SERVICE_ART_IDENTIFY,
        {vol.Optional("force", default=False): cv.boolean},
        "async_art_identify",
        supports_response=SupportsResponse.OPTIONAL,
    )
    platform.async_register_entity_service(
        SERVICE_ART_SELECT_IMAGE,
        {
            vol.Required(ATTR_CONTENT_ID): cv.string,
            vol.Optional(ATTR_CATEGORY_ID): cv.string,
            vol.Optional(ATTR_SHOW, default=True): cv.boolean,
        },
        "async_art_select_image",
        supports_response=SupportsResponse.OPTIONAL,
    )
    platform.async_register_entity_service(
        SERVICE_ART_UPLOAD,
        {
            vol.Required(ATTR_FILE_PATH): cv.string,
            vol.Optional(ATTR_MATTE_ID, default="shadowbox_polar"): cv.string,
            vol.Optional(ATTR_FILE_TYPE, default="jpg"): cv.string,
            vol.Optional(ATTR_SHOW, default=False): cv.boolean,
        },
        "async_art_upload",
        supports_response=SupportsResponse.OPTIONAL,
    )
    platform.async_register_entity_service(
        SERVICE_ART_UPLOAD_BATCH,
        {
            vol.Required("folder"): cv.string,
            vol.Optional(ATTR_MATTE_ID, default="shadowbox_polar"): cv.string,
            vol.Optional("throttle", default=2.0): vol.All(
                vol.Coerce(float), vol.Range(min=0, max=30)
            ),
            vol.Optional("skip_unchanged", default=True): cv.boolean,
            vol.Optional("dedup", default=False): cv.boolean,
        },
        "async_art_upload_batch",
        supports_response=SupportsResponse.OPTIONAL,
    )
    platform.async_register_entity_service(
        SERVICE_ART_DELETE,
        {vol.Required(ATTR_CONTENT_ID): cv.string},
        "async_art_delete",
        supports_response=SupportsResponse.OPTIONAL,
    )
    platform.async_register_entity_service(
        SERVICE_SEND_TEXT,
        {vol.Required(ATTR_TEXT): cv.string},
        "async_send_text",
    )
    platform.async_register_entity_service(
        SERVICE_ART_GET_THUMBNAIL,
        {vol.Required(ATTR_CONTENT_ID): cv.string},
        "async_art_get_thumbnail",
        supports_response=SupportsResponse.OPTIONAL,
    )
    platform.async_register_entity_service(
        SERVICE_ART_GET_THUMBNAILS_BATCH,
        {
            vol.Optional(ATTR_CATEGORY_ID): cv.string,
            vol.Optional("favorites_only", default=False): cv.boolean,
            vol.Optional("personal_only", default=False): cv.boolean,
            vol.Optional("force_download", default=False): cv.boolean,
            vol.Optional("cleanup_orphans", default=True): cv.boolean,
        },
        "async_art_get_thumbnails_batch",
        supports_response=SupportsResponse.OPTIONAL,
    )
    platform.async_register_entity_service(
        SERVICE_ART_SET_BRIGHTNESS,
        {vol.Required(ATTR_BRIGHTNESS): vol.All(vol.Coerce(int), vol.Range(0, 100))},
        "async_art_set_brightness",
        supports_response=SupportsResponse.OPTIONAL,
    )
    platform.async_register_entity_service(
        SERVICE_ART_GET_BRIGHTNESS,
        {},
        "async_art_get_brightness",
        supports_response=SupportsResponse.OPTIONAL,
    )
    platform.async_register_entity_service(
        SERVICE_ART_SET_COLOR_TEMPERATURE,
        {
            vol.Required(ATTR_COLOR_TEMPERATURE): vol.All(
                vol.Coerce(int), vol.Range(-5, 5)
            )
        },
        "async_art_set_color_temperature",
        supports_response=SupportsResponse.OPTIONAL,
    )
    platform.async_register_entity_service(
        SERVICE_ART_GET_COLOR_TEMPERATURE,
        {},
        "async_art_get_color_temperature",
        supports_response=SupportsResponse.OPTIONAL,
    )
    platform.async_register_entity_service(
        SERVICE_ART_CHANGE_MATTE,
        {
            vol.Required(ATTR_CONTENT_ID): cv.string,
            vol.Required(ATTR_MATTE_ID): cv.string,
        },
        "async_art_change_matte",
        supports_response=SupportsResponse.OPTIONAL,
    )
    platform.async_register_entity_service(
        SERVICE_ART_SET_PHOTO_FILTER,
        {
            vol.Required(ATTR_CONTENT_ID): cv.string,
            vol.Required(ATTR_FILTER_ID): cv.string,
        },
        "async_art_set_photo_filter",
        supports_response=SupportsResponse.OPTIONAL,
    )
    platform.async_register_entity_service(
        SERVICE_ART_GET_PHOTO_FILTER_LIST,
        {},
        "async_art_get_photo_filter_list",
        supports_response=SupportsResponse.OPTIONAL,
    )
    platform.async_register_entity_service(
        SERVICE_ART_GET_MATTE_LIST,
        {},
        "async_art_get_matte_list",
        supports_response=SupportsResponse.OPTIONAL,
    )
    platform.async_register_entity_service(
        SERVICE_ART_SET_FAVOURITE,
        {
            vol.Required(ATTR_CONTENT_ID): cv.string,
            vol.Optional(ATTR_STATUS, default="on"): cv.string,
        },
        "async_art_set_favourite",
        supports_response=SupportsResponse.OPTIONAL,
    )
    platform.async_register_entity_service(
        SERVICE_ART_SET_SLIDESHOW,
        {
            # Accept any string: known presets ('3min', '15min', '1h', '12h',
            # '1d', '7d') OR arbitrary integer-coercible values in minutes
            # (e.g. '30', '30min', '180'). Different Frame TV models support
            # different duration sets; the implementation validates the
            # semantic value and falls back to the integer-minutes path for
            # anything not in the preset map.
            vol.Required(ATTR_DURATION): cv.string,
            vol.Optional(ATTR_SHUFFLE, default=True): cv.boolean,
            vol.Optional(ATTR_CATEGORY_ID, default=2): vol.All(
                vol.Coerce(int), vol.Range(2, 8)
            ),
        },
        "async_art_set_slideshow",
        supports_response=SupportsResponse.OPTIONAL,
    )
    platform.async_register_entity_service(
        SERVICE_ART_SET_AUTO_ROTATION,
        {
            # Accept any string: known presets OR arbitrary integer-coercible
            # values in minutes. See art_set_slideshow above for rationale.
            vol.Required(ATTR_DURATION): cv.string,
            vol.Optional(ATTR_SHUFFLE, default=True): cv.boolean,
            vol.Optional(ATTR_CATEGORY_ID, default=2): vol.All(
                vol.Coerce(int), vol.Range(2, 8)
            ),
        },
        "async_art_set_auto_rotation",
        supports_response=SupportsResponse.OPTIONAL,
    )


def _get_default_app_info(app_id):
    """Get information for default app."""
    if not app_id:
        return None, None, None

    if app_id in STD_APP_LIST:
        info = STD_APP_LIST[app_id]
        return app_id, info.get("st_app_id"), info.get("logo")

    for info in STD_APP_LIST.values():
        st_app_id = info.get("st_app_id", "")
        if st_app_id == app_id:
            return app_id, None, info.get("logo")
    return None, None, None


class ArtModeSupport(Enum):
    """Define ArtMode support lever."""

    UNSUPPORTED = 0
    PARTIAL = 1
    FULL = 2


class SamsungTVDevice(SamsungTVEntity, MediaPlayerEntity):
    """Representation of a Samsung TV."""

    _attr_device_class = MediaPlayerDeviceClass.TV
    _attr_name = None

    def __init__(
        self,
        config: dict[str, Any],
        entry_id: str,
        entry_data: dict[str, Any] | None,
        session: ClientSession,
        update_token_func: Callable[[str, str], None],
        logo_file: str,
        local_logo_path: str | None,
    ) -> None:
        """Initialize the Samsung device."""

        super().__init__(config, entry_id)

        self._entry_data = entry_data
        self._entry_id = entry_id
        self._update_token_func = update_token_func
        self._host = config[CONF_HOST]
        self._log = _DeviceLoggerAdapter(_LOGGER, {"host": self._host})

        # Set entity attributes
        self._attr_media_title = None
        self._attr_media_image_url = None
        self._attr_media_image_remotely_accessible = False

        # Assume that the TV is not muted and volume is 0
        self._attr_is_volume_muted = False
        self._attr_volume_level = 0.0

        # Device information from TV
        self._device_info: dict[str, Any] | None = None

        # Save a reference to the imported config
        self._broadcast = config.get(CONF_BROADCAST_ADDRESS)

        # Assume that the TV is in Play mode and state is off
        self._playing = True
        self._state = MediaPlayerState.OFF

        # Mark the end of a shutdown command (need to wait 15 seconds before
        # sending the next command to avoid turning the TV back ON).
        self._started_up = False
        self._end_of_power_off = None
        self._fake_on = None
        self._delayed_set_source = None
        self._delayed_set_source_time = None

        # generic for sources and apps
        self._source = None
        self._running_app = None
        self._yt_app_id = None

        # prepare TV lists options
        self._default_source_used = False
        self._source_list = None
        self._dump_apps = True
        self._app_list = None
        self._app_list_st = None
        self._channel_list = None

        # config options reloaded on change
        self._use_st_status: bool = True
        self._use_channel_info: bool = True
        self._use_mute_check: bool = False
        self._show_channel_number: bool = False

        # ws initialization
        ws_name = config.get(CONF_WS_NAME, self._name)
        self._ws = SamsungTVWS(
            host=self._host,
            token=config.get(CONF_TOKEN),
            port=config.get(CONF_PORT, DEFAULT_PORT),
            timeout=config.get(CONF_TIMEOUT, DEFAULT_TIMEOUT),
            key_press_delay=KEYPRESS_DEFAULT_DELAY,
            name=f"{WS_PREFIX} {ws_name}",  # this is the name shown in the TV external device.
        )

        def new_token_callback():
            """Update config entry with the new token."""
            run_callback_threadsafe(
                self.hass.loop, update_token_func, self._ws.token, CONF_TOKEN
            )

        self._ws.register_new_token_callback(new_token_callback)

        def auth_error_callback():
            """Raise the local-token notification when reconnection is paused."""
            run_callback_threadsafe(self.hass.loop, self._notify_local_token_problem)

        def auth_recovered_callback():
            """Clear the local-token notification once authorized again."""
            run_callback_threadsafe(self.hass.loop, self._clear_local_token_problem)

        self._ws.register_auth_error_callback(auth_error_callback)
        self._ws.register_auth_recovered_callback(auth_recovered_callback)

        def ws_port_changed_callback(port):
            """Persist the remote channel's self-healed port (8001<->8002)."""
            run_callback_threadsafe(self.hass.loop, self._persist_art_port, port)

        self._ws.register_port_changed_callback(ws_port_changed_callback)

        # rest api initialization
        self._rest_api = SamsungTVAsyncRest(
            host=self._host,
            session=session,
            timeout=DEFAULT_TIMEOUT,
            # REST keeps its own learned port, decoupled from CONF_PORT (the
            # WS/token + Art port). On ~2020 Frames REST answers on 8001 while
            # the secure WS/Art channel is on 8002 — sharing one port made the
            # two self-heals overwrite each other forever. Falls back to
            # CONF_PORT for existing installs / single-port TVs.
            port=config.get(CONF_REST_PORT) or config.get(CONF_PORT, DEFAULT_PORT),
        )
        self._rest_api.register_port_callback(self._persist_rest_port)

        # Frame Art API - use shared instance if available, otherwise create new one
        shared_art_api = entry_data.get(DATA_ART_API) if entry_data else None
        if shared_art_api:
            self._art_api = shared_art_api
            self._log.debug("Using shared Frame Art API instance")
            # Disable the old SamsungArt thread in samsungws.py to prevent
            # competing WebSocket connections on the art-app channel.
            # Multiple clients cause the TV to route d2d_service_message
            # responses unpredictably, resulting in art.py timeouts.
            self._ws.disable_art_thread()
        else:
            self._art_api = SamsungTVAsyncArt(
                host=self._host,
                port=config.get(CONF_PORT, DEFAULT_PORT),
                token=config.get(CONF_TOKEN),
                session=session,
                timeout=DEFAULT_TIMEOUT,
                name=f"{WS_PREFIX} {ws_name} Art",
                supports_get_brightness=config.get(CONF_SUPPORTS_GET_BRIGHTNESS),
                supports_get_color_temperature=config.get(
                    CONF_SUPPORTS_GET_COLOR_TEMPERATURE
                ),
            )
        self._art_api.register_capability_callback(self._persist_art_capability)
        self._art_api.register_port_callback(self._persist_art_port)
        self._art_api.register_art_event_callback(self._on_art_transition)
        self._frame_tv_supported: bool | None = None
        self._frame_art_last_result: dict | None = None

        # Phase 2: cached authoritative Art Mode state read via IP Control
        # (artModeControl). Updated by a periodic background task started in
        # async_added_to_hass; consumed (synchronously) by extra_state_attributes
        # to populate art_mode_status without falling back to the device_info
        # PowerState='standby' override or the potentially-stale art_api cache.
        self._ip_control_client: SamsungIPControl | None = None
        # Slow-update warning throttle: last warning time and how many
        # overruns it has stood for since (#248).
        self._slow_update_last_warn: float = 0.0
        self._slow_update_suppressed: int = 0
        self._ip_control_token_cached: str | None = None
        # directVolumeControl is model-dependent. Some Samsung TVs support
        # absolute volume locally, including with an external HDMI/eARC audio
        # device, while others do not. None means "not probed yet".
        self._ip_absolute_volume_supported: bool | None = None
        # Current physical input reported by the shared getTVStates coordinator.
        # Also updated optimistically after a successful local source change.
        self._ip_input_source: str | None = None
        # Content ids whose thumbnail is still being retried in the
        # background after an upload; a failed fetch for one of these is an
        # expected step, not a fault. See _retry_new_thumbnail.
        self._thumbnail_retry_pending: set[str] = set()
        # Last SmartThings input that matched nothing in the source list,
        # so the warning above is logged once per change, not per poll.
        self._last_unmapped_source: str | None = None
        self._ip_art_mode: bool | None = None
        # Consecutive transport-failure count; the cache is cleared once it
        # reaches IP_ART_MODE_MAX_FAILURES so a TV that stops answering on
        # port 1516 (deep standby) cannot pin a stale "art mode on" forever.
        self._ip_art_mode_failures = 0
        self._ip_art_mode_refresh_unsub: Callable[[], None] | None = None
        self._ip_art_mode_initial_task: asyncio.Task | None = None
        self._ip_device_info_refresh_unsub: Callable[[], None] | None = None
        self._ip_device_info_initial_task: asyncio.Task | None = None

        # upnp initialization
        self._upnp = SamsungUPnP(host=self._host, session=session)

        # smartthings initialization
        st_entry_uniqueid: str | None = config.get(CONF_ST_ENTRY_UNIQUE_ID)
        auth_method: str | None = config.get(CONF_AUTH_METHOD)

        # Entries created through the OAuth flow can carry a different
        # auth_method label (e.g. "pat", since the access token doubles as
        # the API key) while still holding a refreshable oauth_token. Treat
        # those as OAuth: their access token expires after 24 hours, and
        # skipping the refresh path kills SmartThings a day later.
        oauth_token = config.get(CONF_OAUTH_TOKEN)
        if (
            auth_method != AUTH_METHOD_OAUTH
            and isinstance(oauth_token, dict)
            and oauth_token.get("refresh_token")
        ):
            auth_method = AUTH_METHOD_OAUTH

        # Store auth method for later use
        self._auth_method = auth_method

        def api_key_callback() -> str | None:
            """Get current api key - for OAuth, read from entry data after refresh."""
            if self._auth_method == AUTH_METHOD_OAUTH:
                # For OAuth, always read from entry data to get refreshed token
                entry = self.hass.config_entries.async_get_entry(self._entry_id)
                if entry:
                    oauth_token = entry.data.get(CONF_OAUTH_TOKEN)
                    if oauth_token and isinstance(oauth_token, dict):
                        new_token = oauth_token.get("access_token")
                        if new_token and new_token != self._st_api_key:
                            self._log.debug("OAuth token updated from entry data")
                            self._st_api_key = new_token
                            return new_token
                return self._st_api_key
            return self._update_smartthing_token(st_entry_uniqueid, update_token_func)

        self._st = None
        self._st_api_key = config.get(CONF_API_KEY)
        device_id = config.get(CONF_DEVICE_ID)

        # For OAuth method, get token from oauth_token if api_key is not set
        if auth_method == AUTH_METHOD_OAUTH and not self._st_api_key:
            oauth_token = config.get(CONF_OAUTH_TOKEN)
            if oauth_token and isinstance(oauth_token, dict):
                self._st_api_key = oauth_token.get("access_token")
                self._log.debug("Using OAuth access token for SmartThings API")

        if self._st_api_key and device_id:
            # Use callback for both ST_ENTRY and OAuth methods
            use_callbck: bool = (
                auth_method == AUTH_METHOD_ST_ENTRY and st_entry_uniqueid is not None
            ) or auth_method == AUTH_METHOD_OAUTH
            self._st = SmartThingsTV(
                api_key=self._st_api_key,
                device_id=device_id,
                use_channel_info=True,
                session=session,
                api_key_callback=api_key_callback if use_callbck else None,
                host=self._host,
            )
            # Seed the setPictureMode capability verified on a previous run
            # (learned by verify-and-fallback, persisted in entry.data) and
            # wire the persistence callback so a newly verified capability
            # survives HA restarts.
            self._st.set_verified_picture_mode_capability(
                config.get(CONF_ST_PICTURE_MODE_CAPABILITY)
            )
            self._st.picture_mode_capability_persist_cb = (
                self._persist_st_picture_mode_capability
            )

        self._st_error_count = 0
        self._st_auth_error_count = 0
        self._st_last_exc = None
        self._st_sources_loaded = False
        # SmartThings poll throttling: the local WebSocket is the primary state
        # source; SmartThings is only polled for cloud-only data at a slower
        # cadence (configurable when ON, fixed keepalive when OFF).
        self._st_last_poll = 0.0
        self._st_poll_on_interval = DEFAULT_ST_POLL_ON_INTERVAL
        self._ws_was_connected = False
        self._setvolumebyst = False

        # logo control initializzation
        self._local_image_url = LocalImageUrl(local_logo_path)
        self._logo_option = LOGO_OPTION_DEFAULT
        self._logo = Logo(
            logo_option=self._logo_option,
            logo_file_download=logo_file,
            session=session,
        )

        # YouTube cast
        self._cast_api = SamsungCastTube(self._host)

        # update config options for first time
        self._update_config_options(True)

    @Throttle(ST_API_KEY_UPDATE_INTERVAL)
    @callback
    def _update_smartthing_token(
        self, st_unique_id: str, update_token_func: Callable[[str, str], None]
    ) -> str | None:
        """Update the smartthing token when change on native integration.

        Note: For OAuth method, this function should not be called as the token
        is managed by Home Assistant's OAuth flow. The callback is disabled for OAuth.
        """
        # For OAuth, token refresh is handled elsewhere
        if self._auth_method == AUTH_METHOD_OAUTH:
            self._log.debug("OAuth token refresh handled by HA OAuth flow")
            return self._st_api_key

        self._log.debug("Trying to update smartthing access token")
        if not (new_token := get_smartthings_api_key(self.hass, st_unique_id)):
            self._log.warning(
                "Failed to retrieve SmartThings integration access token,"
                " using last available"
            )
            return self._st_api_key

        if new_token != self._st_api_key:
            self._log.info("SmartThings access token updated")
            update_token_func(new_token, CONF_API_KEY)
            self._st_api_key = new_token

        return self._st_api_key

    async def _async_refresh_oauth_token(self) -> bool:
        """Refresh OAuth token if needed.

        Returns True if token was refreshed or is still valid, False on error.
        """
        if self._auth_method != AUTH_METHOD_OAUTH:
            return True

        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if not entry:
            self._log.warning("Could not find config entry for OAuth refresh")
            return False

        # Entries that share a refresh token also share this lock. The first
        # refresh propagates the rotated token before releasing it; waiters then
        # adopt that stored token instead of submitting the invalid predecessor.
        lock = get_oauth_refresh_lock(entry)
        async with lock:
            # Double-check after acquiring lock - another entity might have refreshed
            entry = self.hass.config_entries.async_get_entry(self._entry_id)
            if entry:
                oauth_token = entry.data.get(CONF_OAUTH_TOKEN, {})
                expires_at = oauth_token.get("expires_at", 0)
                if expires_at > time.time() + OAUTH_TOKEN_REFRESH_BUFFER:
                    # Token was refreshed by another entity
                    new_token = oauth_token.get("access_token")
                    if new_token and new_token != self._st_api_key:
                        self._log.debug(
                            "Token was refreshed by another entity, using new token"
                        )
                        self._st_api_key = new_token
                        if self._st:
                            self._st._api_key = new_token
                            self._st._st.authenticate(new_token)
                    return True

            set_oauth_refresh_in_progress(self._entry_id, True)
            try:
                return await self._do_oauth_refresh()
            finally:
                set_oauth_refresh_in_progress(self._entry_id, False)

    def _raise_oauth_issue(self) -> None:
        """Surface a Repairs issue when SmartThings OAuth can't be refreshed.

        Uses the issue registry so the alert shows up in
        Settings -> Repairs, is translatable, and is automatically
        cleared once a refresh succeeds again.
        """
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        device_name = entry.title if entry else (self.name or "this Samsung TV")
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            f"oauth_auth_failed_{self._entry_id}",
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key="oauth_auth_failed",
            translation_placeholders={"device_name": device_name},
        )

    def _clear_oauth_issue(self) -> None:
        """Clear the OAuth Repairs issue once a refresh succeeds."""
        ir.async_delete_issue(self.hass, DOMAIN, f"oauth_auth_failed_{self._entry_id}")

    def _device_title(self) -> str:
        """Human-readable name for this entry, for notifications."""
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        return entry.title if entry else (self.name or "this Samsung TV")

    @callback
    def _notify_local_token_problem(self) -> None:
        """Raise the local-token persistent notification (WS PAT rejected)."""
        notify_token_problem(
            self.hass, self._entry_id, METHOD_LOCAL, self._device_title()
        )

    @callback
    def _clear_local_token_problem(self) -> None:
        """Clear the local-token persistent notification."""
        clear_token_problem(self.hass, self._entry_id, METHOD_LOCAL)

    @callback
    def _notify_ip_control_token_problem(self) -> None:
        """Raise the IP Control persistent notification (token rejected)."""
        notify_token_problem(
            self.hass, self._entry_id, METHOD_IP_CONTROL, self._device_title()
        )

    @callback
    def _clear_ip_control_token_problem(self) -> None:
        """Clear the IP Control persistent notification."""
        clear_token_problem(self.hass, self._entry_id, METHOD_IP_CONTROL)

    @callback
    def _persist_art_capability(self, flag_name: str, value: bool) -> None:
        """Persist a learned Art API get-capability to entry.data.

        Called (on the event loop) by the Art API the first time it determines
        whether the TV supports the dedicated get_brightness /
        get_color_temperature request, so the one-off detection probe is not
        re-paid on every restart. Only writes when the value actually changes.
        """
        key = (
            CONF_SUPPORTS_GET_BRIGHTNESS
            if flag_name == "brightness"
            else CONF_SUPPORTS_GET_COLOR_TEMPERATURE
        )
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if entry is None or entry.data.get(key) == value:
            return
        self.hass.config_entries.async_update_entry(
            entry, data={**entry.data, key: value}
        )

    def _persist_st_picture_mode_capability(self, capability: str) -> None:
        """Persist the verified setPictureMode capability to entry.data.

        Called (on the event loop) by the SmartThings API after a picture-mode
        change was VERIFIED applied on the panel, so later changes — including
        after an HA restart — try the working capability first (issue #116:
        some TVs answer 200 COMPLETED on one capability without applying it).
        The key is excluded from the reload fingerprint in __init__.py, so this
        write never triggers an integration reload.
        """
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if entry is None or entry.data.get(CONF_ST_PICTURE_MODE_CAPABILITY) == (
            capability
        ):
            return
        self.hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_ST_PICTURE_MODE_CAPABILITY: capability}
        )

    def _persist_art_port(self, port: int) -> None:
        """Persist the Art API's runtime port fallback to entry.data.

        Called (on the event loop) by the Art API when its configured port
        stopped responding and the alternate port (8001 <-> 8002) worked
        instead, so the next restart connects directly instead of paying the
        failed attempt again.
        """
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if entry is None or entry.data.get(CONF_PORT) == port:
            return
        self.hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_PORT: port}
        )

    def _persist_rest_port(self, port: int) -> None:
        """Persist the REST API's runtime port fallback to entry.data.

        Stored under CONF_REST_PORT, separate from CONF_PORT, so the REST
        self-heal (8001 <-> 8002) does not overwrite the WS/token + Art port.
        On ~2020 Frames the two channels legitimately need different ports;
        sharing one value made each self-heal undo the other's on every reload.
        """
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if entry is None or entry.data.get(CONF_REST_PORT) == port:
            return
        self.hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_REST_PORT: port}
        )

    async def _do_oauth_refresh(self) -> bool:
        """Perform the actual OAuth token refresh."""
        # Get current entry to check token expiration
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if not entry:
            self._log.warning("Could not find config entry for OAuth refresh")
            return False

        oauth_token = entry.data.get(CONF_OAUTH_TOKEN)
        if not oauth_token or not isinstance(oauth_token, dict):
            self._log.warning("No OAuth token found in config entry")
            return False

        # Check if refresh_token exists
        if "refresh_token" not in oauth_token:
            self._log.warning(
                "OAuth token does not contain refresh_token - token cannot be refreshed. "
                "Please reconfigure the integration with OAuth."
            )
            self._raise_oauth_issue()
            return False

        expires_at = oauth_token.get("expires_at", 0)
        current_time = time.time()

        # Check if token needs refresh (within buffer period before expiration)
        if expires_at and current_time < (expires_at - OAUTH_TOKEN_REFRESH_BUFFER):
            # Token still valid, no refresh needed. A valid token means any
            # previous invalid_grant condition has been resolved (reauth done).
            set_oauth_token_invalid(self._entry_id, False)
            return True

        # If the refresh token was already rejected (invalid_grant), a reauth
        # flow is pending — don't keep hammering the auth endpoint every cycle.
        if is_oauth_token_invalid(self._entry_id):
            self._log.debug("Skipping OAuth refresh — reauth pending")
            return False

        # Token is expiring or expired
        time_until_expiry = expires_at - current_time if expires_at else 0
        self._log.warning(
            "OAuth token %s (expires in %.0f seconds), attempting refresh",
            "expired" if time_until_expiry <= 0 else "expiring soon",
            time_until_expiry,
        )

        try:
            # Try to get implementation from entry
            implementation = None
            try:
                implementation = await config_entry_oauth2_flow.async_get_config_entry_implementation(
                    self.hass, entry
                )
            except Exception as ex:
                self._log.debug("Could not get implementation from entry: %s", ex)

            # If not found, try to get it directly from available implementations
            if not implementation:
                self._log.debug("Attempting to get OAuth implementation directly")
                try:
                    implementations = (
                        await config_entry_oauth2_flow.async_get_implementations(
                            self.hass, DOMAIN
                        )
                    )
                    if implementations:
                        # Use the first available implementation
                        implementation = list(implementations.values())[0]
                        self._log.debug(
                            "Found OAuth implementation: %s",
                            type(implementation).__name__,
                        )
                except Exception as impl_ex:
                    self._log.debug("Could not get implementations: %s", impl_ex)

            if not implementation:
                self._log.error(
                    "Could not get OAuth implementation - Application Credentials may be missing. "
                    "Go to Settings > Devices & Services > Application Credentials "
                    "and add credentials for Samsung TV Smart, then reconfigure the integration."
                )
                self._raise_oauth_issue()
                return False

            new_token = await implementation.async_refresh_token(oauth_token)

            update_shared_oauth_token(
                self.hass,
                entry,
                oauth_token,
                new_token,
            )

            # Update local api key
            self._st_api_key = new_token["access_token"]

            # Update SmartThingsTV directly (callback is disabled for OAuth)
            if self._st:
                self._st._api_key = self._st_api_key
                self._st._st.authenticate(self._st_api_key)
                self._log.debug("Updated SmartThingsTV with new OAuth token")

            self._log.info(
                "OAuth token refreshed successfully, new expiration in %.0f seconds",
                new_token.get("expires_at", 0) - time.time(),
            )
            self._clear_oauth_issue()
            set_oauth_token_invalid(self._entry_id, False)
            return True

        except Exception as ex:
            if is_invalid_grant_error(ex):
                # Terminal: the refresh token is dead. Trigger reauth and stop
                # retrying (start_oauth_reauth logs + latches the flag) so we
                # don't hammer the SmartThings auth endpoint every cycle.
                start_oauth_reauth(self.hass, self._entry_id)
            else:
                self._log.error(
                    "Failed to refresh OAuth token: %s. "
                    "You may need to reconfigure the integration with OAuth.",
                    ex,
                )
            self._raise_oauth_issue()
            return False

    def _get_ip_control_client(self) -> SamsungIPControl | None:
        """Return a live IP Control client if this entry is paired, else None.

        The token is read live from ``entry.data`` so that pairing or re-pairing
        done via the options flow takes effect immediately, with no reload of
        the integration required.
        """
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        token = entry.data.get(CONF_IP_CONTROL_TOKEN) if entry else None

        if not token or not self._get_option(CONF_ENABLE_IP_CONTROL, True):
            # Unpaired, or IP Control disabled.
            if self._ip_control_client is not None:
                self._ip_control_client = None
                self._ip_control_token_cached = None
            self._ip_absolute_volume_supported = None
            return None

        port = ip_control_port(entry.data)

        if (
            self._ip_control_client is None
            or self._ip_control_token_cached != token
            or self._ip_control_client.port != port
        ):
            self._ip_control_client = SamsungIPControl(
                self.hass, self._host, port=port, token=token
            )
            self._ip_control_token_cached = token
            self._ip_absolute_volume_supported = None

        return self._ip_control_client

    def _on_art_transition(self) -> None:
        """Confirm Art Mode via IP immediately on an art-channel transition.

        The IP Control ``getTVStates.pictureMode`` read is the authoritative
        panel state, but it is otherwise only polled every
        ``IP_ART_MODE_REFRESH_INTERVAL`` (5 s). When the async art channel
        broadcasts a transition (art_mode_changed / go_to_standby), refresh the
        IP cache straight away so ``art_mode_status`` — and the switch that
        mirrors it — reflects reality within ~1 s rather than up to 5 s. This
        is what keeps the switch from flapping back after a state change. The
        callback fires from the art receive loop (the event loop), so
        scheduling a task is safe; ``_refresh_ip_art_mode`` no-ops for TVs that
        are not IP-paired.

        On TVs without IP Control, ``art_api.art_mode`` (read directly by
        ``extra_state_attributes``) was just updated by the same event, but
        nothing publishes that change until the next polled update — up to
        ``SCAN_INTERVAL`` (5 s) later, and the Art Mode switch may poll in
        between and read the stale value. Write state immediately so the new
        ``art_mode_status`` is visible right away.
        """
        self.async_write_ha_state()
        self.hass.async_create_task(self._refresh_ip_art_mode())
        # Some models (e.g. 2024 Frames where IP Control cannot report the art
        # state and REST PowerState stays "on" in art mode) only learn about an
        # art-mode transition from SmartThings. Since the ST poll is throttled,
        # force it on the next update so entering/leaving Art Mode is reflected
        # within a couple of seconds instead of waiting up to the ST interval.
        if self._st:
            self._st_last_poll = 0.0
            self.async_schedule_update_ha_state(True)

    async def _refresh_ip_art_mode(self, _now=None) -> None:
        """Refresh the cached Art Mode value via IP Control.

        Called periodically by ``async_track_time_interval`` and once eagerly
        from ``async_added_to_hass`` so the first value is available shortly
        after startup. The cached result is consumed by
        ``extra_state_attributes`` as the authoritative source for
        ``art_mode_status`` when this TV is paired.

        The power state is read FIRST (powerControl): artModeControl alone
        cannot tell a powered-off TV from one displaying art — it keeps
        answering with the last mode even when the panel is off. powerControl,
        by contrast, reports 'powerOn' while Art Mode is displayed and
        'powerOff' when the TV is really off, so combining both yields a
        cache that is safe to trust unconditionally. This also makes the
        cache independent of the REST device-info ``PowerState``, which 2024+
        Frame models report as 'standby' WHILE actively displaying art.

        Two operating modes depending on the ``CONF_IP_CONTROL_ART_MODE``
        option (off by default for firmware safety):
        - Option ON → full read: powerControl + artModeControl, caching the
          real art state (True/False).
        - Option OFF → SAFE power-only guard: only powerControl is read. When
          it reports 'powerOff' the cache is pinned to ``False`` (the TV is off,
          so art_mode_status must be off, overriding a frozen art-channel
          WebSocket that keeps reporting 'on'); when it reports 'powerOn' the
          cache is cleared to ``None`` so the other sources decide. This never
          touches the firmware-risky artModeControl method.

        Failure handling:
        - No paired client (no token) → cache cleared to ``None``; the
          attribute falls through to the other (existing) sources.
        - Auth error (``-32010``) → cache cleared to ``None`` and a warning is
          logged so the user knows to re-pair.
        - Any other transport / TLS error → keep the last known value, log at
          debug. Transient unreachability (TV in deep standby briefly closing
          port 1516) should not invalidate a recent reading — but after
          IP_ART_MODE_MAX_FAILURES consecutive failures the cache is cleared
          so a TV that stays unreachable cannot pin a stale value forever.
        """
        client = self._get_ip_control_client()
        if client is None:
            # Un-paired, or the IP Control channel was disabled in options.
            # Disambiguate the two reasons in the logs, drop any cached value
            # and fall through to the other art-mode sources.
            entry = self.hass.config_entries.async_get_entry(self._entry_id)
            has_token = bool(entry and entry.data.get(CONF_IP_CONTROL_TOKEN))
            if not has_token:
                reason = "not paired (no CONF_IP_CONTROL_TOKEN in entry.data)"
            else:
                reason = "IP Control disabled in options (CONF_ENABLE_IP_CONTROL)"
            self._log.debug(
                "IP Control art-mode refresh for %s: %s — skipping",
                self._host,
                reason,
            )
            self._ip_art_mode_failures = 0
            if self._ip_art_mode is not None:
                self._ip_art_mode = None
                self.async_write_ha_state()
            return
        # Whether the user opted into the (firmware-risky) Art Mode reads via
        # artModeControl. Even when this is OFF we still run a SAFE power-only
        # guard below: powerControl is a harmless getter (the same one already
        # used for the Power switch), so reading it lets us force art_mode off
        # whenever the TV is genuinely powered off — without ever touching
        # artModeControl. This fixes the stale-WebSocket false positive where a
        # frozen art channel keeps reporting art='on' after the TV is off
        # (observed on the default config, where CONF_IP_CONTROL_ART_MODE is
        # off and nothing independently verified the WebSocket signal).
        art_mode_enabled = self._get_option(CONF_IP_CONTROL_ART_MODE, False)
        try:
            power = await client.async_get_power_state()
            if power == "powerOff":
                # TV is really off — art cannot be displayed. Don't query
                # artModeControl: it would answer with the last mode.
                value = False
            elif art_mode_enabled:
                value = await client.async_get_art_mode()
            else:
                # Power-only guard: TV is on but the user hasn't enabled the
                # art-mode reads, so we can't safely tell art from a real
                # input. Defer to the other (WebSocket / SmartThings) sources
                # by clearing the cache rather than pinning a value.
                value = None
        except SamsungIPControlAuthError as ex:
            self._log.warning(
                "IP Control art-mode read for %s: token rejected (%s) — "
                "re-pair via the integration options",
                self._host,
                ex,
            )
            self._notify_ip_control_token_problem()
            self._ip_art_mode_failures = 0
            if self._ip_art_mode is not None:
                self._ip_art_mode = None
                self.async_write_ha_state()
            return
        except SamsungIPControlError as ex:
            self._ip_art_mode_failures += 1
            if (
                self._ip_art_mode_failures >= IP_ART_MODE_MAX_FAILURES
                and self._ip_art_mode is not None
            ):
                self._log.debug(
                    "IP Control art-mode read for %s failed %d times in a row "
                    "(%s); clearing stale cached value (%s)",
                    self._host,
                    self._ip_art_mode_failures,
                    ex,
                    self._ip_art_mode,
                )
                self._ip_art_mode = None
                self.async_write_ha_state()
            else:
                self._log.debug(
                    "IP Control art-mode read for %s failed (%s); keeping last "
                    "value (failure %d/%d)",
                    self._host,
                    ex,
                    self._ip_art_mode_failures,
                    IP_ART_MODE_MAX_FAILURES,
                )
            return
        # A successful read means the token is valid again.
        self._ip_art_mode_failures = 0
        self._clear_ip_control_token_problem()
        if value != self._ip_art_mode:
            self._log.debug(
                "IP Control art-mode for %s changed: %s -> %s (writing state)",
                self._host,
                self._ip_art_mode,
                value,
            )
            self._ip_art_mode = value
            self.async_write_ha_state()
        else:
            self._log.debug(
                "IP Control art-mode for %s unchanged (%s)",
                self._host,
                value,
            )

    async def _refresh_device_information(self, _now=None) -> None:
        """Refresh the TV's model / firmware via IP Control and persist it.

        The model/firmware are first learned at pairing, but the firmware
        version can change after an over-the-air upgrade — and it gates which
        IP Control capabilities a TV exposes. So we re-read it periodically
        (see IP_DEVICE_INFO_REFRESH_INTERVAL) and write the new value back to
        entry.data only when it actually changed. No-ops when the TV isn't
        paired, is disabled, or is unreachable (logged at debug).
        """
        client = self._get_ip_control_client()
        if client is None:
            return
        try:
            info = await client.async_get_device_information()
        except SamsungIPControlError as ex:
            self._log.debug(
                "IP Control getDeviceInformation for %s failed (%s); "
                "keeping last known model/firmware",
                self._host,
                ex,
            )
            return
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if entry is None:
            return
        updates: dict[str, str] = {}
        if entry.data.get(CONF_IP_CONTROL_MODEL_ID) != info["modelID"]:
            updates[CONF_IP_CONTROL_MODEL_ID] = info["modelID"]
        if entry.data.get(CONF_IP_CONTROL_FW_VERSION) != info["FWVersion"]:
            updates[CONF_IP_CONTROL_FW_VERSION] = info["FWVersion"]
        if not updates:
            return
        self._log.info(
            "IP Control device info for %s updated: %s",
            self._host,
            updates,
        )
        self.hass.config_entries.async_update_entry(
            entry, data={**entry.data, **updates}
        )

    async def async_added_to_hass(self):
        """Set config parameter when add to hass."""
        await super().async_added_to_hass()

        # this will update config options when changed
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_CONFIG_ENTITY, self._update_config_options
            )
        )

        def update_status_callback():
            """Update current TV status."""
            run_callback_threadsafe(self.hass.loop, self._status_changed_callback)

        self._ws.register_status_callback(update_status_callback)
        await self.hass.async_add_executor_job(self._ws.start_poll)

        # If the WS layer resolved a different port than what is saved in
        # config (e.g. firmware update filtered port 8002 → fell back to 8001),
        # persist the new port so subsequent reloads use the correct one
        # without needing a manual reconfiguration.
        resolved_port = self._ws.port
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if entry and resolved_port and resolved_port != entry.data.get(CONF_PORT):
            self._log.warning(
                "SamsungTV %s: updating saved port from %s to %s"
                " (port auto-detected after connection)",
                self._host,
                entry.data.get(CONF_PORT),
                resolved_port,
            )
            self.hass.config_entries.async_update_entry(
                entry, data={**entry.data, CONF_PORT: resolved_port}
            )

        # Load SmartThings sources if configured
        if self._st:
            self._get_st_sources()

        # Phase 2: start periodic Art Mode poll via IP Control. The
        # _refresh_ip_art_mode method gracefully no-ops when this TV isn't
        # paired (no token), so this is safe to install unconditionally.
        self._log.debug(
            "Phase 2: installing IP Control art-mode refresh timer for %s "
            "(interval=%s)",
            self._host,
            IP_ART_MODE_REFRESH_INTERVAL,
        )
        self._ip_art_mode_refresh_unsub = async_track_time_interval(
            self.hass, self._refresh_ip_art_mode, IP_ART_MODE_REFRESH_INTERVAL
        )
        # Kick off a non-blocking initial read so the value is available
        # well before the first scheduled interval fires. Track the task so
        # it is cancelled if the entity is removed while the (executor-backed,
        # up to ~10s) IP request is still in flight — otherwise it could call
        # async_write_ha_state() on a removed entity.
        self._ip_art_mode_initial_task = self.hass.async_create_task(
            self._refresh_ip_art_mode()
        )

        # Periodically re-read model/firmware so an OTA firmware upgrade is
        # picked up without re-pairing. No-ops for unpaired TVs.
        self._ip_device_info_refresh_unsub = async_track_time_interval(
            self.hass,
            self._refresh_device_information,
            IP_DEVICE_INFO_REFRESH_INTERVAL,
        )
        self._ip_device_info_initial_task = self.hass.async_create_task(
            self._refresh_device_information()
        )

    async def async_will_remove_from_hass(self):
        """Run when entity will be removed from hass."""
        if self._ip_art_mode_initial_task is not None:
            self._ip_art_mode_initial_task.cancel()
            self._ip_art_mode_initial_task = None
        if self._ip_art_mode_refresh_unsub is not None:
            self._ip_art_mode_refresh_unsub()
            self._ip_art_mode_refresh_unsub = None
        if self._ip_device_info_initial_task is not None:
            self._ip_device_info_initial_task.cancel()
            self._ip_device_info_initial_task = None
        if self._ip_device_info_refresh_unsub is not None:
            self._ip_device_info_refresh_unsub()
            self._ip_device_info_refresh_unsub = None
        self._ws.unregister_status_callback()
        await self.hass.async_add_executor_job(self._ws.stop_poll)

    @staticmethod
    def _split_app_list(app_list: dict[str, str]) -> list[dict[str, str]]:
        """Split the application list for standard and SmartThings."""
        apps = {}
        apps_st = {}

        for app_name, app_ids in app_list.items():
            try:
                app_id_split = app_ids.split(ST_APP_SEPARATOR, 1)
            except (ValueError, AttributeError):
                _LOGGER.warning(
                    "Invalid ID [%s] for App [%s] will be ignored."
                    " Use integration options to correct the App ID",
                    app_ids,
                    app_name,
                )
                continue

            app_id = app_id_split[0]
            if len(app_id_split) == 1:
                _, st_app_id, _ = _get_default_app_info(app_id)
            else:
                st_app_id = app_id_split[1]

            apps[app_name] = app_id
            apps_st[app_name] = st_app_id or app_id

        return [apps, apps_st]

    def _load_tv_lists(self, first_load=False):
        """Load TV sources, apps and channels."""

        # load sources list
        default_source_used = False
        source_list = self._get_option(CONF_SOURCE_LIST, {})
        if not source_list:
            source_list = DEFAULT_SOURCE_LIST
            default_source_used = True
        self._source_list = source_list
        self._default_source_used = default_source_used

        # load apps list
        app_list = self._get_option(CONF_APP_LIST, {})
        if app_list:
            double_list = self._split_app_list(app_list)
            self._app_list = double_list[0]
            self._app_list_st = double_list[1]
        else:
            self._app_list = None if first_load else {}
            self._app_list_st = None if first_load else {}

        # load channels list
        self._channel_list = self._get_option(CONF_CHANNEL_LIST, {})

    @callback
    def _update_config_options(self, first_load=False):
        """Update config options."""
        self._load_tv_lists(first_load)
        self._use_st_status = self._get_option(CONF_USE_ST_STATUS_INFO, True)
        self._use_channel_info = self._get_option(CONF_USE_ST_CHANNEL_INFO, True)
        self._st_poll_on_interval = self._get_option(
            CONF_ST_POLL_ON_INTERVAL, DEFAULT_ST_POLL_ON_INTERVAL
        )
        self._use_mute_check = self._get_option(CONF_USE_MUTE_CHECK, False)
        self._show_channel_number = self._get_option(CONF_SHOW_CHANNEL_NR, False)
        self._ws.update_app_list(self._app_list)
        self._ws.set_ping_port(self._get_option(CONF_PING_PORT, 0))

    @callback
    def _status_changed_callback(self):
        """Called when status changed."""
        self._log.debug("status_changed_callback called")
        self.async_schedule_update_ha_state(True)

    def _get_option(self, param, default=None):
        """Get option from entity configuration."""
        if not self._entry_data:
            return default
        # DATA_OPTIONS can be transiently absent from _entry_data during a
        # reload/reconfigure (hass.data is repopulated step by step). Accessing
        # it unconditionally raised `KeyError: 'options'` and crashed callers
        # like the IP art-mode refresh timer ("Task exception was never
        # retrieved"). Fall back to the default instead.
        options = self._entry_data.get(DATA_OPTIONS)
        if not options:
            return default
        option = options.get(param)
        return default if option is None else option

    def _get_device_spec(self, key: str) -> Any | None:
        """Check if a flag exists in latest device info."""
        if not ((info := self._device_info) and (device := info.get("device"))):
            return None
        return device.get(key)

    def _power_off_in_progress(self):
        """Check if a power off request is in progress."""
        return (
            self._end_of_power_off is not None
            and self._end_of_power_off > dt_util.utcnow()
        )

    async def _update_volume_info(self):
        """Update the volume info."""
        if self._state != MediaPlayerState.ON:
            return

        # Prefer local IP Control when this TV implements directVolumeControl.
        # This is discovered per device rather than inferred from model/year.
        client = self._get_ip_control_client()

        if client is not None and self._ip_absolute_volume_supported is not False:
            try:
                volume = await client.async_get_volume()
            except SamsungIPControlUnsupportedError as ex:
                if self._ip_control_ambient_mode_active():
                    self._log.debug(
                        "IP Control absolute volume unavailable while Ambient mode "
                        "is active; not treating it as unsupported: %s",
                        ex,
                    )
                else:
                    self._ip_absolute_volume_supported = False
                    self._log.debug(
                        "IP Control absolute volume is not available on this TV: %s",
                        ex,
                    )
            except SamsungIPControlError as ex:
                # Network/state errors must not be mistaken for a missing
                # capability. We can retry on the next normal update.
                self._log.debug(
                    "IP Control absolute-volume read failed (%s); "
                    "falling back to UPnP",
                    ex,
                )
            else:
                if self._ip_absolute_volume_supported is not True:
                    self._log.info("IP Control absolute volume detected for this TV")

                self._ip_absolute_volume_supported = True
                self._attr_volume_level = volume / 100
                self._attr_is_volume_muted = await self._upnp.async_get_mute()
                return

        # Existing behaviour for TVs without local absolute-volume support.
        if (volume := await self._upnp.async_get_volume()) is not None:
            self._attr_volume_level = int(volume) / 100
        else:
            self._attr_volume_level = None

        self._attr_is_volume_muted = await self._upnp.async_get_mute()

    def _get_external_entity_status(self):
        """Get status from external binary sensor."""
        if not (ext_entity := self._get_option(CONF_EXT_POWER_ENTITY)):
            return True
        return not self.hass.states.is_state(ext_entity, STATE_OFF)

    async def _check_status(self):
        """Check TV status with WS and others method to check power status."""

        if self._get_device_spec("PowerState") is not None:
            self._log.debug("Checking if TV %s is on using device info", self._host)
            # Ensure we get an updated value. A TV we already believe to be off
            # gets the short probe timeout so this poll still fits inside the
            # scan interval; one believed on keeps the full timeout.
            probe_timeout = (
                None if self._state == MediaPlayerState.ON else POWER_PROBE_OFF_TIMEOUT
            )
            info = await self._async_load_device_info(force=True, timeout=probe_timeout)
            return info is not None and info["device"]["PowerState"] == "on"

        result = self._ws.is_connected
        if result and self._st:
            if (
                self._st.state == STStatus.STATE_OFF
                and self._st.prev_state != STStatus.STATE_OFF
                and self._state == MediaPlayerState.ON
                and self._use_st_status
            ):
                result = False

        if result:
            result = self._get_external_entity_status()

        if result:
            if self._ws.artmode_status in (ArtModeStatus.On, ArtModeStatus.Unavailable):
                result = False

        return result

    @callback
    def _resolve_app_name(self, app_id: str) -> str | None:
        """Resolve a SmartThings/Tizen app ID to a human-readable name."""
        # 1. Check configured ST apps (reverse: name -> st_id)
        if self._app_list_st:
            for name, st_id in self._app_list_st.items():
                if st_id == app_id:
                    return name

        # 2. Check configured apps by Tizen ID
        if self._app_list:
            for name, tizen_id in self._app_list.items():
                if tizen_id == app_id:
                    return name

        # 3. Check WS installed apps directly
        installed = self._ws.installed_app
        if installed:
            if app_id in installed:
                return installed[app_id].app_name
            for app in installed.values():
                if app.app_id == app_id:
                    return app.app_name

        # 4. Resolve via STD_APP_LIST: st_app_id -> tizen_id -> installed name
        for tizen_id, info in STD_APP_LIST.items():
            if info.get("st_app_id") == app_id or tizen_id == app_id:
                if installed and tizen_id in installed:
                    return installed[tizen_id].app_name
                # Use hardcoded name from STD_APP_LIST as fallback
                if info.get("name"):
                    return info["name"]
                return None

        self._log.debug(
            "App '%s' not found in any app list (configured=%d, installed=%d)",
            app_id,
            len(self._app_list) if self._app_list else 0,
            len(installed) if installed else 0,
        )
        # Last resort: clean up the SmartThings/Tizen ID into a readable name
        # e.g. "SKCwdZ5Hxp.swisscombluetv" -> "Swisscom Blue TV"
        # e.g. "HEPsqFNie0.tvplusstandalone" -> "TV Plus"
        return self._clean_app_id(app_id)

    @staticmethod
    def _clean_app_id(app_id: str) -> str:
        """Convert a raw SmartThings/Tizen app ID to a human-readable name.

        Examples:
            SKCwdZ5Hxp.swisscombluetv -> Swisscom Blue TV
            HEPsqFNie0.tvplusstandalone -> TV Plus
            org.tizen.netflix-app -> Netflix
        """
        import re

        # Take the part after the last dot
        name = app_id.rsplit(".", 1)[-1] if "." in app_id else app_id
        # Remove common suffixes
        for suffix in ("standalone", "-app", "app", "Launcher2"):
            if name.lower().endswith(suffix.lower()) and len(name) > len(suffix):
                name = name[: -len(suffix)]
        # Remove Tizen prefix
        if name.lower().startswith("tizen"):
            name = name[5:]
        # Insert spaces before uppercase letters (camelCase)
        name = re.sub(r"([a-z])([A-Z])", r"\1 \2", name)
        # Replace hyphens and underscores with spaces
        name = name.replace("-", " ").replace("_", " ")
        # Split known words that may be concatenated in lowercase
        _KNOWN_WORDS = {
            "tv": "TV",
            "plus": "Plus",
            "blue": "Blue",
            "swisscom": "Swisscom",
            "netflix": "Netflix",
            "youtube": "YouTube",
            "spotify": "Spotify",
            "disney": "Disney",
            "prime": "Prime",
            "video": "Video",
            "apple": "Apple",
            "amazon": "Amazon",
            "plex": "Plex",
            "hbo": "HBO",
            "hulu": "Hulu",
            "dazn": "DAZN",
        }
        # Try to split concatenated lowercase words
        result = name
        lower = name.lower().strip()
        if " " not in lower and len(lower) > 3:
            # Greedy match from known words
            remaining = lower
            parts = []
            while remaining:
                matched = False
                for word in sorted(_KNOWN_WORDS, key=len, reverse=True):
                    if remaining.startswith(word):
                        parts.append(_KNOWN_WORDS[word])
                        remaining = remaining[len(word) :]
                        matched = True
                        break
                if not matched:
                    parts.append(remaining.title())
                    break
            if parts:
                result = " ".join(parts)
            else:
                result = name.strip().title()
        else:
            result = name.strip().title()
        return result

    def _get_running_app(self):
        """Retrieve name of running apps."""

        st_running_app = None
        if self._app_list is not None:
            for app, app_id in self._app_list.items():
                if app_running := self._ws.is_app_running(app_id):
                    self._running_app = app
                    return
                if app_running is False:
                    continue
                if self._st and self._st.channel_name != "":
                    st_app_id = self._app_list_st.get(app, "")
                    if st_app_id == self._st.channel_name:
                        st_running_app = app

        self._running_app = st_running_app or DEFAULT_APP

    def _resolve_source_by_id(self, source_id: str) -> tuple[str, str] | None:
        """Resolve a technical source ID to (display_name, source_key).

        Allows automations to use either the display name (e.g. "Home cinéma")
        or the technical ID (e.g. "HDMI3") when selecting a source.
        """
        if not self._source_list:
            return None
        # source_list is {display_name: source_key}, e.g.
        # {"Home cinéma": "ST_HDMI3"} or {"PlayStation": "IP_HDMI2"}
        # Check if source_id matches a SmartThings or IP Control source key.
        source_keys = (f"ST_{source_id}", f"IP_{source_id}")
        for display_name, source_key in self._source_list.items():
            if source_key in source_keys:
                return (display_name, source_key)
        # Also check for TV variant (dtv, digitalTv -> ST_TV)
        if source_id.upper() in ["DTV", "DIGITALTV", "TV"]:
            for display_name, source_key in self._source_list.items():
                if source_key == "ST_TV":
                    return (display_name, source_key)
        return None

    def _get_st_sources(self):
        # A manually configured source list must always have priority
        custom_source_list = self._get_option(CONF_SOURCE_LIST, {})
        if custom_source_list:
            self._log.debug(
                "Samsung TV: using custom source list: %s",
                custom_source_list,
            )
            self._source_list = custom_source_list
            self._default_source_used = False
            return

        if not self._st:
            self._log.debug("SmartThings not configured, _get_st_sources not executed")
            return

        st_source_list = {}
        source_list = self._st.source_list
        self._log.debug(
            "Samsung TV: _get_st_sources called, st.source_list=%s (type=%s)",
            source_list,
            type(source_list).__name__,
        )
        if not source_list:
            return

        # source_list is a dict {source_id: source_name}
        # e.g. {"digitalTv": "digitalTv", "HDMI1": "PlayStation", "HDMI2": "HDMI2"}
        for source_id, source_name in source_list.items():
            try:
                is_tv = source_id.upper() in ["DIGITALTV", "TV", "DTV"]
                is_hdmi = source_id.startswith("HDMI")
                if is_tv or is_hdmi:
                    input_type = "ST_TV" if is_tv else "ST_" + source_id
                    if input_type in st_source_list.values():
                        continue

                    # Use custom name from map, or fall back to source_id
                    name = source_name if source_name != source_id else ""
                    st_source_list[name or source_id] = input_type

            except Exception:  # pylint: disable=broad-except
                pass

        if len(st_source_list) > 0:
            self._log.info(
                "Samsung TV: loaded sources list from SmartThings: %s",
                str(st_source_list),
            )
            self._source_list = st_source_list
            self._default_source_used = False

    def _gen_installed_app_list(self):
        """Get apps installed on TV."""

        if self._dump_apps:
            self._dump_apps = self._get_option(CONF_DUMP_APPS, False)

        if not (self._app_list is None or self._dump_apps):
            return

        app_list = self._ws.installed_app
        if not app_list:
            return

        app_load_method = AppLoadMethod(
            self._get_option(CONF_APP_LOAD_METHOD, AppLoadMethod.All.value)
        )

        # app_list is a list of dict
        filtered_app_list = {}
        filtered_app_list_st = {}
        dump_app_list = {}
        for app in app_list.values():
            try:
                app_name = app.app_name
                app_id = app.app_id
                def_app_id, st_app_id, _ = _get_default_app_info(app_id)
                # app_list is automatically created only with apps in hard coded short
                # list (STD_APP_LIST). Other available apps are dumped in a file that
                # can be used to create a custom list.
                # This is to avoid unuseful long list that can impact performance
                if app_load_method != AppLoadMethod.NotLoad:
                    if def_app_id or app_load_method == AppLoadMethod.All:
                        filtered_app_list[app_name] = app_id
                        filtered_app_list_st[app_name] = st_app_id or app_id

                dump_app_list[app_name] = (
                    app_id + ST_APP_SEPARATOR + st_app_id if st_app_id else app_id
                )

            except Exception:  # pylint: disable=broad-except
                pass

        if self._app_list is None:
            self._app_list = filtered_app_list
            self._app_list_st = filtered_app_list_st

        if self._dump_apps:
            self._log.info(
                "List of available apps for SamsungTV %s: %s",
                self._host,
                dump_app_list,
            )
            self._dump_apps = False

    def _get_ip_control_state_coordinator(self):
        """Return the shared getTVStates coordinator created by sensor.py."""
        return (
            self.hass.data.get(DOMAIN, {})
            .get(self._entry_id, {})
            .get(DATA_IP_CONTROL_STATE_COORDINATOR)
        )

    def _ip_control_panel_art_cached(self) -> bool | None:
        """Tri-state art state from the cached getTVStates snapshot.

        True when the panel reports pictureMode 'Ambient' (art is on screen),
        False when it reports any other picture mode (a real input), and None
        when it cannot be told — no coordinator, no snapshot yet, or the TV is
        powered off (a sleeping panel can't be read). Cached only: reads the IP
        Control state coordinator's last poll, never issues a request, so it is
        safe from a sync property. Independent of the art-mode option — this is
        getTVStates, not the wedge-prone artModeControl flag.
        """
        coordinator = self._get_ip_control_state_coordinator()
        data = getattr(coordinator, "data", None)
        if not isinstance(data, dict) or data.get("powered_off"):
            return None
        tv_states = data.get("tv")
        if not isinstance(tv_states, dict):
            return None
        mode = tv_states.get("pictureMode")
        if not isinstance(mode, str) or not mode:
            return None
        return mode == "Ambient"

    def _ip_control_ambient_mode_active(self) -> bool:
        """Return whether the shared IP Control snapshot reports Ambient mode."""
        return self._ip_control_panel_art_cached() is True

    def _get_ip_control_input_source(self) -> str | None:
        """Return the latest physical input from the shared IP Control snapshot."""
        # Do not consume a stale coordinator/cache after IP Control was disabled
        # or unpaired in Reconfigure.
        if self._get_ip_control_client() is None:
            self._ip_input_source = None
            return None

        coordinator = self._get_ip_control_state_coordinator()
        data = getattr(coordinator, "data", None)
        if isinstance(data, dict) and not data.get("powered_off"):
            tv_states = data.get("tv")
            if isinstance(tv_states, dict):
                value = tv_states.get("inputSource")
                if isinstance(value, str) and value:
                    self._ip_input_source = value

        return self._ip_input_source

    def _get_ip_control_channel(self) -> str | None:
        """Return the latest tuner channel from the shared IP Control snapshot."""
        if self._get_ip_control_client() is None:
            return None

        coordinator = self._get_ip_control_state_coordinator()
        data = getattr(coordinator, "data", None)
        if not isinstance(data, dict) or data.get("powered_off"):
            return None

        # The snapshot is refreshed at the configured IP Control poll cadence, so
        # after switching the TV away from its tuner the previous poll's channel
        # is still in `data` until the next one. Cross-check the input recorded
        # in the same snapshot, or media_channel would keep reporting a channel
        # number — and media_content_type would keep saying CHANNEL — for up to
        # one poll cycle while the TV is already on HDMI.
        tv_states = data.get("tv")
        if isinstance(tv_states, dict):
            input_source = tv_states.get("inputSource")
            if (
                isinstance(input_source, str)
                and input_source.casefold() not in TUNER_INPUT_SOURCES
            ):
                return None

        channel_states = data.get("channel")
        if not isinstance(channel_states, dict):
            return None

        value = channel_states.get("channelNum")
        if isinstance(value, (str, int)):
            channel = str(value).strip()
            if channel:
                return channel

        return None

    def _get_source(self):
        """Return the current input source."""
        if self.state != MediaPlayerState.ON:
            self._source = None
            return self._source

        # A running app is more specific than the physical TV input.
        if self._running_app != DEFAULT_APP:
            self._source = self._running_app
            return self._source

        # When IP Control is paired, prefer its local getTVStates.inputSource
        # over SmartThings. Resolve HDMI1/HDMI2/... back to the configured
        # display name (e.g. "PC" / "Chromecast") so media_player.source stays
        # consistent with source_list and mini-media-player selectors.
        if local_source := self._get_ip_control_input_source():
            if resolved := self._resolve_source_by_id(local_source):
                self._source = resolved[0]
                return self._source
            if self._source_list and local_source in self._source_list:
                self._source = local_source
                return self._source

        use_st: bool = self._st is not None and self._st.state == STStatus.STATE_ON
        if not use_st:
            self._source = self._running_app
            return self._source

        if self._st.source in ["digitalTv", "TV", "dtv"]:
            cloud_key = "ST_TV"
        elif self._st.source:
            cloud_key = "ST_" + self._st.source
        else:
            cloud_key = None

        found_source = self._running_app
        for attr, value in self._source_list.items():
            if value == cloud_key:
                found_source = attr
                break
        else:
            # SmartThings knows which input the TV is on, but no entry in the
            # source list maps to it, so the reported source silently stays at
            # the generic running-app value and looks like "it never updates"
            # (#230). Happens when the source list is the default (KEY_* values,
            # no ST_* keys) or when discovery returned only some of the inputs.
            if cloud_key and cloud_key != self._last_unmapped_source:
                self._log.debug(
                    "SmartThings reports input %s, but no source list entry "
                    "maps to it (list: %s) — reported source stays %s",
                    cloud_key,
                    self._source_list,
                    found_source,
                )
            self._last_unmapped_source = cloud_key

        self._source = found_source
        return self._source

    async def _smartthings_keys(self, source_key: str):
        """Manage the SmartThings key commands."""
        if not self._st:
            self._log.error(
                "SmartThings not configured. Command not valid: %s", source_key
            )
            return False
        if self._st.state != STStatus.STATE_ON:
            self._log.warning(
                "SmartThings not available. Command not sent: %s", source_key
            )
            return False

        if source_key.startswith("ST_HDMI"):
            await self._st.async_select_source(source_key.replace("ST_", ""))
        elif source_key == "ST_TV":
            await self._st.async_select_source("digitalTv")
        elif source_key.startswith("ST_VD:"):
            if cmd := source_key.replace("ST_VD:", ""):
                await self._st.async_select_vd_source(cmd)
        elif source_key == "ST_CHUP":
            await self._st.async_send_command("stepchannel", "up")
        elif source_key == "ST_CHDOWN":
            await self._st.async_send_command("stepchannel", "down")
        elif source_key.startswith("ST_CH"):
            ch_num = source_key.replace("ST_CH", "")
            if ch_num.isdigit():
                await self._st.async_send_command("selectchannel", ch_num)
        elif source_key in ["ST_MUTE", "ST_UNMUTE"]:
            await self._st.async_send_command(
                "audiomute", "off" if source_key == "ST_UNMUTE" else "on"
            )
        elif source_key == "ST_VOLUP":
            await self._st.async_send_command("stepvolume", "up")
        elif source_key == "ST_VOLDOWN":
            await self._st.async_send_command("stepvolume", "down")
        elif source_key.startswith("ST_VOL"):
            vol_lev = source_key.replace("ST_VOL", "")
            if vol_lev.isdigit():
                await self._st.async_send_command("setvolume", vol_lev)
        else:
            raise ValueError(f"Unsupported SmartThings command: {source_key}")

        return True

    def _log_st_error(self, st_error: bool):
        """Log start or end problem in ST communication"""
        if self._st_error_count == 0 and not st_error:
            return

        if st_error:
            if self._st_error_count == MAX_ST_ERROR_COUNT:
                return

            self._st_error_count += 1
            if self._st_error_count == MAX_ST_ERROR_COUNT:
                msg_chk = "Check connection status with TV on the phone App"
                if self._st_last_exc is not None:
                    self._log.error(
                        "%s - Error refreshing from SmartThings. %s. Error: %s",
                        self.entity_id,
                        msg_chk,
                        self._st_last_exc,
                    )
                else:
                    self._log.warning(
                        "%s - SmartThings report TV is off but status detected is on. %s",
                        self.entity_id,
                        msg_chk,
                    )
            return

        if self._st_error_count >= MAX_ST_ERROR_COUNT:
            self._log.warning("%s - Connection to SmartThings restored", self.entity_id)
        self._st_error_count = 0

    async def _async_load_device_info(
        self, force: bool = False, timeout: float | None = None
    ) -> dict[str, Any] | None:
        """Try to gather infos of this TV.

        ``timeout`` overrides the REST client's own (longer) timeout for this
        one call — see POWER_PROBE_OFF_TIMEOUT. A timeout is indistinguishable
        from any other failure here: both mean "no answer", and both return
        None.
        """
        if self._device_info is not None and not force:
            return self._device_info

        try:
            request = self._rest_api.async_rest_device_info()
            if timeout is not None:
                request = asyncio.wait_for(request, timeout=timeout)
            device_info: dict[str, Any] = await request
            # This payload is ~40 mostly-immutable fields (model, MAC, codec
            # support...) refetched every poll cycle, so dumping it whole each
            # time buried real events under tens of MB of noise — 21% of a 26h
            # debug log. Log it in full only when it actually changes, and a
            # one-liner with the field that does (PowerState) otherwise.
            if device_info != self._device_info:
                self._log.debug("Device info on %s is: %s", self._host, device_info)
            else:
                self._log.debug(
                    "Device info on %s unchanged (PowerState=%s)",
                    self._host,
                    (device_info.get("device") or {}).get("PowerState"),
                )
            self._device_info = device_info
        except Exception as ex:  # pylint: disable=broad-except
            self._log.debug("Error retrieving device info on %s: %s", self._host, ex)
            return None

        return self._device_info

    def _should_poll_st(self) -> bool:
        """Decide whether to hit the SmartThings cloud on this cycle.

        The local WebSocket is the primary state source, so SmartThings is
        polled only for cloud-only data (channel name, picture/sound mode) at a
        throttled cadence: the configurable interval while the TV is ON, and a
        fixed keepalive (``ST_POLL_OFF_INTERVAL``) otherwise. Power-on is
        detected locally: on the *transition* of the local WebSocket to
        connected, force one immediate poll so cloud-only fields refresh right
        away instead of lagging by the throttle interval. We key off the
        transition (not the level) because a Frame showing Art keeps the WS
        connected while HA still reports state OFF — a level check would then
        force a poll every cycle and defeat the throttle.

        ``self._state`` still holds the *previous* cycle's power here, because
        this runs before ``_check_status`` in ``_async_update`` — which is
        exactly what we want to gate on.
        """
        now = time.monotonic()
        ws_connected = self._ws.is_connected
        wake_edge = ws_connected and not self._ws_was_connected
        self._ws_was_connected = ws_connected
        if wake_edge:
            self._st_last_poll = now
            return True
        if self._st_auth_error_count >= MAX_ST_AUTH_ERROR_COUNT:
            # SmartThings is refusing this device id outright. Keep a slow
            # heartbeat so a reconfigure is picked up without a restart, but
            # stop polling at the normal cadence.
            interval = ST_POLL_AUTH_ERROR_INTERVAL
        elif self._state == MediaPlayerState.ON:
            interval = self._st_poll_on_interval
        else:
            interval = ST_POLL_OFF_INTERVAL
        if now - self._st_last_poll >= interval:
            self._st_last_poll = now
            return True
        return False

    @staticmethod
    def _st_error_is_authorization(exc: Exception) -> bool:
        """True when SmartThings refused the device rather than merely failing.

        pysmartthings does not expose a stable exception class for this, so the
        check is deliberately loose: the class name and the message are both
        inspected. A false positive only slows polling down and shows a notice
        the user can dismiss; a false negative just keeps today's behaviour.
        """
        text = f"{type(exc).__name__} {exc}".lower()
        return any(
            marker in text for marker in ("forbidden", "unauthorized", "401", "403")
        )

    async def _async_st_update(self, **kwargs) -> bool | None:
        """Update SmartThings state of device."""
        try:
            async with async_timeout.timeout(ST_UPDATE_TIMEOUT):
                await self._st.async_device_update(self._use_channel_info)
        except (
            asyncio.TimeoutError,
            ClientConnectionError,
            ClientResponseError,
        ) as exc:
            self._log.debug("%s - SmartThings error: [%s]", self.entity_id, exc)
            self._st_last_exc = exc
            return False
        except Exception as exc:  # pylint: disable=broad-except
            # A SmartThings cloud failure (expired token, rate limit, API
            # change) must not propagate out of async_update: HA would mark
            # the whole entity unavailable, and dependent entities (art mode
            # switch, frame art sensor) would wrongly report the TV as off.
            # Local WebSocket control keeps working without SmartThings.
            if type(exc) is not type(self._st_last_exc):
                self._log.warning(
                    "%s - SmartThings update failed, continuing with local"
                    " control only: %s",
                    # entity_id is still None during setup, which logged a
                    # bare "None - ..." line right when a first-run failure is
                    # most likely to be read.
                    self.entity_id or self._name,
                    exc,
                )
            self._st_last_exc = exc
            self._note_st_auth_error(exc)
            return False

        self._st_last_exc = None
        if self._st_auth_error_count:
            self._log.info(
                "%s - SmartThings is answering again, resuming normal polling",
                self.entity_id or self._name,
            )
            self._st_auth_error_count = 0
            clear_token_problem(self.hass, self._entry_id, METHOD_SMARTTHINGS)
        return True

    def _note_st_auth_error(self, exc: Exception) -> None:
        """Count an authorization refusal and, past the threshold, act on it."""
        if not self._st_error_is_authorization(exc):
            self._st_auth_error_count = 0
            return
        if self._st_auth_error_count >= MAX_ST_AUTH_ERROR_COUNT:
            return
        self._st_auth_error_count += 1
        if self._st_auth_error_count < MAX_ST_AUTH_ERROR_COUNT:
            return
        self._log.error(
            "%s - SmartThings refused this device %d times in a row (%s). The "
            "stored device id is probably no longer valid -- this happens "
            "after a mainboard repair, or when the TV is re-added in the "
            "SmartThings app. Slowing polling to %ds; fix it under "
            "Reconfigure > SmartThings device.",
            self.entity_id or self._name,
            self._st_auth_error_count,
            exc,
            ST_POLL_AUTH_ERROR_INTERVAL,
        )
        notify_token_problem(self.hass, self._entry_id, METHOD_SMARTTHINGS, self._name)

    async def async_update(self):
        """Update state of device."""
        start_time = time.monotonic()
        try:
            await self._async_update()
        finally:
            elapsed = time.monotonic() - start_time
            if elapsed > SCAN_INTERVAL.total_seconds():
                self._note_slow_update(elapsed)

    def _note_slow_update(self, elapsed: float) -> None:
        """Report an update that overran the scan interval, without flooding.

        An update of a TV that turned out to be OFF is slow *because* the TV is
        off — the probe has to time out before we can say so — and warning about
        the expected consequence of a known state is noise. That case goes to
        debug. A reachable TV that is genuinely slow still warns, but at most
        once per SLOW_UPDATE_WARN_INTERVAL, carrying the count it stands for
        (#248: 13 TVs produced 557 warnings in 84 minutes, 96% of them from the
        two that were asleep).
        """
        if self._state != MediaPlayerState.ON:
            self._log.debug(
                "%s - Update took %.1fs (TV is off; the power probe has to time "
                "out before that is known)",
                self.entity_id,
                elapsed,
            )
            return

        now = time.monotonic()
        if now - self._slow_update_last_warn < SLOW_UPDATE_WARN_INTERVAL:
            self._slow_update_suppressed += 1
            return

        if self._slow_update_suppressed:
            self._log.warning(
                "%s - Update took %.1fs, longer than the %.0fs scan interval "
                "(%d further slow updates since the last warning)",
                self.entity_id,
                elapsed,
                SCAN_INTERVAL.total_seconds(),
                self._slow_update_suppressed,
            )
        else:
            self._log.warning(
                "%s - Update took %.1fs, longer than the %.0fs scan interval",
                self.entity_id,
                elapsed,
                SCAN_INTERVAL.total_seconds(),
            )
        self._slow_update_last_warn = now
        self._slow_update_suppressed = 0

    async def _async_update(self):
        """Perform the actual state update."""

        # Refresh OAuth token if needed (before any SmartThings API call)
        if self._auth_method == AUTH_METHOD_OAUTH:
            await self._async_refresh_oauth_token()

        # Required to get source and media title. SmartThings is the fallback
        # data source (cloud-only fields), throttled and gated on local power;
        # skip the cloud call entirely on cycles where the throttle says so.
        st_error: bool | None = None
        if self._st and self._should_poll_st():
            if (st_update := await self._async_st_update()) is not None:
                st_error = not st_update

        result = await self._check_status()
        if not self._started_up or not result:
            use_mute_check = False
            self._fake_on = None
        else:
            use_mute_check = self._use_mute_check

        if use_mute_check and self._state == MediaPlayerState.OFF:
            first_detect = self._fake_on is None
            if first_detect or self._fake_on is True:
                if (is_muted := await self._upnp.async_get_mute()) is None:
                    self._fake_on = True
                else:
                    self._fake_on = is_muted
                if self._fake_on:
                    if first_detect:
                        self._log.debug(
                            "%s - Detected fake power on, status not updated",
                            self.entity_id,
                        )
                    result = False

        if st_error is not None:
            if result and not st_error:
                st_error = self._st.state != STStatus.STATE_ON
            self._log_st_error(st_error)

        self._state = MediaPlayerState.ON if result else MediaPlayerState.OFF
        self._started_up = True

        # Deferred art thread disable: if media_player loaded before sensor,
        # DATA_ART_API wasn't available in __init__. Check on each update.
        if not self._ws._art_thread_disabled:
            entry_data = self.hass.data.get(DOMAIN, {}).get(self._entry_id, {})
            if entry_data.get(DATA_ART_API):
                self._ws.disable_art_thread()
                self._log.debug(
                    "Deferred: disabled SamsungArt thread (art.py now active)"
                )

        # Reload SmartThings sources once after first successful ST update
        # _st_sources_loaded prevents repeated calls once sources are loaded
        if self._st and not self._st_sources_loaded and self._st.source_list:
            self._get_st_sources()
            if not self._default_source_used:
                self._st_sources_loaded = True

        # NB: We are checking properties, not attribute!
        if self.state == MediaPlayerState.ON:
            if self._delayed_set_source:
                difference = (
                    dt_util.utcnow() - self._delayed_set_source_time
                ).total_seconds()
                if difference > DELAYED_SOURCE_TIMEOUT:
                    self._delayed_set_source = None
                else:
                    await self._async_select_source_delayed(self._delayed_set_source)
            await self._async_load_device_info()
            await self._update_volume_info()
            self._get_running_app()
            await self._update_media()

        if self._state == MediaPlayerState.OFF:
            self._end_of_power_off = None

    def send_command(
        self,
        payload,
        command_type=CMD_SEND_KEY,
        key_press_delay: float = 0,
        press=False,
    ):
        """Send a key to the tv and handles exceptions."""
        if key_press_delay < 0:
            key_press_delay = None  # means "default" provided with constructor

        ret_val = False
        try:
            if command_type == CMD_RUN_APP:
                ret_val = self._ws.run_app(payload)
            elif command_type == CMD_RUN_APP_REMOTE:
                app_cmd = payload.split(",")
                app_id = app_cmd[0]
                action_type = ""
                meta_tag = ""
                if len(app_cmd) > 1:
                    action_type = app_cmd[1].strip()
                if len(app_cmd) > 2:
                    meta_tag = app_cmd[2].strip()
                ret_val = self._ws.run_app(
                    app_id, action_type, meta_tag, use_remote=True
                )
            elif command_type == CMD_RUN_APP_REST:
                result = self._ws.rest_app_run(payload)
                self._log.debug("Rest API result launching app %s: %s", payload, result)
                ret_val = True
            elif command_type == CMD_OPEN_BROWSER:
                ret_val = self._ws.open_browser(payload)
            elif command_type == CMD_SEND_TEXT:
                ret_val = self._ws.send_text(payload)
            elif command_type == CMD_SEND_KEY:
                hold_delay = 0
                source_keys = payload.split(",")
                key_code = source_keys[0]
                if len(source_keys) > 1:

                    def get_hold_time():
                        hold_time = source_keys[1].replace(" ", "")
                        if not hold_time:
                            return 0
                        if not hold_time.isdigit():
                            return 0
                        hold_time = int(hold_time) / 1000
                        return min(hold_time, KEYHOLD_MAX_DELAY)

                    hold_delay = get_hold_time()

                if hold_delay > 0:
                    ret_val = self._ws.hold_key(key_code, hold_delay)
                else:
                    ret_val = self._ws.send_key(
                        key_code, key_press_delay, "Press" if press else "Click"
                    )
            else:
                self._log.debug(
                    "Send command: invalid command type -> %s", command_type
                )

        except (ConnectionResetError, AttributeError, BrokenPipeError):
            self._log.debug(
                "Error in send_command() -> ConnectionResetError/AttributeError/BrokenPipeError"
            )

        except WebSocketTimeoutException:
            self._log.debug(
                "Failed sending payload %s command_type %s",
                payload,
                command_type,
                exc_info=True,
            )

        except OSError:
            self._log.debug("Error in send_command() -> OSError")

        return ret_val

    async def async_send_command(
        self,
        payload,
        command_type=CMD_SEND_KEY,
        key_press_delay: float = 0,
        press=False,
    ):
        """Send a key to the tv in async mode."""
        return await self.hass.async_add_executor_job(
            self.send_command, payload, command_type, key_press_delay, press
        )

    async def async_send_text(self, text: str) -> None:
        """Type a text string on the TV (e.g. into a focused search box).

        Uses the Samsung WebSocket ``SendInputString`` mechanism. The TV only
        accepts it when a text field is currently open and focused on screen
        (a search box, a login form, ...), otherwise the keystrokes go nowhere.
        This is the same path used by ``media_player.play_media`` with
        ``media_content_type: send_text``, exposed as a first-class service so
        it is discoverable and usable by remote-keyboard cards.
        """
        await self.async_send_command(text, CMD_SEND_TEXT)

    async def _update_media(self):
        """Update media and logo status."""
        logo_option_changed = False
        new_media_title = self._get_new_media_title()

        if not new_media_title:
            self._attr_media_title = None
            self._attr_media_image_url = None
            self._attr_media_image_remotely_accessible = False
            return

        new_logo_option = LogoOption(
            self._get_option(CONF_LOGO_OPTION, self._logo_option.value)
        )
        if self._logo_option != new_logo_option:
            self._logo_option = new_logo_option
            self._logo.set_logo_color(new_logo_option)
            logo_option_changed = True

        if not logo_option_changed:
            logo_option_changed = self._logo.check_requested()

        if not logo_option_changed:
            # In Art Mode the title stays "Art Mode" while the underlying
            # artwork (current.jpg) changes, so don't skip the refresh just
            # because the title is unchanged.
            if (
                new_media_title != ART_MODE_MEDIA_TITLE
                and self._attr_media_title
                and new_media_title == self._attr_media_title
            ):
                return

        self._log.debug(
            "New media title is: %s, old media title is: %s, running app is: %s",
            new_media_title,
            self._attr_media_title or "<none>",
            self._running_app,
        )

        remote_access = False
        if new_media_title == ART_MODE_MEDIA_TITLE:
            # Use the currently displayed artwork as the media image, maintained
            # by the Frame Art coordinator at www/frame_art/<entry>/current.jpg.
            media_image_url = self._art_mode_media_image()
        elif (
            media_image_url := await self._local_media_image(new_media_title)
        ) is None:
            media_image_url = await self._logo.async_find_match(new_media_title)
            remote_access = media_image_url is not None

        self._attr_media_title = new_media_title
        self._attr_media_image_url = media_image_url
        self._attr_media_image_remotely_accessible = remote_access

    def _art_mode_media_image(self) -> str | None:
        """Return the local URL of the current artwork, if available.

        The Frame Art coordinator downloads the currently displayed artwork to
        ``www/frame_art/<entry_id>/current.jpg`` and exposes it at the matching
        ``/local/...`` URL. We reuse it as the media image while in Art Mode so
        the media_player card shows the artwork instead of a generic logo.

        The file is overwritten in place when the artwork changes, so the URL
        never changes — the browser would keep showing a stale cached image.
        Append the file's mtime as a cache-busting query parameter.
        """
        rel_path = os.path.join("frame_art", self._entry_id, "current.jpg")
        abs_path = self.hass.config.path("www", rel_path)
        try:
            mtime = int(os.path.getmtime(abs_path))
        except OSError:
            return None
        return f"/local/{rel_path.replace(os.sep, '/')}?v={mtime}"

    def _get_new_media_title(self):
        """Get the current media title."""
        if self._state != MediaPlayerState.ON:
            return None

        # A Frame entering Art Mode keeps reporting its previous source over
        # SmartThings (cloud) for ~30-45s before it switches the running app
        # to "art", so relying on the ST signal alone leaves the card showing
        # the stale source (e.g. an HDMI input) the whole time. The local
        # art-mode signal (IP Control / async Art API / WS) flips within ~1s,
        # so trust it for the title too.
        if self._art_mode_is_on():
            return ART_MODE_MEDIA_TITLE

        if self._running_app == DEFAULT_APP:
            if self._st and self._st.state != STStatus.STATE_OFF:
                if self._st.source in ["digitalTv", "TV"]:
                    if self._st.channel_name and self._st.channel_name != "":
                        if (
                            self._show_channel_number
                            and self._st.channel
                            and self._st.channel != ""
                        ):
                            return self._st.channel_name + " (" + self._st.channel + ")"
                        return self._st.channel_name
                    if self._st.channel and self._st.channel != "":
                        return self._st.channel
                    return None

                if (run_app := self._st.channel_name) and run_app != "":
                    # On a Frame TV, SmartThings reports the "running app" as
                    # "art" while Art Mode is displayed. That is not a real app
                    # — surface it as Art Mode (the artwork image is set as the
                    # media image in _update_media).
                    if run_app.lower() == "art":
                        return ART_MODE_MEDIA_TITLE
                    # the channel name holds the running app ID
                    # regardless of the self._cloud_source value
                    # if the app ID is in the configured apps but is not running_app,
                    # means that this is not the real running app / media title
                    st_apps = self._app_list_st or {}
                    if run_app not in list(st_apps.values()):
                        # Resolve app ID to human-readable name
                        app_name = self._resolve_app_name(run_app)
                        return app_name or run_app

        media_title = self._get_source()
        if media_title and media_title != DEFAULT_APP:
            return media_title
        return None

    async def _local_media_image(self, media_title):
        """Get local media image if available."""
        if not self._get_option(CONF_USE_LOCAL_LOGO, True):
            return None
        app_id = media_title
        if self._running_app != DEFAULT_APP:
            if run_app_id := self._app_list.get(self._running_app):
                app_id = run_app_id

        _, _, logo_file = _get_default_app_info(app_id)
        return await self.hass.async_add_executor_job(
            self._local_image_url.get_image_url, media_title, logo_file
        )

    def _speaker_output_is_internal(self) -> bool | None:
        """Return whether the TV's speaker output is its internal speakers.

        Read from the Speaker Select entity's published state (IP Control or
        SmartThings variant — whichever this entry created). Returns None when
        unknown (no such entity, or it is unavailable), so callers keep the
        default behaviour instead of guessing.
        """
        registry = er.async_get(self.hass)
        for entity in registry.entities.get_entries_for_config_entry_id(self._entry_id):
            if entity.domain != "select" or not (
                entity.unique_id.endswith("_ip_control_speaker_select")
                or entity.unique_id.endswith("_st_media_output")
            ):
                continue
            state = self.hass.states.get(entity.entity_id)
            if state is None or state.state in ("unavailable", "unknown"):
                return None
            return "internal" in state.state.lower()
        return None

    @property
    def supported_features(self) -> int:
        """Flag media player features that are supported."""
        features = SUPPORT_SAMSUNGTV_SMART

        if self.state == MediaPlayerState.ON:
            features |= MediaPlayerEntityFeature.BROWSE_MEDIA

        if self._st:
            features |= MediaPlayerEntityFeature.SELECT_SOUND_MODE

        # Preserve the 8.7.0 protection for external receivers/soundbars unless
        # this specific TV has positively demonstrated directVolumeControl.
        if (
            self._speaker_output_is_internal() is False
            and self._ip_absolute_volume_supported is not True
        ):
            features &= ~MediaPlayerEntityFeature.VOLUME_SET

        return features

    def _smartthings_reports_art(self) -> bool:
        """Return True if the SmartThings cloud reports the running app as art."""
        if not self._st or self._st.state == STStatus.STATE_OFF:
            return False
        return (self._st.channel_name or "").lower() == "art"

    def _art_mode_is_on(self) -> bool | None:
        """Return the authoritative local Art Mode state, or None if unknown.

        Single source of truth for both the ``art_mode_status`` attribute and
        the media title. Priority order (see extra_state_attributes for the
        full rationale):
          1. IP Control cache (power-state aware) — wins when this TV is paired
          2. device_info PowerState='standby' — TV fully off, art cannot be on
          3. async Art API cache — kept live by art-channel WebSocket events,
             corroborated by SmartThings: on TVs without IP Control, the art
             WebSocket channel can latch onto a False reading (e.g. a
             go_to_standby-like transition) and never receive a follow-up
             event confirming the panel actually came back on. If SmartThings
             (which independently polls the TV) already reports the running
             app as "art", trust that over a local False.
             Trade-off: SmartThings itself lags ~30-45s on every transition,
             so on a genuine exit from Art Mode this can keep reporting "on"
             for up to that long, until SmartThings catches up.
          4. legacy WS artmode_status — fallback before the async API has a value
          5. SmartThings alone — covers the case where the async API has no
             value at all yet (e.g. right after HA startup)
        """
        # A real app visibly in the foreground means the panel is showing that
        # app, not artwork — whatever the art WebSocket channel claims. On 2024
        # Frames that channel is unreliable: it emits spurious
        # art_mode_changed='on' events while an app is actually on screen,
        # which made the switch and media title flap back to "Art Mode" during
        # normal app use. self._running_app is driven by the app's visibility
        # flag, so it is DEFAULT_APP whenever no app is foreground (live TV or
        # Art Mode) and the app id only while that app is genuinely visible.
        if self._running_app not in (None, DEFAULT_APP):
            return False
        if self._ip_art_mode is not None:
            return self._ip_art_mode
        # With the art-mode option off (the recommended default) _ip_art_mode is
        # None, so the value used to come from the WebSocket art channel — which
        # on some Frames never receives the art_mode_changed event and freezes
        # art_mode_status at its pre-transition value for hours (#248: measured
        # 4 stretches of 7-10 h, art shown but the attribute stuck at off). The
        # cached getTVStates.pictureMode is the panel's own truth, refreshed
        # every IP Control poll and independent of that option, so consult it
        # before the WS/SmartThings fallbacks. None = not readable -> fall
        # through unchanged (no IP Control, no snapshot, or TV asleep).
        panel_art = self._ip_control_panel_art_cached()
        if panel_art is not None:
            return panel_art
        if self._get_device_spec("PowerState") == "standby":
            return False
        # Cloud power-off fallback for TVs without IP Control. SmartThings
        # reports the switch capability as 'on' while the Frame is in Art Mode
        # and 'off' only when it is truly powered off, so STATE_OFF means the
        # panel is off and a stale art-channel WebSocket (which can latch 'on'
        # after a cloud/remote power-off it never witnessed) must not be
        # trusted. Trade-off: SmartThings lags ~30-45s, so powering straight on
        # into Art Mode may briefly read 'off' until the cloud catches up — far
        # better than a permanently stuck 'on'.
        if self._st is not None and self._st.state == STStatus.STATE_OFF:
            return False
        art_api = (
            self.hass.data.get(DOMAIN, {}).get(self._entry_id, {}).get(DATA_ART_API)
        )
        if art_api is not None and art_api.art_mode is not None:
            if not art_api.art_mode and self._smartthings_reports_art():
                return True
            return art_api.art_mode
        if self._ws.artmode_status != ArtModeStatus.Unsupported:
            return self._ws.artmode_status == ArtModeStatus.On
        if self._smartthings_reports_art():
            return True
        return None

    @property
    def extra_state_attributes(self):
        """Return the optional state attributes."""
        # Surfaced mainly for troubleshooting: automations/scripts that
        # reference a config entry directly (e.g. homeassistant.reload_config_entry)
        # silently break after a re-pairing changes the entry_id, with no
        # obvious link back to this entity from the error alone.
        data = {ATTR_IP_ADDRESS: self._host, ATTR_CONFIG_ENTRY_ID: self._entry_id}

        # Panel resolution as the TV reports it ("3840x2160"). Consumers that
        # send images to the TV (the upload card) use it to avoid pushing more
        # pixels than the panel can show — which the Frame rejects outright —
        # without hardcoding 4K and penalising 8K sets.
        if (info := self._device_info) and (device := info.get("device")):
            if resolution := device.get("resolution"):
                data[ATTR_SCREEN_RESOLUTION] = resolution

        # Art Mode status: delegate to _art_mode_is_on(), the single source of
        # truth (it layers IP Control cache → REST PowerState='standby' →
        # SmartThings switch=off → async Art API → WS artmode_status →
        # SmartThings-reports-art, with the running-app guard on top). This MUST
        # be the same logic the media title and the is_on property use, so the
        # Power / Frame Art switches that mirror this attribute stay consistent
        # with everything else. Previously this block re-implemented a subset
        # that ignored the IP Control cache and the SmartThings power signal,
        # which let a stale art WebSocket pin the switches "on" after the TV was
        # powered off.
        art_status = self._art_mode_is_on()
        if art_status is not None:
            data[ATTR_ART_MODE_STATUS] = STATE_ON if art_status else STATE_OFF
        if self._st:
            picture_mode = self._st.picture_mode
            picture_mode_list = self._st.picture_mode_list
            if picture_mode:
                data[ATTR_PICTURE_MODE] = picture_mode
            if picture_mode_list:
                data[ATTR_PICTURE_MODE_LIST] = picture_mode_list

        # Add Frame Art last result if available
        if hasattr(self, "_frame_art_last_result") and self._frame_art_last_result:
            data["frame_art_last_result"] = self._frame_art_last_result

        return data

    @property
    def media_channel(self):
        """Channel currently playing."""
        if self._state != MediaPlayerState.ON:
            return None

        if local_channel := self._get_ip_control_channel():
            return local_channel

        if self._st:
            if self._st.source in ["digitalTv", "TV"] and self._st.channel != "":
                return self._st.channel

        return None

    @property
    def media_content_type(self):
        """Return the content type of current playing media."""
        if self._state == MediaPlayerState.ON:
            if self._running_app == DEFAULT_APP:
                if self.media_channel:
                    return MediaType.CHANNEL
                return MediaType.VIDEO
            return MediaType.APP
        return None

    @property
    def app_id(self):
        """ID of the current running app."""
        if self._state != MediaPlayerState.ON:
            return None

        if self._app_list_st and self._running_app != DEFAULT_APP:
            if app := self._app_list_st.get(self._running_app):
                return app

        if self._st:
            if not self._st.channel and self._st.channel_name:
                return self._st.channel_name
        return DEFAULT_APP

    @property
    def state(self):
        """Return the state of the device."""

        # Warning: we assume that after a sending a power off command, the command is successful
        # so for 20 seconds (defined in POWER_OFF_DELAY) the state will be off regardless of the
        # actual state. This is to have better feedback to the command in the UI, but the logic
        # might cause other issues in the future
        if self._power_off_in_progress():
            return MediaPlayerState.OFF

        return self._state

    @property
    def source_list(self):
        """List of available input sources."""
        # try to get source list from SmartThings if a custom source list is not defined
        if self._st and self._default_source_used:
            self._get_st_sources()

        self._gen_installed_app_list()

        source_list = []
        source_list.extend(list(self._source_list))
        if self._app_list:
            source_list.extend(list(self._app_list))
        if self._channel_list:
            source_list.extend(list(self._channel_list))
        return source_list

    @property
    def channel_list(self):
        """List of available channels."""
        if not self._channel_list:
            return None
        return list(self._channel_list)

    @property
    def source(self):
        """Return the current input source."""
        return self._get_source()

    @property
    def sound_mode(self):
        """Name of the current sound mode."""
        if self._st:
            return self._st.sound_mode
        return None

    @property
    def sound_mode_list(self):
        """List of available sound modes."""
        if self._st:
            return self._st.sound_mode_list or None
        return None

    @property
    def support_art_mode(self) -> ArtModeSupport:
        """Return if art mode is supported."""
        if self._ws.artmode_status != ArtModeStatus.Unsupported:
            return ArtModeSupport.FULL
        if self._get_device_spec("FrameTVSupport") == "true":
            return ArtModeSupport.PARTIAL
        return ArtModeSupport.UNSUPPORTED

    def _send_wol_packet(self, wol_repeat=None):
        """Send a WOL packet to turn on the TV."""
        if not self._mac:
            self._log.error("MAC address not configured, impossible send WOL packet")
            return False

        if not wol_repeat:
            wol_repeat = self._get_option(CONF_WOL_REPEAT, 1)
        wol_repeat = max(1, min(wol_repeat, MAX_WOL_REPEAT))
        ip_address = self._broadcast or "255.255.255.255"
        send_success = False
        for i in range(wol_repeat):
            if i > 0:
                time.sleep(0.25)
            try:
                send_magic_packet(self._mac, ip_address=ip_address)
                send_success = True
            except socketError as exc:
                self._log.warning(
                    "Failed tentative n.%s to send WOL packet: %s",
                    i,
                    exc,
                )
            except (TypeError, ValueError) as exc:
                self._log.error("Error sending WOL packet: %s", exc)
                return False

        return send_success

    async def _async_power_on(self, set_art_mode=False):
        """Turn the media player on."""
        cmd_power_on = "KEY_POWER"
        cmd_power_art = "KEY_POWER"
        if set_art_mode:
            if self._ws.artmode_status == ArtModeStatus.Off:
                # art mode from on
                await self.async_send_command(cmd_power_art)
                self._state = MediaPlayerState.OFF
                return True

        if self._ws.artmode_status == ArtModeStatus.On:
            if set_art_mode:
                return False
            # power on from art mode
            await self.async_send_command(cmd_power_art)
            return True

        if self.state != MediaPlayerState.OFF:
            return False

        result = True
        if not await self.async_send_command(cmd_power_on):
            turn_on_method = PowerOnMethod(
                self._get_option(CONF_POWER_ON_METHOD, PowerOnMethod.WOL.value)
            )

            if turn_on_method == PowerOnMethod.SmartThings and self._st:
                await self._st.async_turn_on()
            elif turn_on_method == PowerOnMethod.IPControl:
                ip_client = self._get_ip_control_client()
                if ip_client is not None:
                    try:
                        await ip_client.async_power_on()
                    except SamsungIPControlError as ex:
                        self._log.warning(
                            "IP Control power-on for %s failed (%s); falling "
                            "back to WOL",
                            self._host,
                            ex,
                        )
                        result = await self.hass.async_add_executor_job(
                            self._send_wol_packet
                        )
                else:
                    result = await self.hass.async_add_executor_job(
                        self._send_wol_packet
                    )
            else:
                result = await self.hass.async_add_executor_job(self._send_wol_packet)

        if result:
            self._state = MediaPlayerState.OFF
            self._end_of_power_off = None
            self._ws.set_power_on_request(set_art_mode)

        return result

    async def _async_turn_on(self, set_art_mode=False):
        """Turn the media player on."""
        self._delayed_set_source = None
        if not await self._async_power_on(set_art_mode):
            return False
        if self._state != MediaPlayerState.OFF:
            return True

        await self._async_switch_entity(not set_art_mode)

        return True

    async def async_turn_on(self):
        """Turn the media player on."""
        await self._async_turn_on()

    async def async_set_art_mode(self):
        """Turn the media player on setting in art mode."""
        if (
            self._state == MediaPlayerState.ON
            and self.support_art_mode == ArtModeSupport.PARTIAL
        ):
            await self.async_send_command("KEY_POWER")
        elif self.support_art_mode == ArtModeSupport.FULL:
            await self._async_turn_on(True)

    def _turn_off(self):
        """Turn off media player."""
        if self._power_off_in_progress():
            return False

        cmd_power_off = "KEY_POWER"
        cmd_power_art = "KEY_POWER"
        self._ws.set_power_off_request()
        if self._state == MediaPlayerState.ON:
            if self.support_art_mode == ArtModeSupport.UNSUPPORTED:
                self.send_command(cmd_power_off)
            else:
                self.send_command(f"{cmd_power_art},3000")
        elif self._ws.artmode_status == ArtModeStatus.On:
            self.send_command(f"{cmd_power_art},3000")
        else:
            return False

        self._end_of_power_off = dt_util.utcnow() + timedelta(seconds=POWER_OFF_DELAY)

        return True

    async def async_turn_off(self):
        """Turn the media player off.

        Frame TV + SmartThings: use SmartThings REST switch/off which
        reliably powers off regardless of current state (ON or Art Mode).
        The WS KEY_POWER hold only toggles between ON and Art Mode on
        Frame 2024 and never truly powers off.
        Falls back to WS for non-Frame TVs or when SmartThings is absent.
        """
        if self._st and self.support_art_mode != ArtModeSupport.UNSUPPORTED:
            try:
                await self._st.async_turn_off()
                self._ws.set_power_off_request()
                self._end_of_power_off = dt_util.utcnow() + timedelta(
                    seconds=POWER_OFF_DELAY
                )
                await self._async_switch_entity(False)
                return
            except Exception:
                self._log.debug("SmartThings turn_off failed, falling back to WS")

        result = await self.hass.async_add_executor_job(self._turn_off)
        if result:
            await self._async_switch_entity(False)

    async def async_toggle(self):
        """Toggle the power on the media player."""
        if (
            self.state == MediaPlayerState.ON
            and self.support_art_mode != ArtModeSupport.UNSUPPORTED
        ):
            if self._get_option(CONF_TOGGLE_ART_MODE, False):
                await self.async_set_art_mode()
                return
        await super().async_toggle()

    async def async_volume_up(self):
        """Volume up the media player."""
        if self._state != MediaPlayerState.ON:
            return
        if not await self._async_ip_control_volume_step(up=True):
            await self.async_send_command("KEY_VOLUP")
        if self.volume_level is not None:
            self._attr_volume_level = min(1.0, self.volume_level + 0.01)

    async def async_volume_down(self):
        """Volume down media player."""
        if self._state != MediaPlayerState.ON:
            return
        if not await self._async_ip_control_volume_step(up=False):
            await self.async_send_command("KEY_VOLDOWN")
        if self.volume_level is not None:
            self._attr_volume_level = max(0.0, self.volume_level - 0.01)

    async def _async_ip_control_volume_step(self, *, up: bool) -> bool:
        """Step volume via IP Control's ``volumeUpDnControl``, if paired.

        Returns ``True`` on success so the caller skips the WebSocket
        ``KEY_VOLUP``/``KEY_VOLDOWN`` fallback. ``volumeUpDnControl`` is
        relative-only (no absolute level over IP Control on Frame 2024/2025),
        matching the WS keys it replaces.
        """
        client = self._get_ip_control_client()
        if client is None:
            return False
        try:
            if up:
                await client.async_volume_up()
            else:
                await client.async_volume_down()
            return True
        except SamsungIPControlError as ex:
            self._log.debug(
                "IP Control volume step failed (%s); falling back to WebSocket",
                ex,
            )
            return False

    async def async_mute_volume(self, mute):
        """Set mute state."""
        if self._state != MediaPlayerState.ON:
            return

        if self.is_volume_muted is not None and mute == self.is_volume_muted:
            return

        # With an external receiver/soundbar, the TV remote's KEY_MUTE is
        # relayed to the external audio device over HDMI-CEC/ARC/eARC.
        # An explicit IP Control muteControl state is not equally reliable
        # with external audio, so use the same toggle path as the physical
        # remote. The state guard above ensures we only toggle when needed.
        if self._speaker_output_is_internal() is False:
            await self.async_send_command("KEY_MUTE")

            if self.is_volume_muted is not None:
                self._attr_is_volume_muted = mute

            return

        # Internal speakers retain the existing IP Control behaviour, with
        # WebSocket KEY_MUTE as fallback.
        client = self._get_ip_control_client()
        sent_via_ip_control = False

        if client is not None:
            try:
                await client.async_set_mute(mute)
                sent_via_ip_control = True
            except SamsungIPControlError as ex:
                self._log.debug(
                    "IP Control mute failed (%s); falling back to WebSocket",
                    ex,
                )

        if not sent_via_ip_control:
            await self.async_send_command("KEY_MUTE")

        if self.is_volume_muted is not None:
            self._attr_is_volume_muted = mute

    async def async_set_volume_level(self, volume):
        """Set the volume level."""
        if self._state != MediaPlayerState.ON:
            return

        if self.volume_level is None:
            return

        target = int(volume * 100)
        speaker_internal = self._speaker_output_is_internal()

        # Prefer directVolumeControl whenever the capability has not already
        # been ruled out for this IP Control client.
        client = self._get_ip_control_client()

        if client is not None and self._ip_absolute_volume_supported is not False:
            try:
                applied = await client.async_set_volume(target)
            except SamsungIPControlUnsupportedError as ex:
                if self._ip_control_ambient_mode_active():
                    self._log.debug(
                        "IP Control absolute volume unavailable while Ambient mode "
                        "is active; not treating it as unsupported: %s",
                        ex,
                    )
                else:
                    self._ip_absolute_volume_supported = False
                    self._log.debug(
                        "IP Control absolute volume is not available on this TV: %s",
                        ex,
                    )
            except SamsungIPControlError as ex:
                self._log.debug(
                    "IP Control absolute-volume set failed (%s); "
                    "using the existing fallback when possible",
                    ex,
                )
            else:
                self._ip_absolute_volume_supported = True
                self._attr_volume_level = applied / 100
                return

        # Without confirmed local absolute-volume support, retain the 8.7.0
        # protection for external receivers/soundbars.
        if speaker_internal is False:
            self._log.warning(
                "volume_set ignored: the TV's speaker output is external "
                "(receiver/soundbar) and IP Control absolute volume is not "
                "available — use volume_up/volume_down instead"
            )
            return

        # Internal speakers retain the existing SmartThings/UPnP behaviour.
        if self._st and self._setvolumebyst:
            await self._st.async_send_command("setvolume", target)
        else:
            await self._upnp.async_set_volume(target)

        self._attr_volume_level = target / 100

    def media_play_pause(self):
        """Simulate play pause media player."""
        if self._playing:
            self.media_pause()
        else:
            self.media_play()

    def media_play(self):
        """Send play command."""
        self._playing = True
        self.send_command("KEY_PLAY")

    def media_pause(self):
        """Send media pause command to media player."""
        self._playing = False
        self.send_command("KEY_PAUSE")

    def media_stop(self):
        """Send media pause command to media player."""
        self._playing = False
        self.send_command("KEY_STOP")

    def media_next_track(self):
        """Send next track command."""
        if self.media_channel:
            self.send_command("KEY_CHUP")
        else:
            self.send_command("KEY_FF")

    def media_previous_track(self):
        """Send the previous track command."""
        if self.media_channel:
            self.send_command("KEY_CHDOWN")
        else:
            self.send_command("KEY_REWIND")

    async def _async_open_browser(self, url: str) -> bool:
        """Open URL using IP Control, falling back to WebSocket."""

        ip_client = self._get_ip_control_client()

        if ip_client is not None:
            try:
                await ip_client.async_open_browser(url)
                # Name the URL we asked for: the TV confirms only that the
                # browser was launched (its getter returns applicationName and
                # never the current page), so a "it opened the wrong page"
                # report is only diagnosable if the log says what we requested.
                self._log.info(
                    "Browser launched via IP Control at %s; the TV does not "
                    "report which URL it actually opened",
                    url,
                )
                return True

            except SamsungIPControlError as ex:
                self._log.debug(
                    "IP Control browser launch failed (%s); "
                    "falling back to WebSocket",
                    ex,
                )

        return await self.async_send_command(url, CMD_OPEN_BROWSER)

    async def _async_ip_control_source(self, source_key: str) -> bool:
        """Select an input source through local Samsung IP Control."""
        input_source = source_key.removeprefix("IP_")
        if not input_source:
            self._log.error("Invalid IP Control source command: %s", source_key)
            return False

        client = self._get_ip_control_client()
        if client is None:
            self._log.error(
                "IP Control is not paired or is disabled. Command not sent: %s",
                source_key,
            )
            return False

        try:
            selected_source = await client.async_set_input_source(input_source)
        except SamsungIPControlError as ex:
            self._log.error("IP Control input source %s failed: %s", input_source, ex)
            return False

        # Keep media_player.source and the existing diagnostic input-source
        # sensor in sync immediately. The next coordinator poll will confirm the
        # physical state; no extra JSON-RPC request is needed here.
        self._ip_input_source = selected_source
        coordinator = self._get_ip_control_state_coordinator()
        if coordinator is not None:
            current_data = getattr(coordinator, "data", None)
            updated_data = dict(current_data) if isinstance(current_data, dict) else {}
            current_tv = updated_data.get("tv")
            tv_states = dict(current_tv) if isinstance(current_tv, dict) else {}
            tv_states["inputSource"] = selected_source
            updated_data["tv"] = tv_states
            updated_data["powered_off"] = False
            set_updated_data = getattr(coordinator, "async_set_updated_data", None)
            if callable(set_updated_data):
                set_updated_data(updated_data)

        self._log.debug("IP Control input source selected: %s", selected_source)
        return True

    async def _async_send_keys(self, source_key):
        """Send key / chained keys."""
        prev_wait = True

        if "+" in source_key:
            all_source_keys = source_key.split("+")
            for this_key in all_source_keys:
                if this_key.isdigit():
                    prev_wait = True
                    await asyncio.sleep(
                        min(
                            max((int(this_key) / 1000), KEYPRESS_MIN_DELAY),
                            KEYPRESS_MAX_DELAY,
                        )
                    )
                else:
                    # put a default delay between key if set explicit
                    if not prev_wait:
                        await asyncio.sleep(KEYPRESS_DEFAULT_DELAY)
                    prev_wait = False
                    if this_key.startswith("IP_"):
                        if not await self._async_ip_control_source(this_key):
                            return False
                    elif this_key.startswith("ST_"):
                        await self._smartthings_keys(this_key)
                    else:
                        await self.async_send_command(this_key)

            return True

        if source_key.startswith("IP_"):
            return await self._async_ip_control_source(source_key)

        if source_key.startswith("ST_"):
            return await self._smartthings_keys(source_key)

        return await self.async_send_command(source_key)

    async def _async_set_channel_source(self, channel_source=None):
        """Select the source for a channel."""

        if not channel_source:
            if self._running_app == DEFAULT_APP:
                return True
            self._log.error("Current source invalid for channel")
            return False

        if self._source == channel_source:
            return True

        if channel_source not in self._source_list:
            self._log.error("Invalid channel source: %s", channel_source)
            return False

        await self.async_select_source(channel_source)
        if self._source != channel_source:
            self._log.error("Error selecting channel source: %s", channel_source)
            return False
        await asyncio.sleep(3)

        return True

    async def _async_set_channel(self, channel):
        """Set a specific channel."""

        if channel.startswith("http"):
            await self.async_play_media(MediaType.URL, channel)
            return True

        channel_cmd = channel.split("@")
        channel_no = channel_cmd[0]
        channel_source = None
        if len(channel_cmd) > 1:
            channel_source = channel_cmd[1]

        try:
            cv.positive_int(channel_no)
        except vol.Invalid:
            self._log.error("Channel must be positive integer")
            return False

        if not await self._async_set_channel_source(channel_source):
            return False

        if self._st:
            return await self._smartthings_keys(f"ST_CH{channel_no}")

        def send_digit():
            for digit in channel_no:
                self.send_command("KEY_" + digit)
                time.sleep(KEYPRESS_DEFAULT_DELAY)
            self.send_command("KEY_ENTER")

        await self.hass.async_add_executor_job(send_digit)
        return True

    async def _async_launch_app(self, app_data, meta_data=None):
        """Launch app with different methods."""

        method = ""
        app_cmd = app_data.split("@")
        app_id = app_cmd[0]
        if self._app_list:
            if app_id_from_list := self._app_list.get(app_id):
                app_id = app_id_from_list
        if meta_data:
            app_id += f",,{meta_data}"
            method = CMD_RUN_APP_REMOTE
        elif len(app_cmd) > 1:
            req_method = app_cmd[1].strip()
            if req_method in (CMD_RUN_APP, CMD_RUN_APP_REMOTE, CMD_RUN_APP_REST):
                method = req_method

        if not method:
            app_launch_method = AppLaunchMethod(
                self._get_option(CONF_APP_LAUNCH_METHOD, AppLaunchMethod.Standard.value)
            )

            if app_launch_method == AppLaunchMethod.Remote:
                method = CMD_RUN_APP_REMOTE
            elif app_launch_method == AppLaunchMethod.Rest:
                method = CMD_RUN_APP_REST
            else:
                method = CMD_RUN_APP

        await self.async_send_command(app_id, method)

    def _get_youtube_app_id(self):
        """Search youtube app id used to launch video."""
        if self._yt_app_id is not None:
            return len(self._yt_app_id) > 0
        if not self._app_list:
            return False
        self._yt_app_id = ""
        for app_name, app_id in self._app_list.items():
            if app_name.casefold().find("youtube") >= 0:
                if not self._yt_app_id:
                    self._yt_app_id = app_id
            if app_id in YT_APP_IDS:
                self._yt_app_id = app_id
                break

        self._log.debug("YouTube App ID: %s", self._yt_app_id or "not found")
        return len(self._yt_app_id) > 0

    def _get_youtube_video_id(self, url):
        """Try to get youtube video id from url."""
        url_parsed = urlparse(url)
        url_host = str(url_parsed.hostname).casefold()
        url_path = url_parsed.path
        if url_host.find("youtube") < 0:
            self._log.debug("URL not related to Youtube")
            return None

        video_id = None
        url_query = parse_qs(url_parsed.query)
        if YT_VIDEO_QS in url_query:
            video_id = url_query[YT_VIDEO_QS][0]
        elif url_path and str(url_path).casefold().startswith(YT_SVIDEO):
            video_id = url_path[len(YT_SVIDEO) :]

        if not video_id:
            self._log.warning("Youtube video ID not found in url: %s", url)
            return None

        if not self._get_youtube_app_id():
            self._log.warning("Youtube app ID not available, configure in apps list")
            return None

        self._log.debug("Youtube video ID: %s", video_id)
        return video_id

    def _cast_youtube_video(self, video_id: str, enqueue: MediaPlayerEnqueue):
        """
        Cast a youtube video using samsungcast library.
        This method is sync and must run in job executor.
        """
        if enqueue == MediaPlayerEnqueue.PLAY:
            self._cast_api.play_video(video_id)
        elif enqueue == MediaPlayerEnqueue.NEXT:
            self._cast_api.play_next(video_id)
        elif enqueue == MediaPlayerEnqueue.ADD:
            self._cast_api.add_to_queue(video_id)
        elif enqueue == MediaPlayerEnqueue.REPLACE:
            self._cast_api.clear_queue()
            self._cast_api.play_video(video_id)

    async def _async_play_youtube_video(
        self, video_id: str, enqueue: MediaPlayerEnqueue
    ):
        """Play a YouTube video using YouTube app."""
        run_app_id = None
        if self._running_app != DEFAULT_APP:
            run_app_id = self._app_list.get(self._running_app)

        # launch youtube app if not running
        if run_app_id != self._yt_app_id:
            await self._async_launch_app(self._yt_app_id)
            await asyncio.sleep(3)  # we wait for YouTube app to start

        await self.hass.async_add_executor_job(
            self._cast_youtube_video, video_id, enqueue
        )

    async def async_play_media(
        self, media_type: MediaType | str, media_id: str, **kwargs
    ):
        """Support running different media type command."""
        enqueue: MediaPlayerEnqueue | None = kwargs.get(ATTR_MEDIA_ENQUEUE)

        if media_source.is_media_source_id(media_id):
            media_type = MediaType.URL
            play_item = await media_source.async_resolve_media(self.hass, media_id)
            media_id = play_item.url
        else:
            media_type = media_type.lower()

        if media_type in [MEDIA_TYPE_BROWSER, MediaType.URL]:
            media_id = async_process_play_media_url(self.hass, media_id)
            try:
                cv.url(media_id)
            except vol.Invalid:
                self._log.error('Media ID must be a valid url (ex: "http://"')
                return

        # Type channel
        if media_type == MediaType.CHANNEL:
            await self._async_set_channel(media_id)

        # Launch an app
        elif media_type == MediaType.APP:
            await self._async_launch_app(media_id)

        # Send custom key
        elif media_type == MEDIA_TYPE_KEY:
            try:
                cv.string(media_id)
            except vol.Invalid:
                self._log.error('Media ID must be a string (ex: "KEY_HOME"')
                return

            await self._async_send_keys(media_id)

        # Open url or youtube app
        elif media_type == MediaType.URL:
            if enqueue and (video_id := self._get_youtube_video_id(media_id)):
                await self._async_play_youtube_video(video_id, enqueue)
                return

            if await self._upnp.async_set_current_media(media_id):
                self._playing = True
                return

            await self._async_open_browser(media_id)

        # Open url in browser
        elif media_type == MEDIA_TYPE_BROWSER:
            await self._async_open_browser(media_id)

        # Trying to make stream component work on TV
        elif media_type == "application/vnd.apple.mpegurl":
            if await self._upnp.async_set_current_media(media_id):
                self._playing = True

        elif media_type == MEDIA_TYPE_TEXT:
            await self.async_send_command(media_id, CMD_SEND_TEXT)

        else:
            raise NotImplementedError(f"Unsupported media type: {media_type}")

    async def async_browse_media(self, media_content_type=None, media_content_id=None):
        """Implement the websocket media browsing helper."""
        return await media_source.async_browse_media(self.hass, media_content_id)

    async def _async_wake_for_source_switch(self, source, source_key) -> None:
        """Wake the panel from Art Mode before sending a source-switch key.

        2024+ Frame panels ignore HDMI/source keys while in Art Mode, so we
        send KEY_HOME first (matching the manual "home, wait, source"
        workaround). The art state is gated on ``is not False`` rather than a
        plain truthiness check: on 2024 Frames ``_art_mode_is_on()`` can return
        None/stale-False even while artwork is on screen (the art WebSocket
        channel is unreliable there), and skipping the wake makes the TV reject
        the source key with an on-screen "not available" error. Frame TVs only,
        so a non-Frame source switch is never burdened with an extra HOME press.
        """
        art_on = self._art_mode_is_on()
        is_frame = self._frame_tv_supported or art_on is not None
        wake = art_on is True or (is_frame and art_on is not False)
        self._log.debug(
            "Source switch to '%s' (key=%s): art_mode_is_on=%s, frame=%s, wake=%s",
            source,
            source_key,
            art_on,
            is_frame,
            wake,
        )
        if wake:
            self._log.debug(
                "Waking panel from Art Mode with KEY_HOME before source switch"
            )
            await self.async_send_command("KEY_HOME")
            await asyncio.sleep(SOURCE_WAKE_DELAY)

    async def async_select_source(self, source):
        """Select input source."""
        running_app = DEFAULT_APP
        self._delayed_set_source = None

        if self.state != MediaPlayerState.ON:
            if await self._async_turn_on():
                self._delayed_set_source = source
                self._delayed_set_source_time = dt_util.utcnow()
            return

        if self._source_list and source in self._source_list:
            source_key = self._source_list[source]
            await self._async_wake_for_source_switch(source, source_key)
            if not await self._async_send_keys(source_key):
                return
        elif self._source_list and (resolved := self._resolve_source_by_id(source)):
            # Accept technical IDs (e.g. "HDMI3") in addition to display names
            source_key = resolved[1]
            source = resolved[0]  # Use display name for state
            await self._async_wake_for_source_switch(source, source_key)
            if not await self._async_send_keys(source_key):
                return
        elif self._app_list and source in self._app_list:
            app_id = self._app_list[source]
            running_app = source
            await self._async_launch_app(app_id)
            if self._st:
                self._st.set_application(self._app_list_st[source])
        elif self._channel_list and source in self._channel_list:
            source_key = self._channel_list[source]
            await self._async_set_channel(source_key)
            return
        else:
            self._log.error("Unsupported source")
            return

        self._running_app = running_app
        self._source = source

        # Reflect the new source on the card right away instead of waiting for
        # the next scheduled poll (~SCAN_INTERVAL). Recompute the media title /
        # image from the optimistic running_app and push the state; the Art
        # Mode switch (a separate entity tracking our state changes) updates
        # with it, so it no longer lingers on "Art Mode"/on for several seconds
        # after launching an app.
        await self._update_media()
        self.async_write_ha_state()

    async def _async_select_source_delayed(self, source):
        """Select input source with delayed ST option."""
        if self._st:
            if self._st.state != STStatus.STATE_ON:
                # wait for smartthings available
                return

        await self.async_select_source(source)

    async def async_select_sound_mode(self, sound_mode):
        """Select sound mode."""
        if not self._st:
            raise NotImplementedError()
        await self._st.async_set_sound_mode(sound_mode)

    async def async_select_picture_mode(self, picture_mode):
        """Select picture mode: SmartThings first, WS key when it is needed.

        The WS key exists because SmartThings can answer COMPLETED while the
        TV shows "function not available" — HDMI content protection blocks the
        cloud command on some inputs. It used to be sent on EVERY change, even
        when the cloud write had been read back as applied, so one user action
        wrote the same setting twice over two channels within a second.

        It is now sent only when SmartThings did not demonstrably apply the
        mode: no SmartThings at all, an exception, an outright failure, or a
        send that could not be verified. A verified apply sends nothing more.
        The ambiguous case still sends the key, so nothing that used to work
        stops working — this only removes the provably redundant write.
        """
        applied = False
        if self._st:
            try:
                applied = await self._st.async_set_picture_mode(picture_mode) is True
            except Exception:
                applied = False

        if applied:
            self._log.debug(
                "Picture mode '%s' verified applied via SmartThings; "
                "not sending the WS key as well",
                picture_mode,
            )
            return

        # SmartThings could not be confirmed to have applied it — send the key,
        # which also covers the HDMI-restricted case above.
        mode_id = ""
        if self._st:
            mode_id = self._st.picture_mode_map.get(picture_mode, "")
        ws_key = picture_mode_ws_key(picture_mode, mode_id)
        if ws_key:
            await self.async_send_command(ws_key)
            self._log.debug(
                "Picture mode '%s' sent via WS key %s (SmartThings did not "
                "confirm it applied)",
                picture_mode,
                ws_key,
            )
        else:
            self._log.debug(
                "No WS key for picture mode '%s' (id=%s) — skipping WS fallback",
                picture_mode,
                mode_id or "unknown",
            )

    async def _async_set_hue_sync(self, enabled: bool) -> None:
        """Start or stop Hue Sync, turning a missing capability into a clear error."""
        if not self._st:
            raise HomeAssistantError("SmartThings is not configured for this TV")
        try:
            await self._st.async_set_hue_sync(enabled)
        except SmartThingsCapabilityUnsupported as err:
            raise HomeAssistantError(
                f"This TV does not currently expose the {err} SmartThings "
                "capability, so Hue Sync cannot be controlled. Check that the "
                "Philips Hue Sync TV app is installed on the TV and paired with "
                "your Hue bridge — on some models the capability only appears "
                "once it is set up. If it is already set up, the model does not "
                "support this."
            ) from err

    async def async_start_hue_sync(self) -> None:
        """Start Philips Hue Sync without opening the TV app."""
        await self._async_set_hue_sync(True)

    async def async_stop_hue_sync(self) -> None:
        """Stop Philips Hue Sync without opening the TV app."""
        await self._async_set_hue_sync(False)

    # ==========================================
    # Frame Art Extended Service Methods
    # ==========================================

    def _store_art_result(self, result: dict) -> None:
        """Store art service result and trigger state update.

        Important: Remove thumbnail_base64 from stored result to prevent
        entity attributes from becoming too large.
        """
        # Create a copy without the base64 data
        stored_result = result.copy()
        if "thumbnail_base64" in stored_result:
            # Replace with size info instead of full base64
            base64_size = len(stored_result["thumbnail_base64"])
            stored_result.pop("thumbnail_base64")
            stored_result["thumbnail_base64_size"] = base64_size
            stored_result["thumbnail_note"] = "Base64 data removed to save space"

        self._frame_art_last_result = stored_result
        self.async_write_ha_state()

    async def _ensure_frame_tv_check(self) -> bool:
        """Check if Frame TV is supported (cached on success only).

        A failed check (exception or False) is NOT cached so that transient
        startup failures (TV not yet reachable) are retried on the next call,
        avoiding a permanent "Frame TV not supported" state until reload.

        NOTE: this intentionally does NOT read or persist CONF_IS_FRAME_TV.
        Having media_player also write that flag at runtime (via
        async_update_entry, on top of the sensor platform already doing so)
        regressed the power/Art Mode state sync — the switch could report off
        while the TV was on. The flag is cheap to re-derive: a single ~5s REST
        probe per startup, cached in memory for the rest of the session.
        """
        if self._frame_tv_supported:
            return True
        try:
            result = await self._art_api.supported()
            if result:
                self._frame_tv_supported = True
            return bool(result)
        except Exception as ex:
            self._log.debug("Frame TV support check failed: %s", ex)
            return False

    async def async_art_get_artmode(self) -> dict:
        """Get the current Art Mode status."""
        if not await self._ensure_frame_tv_check():
            result = {"error": "Frame TV not supported"}
            self._store_art_result(result)
            return result
        try:
            status = await self._art_api.get_artmode()
            result = {"service": "art_get_artmode", "status": status}
            self._store_art_result(result)
            return result
        except Exception as ex:
            result = {"service": "art_get_artmode", "error": str(ex)}
            self._store_art_result(result)
            return result

    async def async_art_set_artmode(self, enabled: bool) -> dict:
        """Enable or disable Art Mode."""
        if not await self._ensure_frame_tv_check():
            result = {"error": "Frame TV not supported"}
            self._store_art_result(result)
            return result
        try:
            await self._art_api.set_artmode(enabled)
            result = {"service": "art_set_artmode", "success": True, "enabled": enabled}
            self._store_art_result(result)
            return result
        except Exception as ex:
            result = {"service": "art_set_artmode", "error": str(ex)}
            self._store_art_result(result)
            return result

    async def async_art_available(self, category_id: str | None = None) -> dict:
        """Get list of available artwork on the TV."""
        if not await self._ensure_frame_tv_check():
            result = {"error": "Frame TV not supported"}
            self._store_art_result(result)
            return result
        try:
            artwork_list = await self._art_api.available(category_id)
            result = {
                "service": "art_available",
                "count": len(artwork_list),
                "artwork": artwork_list,
            }
            self._store_art_result(result)
            return result
        except Exception as ex:
            result = {"service": "art_available", "error": str(ex)}
            self._store_art_result(result)
            return result

    async def async_art_get_current(self) -> dict:
        """Get information about the currently displayed artwork."""
        if not await self._ensure_frame_tv_check():
            result = {"error": "Frame TV not supported"}
            self._store_art_result(result)
            return result
        try:
            current = await self._art_api.get_current()
            result = {"service": "art_get_current", "current": current}
            self._store_art_result(result)
            return result
        except Exception as ex:
            result = {"service": "art_get_current", "error": str(ex)}
            self._store_art_result(result)
            return result

    async def async_art_identify(self, force: bool = False) -> dict:
        """Identify the currently displayed artwork (reverse search + LLM).

        Opt-in pipeline (v8.4). Resolves the current content_id, then delegates
        to the shared entry point (config resolve + cache-first identification
        on the byte-stable current.jpg). Returns the result dict, usable via a
        service ``response_variable``. The automatic per-artwork trigger lives
        on the Art Metadata sensor.
        """
        from .art_identify import async_identify_for_entry, get_shared_cache

        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if entry is None:
            return {"error": "config entry not found"}
        try:
            current = await self._art_api.get_current()
        except Exception as ex:  # noqa: BLE001 — surfaced to the caller
            return {"error": f"could not read current artwork: {ex}"}
        content_id = (current or {}).get("content_id")

        cache = get_shared_cache(self.hass, self._entry_id)
        return await async_identify_for_entry(
            self.hass, entry, content_id, cache=cache, force=force
        )

    def _art_panel_size(self) -> tuple[int, int]:
        """Panel resolution for upload fitting, as the TV reports it."""
        from .api._image_prep import panel_size  # noqa: PLC0415 - lazy

        resolution = None
        if (info := self._device_info) and (device := info.get("device")):
            resolution = device.get("resolution")
        return panel_size(resolution)

    async def _panel_shows_art(self) -> bool | None:
        """getTVStates.pictureMode == "Ambient", or None when it cannot be read.

        Needs only IP Control to be paired: this is a plain state getter, not
        the artModeControl flag the art-mode option guards.
        """
        client = self._get_ip_control_client()
        if client is None:
            return None
        try:
            return await client.async_panel_shows_art()
        except SamsungIPControlError:
            return None

    async def _ensure_tv_awake_for_art(self) -> bool:
        """Ensure the TV is powered, WITHOUT forcing it into Art Mode.

        Uploading artwork only needs the art app reachable, not the panel
        showing art — the SmartThings app uploads happily while the TV is on
        a normal input. Yanking the picture away from whatever the user is
        watching just to store a file is the wrong trade-off, so operations
        that merely write to the art library use this instead of
        ``_ensure_art_mode_ready``.

        A Frame in Art Mode reports media_player state OFF, so art_mode_status
        is checked first: that is "powered and art app alive". Otherwise any
        non-OFF state means the TV is on in normal viewing and the art channel
        answers there too. Only a genuinely powered-off TV is woken up — and
        even then Art Mode is not forced; the TV returns to whatever state it
        was left in.
        """
        if self.extra_state_attributes.get(ATTR_ART_MODE_STATUS) == STATE_ON:
            self._log.debug("Frame Art: TV in Art Mode, ready")
            return True

        if self.state != MediaPlayerState.OFF:
            self._log.debug("Frame Art: TV on (normal viewing), ready")
            return True

        # state OFF is ambiguous on a Frame — Art Mode reports OFF too, and the
        # art_mode_status attribute can be stale or unknown. Confirm with the
        # TV itself before waking anything: powering on a panel that is already
        # running toggles it out of Art Mode onto the last input (HDMI).
        if self._art_api is not None:
            try:
                if await self._art_api.on():
                    self._log.debug("Frame Art: TV reports powered, ready")
                    return True
            except Exception:  # noqa: BLE001 - best effort probe
                pass

        # Off, or gone. Tell the two apart before spending 10s trying to wake
        # something that will never answer: a Frame in standby still serves its
        # REST endpoint, an unplugged or faulty one does not. Without this an
        # upload to a dead TV simply hung with nothing in the UI to explain it.
        if await self._async_load_device_info(force=True) is None:
            self._log.warning(
                "Frame Art: %s is unreachable — cannot upload. Check the TV is "
                "powered at the mains and on the network.",
                self._host,
            )
            return False

        # Genuinely off: power it on, then leave the mode alone.
        ip_client = self._get_ip_control_client()
        if ip_client is not None:
            try:
                if await ip_client.async_get_power_state() != "powerOff":
                    self._log.debug("Frame Art: IP Control reports powered, ready")
                    return True
                await ip_client.async_power_on()
                await asyncio.sleep(3)
                self._log.debug("Frame Art: TV powered on via IP Control")
                return True
            except SamsungIPControlError as ex:
                self._log.debug(
                    "Frame Art: IP Control power-on failed (%s); "
                    "falling back to Art Mode activation",
                    ex,
                )

        # No IP Control (or it failed): the WebSocket path can only wake a
        # Frame into Art Mode, so fall back to the full helper.
        return await self._ensure_art_mode_ready()

    async def _ensure_art_mode_ready(self) -> bool:
        """Ensure TV is on and in Art Mode. Turn it on and activate Art Mode if needed.

        Uses SmartThings as fallback if WebSocket connection fails.

        Returns:
            bool: True if TV is ready in Art Mode, False if failed
        """
        # Fast path: if the TV is already in Art Mode, there is nothing to do.
        # A Frame in Art Mode reports media_player state OFF, so we must NOT
        # treat state==OFF as "powered off" without checking art mode first —
        # otherwise async_turn_on() would send KEY_POWER and toggle the panel
        # OUT of Art Mode. The art_mode_status attribute (IP Control cache or
        # WebSocket art channel) is the authoritative signal here.
        if self.extra_state_attributes.get(ATTR_ART_MODE_STATUS) == STATE_ON:
            self._log.debug("Frame Art: already in Art Mode, ready")
            return True

        guard = guard_for(
            self.hass.data.setdefault(DOMAIN, {}).setdefault(self._entry_id, {})
        )

        # Panel truth before anything is written or toggled. art_mode_status
        # is a derived reading (IP flag, WebSocket art channel, SmartThings)
        # and every one of those sources can be stale or wedged; the panel's
        # own pictureMode cannot. If it shows Ambient, the TV IS in Art Mode
        # and the only wrong move is to act on the stale reading — which used
        # to mean an artModeOn at a panel already showing art on every
        # automation run, or a KEY_POWER that toggled it out of art and back.
        panel = await self._panel_shows_art()
        if panel:
            self._log.warning(
                "Frame Art: art_mode_status reads off but the panel shows Ambient "
                "— the reading is stale; treating the TV as in Art Mode and NOT "
                "writing"
            )
            guard.record_verified(True)
            return True

        try:
            guard.check(True)
        except ArtModeWriteSuppressed as ex:
            self._log.warning("Frame Art: %s", ex)
            return False

        # Prefer the reliable IP Control path to enter Art Mode when paired:
        # the explicit artModeOn command moves the panel even on TVs whose
        # WebSocket art channel is unresponsive ("zombie") or whose
        # set_artmode times out. Power on first (returns into the last state)
        # then force Art Mode on.
        #
        # Gated on CONF_IP_CONTROL_ART_MODE like every other art-mode use of
        # IP Control: with that option off, no artModeControl request is sent
        # at all, read or write. See the switch's _set_artmode for why the
        # write path used to escape the setting, and why that was wrong.
        ip_client = (
            self._get_ip_control_client()
            if self._get_option(CONF_IP_CONTROL_ART_MODE, False)
            else None
        )
        if ip_client is not None:
            try:
                if self.state == MediaPlayerState.OFF:
                    if await ip_client.async_get_power_state() == "powerOff":
                        await ip_client.async_power_on()
                        await asyncio.sleep(3)
                await ip_client.async_set_art_mode_on()
            except SamsungIPControlError as ex:
                self._log.debug(
                    "Frame Art: IP Control art-mode activation failed (%s); "
                    "falling back to WebSocket",
                    ex,
                )
            else:
                # Read the panel back rather than trusting the accepted command.
                await asyncio.sleep(2)
                after = await self._panel_shows_art()
                if after:
                    guard.record_verified(True)
                    self._log.debug("Frame Art: Art Mode activated via IP Control")
                    return True
                guard.record_unverified(True)
                if after is None:
                    self._log.debug(
                        "Frame Art: artModeOn accepted but the panel could not be "
                        "read back"
                    )
                    return True
                self._log.warning(
                    "Frame Art: artModeOn was accepted but the panel did not "
                    "switch to Ambient — not retrying within the cooldown"
                )
                return False

        # Check if TV is off, turn it on if needed
        if self.state == MediaPlayerState.OFF:
            self._log.info("Frame Art: TV is off, turning it on first...")

            try:
                # Never send the power key at a Frame whose art channel says
                # art is on: on a Frame that key TOGGLES the panel out of Art
                # Mode onto the last input. The panel read above already
                # returned for a confirmed Ambient; this covers the case where
                # the panel could not be read.
                if self._ws.artmode_status == ArtModeStatus.On:
                    self._log.debug(
                        "Frame Art: art channel reports art on; not sending "
                        "the power key"
                    )
                    raise _SkipPowerOn
                # Try normal WebSocket turn on first
                await self.async_turn_on()

                # Wait for TV to power up and be ready
                self._log.debug("Frame Art: Waiting for TV to be ready...")
                await asyncio.sleep(10)  # Wait for full TV startup

                self._log.info("Frame Art: TV should now be on")

            except _SkipPowerOn:
                pass
            except Exception as ex:
                # WebSocket failed, try SmartThings fallback
                if (
                    "1005" in str(ex)
                    or "saturated" in str(ex).lower()
                    or "closed" in str(ex).lower()
                ):
                    self._log.warning(
                        "Frame Art: WebSocket connection failed (TV may be in sleep mode), "
                        "trying SmartThings fallback..."
                    )

                    # Try SmartThings if available
                    if self._st:
                        try:
                            await self._st.async_turn_on()
                            self._log.info("Frame Art: TV turned on via SmartThings")

                            # Wait longer for TV to wake from sleep mode
                            self._log.debug("Frame Art: Waiting for TV to wake up...")
                            await asyncio.sleep(15)

                            self._log.info(
                                "Frame Art: TV should now be on (via SmartThings)"
                            )

                        except Exception as st_ex:
                            self._log.error(
                                "Frame Art: SmartThings fallback also failed: %s", st_ex
                            )
                            return False
                    else:
                        self._log.error(
                            "Frame Art: SmartThings not configured, cannot use fallback"
                        )
                        return False
                else:
                    self._log.error("Frame Art: Failed to turn on TV: %s", ex)
                    return False

        # TV is now on (or was already on), check if Art Mode is active
        self._log.debug("Frame Art: TV is on, checking if Art Mode is active...")

        try:
            # Check current Art Mode status
            async with asyncio.timeout(8):
                art_mode_status = await self._art_api.get_artmode()

            if art_mode_status == "on":
                self._log.debug("Frame Art: Art Mode already active")
                guard.record_verified(True)
                return True

            # Art Mode is not active, activate it
            self._log.info("Frame Art: Art Mode is OFF, activating it...")
            async with asyncio.timeout(10):
                result = await self._art_api.set_artmode(True)

            if not result:
                self._log.error("Frame Art: Failed to activate Art Mode")
                guard.record_unverified(True)
                return False

            # Wait a bit, then read back rather than trust the return value —
            # set_artmode can report success without moving the panel.
            await asyncio.sleep(2)
            try:
                async with asyncio.timeout(8):
                    confirmed = await self._art_api.get_artmode()
            except Exception:  # noqa: BLE001 - read-back is best effort
                confirmed = None
            if confirmed == "on":
                guard.record_verified(True)
                self._log.info("Frame Art: Art Mode successfully activated")
                return True
            guard.record_unverified(True)
            if confirmed is None:
                self._log.info("Frame Art: Art Mode activated (not read back)")
                return True
            self._log.warning(
                "Frame Art: set_artmode reported success but the TV still reports "
                "art mode %s — not retrying within the cooldown",
                confirmed,
            )
            return False

        except asyncio.TimeoutError:
            self._log.error("Frame Art: Timeout checking/activating Art Mode")
            return False
        except Exception as ex:
            self._log.error("Frame Art: Error ensuring Art Mode: %s", ex)
            return False

    async def async_art_select_image(
        self,
        content_id: str,
        category_id: str | None = None,
        show: bool = True,
    ) -> dict:
        """Select and display a piece of artwork."""
        if not await self._ensure_frame_tv_check():
            self._log.warning("Frame TV art mode is not supported on this device")
            return {"error": "Frame TV not supported"}

        # Ensure TV is on and in Art Mode
        if not await self._ensure_art_mode_ready():
            return {"error": "Failed to turn on TV"}

        # Select the artwork
        try:
            await self._art_api.select_image(content_id, category_id, show)

            return {"success": True, "content_id": content_id}
        except Exception as ex:
            self._log.error("Error selecting artwork: %s", ex)
            return {"error": str(ex)}

    async def _validate_matte_id(self, matte_id: str | None) -> str | None:
        """Return an error message when the TV cannot render this matte id.

        A matte is "<type>_<color>" and BOTH halves must come from the TV's own
        lists, which differ by model — sending an id built from a type and a
        colour this panel does not know makes the TV reject the upload with its
        own error, or (worse, seen on a QN55LS03HEFXZA) store the artwork and
        then fail rendering it, which looks like a TV fault rather than a bad
        parameter (#243).

        Returns None when the id is usable, or when the TV's lists could not be
        read — an unreachable list must never block an upload that would have
        worked.
        """
        if not matte_id or matte_id == "none":
            return None

        try:
            matte_types, matte_colors = await self._art_api.get_matte_list(
                include_color=True
            )
        except Exception as ex:  # noqa: BLE001 - never block an upload on this
            self._log.debug("Frame Art: could not read the matte list: %s", ex)
            return None

        def _ids(entries, key: str) -> set[str]:
            """Names from a matte list, which is dicts on some models, strings
            on others — the same shape the matte selects handle."""
            out: set[str] = set()
            for entry in entries or []:
                value = entry.get(key) if isinstance(entry, dict) else entry
                if isinstance(value, str) and value:
                    out.add(value)
            return out

        types = _ids(matte_types, "matte_type")
        colors = _ids(matte_colors, "color")
        if not types or not colors:
            return None

        matte_type, _, matte_color = matte_id.partition("_")
        unknown = []
        if matte_type not in types:
            unknown.append(f"type '{matte_type}' (this TV has: {sorted(types)})")
        if matte_color not in colors:
            unknown.append(f"colour '{matte_color}' (this TV has: {sorted(colors)})")
        if unknown:
            return f"matte_id '{matte_id}' is not valid on this TV: " + "; ".join(
                unknown
            )
        return None

    async def async_art_upload(
        self,
        file_path: str,
        matte_id: str = "shadowbox_polar",
        file_type: str = "jpg",
        show: bool = False,
    ) -> dict:
        """Upload an image to the TV as artwork."""
        self._log.info("Frame Art: Starting upload of %s", file_path)

        if not await self._ensure_frame_tv_check():
            self._log.warning("Frame TV art mode is not supported on this device")
            return {"error": "Frame TV not supported"}

        # Only needs the art app reachable — do not force Art Mode on.
        if not await self._ensure_tv_awake_for_art():
            return {"error": "TV unreachable or could not be turned on"}

        try:
            # Check if file exists
            file_exists = await self.hass.async_add_executor_job(
                lambda: __import__("os").path.exists(file_path)
            )
            if not file_exists:
                self._log.error("Frame Art: File not found: %s", file_path)
                return {"error": f"File not found: {file_path}"}

            # Get file size for logging
            file_size = await self.hass.async_add_executor_job(
                lambda: __import__("os").path.getsize(file_path)
            )
            self._log.info(
                "Frame Art: Uploading file %s (%d bytes) with matte=%s",
                file_path,
                file_size,
                matte_id,
            )

            if matte_error := await self._validate_matte_id(matte_id):
                self._log.error("Frame Art: %s", matte_error)
                return {"error": matte_error}

            content_id = await self._art_api.upload(
                file_path,
                matte=matte_id,
                file_type=file_type,
                hass=self.hass,
                panel=self._art_panel_size(),
            )
            if content_id:
                self._log.info(
                    "Frame Art: Upload successful, content_id=%s", content_id
                )

                # The TV often needs a while (sometimes minutes) to generate the
                # thumbnail for a just-uploaded image, so the immediate batch
                # fetch fails for it ("No data"/"Connection reset") and the
                # gallery shows a missing thumbnail until a later refresh happens
                # to pick it up. Retry this specific content_id with backoff in
                # the background until its thumbnail is available.
                self.hass.async_create_task(self._retry_new_thumbnail(content_id))

                if show:
                    await self.async_art_select_image(content_id, show=True)

                return {"success": True, "content_id": content_id}

            self._log.error("Frame Art: Upload failed - no content_id returned")
            return {"error": "Upload failed - no content_id returned"}
        except Exception as ex:
            self._log.error("Error uploading artwork: %s", ex)
            import traceback

            self._log.debug("Frame Art: Upload traceback: %s", traceback.format_exc())
            return {"error": str(ex)}

    async def async_art_upload_batch(
        self,
        folder: str,
        matte_id: str = "shadowbox_polar",
        throttle: float = 2.0,
        skip_unchanged: bool = True,
        dedup: bool = False,
    ) -> dict:
        """Upload every image in a folder, cheaply and idempotently.

        Re-running the same folder uploads only new/changed files (a per-folder
        sidecar tracks what was uploaded), and uploads are throttled so large
        batches complete instead of failing partway through. With ``dedup``,
        also skip a candidate that perceptually matches art already on the TV
        (compared against the downloaded thumbnails), robust to the TV's
        re-encode, with a strict anti-false-skip threshold.
        """
        import os

        self._log.info("Frame Art: batch upload from %s (dedup=%s)", folder, dedup)

        if not await self._ensure_frame_tv_check():
            return {"error": "Frame TV not supported"}
        # Writing to the art library does not require the panel to show art.
        if not await self._ensure_tv_awake_for_art():
            return {"error": "TV unreachable or could not be turned on"}

        from .api._upload_sidecar import list_images

        is_dir = await self.hass.async_add_executor_job(os.path.isdir, folder)
        if not is_dir:
            return {"error": f"Folder not found: {folder}"}
        files = await self.hass.async_add_executor_job(list_images, folder)
        if not files:
            return {"uploaded": [], "skipped": 0, "failed": [], "note": "no images"}

        # Per-folder sidecar (hidden; excluded from the *.jpg gallery filter).
        sidecar_path = (
            os.path.join(folder, ".samsungtv_upload.json") if skip_unchanged else None
        )
        # Reference = the personal thumbnails already downloaded from the TV.
        dedup_dir = None
        if dedup:
            dedup_dir = self.hass.config.path(
                "www", "frame_art", self._entry_id, "personal"
            )

        result = await self._art_api.upload_batch(
            files,
            matte=matte_id,
            hass=self.hass,
            throttle=throttle,
            sidecar_path=sidecar_path,
            dedup_dir=dedup_dir,
            panel=self._art_panel_size(),
        )

        # Reflect the new art and fetch the (delayed) TV-side thumbnails.
        if result.get("uploaded"):
            for content_id in result["uploaded"]:
                self.hass.async_create_task(self._retry_new_thumbnail(content_id))

        self._log.info(
            "Frame Art: batch upload from %s — %d uploaded, %d skipped, %d failed",
            folder,
            len(result.get("uploaded", [])),
            result.get("skipped", 0),
            len(result.get("failed", [])),
        )
        return result

    async def _retry_new_thumbnail(self, content_id: str) -> None:
        """Fetch a just-uploaded image's thumbnail once the TV has built it.

        Right after an upload the TV hasn't finished generating the thumbnail,
        so the immediate batch download fails for it. Retry this one content_id
        with a back-off (well past the TV's typical generation delay), stopping
        as soon as it's available so the gallery fills in without waiting for an
        unrelated later refresh.
        """
        # ~6.5 min total: covers the multi-minute generation delay seen in logs.
        retry_delays = [15, 30, 60, 120, 180]
        self._thumbnail_retry_pending.add(content_id)
        try:
            for delay in retry_delays:
                await asyncio.sleep(delay)
                try:
                    result = await self.async_art_get_thumbnail(content_id)
                except Exception as ex:  # noqa: BLE001 - best-effort retry
                    self._log.debug(
                        "Frame Art: delayed thumbnail retry for %s errored: %s",
                        content_id,
                        ex,
                    )
                    continue
                if result and not result.get("error"):
                    self._log.info(
                        "Frame Art: thumbnail for %s is now available (delayed retry)",
                        content_id,
                    )
                    return
        finally:
            self._thumbnail_retry_pending.discard(content_id)
        # Only now has the TV genuinely failed to produce it.
        self._log.warning(
            "Frame Art: thumbnail for %s still unavailable after %d delayed "
            "retries over ~%d minutes — the TV never generated it",
            content_id,
            len(retry_delays),
            sum(retry_delays) // 60,
        )

    async def async_art_delete(self, content_id: str) -> dict:
        """Delete an uploaded piece of artwork."""
        if not await self._ensure_frame_tv_check():
            self._log.warning("Frame TV art mode is not supported on this device")
            return {"error": "Frame TV not supported"}
        if not content_id.startswith("MY"):
            return {"error": "Can only delete user-uploaded content (MY-*)"}
        try:
            await self._art_api.delete(content_id)

            return {"success": True}
        except Exception as ex:
            self._log.error("Error deleting artwork: %s", ex)
            return {"error": str(ex)}

    async def async_art_get_thumbnail(
        self, content_id: str, save_to_file: bool = True, force_download: bool = False
    ) -> dict:
        """Get thumbnail for a specific piece of artwork.

        If save_to_file is True, saves the thumbnail to:
        - /config/www/frame_art/personal/ for user-uploaded images (MY_F*)
        - /config/www/frame_art/store/ for Samsung Art Store images (SAM-*)
        - /config/www/frame_art/other/ for other content types

        If force_download is False, checks if file already exists before downloading.
        """
        if not await self._ensure_frame_tv_check():
            self._log.warning("Frame TV art mode is not supported on this device")
            result = {"error": "Frame TV not supported"}
            self._store_art_result(result)
            return result

        try:
            import os

            # Determine subdirectory and file path based on content type
            if content_id.startswith("MY_F"):
                subdir = "personal"
            elif content_id.startswith("SAM-"):
                subdir = "store"
            else:
                subdir = "other"

            # Create directory path
            www_path = self.hass.config.path("www", "frame_art", self._entry_id, subdir)
            file_name = f"{content_id.replace(':', '_')}.jpg"
            file_path = os.path.join(www_path, file_name)

            # Check if file already exists (unless force_download=True)
            if save_to_file and not force_download:

                def _check_file_exists():
                    return os.path.isfile(file_path)

                file_exists = await self.hass.async_add_executor_job(_check_file_exists)

                if file_exists:
                    self._log.info(
                        "Thumbnail already exists for %s, skipping download", content_id
                    )
                    result = {
                        "service": "art_get_thumbnail",
                        "content_id": content_id,
                        "thumbnail_url": f"/local/frame_art/{self._entry_id}/{subdir}/{file_name}",
                        "thumbnail_path": file_path,
                        "subdirectory": subdir,
                        "cached": True,
                        "message": "File already exists",
                    }
                    self._store_art_result(result)
                    return result

            # Download thumbnail with improved retry logic
            max_retries = 3
            retry_delays = [1, 2, 5]  # Progressive delays (aligned with sensor.py)
            thumbnail_data = None
            last_error = None

            for attempt in range(max_retries):
                try:
                    self._log.debug(
                        "Downloading thumbnail for %s (attempt %d/%d)",
                        content_id,
                        attempt + 1,
                        max_retries,
                    )
                    thumbnail_data = await self._art_api.get_thumbnail(content_id)

                    if thumbnail_data and len(thumbnail_data) > 0:
                        self._log.debug(
                            "Successfully downloaded thumbnail for %s (%d bytes)",
                            content_id,
                            len(thumbnail_data),
                        )
                        break
                    else:
                        last_error = "No thumbnail data received"
                        self._log.debug(
                            "No data for %s on attempt %d", content_id, attempt + 1
                        )
                except Exception as retry_ex:
                    last_error = str(retry_ex)
                    self._log.debug(
                        "Error downloading %s on attempt %d: %s",
                        content_id,
                        attempt + 1,
                        retry_ex,
                    )

                # Wait before retry (except on last attempt)
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delays[attempt])

            if thumbnail_data and len(thumbnail_data) > 0:
                import base64

                encoded = base64.b64encode(thumbnail_data).decode("utf-8")
                result = {
                    "service": "art_get_thumbnail",
                    "content_id": content_id,
                    "thumbnail_base64": encoded,
                    "size": len(thumbnail_data),
                }

                # Save to file for Lovelace access
                if save_to_file:
                    try:

                        def _write_thumbnail():
                            os.makedirs(www_path, exist_ok=True)
                            with open(file_path, "wb") as f:
                                f.write(thumbnail_data)
                            return file_name, file_path, subdir

                        # Run file I/O in executor to avoid blocking
                        file_name, file_path, subdir = (
                            await self.hass.async_add_executor_job(_write_thumbnail)
                        )

                        # Add URL to result
                        result["thumbnail_url"] = (
                            f"/local/frame_art/{self._entry_id}/{subdir}/{file_name}"
                        )
                        result["thumbnail_path"] = file_path
                        result["subdirectory"] = subdir
                        self._log.debug("Saved thumbnail to %s", file_path)
                    except Exception as file_ex:
                        self._log.warning(
                            "Could not save thumbnail to file: %s", file_ex
                        )

                self._store_art_result(result)
                return result

            # All retries failed. For a just-uploaded image this is an
            # expected step, not a fault: the TV takes 30 s to several minutes
            # to generate the thumbnail, and _retry_new_thumbnail is already
            # waiting it out — 11 such WARNINGs in one hour on a maintainer's
            # install, every one of which ended with the thumbnail arriving.
            # Warn only when nothing is going to try again.
            error_msg = f"Failed after {max_retries} attempts: {last_error}"
            log = (
                self._log.debug
                if content_id in self._thumbnail_retry_pending
                else self._log.warning
            )
            log("Could not download thumbnail for %s: %s", content_id, error_msg)
            result = {"error": error_msg, "content_id": content_id}
            self._store_art_result(result)
            return result

        except Exception as ex:
            self._log.error("Error getting thumbnail for %s: %s", content_id, ex)
            result = {"error": str(ex), "content_id": content_id}
            self._store_art_result(result)
            return result

    async def _cleanup_orphan_thumbnails(
        self,
        valid_content_ids: set,
        favorites_only: bool = False,
        personal_only: bool = False,
        category_id: str | None = None,
    ) -> list:
        """Remove local thumbnail files that are no longer in the artwork list.

        Args:
            valid_content_ids: Set of content IDs that should be kept
            favorites_only: If True, only clean store/ directory (favorites are SAM-* images)
            personal_only: If True, only clean personal/ directory
            category_id: If set, determine which directory to clean based on category

        Returns:
            List of removed file paths
        """
        removed_files = []

        # Determine which directories to clean
        dirs_to_clean = []
        base_path = self.hass.config.path("www", "frame_art", self._entry_id)

        if favorites_only:
            # Favorites are typically SAM-* (store) images
            dirs_to_clean = [("store", os.path.join(base_path, "store"))]
        elif personal_only:
            # Personal photos are MY_F* images
            dirs_to_clean = [("personal", os.path.join(base_path, "personal"))]
        elif category_id:
            # Determine based on category
            if category_id == "MY-C0002":  # Personal photos
                dirs_to_clean = [("personal", os.path.join(base_path, "personal"))]
            elif category_id == "MY-C0004":  # Favorites (mostly store)
                dirs_to_clean = [("store", os.path.join(base_path, "store"))]
            else:
                # Clean all directories for other categories
                dirs_to_clean = [
                    ("personal", os.path.join(base_path, "personal")),
                    ("store", os.path.join(base_path, "store")),
                    ("other", os.path.join(base_path, "other")),
                ]
        else:
            # Clean all directories
            dirs_to_clean = [
                ("personal", os.path.join(base_path, "personal")),
                ("store", os.path.join(base_path, "store")),
                ("other", os.path.join(base_path, "other")),
            ]

        def _do_cleanup():
            """Synchronous cleanup function to run in executor."""
            removed = []
            for subdir_name, dir_path in dirs_to_clean:
                if not os.path.exists(dir_path):
                    continue

                try:
                    for filename in os.listdir(dir_path):
                        if not filename.endswith(".jpg"):
                            continue

                        # Extract content_id from filename (remove .jpg extension)
                        content_id = filename[:-4]

                        # Check if this content_id is still valid
                        if content_id not in valid_content_ids:
                            file_path = os.path.join(dir_path, filename)
                            try:
                                os.remove(file_path)
                                removed.append(
                                    {
                                        "content_id": content_id,
                                        "path": file_path,
                                        "subdirectory": subdir_name,
                                    }
                                )
                                self._log.info(
                                    "Removed orphan thumbnail: %s", file_path
                                )
                            except OSError as ex:
                                self._log.warning(
                                    "Failed to remove orphan thumbnail %s: %s",
                                    file_path,
                                    ex,
                                )
                except OSError as ex:
                    self._log.warning("Error scanning directory %s: %s", dir_path, ex)

            return removed

        try:
            removed_files = await self.hass.async_add_executor_job(_do_cleanup)
            if removed_files:
                self._log.info("Cleaned up %d orphan thumbnail(s)", len(removed_files))
        except Exception as ex:
            self._log.error("Error during orphan thumbnail cleanup: %s", ex)

        return removed_files

    async def async_art_get_thumbnails_batch(
        self,
        category_id: str | None = None,
        favorites_only: bool = False,
        personal_only: bool = False,
        force_download: bool = False,
        cleanup_orphans: bool = True,
    ) -> dict:
        """Download thumbnails for multiple artworks.

        Downloads thumbnails for:
        - All favorites (if favorites_only=True)
        - All personal images (if personal_only=True)
        - All artworks in a specific category (if category_id provided)
        - All artworks (if no filters specified)

        Saves thumbnails to organized subdirectories:
        - /config/www/frame_art/personal/ for user-uploaded images (MY_F*)
        - /config/www/frame_art/store/ for Samsung Art Store images (SAM-*)
        - /config/www/frame_art/other/ for other content types

        If force_download=False, skips files that already exist.
        If cleanup_orphans=True, removes local files not in the current artwork list.
        """
        if not await self._ensure_frame_tv_check():
            self._log.warning("Frame TV art mode is not supported on this device")
            result = {"error": "Frame TV not supported"}
            self._store_art_result(result)
            return result

        try:
            # Get artwork list based on filters
            if favorites_only:
                # Get favorites (category 4 = MY-C0004)
                artwork_list = await self._art_api.available("MY-C0004")
            elif personal_only:
                # Get personal photos (category 2 = MY-C0002)
                artwork_list = await self._art_api.available("MY-C0002")
            elif category_id:
                # Get specific category
                artwork_list = await self._art_api.available(category_id)
            else:
                # Get all artworks
                artwork_list = await self._art_api.available()

            # Build set of valid content IDs (empty set if the TV reports no
            # artworks at all for this filter, e.g. after a factory reset wipes
            # all personal photos -- that's a legitimate signal that every
            # locally cached file for this category is now an orphan, not a
            # reason to skip cleanup).
            valid_content_ids = {
                artwork.get("content_id")
                for artwork in artwork_list
                if artwork.get("content_id")
            }

            # Cleanup orphans if requested
            removed_files = []
            if cleanup_orphans:
                removed_files = await self._cleanup_orphan_thumbnails(
                    valid_content_ids, favorites_only, personal_only, category_id
                )

            if not artwork_list:
                result = {
                    "service": "art_get_thumbnails_batch",
                    "success": False,
                    "message": "No artworks found",
                    "downloaded": 0,
                    "skipped": 0,
                    "failed": 0,
                    "removed": len(removed_files),
                    "removed_list": removed_files,
                }
                self._store_art_result(result)
                return result

            # Download thumbnails with progress tracking
            downloaded = []
            skipped = []
            failed = []
            total = len(artwork_list)

            self._log.info(
                "Starting batch thumbnail download for %d artworks "
                "(force_download=%s, cleanup_orphans=%s, "
                "batch_capable=%s)",
                total,
                force_download,
                cleanup_orphans,
                self._art_api._supports_thumbnail_list,
            )

            for idx, artwork in enumerate(artwork_list, 1):
                content_id = artwork.get("content_id")
                if not content_id:
                    continue

                try:
                    self._log.debug(
                        "Processing thumbnail %d/%d: %s", idx, total, content_id
                    )

                    # Download with file existence check (unless force_download)
                    result = await self.async_art_get_thumbnail(
                        content_id, save_to_file=True, force_download=force_download
                    )

                    if "error" in result:
                        failed.append(
                            {"content_id": content_id, "error": result.get("error")}
                        )
                    elif result.get("cached"):
                        skipped.append(
                            {
                                "content_id": content_id,
                                "url": result.get("thumbnail_url"),
                                "path": result.get("thumbnail_path"),
                                "subdirectory": result.get("subdirectory"),
                                "reason": "Already exists",
                            }
                        )
                    else:
                        downloaded.append(
                            {
                                "content_id": content_id,
                                "url": result.get("thumbnail_url"),
                                "path": result.get("thumbnail_path"),
                                "subdirectory": result.get("subdirectory"),
                                "size": result.get("size"),
                            }
                        )

                    # Shorter delay between downloads
                    await asyncio.sleep(0.05)

                except Exception as ex:
                    self._log.warning(
                        "Failed to process thumbnail for %s: %s", content_id, ex
                    )
                    failed.append({"content_id": content_id, "error": str(ex)})

            # Build summary with metadata
            result = {
                "service": "art_get_thumbnails_batch",
                "success": True,
                "total_artworks": total,
                "downloaded": len(downloaded),
                "skipped": len(skipped),
                "failed": len(failed),
                "removed": len(removed_files),
                "downloaded_list": downloaded,
                "skipped_list": skipped,
                "failed_list": failed,
                "removed_list": removed_files,
                "filters": {
                    "category_id": category_id,
                    "favorites_only": favorites_only,
                    "personal_only": personal_only,
                    "force_download": force_download,
                    "cleanup_orphans": cleanup_orphans,
                },
            }

            self._log.info(
                "Batch thumbnail download complete: %d downloaded, %d skipped (already exist), %d failed, %d removed out of %d total",
                len(downloaded),
                len(skipped),
                len(failed),
                len(removed_files),
                total,
            )

            self._store_art_result(result)
            return result

        except Exception as ex:
            self._log.error("Error in batch thumbnail download: %s", ex)
            result = {
                "service": "art_get_thumbnails_batch",
                "error": str(ex),
            }
            self._store_art_result(result)
            return result
            self._store_art_result(result)
            return result

    async def async_art_set_brightness(self, brightness: int) -> dict:
        """Set Art Mode brightness.

        Accepts brightness 0-100 from UI and converts to TV's 1-10 scale.
        """
        if not await self._ensure_frame_tv_check():
            self._log.warning("Frame TV art mode is not supported on this device")
            return {"error": "Frame TV not supported"}

        # Ensure TV is on and in Art Mode
        if not await self._ensure_art_mode_ready():
            return {"error": "Failed to turn on TV"}

        try:
            # Convert 0-100 scale to 1-10 scale for the TV API
            # 0-10 -> 1, 11-20 -> 2, ..., 91-100 -> 10
            tv_brightness = max(
                1,
                min(
                    10,
                    (brightness // 10)
                    + (1 if brightness % 10 > 0 or brightness == 0 else 0),
                ),
            )
            # Simpler: map 0->1, 10->1, 20->2, ..., 100->10
            if brightness == 0:
                tv_brightness = 0
            else:
                tv_brightness = max(1, min(10, round(brightness / 10)))

            self._log.debug(
                "Frame Art: Converting brightness %d -> %d (TV scale)",
                brightness,
                tv_brightness,
            )
            await self._art_api.set_brightness(tv_brightness)
            return {
                "success": True,
                "brightness_ui": brightness,
                "brightness_tv": tv_brightness,
            }
        except Exception as ex:
            self._log.error("Error setting brightness: %s", ex)
            return {"error": str(ex)}

    async def async_art_get_brightness(self) -> dict:
        """Get Art Mode brightness.

        Returns brightness in both TV scale (1-10) and UI scale (0-100).
        """
        if not await self._ensure_frame_tv_check():
            self._log.warning("Frame TV art mode is not supported on this device")
            return {"error": "Frame TV not supported"}
        try:
            result = await self._art_api.get_brightness()
            # API may return a dict {"value": N} or a raw int depending on path
            tv_value = result.get("value") if isinstance(result, dict) else result
            if tv_value is not None:
                tv_value = int(tv_value)
            # Convert TV's 1-10 scale to 0-100 for UI
            ui_brightness = tv_value * 10 if tv_value is not None else None
            return {"brightness_tv": tv_value, "brightness_ui": ui_brightness}
        except Exception as ex:
            self._log.error("Error getting brightness: %s", ex)
            return {"error": str(ex)}

    async def async_art_set_color_temperature(self, color_temperature: int) -> dict:
        """Set Art Mode color temperature.

        Accepts -5 to +5 (negative = warmer, 0 = neutral, positive = cooler).
        """
        if not await self._ensure_frame_tv_check():
            self._log.warning("Frame TV art mode is not supported on this device")
            return {"error": "Frame TV not supported"}

        # Ensure TV is on and in Art Mode
        if not await self._ensure_art_mode_ready():
            return {"error": "Failed to turn on TV"}

        try:
            self._log.debug(
                "Frame Art: Setting color temperature to %d", color_temperature
            )
            await self._art_api.set_color_temperature(color_temperature)
            return {
                "success": True,
                "color_temperature": color_temperature,
            }
        except Exception as ex:
            self._log.error("Error setting color temperature: %s", ex)
            return {"error": str(ex)}

    async def async_art_get_color_temperature(self) -> dict:
        """Get Art Mode color temperature.

        Returns the value in the TV's native scale (typically -5 to +5).
        """
        if not await self._ensure_frame_tv_check():
            self._log.warning("Frame TV art mode is not supported on this device")
            return {"error": "Frame TV not supported"}
        try:
            result = await self._art_api.get_color_temperature()
            # API may return a dict {"value": N} or a raw int depending on path
            value = result.get("value") if isinstance(result, dict) else result
            if value is not None:
                value = int(value)
            return {"color_temperature": value}
        except Exception as ex:
            self._log.error("Error getting color temperature: %s", ex)
            return {"error": str(ex)}

    async def async_art_change_matte(
        self,
        content_id: str,
        matte_id: str,
    ) -> dict:
        """Change the matte/frame style for artwork."""
        if not await self._ensure_frame_tv_check():
            self._log.warning("Frame TV art mode is not supported on this device")
            return {"error": "Frame TV not supported"}

        # Ensure TV is on and in Art Mode
        if not await self._ensure_art_mode_ready():
            return {"error": "Failed to turn on TV"}

        try:
            await self._art_api.change_matte(content_id, matte_id)
            return {"success": True}
        except Exception as ex:
            self._log.error("Error changing matte: %s", ex)
            return {"error": str(ex)}

    async def async_art_set_photo_filter(
        self,
        content_id: str,
        filter_id: str,
    ) -> dict:
        """Apply a photo filter to artwork."""
        if not await self._ensure_frame_tv_check():
            self._log.warning("Frame TV art mode is not supported on this device")
            return {"error": "Frame TV not supported"}
        try:
            await self._art_api.set_photo_filter(content_id, filter_id)
            return {"success": True}
        except Exception as ex:
            self._log.error("Error setting photo filter: %s", ex)
            return {"error": str(ex)}

    async def async_art_get_photo_filter_list(self) -> dict:
        """Get list of available photo filters."""
        if not await self._ensure_frame_tv_check():
            self._log.warning("Frame TV art mode is not supported on this device")
            return {"error": "Frame TV not supported"}
        try:
            filters = await self._art_api.get_photo_filter_list()
            self._log.info("%s - Available photo filters: %s", self.entity_id, filters)
            return {"filters": filters}
        except Exception as ex:
            self._log.error("Error getting photo filter list: %s", ex)
            return {"error": str(ex)}

    async def async_art_get_matte_list(self) -> dict:
        """Get list of available matte styles."""
        if not await self._ensure_frame_tv_check():
            self._log.warning("Frame TV art mode is not supported on this device")
            return {"error": "Frame TV not supported"}
        try:
            matte_types, matte_colors = await self._art_api.get_matte_list(
                include_color=True
            )
            result = {"matte_types": matte_types, "matte_colors": matte_colors}
            self._log.info(
                "%s - Available matte types: %s | Available matte colors: %s",
                self.entity_id,
                matte_types,
                matte_colors,
            )
            return result
        except Exception as ex:
            self._log.error("Error getting matte list: %s", ex)
            return {"error": str(ex)}

    async def async_art_set_favourite(
        self,
        content_id: str,
        status: str = "on",
    ) -> dict:
        """Add or remove artwork from favourites."""
        if not await self._ensure_frame_tv_check():
            self._log.warning("Frame TV art mode is not supported on this device")
            return {"error": "Frame TV not supported"}
        try:
            await self._art_api.set_favourite(content_id, status)
            return {"success": True}
        except Exception as ex:
            self._log.error("Error setting favourite status: %s", ex)
            return {"error": str(ex)}

    def _get_active_slideshow_api(self) -> str:
        """Return the slideshow API name the TV is known to speak.

        Reads the value persisted in entry data by the coordinator's
        one-shot detection. Falls back to ``"slideshow"`` (the modern
        Frame TV API, used by 2024+ models) when detection hasn't run
        yet or was inconclusive — this preserves the historical default
        behavior for users upgrading from earlier releases.
        """
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if entry is None:
            return "slideshow"
        return entry.data.get(CONF_SLIDESHOW_API) or "slideshow"

    async def _set_slideshow_via_active_api(
        self,
        duration_minutes: int,
        shuffle: bool,
        category_id: int,
    ) -> None:
        """Route a slideshow SET call to whichever API the TV speaks.

        Called by both art_set_slideshow and art_set_auto_rotation —
        the two service names are aliases that target the same TV
        feature; the integration just sends the request to the
        endpoint the TV actually responds to (detected and persisted
        by the coordinator).
        """
        active_api = self._get_active_slideshow_api()
        self._log.debug(
            "Frame Art: routing slideshow set via %r API "
            "(duration=%d min, shuffle=%s, category=%d)",
            active_api,
            duration_minutes,
            shuffle,
            category_id,
        )
        if active_api == "auto_rotation":
            await self._art_api.set_auto_rotation_status(
                duration_minutes, shuffle, category_id
            )
        else:
            await self._art_api.set_slideshow_status(
                duration_minutes, shuffle, category_id
            )

    async def async_art_set_slideshow(
        self,
        duration: str,
        shuffle: bool = True,
        category_id: int = 2,
    ) -> dict:
        """Configure slideshow settings.

        Duration accepts the named presets '3min', '15min', '1h', '12h',
        '1d', '7d', or any other integer-coercible string representing
        minutes (e.g. '30', '30min', '180'). The set of durations the
        TV actually supports varies by Frame model and firmware — values
        outside the TV's supported set may be silently ignored by the TV.

        The request is routed to ``slideshow_status`` or
        ``auto_rotation_status`` depending on what the coordinator
        detected the TV listens to.
        """
        if not await self._ensure_frame_tv_check():
            self._log.warning("Frame TV art mode is not supported on this device")
            return {"error": "Frame TV not supported"}

        # Convert string duration to minutes
        duration_map = {
            "3min": 3,
            "15min": 15,
            "1h": 60,
            "12h": 720,
            "1d": 1440,
            "7d": 10080,
        }

        duration_minutes = duration_map.get(duration)
        if duration_minutes is None:
            # Try to parse as integer for backwards compatibility
            try:
                duration_minutes = int(duration)
            except (ValueError, TypeError):
                return {
                    "error": (
                        f"Invalid duration: {duration!r}. "
                        "Use one of the presets (3min, 15min, 1h, 12h, 1d, 7d) "
                        "or an integer number of minutes."
                    )
                }

        # Ensure TV is on and in Art Mode
        if not await self._ensure_art_mode_ready():
            return {"error": "Failed to turn on TV"}

        try:
            await self._set_slideshow_via_active_api(
                duration_minutes, shuffle, category_id
            )
            return {
                "success": True,
                "duration": duration,
                "duration_minutes": duration_minutes,
                "api": self._get_active_slideshow_api(),
            }
        except Exception as ex:
            self._log.error("Error setting slideshow: %s", ex)
            return {"error": str(ex)}

    async def async_art_set_auto_rotation(
        self,
        duration: str,
        shuffle: bool = True,
        category_id: int = 2,
    ) -> dict:
        """Configure auto rotation settings.

        Functionally identical to ``art_set_slideshow``: both service
        names are aliases that target the Frame TV's artwork rotation
        feature. The integration routes the request to whichever
        underlying Samsung API the TV responds to
        (``slideshow_status`` or ``auto_rotation_status``).

        Duration accepts the named presets '3min', '15min', '1h', '12h',
        '1d', '7d', or any other integer-coercible string representing
        minutes (e.g. '30', '30min', '180').
        """
        if not await self._ensure_frame_tv_check():
            self._log.warning("Frame TV art mode is not supported on this device")
            return {"error": "Frame TV not supported"}

        # Convert string duration to minutes
        duration_map = {
            "3min": 3,
            "15min": 15,
            "1h": 60,
            "12h": 720,
            "1d": 1440,
            "7d": 10080,
        }

        duration_minutes = duration_map.get(duration)
        if duration_minutes is None:
            try:
                duration_minutes = int(duration)
            except (ValueError, TypeError):
                return {
                    "error": (
                        f"Invalid duration: {duration!r}. "
                        "Use one of the presets (3min, 15min, 1h, 12h, 1d, 7d) "
                        "or an integer number of minutes."
                    )
                }

        # Ensure TV is on and in Art Mode
        if not await self._ensure_art_mode_ready():
            return {"error": "Failed to turn on TV"}

        try:
            await self._set_slideshow_via_active_api(
                duration_minutes, shuffle, category_id
            )
            return {
                "success": True,
                "duration": duration,
                "duration_minutes": duration_minutes,
                "api": self._get_active_slideshow_api(),
            }
        except Exception as ex:
            self._log.error("Error setting auto rotation: %s", ex)
            return {"error": str(ex)}

    async def _async_switch_entity(self, power_on: bool):
        """Switch on/off related configure HA entity."""

        if power_on:
            service_name = f"{HA_DOMAIN}.{SERVICE_TURN_ON}"
            conf_entity = CONF_SYNC_TURN_ON
        else:
            service_name = f"{HA_DOMAIN}.{SERVICE_TURN_OFF}"
            conf_entity = CONF_SYNC_TURN_OFF

        entity_list = self._get_option(conf_entity)
        if not entity_list:
            return

        for index, entity in enumerate(entity_list):
            if index >= MAX_CONTROLLED_ENTITY:
                self._log.warning(
                    "SamsungTV Smart - Maximum %s entities can be controlled",
                    MAX_CONTROLLED_ENTITY,
                )
                break
            if entity:
                await _async_call_service(self.hass, service_name, entity)

        return


async def _async_call_service(
    hass,
    service_name,
    entity_id,
    variable_data=None,
):
    """Call a HA service."""
    service_data = {
        CONF_SERVICE: service_name,
        CONF_SERVICE_ENTITY_ID: entity_id,
    }

    if variable_data:
        service_data[CONF_SERVICE_DATA] = variable_data

    try:
        await async_call_from_config(
            hass,
            service_data,
            blocking=False,
            validate_config=True,
        )
    except HomeAssistantError as ex:
        _LOGGER.error("SamsungTV Smart - error %s", ex)

    return
