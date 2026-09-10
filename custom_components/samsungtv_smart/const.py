"""Constants for the samsungtv_smart integration."""

from collections.abc import Mapping
from enum import Enum
from typing import Any


class AppLoadMethod(Enum):
    """Valid application load methods."""

    All = 1
    Default = 2
    NotLoad = 3


class AppLaunchMethod(Enum):
    """Valid application launch methods."""

    Standard = 1
    Remote = 2
    Rest = 3


class PowerOnMethod(Enum):
    """Valid power on methods."""

    WOL = 1
    SmartThings = 2
    IPControl = 3


DOMAIN = "samsungtv_smart"

MIN_HA_MAJ_VER = 2025
MIN_HA_MIN_VER = 6
__min_ha_version__ = f"{MIN_HA_MAJ_VER}.{MIN_HA_MIN_VER}.0"

DATA_CFG = "cfg"
DATA_CFG_YAML = "cfg_yaml"
DATA_OPTIONS = "options"
DATA_ENTRY_DATA = "entry_data"  # Snapshot of entry.data to detect data changes
DATA_ART_API = "art_api"  # Shared Frame Art API instance
DATA_ART_CACHE = "art_cache"  # Shared ArtIdentifyCache instance (per entry)
DATA_IP_CONTROL_STATE_COORDINATOR = "ip_control_state_coordinator"
CONF_IS_FRAME_TV = "is_frame_tv"  # Persisted flag: TV confirmed as Frame TV
# Bumped to a fresh value on every reconfigure so a reconfigure always changes
# the reload fingerprint — even a same-host re-pair or port re-detect that
# only touches keys listed in _NO_RELOAD_DATA_KEYS. This lets those
# runtime-learned keys be excluded from the fingerprint (so their automatic
# refresh no longer reloads the integration, #12) without also blinding the
# reconfigure-driven reload.
CONF_RECONFIGURE_GENERATION = "reconfigure_generation"
# V7: persisted capability flags for the dedicated brightness / colour-temp
# WebSocket requests. On TVs that don't respond (e.g. Frame 2024) we learn
# this at runtime and remember it across restarts so we don't pay the probe
# cost on every start.
CONF_SUPPORTS_GET_BRIGHTNESS = "supports_get_brightness"
CONF_SUPPORTS_GET_COLOR_TEMPERATURE = "supports_get_color_temperature"
# V7: content list polling interval (seconds). The Frame Art sensor refreshes
# at SCAN_INTERVAL (15 s) for cheap state, but the full get_content_list call
# is now throttled to this longer cycle. Default 5 min, configurable via
# advanced options.
CONF_CONTENT_LIST_INTERVAL = "content_list_interval"
DEFAULT_CONTENT_LIST_INTERVAL = 300
MIN_CONTENT_LIST_INTERVAL = 30
MAX_CONTENT_LIST_INTERVAL = 3600

# SmartThings cloud poll cadence (seconds), while the TV is ON. The local
# WebSocket / UPnP / IP Control are the primary state source (power, app,
# volume, mute) and keep polling every SCAN_INTERVAL; SmartThings is only
# hit for cloud-only data (channel name, picture mode, sound mode, power
# metering) at this slower cadence to cut cloud API usage. While the TV is
# OFF, SmartThings is throttled to ST_POLL_OFF_INTERVAL regardless.
CONF_ST_POLL_ON_INTERVAL = "st_poll_on_interval"
DEFAULT_ST_POLL_ON_INTERVAL = 30
MIN_ST_POLL_ON_INTERVAL = 5
MAX_ST_POLL_ON_INTERVAL = 600
# IP Control (local JSON-RPC) poll cadence (seconds) for the read-only state
# sensors: speaker, volume, mute, picture size/mode, sound mode, input source
# and — when the tuner is the active input — the local channel number. This is
# a LAN call to the TV with no cloud quota behind it, which is why it is a
# separate setting from CONF_ST_POLL_ON_INTERVAL rather than sharing it: the
# reason to slow SmartThings down (cloud rate limits) does not apply here.
# The floor is deliberately low but not zero — old panels (e.g. the 2018 set of
# issue #206) are slow to answer, and below ~5 s the requests start to overlap
# the responses rather than making anything more responsive.
CONF_IP_CONTROL_POLL_INTERVAL = "ip_control_poll_interval"
DEFAULT_IP_CONTROL_POLL_INTERVAL = 10
MIN_IP_CONTROL_POLL_INTERVAL = 5
MAX_IP_CONTROL_POLL_INTERVAL = 600

# Fixed SmartThings poll cadence (seconds) while the TV is OFF. A short
# keepalive so a power-on is still picked up from the cloud as a backup to
# the local WebSocket, without hammering the API during standby.
ST_POLL_OFF_INTERVAL = 30

# Persisted (entry.data): which SmartThings capability was VERIFIED to actually
# actuate the panel for setPictureMode ("custom.picturemode" or
# "samsungvd.pictureMode"). Learned at runtime by the verify-and-fallback logic
# (some TVs answer 200 COMPLETED on one capability without applying it — issue
# #116) and prioritized on later changes, surviving HA restarts. The other
# capability is always kept as fallback. Excluded from the reload fingerprint
# in __init__.py so persisting it never triggers an integration reload.
CONF_ST_PICTURE_MODE_CAPABILITY = "st_picture_mode_capability"

# ── Artwork identification (Frame TV, v8.4) ────────────────────────────────
# Opt-in pipeline: on each artwork change, a reverse image search (Google Cloud
# Vision Web Detection) produces candidate titles/artists, then an LLM
# (Anthropic or OpenAI) confirms/enriches against the thumbnail. Results are
# cached (keyed by the Samsung content_id for Store artworks, by image hash for
# personal ones) so each artwork is identified only once. Keys live in
# entry.data (secrets, like the ST/IP tokens).
CONF_ART_IDENTIFY_ENABLE = "art_identify_enable"
CONF_ART_VISION_API_KEY = "art_vision_api_key"
CONF_ART_LLM_PROVIDER = "art_llm_provider"  # "anthropic" | "openai"
CONF_ART_LLM_API_KEY = "art_llm_api_key"
CONF_ART_LLM_MODEL = "art_llm_model"
# Also run on personal uploads (MY-*). Off by default: they are almost never in
# Vision's art index, so they'd just burn quota returning "not identified".
CONF_ART_IDENTIFY_PERSONAL = "art_identify_personal"

ART_LLM_PROVIDERS = ("anthropic", "openai", "gemini")
DEFAULT_ART_LLM_PROVIDER = "anthropic"
# Last-resort defaults, used ONLY when the provider's model list cannot be
# read (no key yet, or the API is unreachable). The real default is picked
# from the live list — see art_identify.async_pick_default_model — because a
# pinned id eventually gets retired and breaks identification for everyone who
# never touched the setting (issue #188).
DEFAULT_ART_LLM_MODEL = {
    "anthropic": "claude-haiku-4-5",
    "openai": "gpt-4o-mini",
    "gemini": "gemini-flash-latest",
}

# Cache TTLs (seconds). A successful identification never changes, so it is kept
# effectively forever; a "not identified" result is retried after a couple of
# weeks (better model / better web indexing since). Transport errors are never
# cached, so they retry on the next artwork change.
ART_CACHE_TTL_HIT = 3650 * 86400
ART_CACHE_TTL_MISS = 14 * 86400
# Debounce (seconds) after a content_id change before running the pipeline, so a
# quick slideshow flip-through doesn't fire an identification per frame.
ART_IDENTIFY_DEBOUNCE = 8

CONF_SLIDESHOW_API = "slideshow_api"  # Persisted: "slideshow" or "auto_rotation"
LOCAL_LOGO_PATH = "local_logo_path"
WS_PREFIX = "[Home Assistant]"

ATTR_DEVICE_MAC = "device_mac"
ATTR_DEVICE_MODEL = "device_model"
ATTR_DEVICE_NAME = "device_name"
ATTR_DEVICE_OS = "device_os"

CONF_APP_LAUNCH_METHOD = "app_launch_method"
CONF_APP_LIST = "app_list"
CONF_APP_LOAD_METHOD = "app_load_method"
CONF_CHANNEL_LIST = "channel_list"
CONF_DEVICE_MODEL = "device_model"
CONF_DEVICE_NAME = "device_name"
CONF_DEVICE_OS = "device_os"
CONF_DUMP_APPS = "dump_apps"
CONF_EXT_POWER_ENTITY = "ext_power_entity"
CONF_LOAD_ALL_APPS = "load_all_apps"
CONF_LOGO_OPTION = "logo_option"
CONF_PING_PORT = "ping_port"
CONF_POWER_ON_METHOD = "power_on_method"
# REST/HTTP port, learned independently of CONF_PORT (the WS/token + Art port).
# Some older Frames (~2020) serve the REST API on 8001 while the secure token
# WebSocket + Art channel live on 8002, so a single shared port made the two
# self-heal mechanisms fight forever (8001 <-> 8002 ping-pong). Falls back to
# CONF_PORT when unset (existing installs / TVs where one port serves both).
CONF_REST_PORT = "rest_port"
CONF_SHOW_CHANNEL_NR = "show_channel_number"
CONF_SOURCE_LIST = "source_list"
CONF_SYNC_TURN_OFF = "sync_turn_off"
CONF_SYNC_TURN_ON = "sync_turn_on"
CONF_TOGGLE_ART_MODE = "toggle_art_mode"
CONF_USE_LOCAL_LOGO = "use_local_logo"
CONF_USE_MUTE_CHECK = "use_mute_check"
CONF_USE_ST_CHANNEL_INFO = "use_st_channel_info"
CONF_USE_ST_STATUS_INFO = "use_st_status_info"
CONF_WOL_REPEAT = "wol_repeat"
CONF_WS_NAME = "ws_name"

# for SmartThings integration api key usage
CONF_ST_ENTRY_UNIQUE_ID = "st_entry_unique_id"
CONF_USE_ST_INT_API_KEY = "use_st_int_api_key"  # obsolete used for migration
CONF_API_KEY = "api_key"
CONF_DEVICE_ID = "device_id"

# OAuth2 authentication
CONF_AUTH_METHOD = "auth_method"
CONF_OAUTH_TOKEN = "oauth_token"

# IP Control (JSON-RPC, port 1516) — persisted access token, per TV (entry.data).
# Presence of a token means the feature is paired/enabled for that TV.
CONF_IP_CONTROL_TOKEN = "ip_control_token"
CONF_ENABLE_IP_CONTROL = "enable_ip_control"
# Whether IP Control's artModeControl/getTVStates are used for Art Mode
# detection and switching. Disabled by default: some firmwares (e.g. after a
# factory reset) leave artModeControl wedged "on" regardless of the actual
# panel state, causing false art_mode_status readings. Power on/off via IP
# Control (CONF_ENABLE_IP_CONTROL / CONF_POWER_ON_METHOD) is unaffected by
# this setting. Re-enable once the TV's firmware reports artModeControl
# correctly again.
#
# OFF MEANS NO artModeControl TRAFFIC AT ALL — reads and writes. Until 8.7.7
# this gated only the getter, so a user who disabled it on our own advice (the
# path "can break Art Mode entirely and may need a factory reset", seen on a
# QE55LS03D) still had every art-mode toggle sent to the TV over JSON-RPC.
# Art mode then uses the WebSocket art channel for both reading and switching,
# which is what the documentation always said it did.
CONF_IP_CONTROL_ART_MODE = "ip_control_art_mode"

# Device identity, learned once via IP Control's getDeviceInformation right
# after pairing. Used to gate which model-dependent features/entities get
# created (not every TV supports the same IP Control capability set).
# Distinct from CONF_DEVICE_MODEL (the WS-discovered friendly model name):
# modelID here is the raw internal code IP Control returns (e.g. "25_PTM_FTV").
CONF_IP_CONTROL_MODEL_ID = "ip_control_model_id"
CONF_IP_CONTROL_FW_VERSION = "ip_control_fw_version"

# The port the TV answered on, learned at pairing time and reused afterwards.
# Samsung moved this once: 1515 up to the 2019 models, 1516 from 2020 onwards
# (documented by the RTI and Allonis control drivers). This integration was
# built against 2024/2025 Frames, so it only ever tried 1516 — leaving older
# sets looking unsupported when they simply listen elsewhere (#206).
# Absent from entry.data on TVs paired before this was introduced, hence the
# default: those all paired on 1516 by definition.
CONF_IP_CONTROL_PORT = "ip_control_port"
# Mirrors api.ipcontrol.DEFAULT_IP_CONTROL_PORT — kept here so the config layer
# does not have to import the api layer. Ordered: the modern port is tried
# first, since it covers every model this integration primarily targets.
IP_CONTROL_PORTS = (1516, 1515)


def ip_control_port(entry_data: Mapping[str, Any]) -> int:
    """Return the IP Control port to use for a TV, from its entry data."""
    return entry_data.get(CONF_IP_CONTROL_PORT) or IP_CONTROL_PORTS[0]


# Authentication methods
AUTH_METHOD_OAUTH = "oauth"
AUTH_METHOD_PAT = "pat"
AUTH_METHOD_ST_ENTRY = "st_entry"

# obsolete
CONF_UPDATE_METHOD = "update_method"
CONF_UPDATE_CUSTOM_PING_URL = "update_custom_ping_url"
CONF_SCAN_APP_HTTP = "scan_app_http"

DEFAULT_APP = "TV/HDMI"
DEFAULT_PORT = 8001
# Input source ids (casefolded) that mean "the built-in tuner", i.e. the only
# inputs on which a tuner channel number is meaningful. Samsung reports the
# tuner under several spellings depending on model and API.
TUNER_INPUT_SOURCES = frozenset({"tv", "digitaltv", "dtv"})
DEFAULT_SOURCE_LIST = {"TV": "KEY_TV", "HDMI": "KEY_HDMI"}
DEFAULT_TIMEOUT = 6

MAX_WOL_REPEAT = 5

RESULT_NOT_SUCCESSFUL = "not_successful"
RESULT_NOT_SUPPORTED = "not_supported"
RESULT_ST_DEVICE_USED = "st_device_used"
RESULT_ST_DEVICE_NOT_FOUND = "st_device_not_found"
RESULT_ST_MULTI_DEVICES = "st_multiple_device"
RESULT_SUCCESS = "success"
RESULT_WRONG_APIKEY = "wrong_api_key"

SERVICE_SELECT_PICTURE_MODE = "select_picture_mode"
SERVICE_START_HUE_SYNC = "start_hue_sync"
SERVICE_STOP_HUE_SYNC = "stop_hue_sync"

# Frame Art Extended Services
SERVICE_ART_GET_ARTMODE = "art_get_artmode"
SERVICE_ART_SET_ARTMODE = "art_set_artmode"
SERVICE_ART_AVAILABLE = "art_available"
SERVICE_ART_GET_CURRENT = "art_get_current"
SERVICE_ART_IDENTIFY = "art_identify"
SERVICE_ART_SELECT_IMAGE = "art_select_image"
SERVICE_ART_UPLOAD = "art_upload"
SERVICE_ART_UPLOAD_BATCH = "art_upload_batch"
SERVICE_ART_DELETE = "art_delete"
SERVICE_ART_GET_THUMBNAIL = "art_get_thumbnail"
SERVICE_ART_GET_THUMBNAILS_BATCH = "art_get_thumbnails_batch"
SERVICE_ART_SET_BRIGHTNESS = "art_set_brightness"
SERVICE_ART_GET_BRIGHTNESS = "art_get_brightness"
SERVICE_ART_SET_COLOR_TEMPERATURE = "art_set_color_temperature"
SERVICE_ART_GET_COLOR_TEMPERATURE = "art_get_color_temperature"
SERVICE_ART_CHANGE_MATTE = "art_change_matte"
SERVICE_ART_SET_PHOTO_FILTER = "art_set_photo_filter"
SERVICE_ART_GET_PHOTO_FILTER_LIST = "art_get_photo_filter_list"
SERVICE_ART_GET_MATTE_LIST = "art_get_matte_list"
SERVICE_ART_SET_FAVOURITE = "art_set_favourite"
SERVICE_ART_SET_SLIDESHOW = "art_set_slideshow"
SERVICE_ART_SET_AUTO_ROTATION = "art_set_auto_rotation"
SERVICE_SEND_TEXT = "send_text"

# Frame Art Service Attributes
ATTR_CONTENT_ID = "content_id"
ATTR_CATEGORY_ID = "category_id"
ATTR_FILE_PATH = "file_path"
ATTR_FILE_TYPE = "file_type"
ATTR_TEXT = "text"
ATTR_SCREEN_RESOLUTION = "screen_resolution"
ATTR_MATTE_ID = "matte_id"
ATTR_FILTER_ID = "filter_id"
ATTR_BRIGHTNESS = "brightness"
ATTR_COLOR_TEMPERATURE = "color_temperature"
ATTR_SHOW = "show"
ATTR_DURATION = "duration"
ATTR_SHUFFLE = "shuffle"
ATTR_ENABLED = "enabled"
ATTR_STATUS = "status"

SIGNAL_CONFIG_ENTITY = f"{DOMAIN}_config"

STD_APP_LIST = {
    "org.tizen.browser": {
        "st_app_id": "",
        "logo": "tizenbrowser.png",
        "name": "Internet",
    },
    "11101200001": {
        "st_app_id": "RN1MCdNq8t.Netflix",
        "logo": "netflix.png",
        "name": "Netflix",
    },
    "3201907018807": {
        "st_app_id": "org.tizen.netflix-app",
        "logo": "netflix.png",
        "name": "Netflix",
    },
    "111299001912": {
        "st_app_id": "9Ur5IzDKqV.TizenYouTube",
        "logo": "youtube.png",
        "name": "YouTube",
    },
    "3201512006785": {
        "st_app_id": "org.tizen.ignition",
        "logo": "primevideo.png",
        "name": "Prime Video",
    },
    "3201910019365": {
        "st_app_id": "org.tizen.primevideo",
        "logo": "primevideo.png",
        "name": "Prime Video",
    },
    "3201901017640": {
        "st_app_id": "MCmYXNxgcu.DisneyPlus",
        "logo": "disneyplus.png",
        "name": "Disney+",
    },
    "3202110025305": {
        "st_app_id": "rJyOSqC6Up.PPlusIntl",
        "logo": "paramountplus.png",
        "name": "Paramount+",
    },
    "11091000000": {
        "st_app_id": "4ovn894vo9.Facebook",
        "logo": "facebook.png",
        "name": "Facebook",
    },
    "3201806016390": {
        "st_app_id": "yu1NM3vHsU.DAZN",
        "logo": "dazn.png",
        "name": "DAZN",
    },
    "3201601007250": {
        "st_app_id": "QizQxC7CUf.PlayMovies",
        "logo": "",
        "name": "Google Play",
    },
    "3201606009684": {
        "st_app_id": "rJeHak5zRg.Spotify",
        "logo": "spotify.png",
        "name": "Spotify",
    },
    "3201512006963": {
        "st_app_id": "kIciSQlYEM.plex",
        "logo": "",
        "name": "Plex",
    },
    "com.samsung.tv.csfs": {
        "st_app_id": "",
        "logo": "",
        "name": "Smart Hub",
    },
}
