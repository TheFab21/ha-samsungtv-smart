"""The samsungtv_smart integration."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from pathlib import Path
import socket
import time
from weakref import WeakValueDictionary

from aiohttp import ClientConnectionError, ClientResponseError, ClientSession
import async_timeout
import voluptuous as vol
from websocket import WebSocketException

from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_DEVICE_ID,
    CONF_ACCESS_TOKEN,
    CONF_API_KEY,
    CONF_BROADCAST_ADDRESS,
    CONF_DEVICE_ID,
    CONF_HOST,
    CONF_ID,
    CONF_MAC,
    CONF_NAME,
    CONF_PORT,
    CONF_TIMEOUT,
    CONF_TOKEN,
    EVENT_HOMEASSISTANT_STARTED,
    MAJOR_VERSION,
    MINOR_VERSION,
    Platform,
    __version__,
)
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.storage import STORAGE_DIR
from homeassistant.helpers.typing import ConfigType

from .api.art import SamsungTVAsyncArt
from .api.samsungws import ConnectionFailure, SamsungTVWS
from .api.smartthings import SmartThingsTV
from .const import (
    ATTR_DEVICE_MAC,
    ATTR_DEVICE_MODEL,
    ATTR_DEVICE_NAME,
    ATTR_DEVICE_OS,
    AUTH_METHOD_OAUTH,
    AUTH_METHOD_ST_ENTRY,
    CONF_APP_LIST,
    CONF_ART_IDENTIFY_PERSONAL,
    CONF_ART_LLM_API_KEY,
    CONF_ART_LLM_MODEL,
    CONF_ART_LLM_PROVIDER,
    CONF_ART_VISION_API_KEY,
    CONF_AUTH_METHOD,
    CONF_CHANNEL_LIST,
    CONF_DEVICE_NAME,
    CONF_ENABLE_IP_CONTROL,
    CONF_IP_CONTROL_ART_MODE,
    CONF_IP_CONTROL_FW_VERSION,
    CONF_IP_CONTROL_MODEL_ID,
    CONF_IP_CONTROL_POLL_INTERVAL,
    CONF_IP_CONTROL_TOKEN,
    CONF_IS_FRAME_TV,
    CONF_LOAD_ALL_APPS,
    CONF_OAUTH_TOKEN,
    CONF_REST_PORT,
    CONF_SCAN_APP_HTTP,
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
    CONF_UPDATE_CUSTOM_PING_URL,
    CONF_UPDATE_METHOD,
    CONF_USE_ST_INT_API_KEY,
    CONF_WS_NAME,
    DATA_ART_API,
    DATA_CFG,
    DATA_CFG_YAML,
    DATA_ENTRY_DATA,
    DATA_OPTIONS,
    DEFAULT_PORT,
    DEFAULT_SOURCE_LIST,
    DEFAULT_TIMEOUT,
    DOMAIN,
    LOCAL_LOGO_PATH,
    MIN_HA_MAJ_VER,
    MIN_HA_MIN_VER,
    RESULT_NOT_SUCCESSFUL,
    RESULT_ST_DEVICE_NOT_FOUND,
    RESULT_SUCCESS,
    RESULT_WRONG_APIKEY,
    SIGNAL_CONFIG_ENTITY,
    WS_PREFIX,
    __min_ha_version__,
)
from .logo import CUSTOM_IMAGE_BASE_URL, STATIC_IMAGE_BASE_URL
from .token_notify import METHOD_IP_CONTROL, METHOD_LOCAL, clear_token_problem

# workaroud for failing import native domain when custom integration is present
try:
    from homeassistant.components.smartthings.const import DOMAIN as ST_DOMAIN
except ImportError:
    ST_DOMAIN = "smartthings"

DEVICE_INFO = {
    ATTR_DEVICE_ID: "id",
    ATTR_DEVICE_MAC: "wifiMac",
    ATTR_DEVICE_NAME: "name",
    ATTR_DEVICE_MODEL: "modelName",
    ATTR_DEVICE_OS: "OS",
}

SAMSMART_PLATFORM = [
    Platform.SENSOR,
    Platform.MEDIA_PLAYER,
    Platform.REMOTE,
    Platform.SWITCH,
    Platform.SELECT,
    Platform.NUMBER,
    Platform.BUTTON,
]

SAMSMART_SCHEMA = {
    vol.Optional(CONF_SOURCE_LIST, default=DEFAULT_SOURCE_LIST): cv.string,
    vol.Optional(CONF_APP_LIST): cv.string,
    vol.Optional(CONF_CHANNEL_LIST): cv.string,
    vol.Optional(CONF_TIMEOUT, default=DEFAULT_TIMEOUT): cv.positive_int,
    vol.Optional(CONF_MAC): cv.string,
    vol.Optional(CONF_BROADCAST_ADDRESS): cv.string,
}


def ensure_unique_hosts(value):
    """Validate that all configs have a unique host."""
    vol.Schema(vol.Unique("duplicate host entries found"))(
        [socket.gethostbyname(entry[CONF_HOST]) for entry in value]
    )
    return value


CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.All(
            cv.ensure_list,
            [
                cv.deprecated(CONF_LOAD_ALL_APPS),
                cv.deprecated(CONF_PORT),
                cv.deprecated(CONF_UPDATE_METHOD),
                cv.deprecated(CONF_UPDATE_CUSTOM_PING_URL),
                cv.deprecated(CONF_SCAN_APP_HTTP),
                vol.Schema(
                    {
                        vol.Required(CONF_HOST): cv.string,
                        vol.Optional(CONF_NAME): cv.string,
                        vol.Optional(CONF_PORT, default=DEFAULT_PORT): cv.port,
                        vol.Optional(CONF_API_KEY): cv.string,
                        vol.Optional(CONF_DEVICE_NAME): cv.string,
                        vol.Optional(CONF_DEVICE_ID): cv.string,
                        vol.Optional(CONF_LOAD_ALL_APPS, default=True): cv.boolean,
                        vol.Optional(CONF_UPDATE_METHOD): cv.string,
                        vol.Optional(CONF_UPDATE_CUSTOM_PING_URL): cv.string,
                        vol.Optional(CONF_SCAN_APP_HTTP, default=True): cv.boolean,
                        vol.Optional(CONF_SHOW_CHANNEL_NR, default=False): cv.boolean,
                        vol.Optional(CONF_WS_NAME): cv.string,
                    }
                ).extend(SAMSMART_SCHEMA),
            ],
            ensure_unique_hosts,
        )
    },
    extra=vol.ALLOW_EXTRA,
)

_LOGGER = logging.getLogger(__name__)

# Global lock dictionary to prevent concurrent OAuth refreshes for entries that
# share a SmartThings refresh token. SmartThings rotates that token, so allowing
# each TV entry to refresh it independently makes the first refresh invalidate
# every sibling entry. Weak references avoid retaining one lock per daily token
# rotation indefinitely.
_OAUTH_REFRESH_LOCKS: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()
_OAUTH_REFRESH_IN_PROGRESS: dict[str, bool] = {}
# Set once the SmartThings refresh token is rejected with invalid_grant: the
# refresh token is dead and retrying only hammers the auth endpoint (observed
# thousands of times/hour). We stop attempting refresh and trigger a reauth
# flow; the flag is cleared automatically once a valid token is stored again.
_OAUTH_TOKEN_INVALID: dict[str, bool] = {}


def is_oauth_token_invalid(entry_id: str) -> bool:
    """True when the entry's refresh token was rejected and reauth is needed."""
    return _OAUTH_TOKEN_INVALID.get(entry_id, False)


def set_oauth_token_invalid(entry_id: str, invalid: bool) -> None:
    """Mark/clear the entry's refresh token as invalid (reauth required)."""
    _OAUTH_TOKEN_INVALID[entry_id] = invalid


def is_invalid_grant_error(ex: Exception) -> bool:
    """True when an OAuth refresh failure is the terminal invalid_grant case."""
    text = str(ex).lower()
    return "invalid_grant" in text or "invalid refresh token" in text


def start_oauth_reauth(hass: HomeAssistant, entry_id: str) -> None:
    """Latch the invalid-token flag and kick off HA's reauth flow once."""
    if _OAUTH_TOKEN_INVALID.get(entry_id):
        return  # already handled — don't spawn duplicate reauth flows
    _OAUTH_TOKEN_INVALID[entry_id] = True
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is not None:
        _LOGGER.warning(
            "[%s] SmartThings refresh token rejected (invalid_grant) — reauth "
            "required; stopping refresh attempts until re-authenticated",
            entry.title,
        )
        try:
            entry.async_start_reauth(hass)
        except Exception as exc:  # pylint: disable=broad-except
            _LOGGER.debug("Could not start reauth flow: %s", exc)


def _oauth_refresh_group_key(entry: ConfigEntry) -> str:
    """Return a non-secret key for entries sharing an OAuth refresh token."""
    oauth_token = entry.data.get(CONF_OAUTH_TOKEN)
    refresh_token = (
        oauth_token.get("refresh_token") if isinstance(oauth_token, dict) else None
    )
    if not isinstance(refresh_token, str) or not refresh_token:
        return f"entry:{entry.entry_id}"

    implementation = entry.data.get("auth_implementation", DOMAIN)
    token_digest = hashlib.sha256(refresh_token.encode()).hexdigest()
    return f"{implementation}:{token_digest}"


def get_oauth_refresh_lock(entry: ConfigEntry) -> asyncio.Lock:
    """Get or create a lock for entries sharing an OAuth refresh token."""
    group_key = _oauth_refresh_group_key(entry)
    lock = _OAUTH_REFRESH_LOCKS.get(group_key)
    if lock is None:
        lock = asyncio.Lock()
        _OAUTH_REFRESH_LOCKS[group_key] = lock
    return lock


@callback
def update_shared_oauth_token(
    hass: HomeAssistant,
    source_entry: ConfigEntry,
    previous_token: dict,
    new_token: dict,
    source_data_updates: dict | None = None,
) -> int:
    """Store a rotated OAuth token in all entries that shared its predecessor."""
    previous_refresh_token = previous_token.get("refresh_token")
    implementation = source_entry.data.get("auth_implementation", DOMAIN)
    updated_count = 0

    for entry in hass.config_entries.async_entries(DOMAIN):
        entry_token = entry.data.get(CONF_OAUTH_TOKEN)
        is_source = entry.entry_id == source_entry.entry_id
        if not is_source:
            if not previous_refresh_token or not isinstance(entry_token, dict):
                continue
            if entry_token.get("refresh_token") != previous_refresh_token:
                continue
            if entry.data.get("auth_implementation", DOMAIN) != implementation:
                continue

        hass.config_entries.async_update_entry(
            entry,
            data={
                **entry.data,
                CONF_OAUTH_TOKEN: new_token,
                CONF_API_KEY: new_token["access_token"],
                "auth_implementation": implementation,
                **(source_data_updates if is_source and source_data_updates else {}),
            },
        )
        set_oauth_token_invalid(entry.entry_id, False)
        ir.async_delete_issue(hass, DOMAIN, f"oauth_auth_failed_{entry.entry_id}")
        updated_count += 1

    _LOGGER.info(
        "OAuth token updated for %d TV entr%s sharing the previous token",
        updated_count,
        "y" if updated_count == 1 else "ies",
    )
    return updated_count


def is_oauth_refresh_in_progress(entry_id: str) -> bool:
    """Check if OAuth refresh is already in progress for an entry."""
    return _OAUTH_REFRESH_IN_PROGRESS.get(entry_id, False)


def set_oauth_refresh_in_progress(entry_id: str, in_progress: bool) -> None:
    """Set OAuth refresh in progress state for an entry."""
    _OAUTH_REFRESH_IN_PROGRESS[entry_id] = in_progress


def tv_url(host: str, address: str = "") -> str:
    """Return url to the TV."""
    return f"http://{host}:8001/api/v2/{address}"


def is_min_ha_version(min_ha_major_ver: int, min_ha_minor_ver: int) -> bool:
    """Check if HA version at least a specific version."""
    return MAJOR_VERSION > min_ha_major_ver or (
        MAJOR_VERSION == min_ha_major_ver and MINOR_VERSION >= min_ha_minor_ver
    )


def is_valid_ha_version() -> bool:
    """Check if HA version is valid for this integration."""
    return is_min_ha_version(MIN_HA_MAJ_VER, MIN_HA_MIN_VER)


def _notify_message(
    hass: HomeAssistant, notification_id: str, title: str, message: str
) -> None:
    """Notify user with persistent notification."""
    hass.async_create_task(
        hass.services.async_call(
            domain="persistent_notification",
            service="create",
            service_data={
                "title": title,
                "message": message,
                "notification_id": f"{DOMAIN}.{notification_id}",
            },
        )
    )


def _load_option_list(src_list):
    """Load list parameters in JSON from configuration.yaml."""

    if src_list is None:
        return None
    if isinstance(src_list, dict):
        return src_list

    result = {}
    try:
        result = json.loads(src_list)
    except TypeError:
        _LOGGER.error("Invalid format parameter: %s", str(src_list))
    return result


def token_file_name(hostname: str) -> str:
    """Return token file name."""
    return f"{DOMAIN}_{hostname}_token"


def _remove_token_file(hass, hostname, token_file=None):
    """Try to remove token file."""
    if not token_file:
        token_file = hass.config.path(STORAGE_DIR, token_file_name(hostname))

    if os.path.isfile(token_file):
        try:
            os.remove(token_file)
        except Exception as exc:  # pylint: disable=broad-except
            _LOGGER.error(
                "Samsung TV - Error deleting token file %s: %s", token_file, str(exc)
            )


def _migrate_token(hass: HomeAssistant, entry: ConfigEntry, hostname: str) -> None:
    """Migrate token from old file to registry entry."""
    token_file = hass.config.path(STORAGE_DIR, token_file_name(hostname))
    if not os.path.isfile(token_file):
        token_file = (
            os.path.dirname(os.path.realpath(__file__)) + f"/token-{hostname}.txt"
        )
        if not os.path.isfile(token_file):
            return

    try:
        with open(token_file, "r", encoding="utf-8") as os_token_file:
            token = os_token_file.readline()
    except Exception as exc:  # pylint: disable=broad-except
        _LOGGER.error("Error reading token file %s: %s", token_file, str(exc))
        return

    if not token:
        _LOGGER.warning("No token found inside token file %s", token_file)
        return

    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_TOKEN: token}
    )
    _remove_token_file(hass, hostname, token_file)


@callback
def _migrate_options_format(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Migrate options to new format."""
    opt_migrated = False
    new_options = {}

    for key, option in entry.options.items():
        if key in [CONF_SYNC_TURN_OFF, CONF_SYNC_TURN_ON]:
            if isinstance(option, str):
                new_options[key] = option.split(",")
                opt_migrated = True
                continue
        new_options[key] = option

    # load the option lists in entry option
    yaml_opt = hass.data.get(DOMAIN, {}).get(entry.entry_id, {}).get(DATA_CFG_YAML, {})
    for key in [CONF_APP_LIST, CONF_CHANNEL_LIST, CONF_SOURCE_LIST]:
        if key not in new_options:  # import will occurs only on first restart
            if option := _load_option_list(yaml_opt.get(key, {})):
                message = (
                    f"Configuration key '{key}' has been in imported in integration options,"
                    " you can now remove from configuration.yaml"
                )
                _notify_message(
                    hass, f"config-import-{key}", "SamsungTV Smart", message
                )
                _LOGGER.warning(message)
            new_options[key] = option
            opt_migrated = True

    if opt_migrated:
        hass.config_entries.async_update_entry(entry, options=new_options)


@callback
def _migrate_entry_unique_id(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Migrate unique_is to new format."""
    if CONF_ID in entry.data:
        new_unique_id = entry.data[CONF_ID]
    elif CONF_MAC in entry.data:
        new_unique_id = entry.data[CONF_MAC]
    else:
        new_unique_id = entry.data[CONF_HOST]

    if entry.unique_id == new_unique_id:
        return

    entries_list = hass.config_entries.async_entries(DOMAIN)
    for other_entry in entries_list:
        if other_entry.unique_id == new_unique_id:
            _LOGGER.warning(
                "Found duplicated entries %s and %s that refer to the same device."
                " Please remove unused entry",
                entry.data[CONF_HOST],
                other_entry.data[CONF_HOST],
            )
            return

    _LOGGER.info(
        "Migrated entry unique id from %s to %s", entry.unique_id, new_unique_id
    )
    hass.config_entries.async_update_entry(entry, unique_id=new_unique_id)


@callback
def _migrate_smartthings_config(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Migrate smartthings entry usage configuration."""
    if CONF_USE_ST_INT_API_KEY not in entry.data:
        return

    new_data = entry.data.copy()
    use_st = new_data.pop(CONF_USE_ST_INT_API_KEY)
    if use_st:
        if entries_list := hass.config_entries.async_entries(ST_DOMAIN, False, False):
            new_data[CONF_ST_ENTRY_UNIQUE_ID] = entries_list[0].unique_id

    hass.config_entries.async_update_entry(entry, data=new_data)


@callback
def get_smartthings_entries(hass: HomeAssistant) -> dict[str, str] | None:
    """Get the smartthing integration configured entries.

    Returns entries that have either:
    - CONF_TOKEN (PAT or OAuth in token dict)
    - CONF_ACCESS_TOKEN (direct OAuth token)
    """
    entries_list = hass.config_entries.async_entries(ST_DOMAIN, False, False)
    if not entries_list:
        return None

    result = {}
    for entry in entries_list:
        # Include entries with token (PAT or OAuth dict)
        # OR direct access_token (OAuth alternative structure)
        if CONF_TOKEN in entry.data or CONF_ACCESS_TOKEN in entry.data:
            result[entry.unique_id] = entry.title

    return result if result else None


@callback
def get_smartthings_api_key(hass: HomeAssistant, st_unique_id: str) -> str | None:
    """Get the smartthing integration configured API key.

    Supports both:
    - Legacy PAT (Personal Access Token) - stored as string
    - OAuth tokens - stored as dict with access_token
    """
    entries_list = hass.config_entries.async_entries(ST_DOMAIN, False, False)
    if not entries_list:
        return None

    for entry in entries_list:
        if entry.unique_id == st_unique_id:
            config_data = entry.data

            # Try OAuth token structure first (new method)
            # OAuth tokens are in entry.data['token'] as dict
            if CONF_TOKEN in config_data:
                token_data = config_data[CONF_TOKEN]

                # OAuth: token is a dict with access_token key
                if isinstance(token_data, dict):
                    if CONF_ACCESS_TOKEN in token_data:
                        _LOGGER.debug(
                            "SmartThings: Found OAuth access_token for %s", st_unique_id
                        )
                        return token_data[CONF_ACCESS_TOKEN]

                # Legacy PAT: token is a string directly
                elif isinstance(token_data, str):
                    _LOGGER.debug(
                        "SmartThings: Found legacy PAT token for %s", st_unique_id
                    )
                    return token_data

            # Also try direct access_token key (alternative OAuth structure)
            if CONF_ACCESS_TOKEN in config_data:
                _LOGGER.debug(
                    "SmartThings: Found direct access_token for %s", st_unique_id
                )
                return config_data[CONF_ACCESS_TOKEN]

            _LOGGER.warning(
                "SmartThings: No valid token found for %s in entry data keys: %s",
                st_unique_id,
                list(config_data.keys()),
            )
            return None

    return None


@callback
def has_refreshable_oauth_token(entry: ConfigEntry) -> bool:
    """Return True if the entry stores an OAuth token that can be refreshed.

    Some entries created through the OAuth flow end up labeled with a
    different auth_method (e.g. "pat", because the access token doubles as
    the API key) while still holding a refreshable oauth_token. Token
    management must follow the token data, not the label: SmartThings
    access tokens expire after 24 hours, and skipping refresh for these
    entries kills every SmartThings-backed entity a day later.
    """
    oauth_token = entry.data.get(CONF_OAUTH_TOKEN)
    return isinstance(oauth_token, dict) and bool(oauth_token.get("refresh_token"))


async def async_get_samsungtv_api_key(  # noqa: C901
    hass: HomeAssistant, entry: ConfigEntry
) -> str | None:
    """Get API key based on authentication method configured for this entry.

    This function handles all three auth methods:
    - OAuth2: Uses own OAuth token with auto-refresh
    - PAT: Uses Personal Access Token from entry data
    - ST_ENTRY: Gets token from SmartThings integration

    Returns:
        API key/access token string if available, None otherwise
    """
    auth_method = entry.data.get(CONF_AUTH_METHOD)

    # Method 1: OAuth2 - own token with refresh. Entries holding a
    # refreshable oauth_token take this path regardless of their label,
    # so their 24h access token keeps being renewed.
    if auth_method == AUTH_METHOD_OAUTH or has_refreshable_oauth_token(entry):
        oauth_token = entry.data.get(CONF_OAUTH_TOKEN)
        if oauth_token and isinstance(oauth_token, dict):
            access_token = oauth_token.get("access_token")
            if access_token:
                # Check if token needs refresh (5 minutes before expiration)
                expires_at = oauth_token.get("expires_at", 0)
                current_time = time.time()

                if expires_at and current_time > (expires_at - 300):
                    # Check if refresh_token exists
                    if "refresh_token" not in oauth_token:
                        _LOGGER.warning(
                            "OAuth token expired and no refresh_token available. "
                            "Please reconfigure the integration with OAuth."
                        )
                        return access_token  # Try with expired token anyway

                    # If the refresh token was already rejected (invalid_grant),
                    # a reauth flow is pending — don't keep hammering the auth
                    # endpoint on every cycle. Use the (expired) token and wait
                    # for the user to re-authenticate.
                    if is_oauth_token_invalid(entry.entry_id):
                        _LOGGER.debug(
                            "[%s] Skipping OAuth refresh — reauth pending",
                            entry.title,
                        )
                        return access_token

                    # Acquire lock to prevent concurrent refresh. If another
                    # task is already refreshing, WAIT for it instead of
                    # returning the current (expired) token: platform setups
                    # (ST sensors, power switch) race the media_player refresh
                    # at startup, and a stale token here makes their setup
                    # fail with an auth error until the next reload.
                    lock = get_oauth_refresh_lock(entry)
                    async with lock:
                        # Double-check after acquiring lock - token might have been refreshed
                        updated_entry = hass.config_entries.async_get_entry(
                            entry.entry_id
                        )
                        if updated_entry:
                            updated_token = updated_entry.data.get(CONF_OAUTH_TOKEN, {})
                            updated_expires = updated_token.get("expires_at", 0)
                            if updated_expires > current_time + 300:
                                _LOGGER.debug(
                                    "Token was refreshed by another entity, using new token"
                                )
                                return updated_token.get("access_token")
                            entry = updated_entry
                            oauth_token = updated_token

                        set_oauth_refresh_in_progress(entry.entry_id, True)
                        try:
                            _LOGGER.warning(
                                "OAuth token %s, attempting refresh",
                                (
                                    "expired"
                                    if current_time > expires_at
                                    else "expiring soon"
                                ),
                            )

                            # Try to get implementation from entry
                            implementation = None
                            try:
                                implementation = await config_entry_oauth2_flow.async_get_config_entry_implementation(
                                    hass, entry
                                )
                            except Exception as ex:
                                _LOGGER.debug(
                                    "Could not get implementation from entry: %s", ex
                                )

                            # If not found, try to create it directly from application credentials
                            if not implementation:
                                _LOGGER.debug(
                                    "Attempting to create OAuth implementation directly"
                                )
                                try:
                                    implementations = await config_entry_oauth2_flow.async_get_implementations(
                                        hass, DOMAIN
                                    )
                                    if implementations:
                                        # Use the first available implementation
                                        implementation = list(implementations.values())[
                                            0
                                        ]
                                        _LOGGER.debug(
                                            "Found OAuth implementation: %s",
                                            type(implementation).__name__,
                                        )

                                        # Update entry with auth_implementation for future refreshes
                                        if "auth_implementation" not in entry.data:
                                            hass.config_entries.async_update_entry(
                                                entry,
                                                data={
                                                    **entry.data,
                                                    "auth_implementation": DOMAIN,
                                                },
                                            )
                                except Exception as impl_ex:
                                    _LOGGER.debug(
                                        "Could not get implementations: %s", impl_ex
                                    )

                            if implementation:
                                new_token = await implementation.async_refresh_token(
                                    oauth_token
                                )
                                update_shared_oauth_token(
                                    hass,
                                    entry,
                                    oauth_token,
                                    new_token,
                                )
                                _LOGGER.info("OAuth token refreshed successfully")
                                set_oauth_token_invalid(entry.entry_id, False)
                                return new_token["access_token"]
                            else:
                                _LOGGER.error(
                                    "Could not get OAuth implementation - Application Credentials missing. "
                                    "Go to Settings > Devices & Services > Application Credentials "
                                    "and add credentials for Samsung TV Smart."
                                )
                        except Exception as ex:
                            if is_invalid_grant_error(ex):
                                # Terminal: refresh token is dead → reauth, stop
                                # retrying (start_oauth_reauth logs + latches).
                                start_oauth_reauth(hass, entry.entry_id)
                            else:
                                _LOGGER.error("Failed to refresh OAuth token: %s", ex)
                            # Try to use existing token anyway
                        finally:
                            set_oauth_refresh_in_progress(entry.entry_id, False)

                _LOGGER.debug("[%s] Using OAuth access token", entry.title)
                return access_token

        _LOGGER.warning(
            "[%s] OAuth method configured but no valid token found", entry.title
        )
        return entry.data.get(CONF_API_KEY)

    # Method 2: SmartThings Integration token
    if auth_method == AUTH_METHOD_ST_ENTRY:
        st_unique_id = entry.data.get(CONF_ST_ENTRY_UNIQUE_ID)
        if st_unique_id:
            api_key = get_smartthings_api_key(hass, st_unique_id)
            if api_key:
                _LOGGER.debug("[%s] Using SmartThings integration token", entry.title)
                return api_key
            _LOGGER.warning(
                "Failed to retrieve SmartThings integration access token, using last available"
            )
        return entry.data.get(CONF_API_KEY)

    # Method 3: PAT (default/legacy) - also handles old configs without auth_method
    api_key = entry.data.get(CONF_API_KEY)

    # Fallback for old configs using ST entry without CONF_AUTH_METHOD set
    if not api_key and CONF_ST_ENTRY_UNIQUE_ID in entry.data:
        st_unique_id = entry.data.get(CONF_ST_ENTRY_UNIQUE_ID)
        if st_unique_id:
            api_key = get_smartthings_api_key(hass, st_unique_id)
            if api_key:
                _LOGGER.debug("Using SmartThings integration token (legacy config)")
                return api_key
            _LOGGER.warning(
                "Failed to retrieve SmartThings integration access token, using last available"
            )
        return entry.data.get(CONF_API_KEY)

    if api_key:
        _LOGGER.debug("Using PAT token")
    return api_key


async def _register_logo_paths(hass: HomeAssistant) -> str | None:
    """Register paths for local logos."""

    static_logo_path = Path(__file__).parent / "static"
    static_paths = [
        StaticPathConfig(
            STATIC_IMAGE_BASE_URL, str(static_logo_path), cache_headers=False
        )
    ]

    local_logo_path = Path(hass.config.path("www", f"{DOMAIN}_logos"))
    url_logo_path = str(local_logo_path)
    if not local_logo_path.exists():
        try:
            local_logo_path.mkdir(parents=True)
        except Exception as exc:  # pylint: disable=broad-except
            _LOGGER.warning(
                "Error registering custom logo folder %s: %s", str(local_logo_path), exc
            )
            url_logo_path = None

    if url_logo_path is not None:
        static_paths.append(
            StaticPathConfig(CUSTOM_IMAGE_BASE_URL, url_logo_path, cache_headers=False)
        )

    await hass.http.async_register_static_paths(static_paths)
    return url_logo_path


async def _register_gallery_card(hass: HomeAssistant) -> None:
    """Register folder-gallery-card.js as a static path and Lovelace resource.

    The static path is registered immediately so the file can be served.
    The Lovelace resource registration is deferred until HA is fully started,
    because the Lovelace frontend is not ready during async_setup.
    """
    js_file = Path(__file__).parent / "www" / "folder-gallery-card.js"
    if not js_file.exists():
        _LOGGER.warning(
            "SamsungTV Smart: folder-gallery-card.js not found at %s, skipping",
            js_file,
        )
        return

    url = f"/api/{DOMAIN}/folder-gallery-card.js"

    # Register static path immediately (needed to serve the file)
    await hass.http.async_register_static_paths(
        [StaticPathConfig(url, str(js_file), cache_headers=False)]
    )
    _LOGGER.debug("SamsungTV Smart: folder-gallery-card.js registered at %s", url)

    # Defer Lovelace resource registration until HA is fully started
    async def _register_lovelace_resource(event: Event) -> None:
        try:
            lovelace_data = hass.data.get("lovelace")
            if lovelace_data is None:
                _LOGGER.warning(
                    "SamsungTV Smart: Lovelace not available, cannot register folder-gallery-card"
                )
                return
            # In recent HA versions, lovelace is a LovelaceData object (not a dict)
            resources = getattr(lovelace_data, "resources", None)
            if resources is None:
                _LOGGER.warning("SamsungTV Smart: Lovelace resources not available")
                return
            await resources.async_get_info()
            existing_urls = [r["url"] for r in resources.async_items()]
            if url not in existing_urls:
                await resources.async_create_item({"res_type": "module", "url": url})
                _LOGGER.info(
                    "SamsungTV Smart: folder-gallery-card registered as Lovelace resource"
                )
            else:
                _LOGGER.debug(
                    "SamsungTV Smart: folder-gallery-card already registered as Lovelace resource"
                )
        except Exception as err:  # pylint: disable=broad-except
            _LOGGER.warning(
                "SamsungTV Smart: could not register folder-gallery-card as Lovelace resource: %s",
                err,
            )

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _register_lovelace_resource)


async def _register_bundled_card(hass: HomeAssistant, filename: str) -> None:
    """Serve a bundled Lovelace card JS and auto-add it as a resource.

    Generic version of the gallery-card registration: registers the static
    path immediately, then adds the Lovelace resource once HA has started.
    """
    js_file = Path(__file__).parent / "www" / filename
    if not js_file.exists():
        _LOGGER.warning(
            "SamsungTV Smart: %s not found at %s, skipping", filename, js_file
        )
        return

    url = f"/api/{DOMAIN}/{filename}"
    await hass.http.async_register_static_paths(
        [StaticPathConfig(url, str(js_file), cache_headers=False)]
    )
    _LOGGER.debug("SamsungTV Smart: %s registered at %s", filename, url)

    async def _register_resource(event: Event | None = None) -> None:
        try:
            lovelace_data = hass.data.get("lovelace")
            resources = getattr(lovelace_data, "resources", None)
            if resources is None:
                return
            await resources.async_get_info()
            if url not in [r["url"] for r in resources.async_items()]:
                await resources.async_create_item({"res_type": "module", "url": url})
                _LOGGER.info(
                    "SamsungTV Smart: %s registered as Lovelace resource", filename
                )
        except Exception as err:  # pylint: disable=broad-except
            _LOGGER.warning(
                "SamsungTV Smart: could not register %s as Lovelace resource: %s",
                filename,
                err,
            )

    # Register right away if HA is already running (e.g. the integration was
    # reloaded or set up after startup); otherwise wait for the started event.
    if hass.is_running:
        await _register_resource()
    else:
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _register_resource)


async def get_device_info(hostname: str, session: ClientSession) -> dict:
    """Try retrieve device information"""
    try:
        async with async_timeout.timeout(2):
            async with session.get(
                tv_url(host=hostname), raise_for_status=True
            ) as resp:
                info = await resp.json()
    except (asyncio.TimeoutError, ClientConnectionError):
        _LOGGER.warning("Error getting HTTP device info for TV: %s", hostname)
        return {}

    device = info.get("device")
    if not device:
        _LOGGER.warning("Error getting HTTP device info for TV: %s", hostname)
        return {}

    result = {
        key: device[value] for key, value in DEVICE_INFO.items() if value in device
    }

    if ATTR_DEVICE_ID in result:
        device_id = result[ATTR_DEVICE_ID]
        if device_id.startswith("uuid:"):
            result[ATTR_DEVICE_ID] = device_id[len("uuid:") :]

    return result


class SamsungTVInfo:
    """Class to connect and collect TV information."""

    def __init__(self, hass, hostname, ws_name):
        """Initialize the object."""
        self._hass = hass
        self._hostname = hostname
        self._ws_name = ws_name
        self._ws_port = 0
        self._ws_token = None
        self._ping_port = None

    @property
    def ws_port(self):
        """Return used WebSocket port."""
        return self._ws_port

    @property
    def ws_token(self):
        """Return WebSocket token."""
        return self._ws_token

    @property
    def ping_port(self):
        """Return the port used to ping the TV."""
        return self._ping_port

    def _try_connect_ws(self):
        """Try to connect to device using web sockets on port 8001 and 8002.

        Port fallback strategy:
        - If a preferred port is known (ws_port set), try it first with existing
          token (fast path), then without token, then the alternate port.
        - Otherwise try 8001 then 8002 (8001 is the default since Tizen 9.0
          filtered port 8002 on 2024 models).
        """

        self._ping_port = SamsungTVWS.ping_probe(self._hostname)
        if self._ping_port is None:
            _LOGGER.error(
                "Connection to SamsungTV %s failed. Check that TV is on", self._hostname
            )
            return RESULT_NOT_SUCCESSFUL

        # Build port list: preferred port first (with token fast-path), then alternate
        if self._ws_port:
            alternate = 8001 if self._ws_port == 8002 else 8002
            # Each entry is (port, use_token)
            # First attempt: preferred port + existing token (quick, no popup)
            # Second attempt: preferred port without token (new pairing on same port)
            # Third attempt: alternate port without token (firmware changed port)
            if self._ws_token:
                attempts = [
                    (self._ws_port, True),
                    (self._ws_port, False),
                    (alternate, False),
                ]
            else:
                attempts = [
                    (self._ws_port, False),
                    (alternate, False),
                ]
        else:
            attempts = [(8001, False), (8002, False)]

        for port, use_token in attempts:
            timeout = DEFAULT_TIMEOUT if use_token else 45
            token = self._ws_token if use_token else None

            try:
                _LOGGER.info(
                    "Try to configure SamsungTV %s using port %s%s",
                    self._hostname,
                    str(port),
                    " with existing token" if token else "",
                )
                with SamsungTVWS(
                    name=f"{WS_PREFIX} {self._ws_name}",  # this is the name shown in the TV
                    host=self._hostname,
                    port=port,
                    token=token,
                    timeout=timeout,
                ) as remote:
                    remote.open()
                    self._ws_token = remote.token
                _LOGGER.info("Found working configuration using port %s", str(port))
                if self._ws_port != port:
                    _LOGGER.warning(
                        "SamsungTV %s: port changed from %s to %s"
                        " (likely a firmware update filtered the previous port)",
                        self._hostname,
                        self._ws_port,
                        port,
                    )
                self._ws_port = port
                return RESULT_SUCCESS
            except (OSError, ConnectionFailure, WebSocketException) as err:
                _LOGGER.info(
                    "Configuration failed using port %s, error: %s", str(port), err
                )

        _LOGGER.error("Web socket connection to SamsungTV %s failed", self._hostname)
        return RESULT_NOT_SUCCESSFUL

    @staticmethod
    async def _try_connect_st(api_key, device_id, session: ClientSession):
        """Try to connect to ST device"""

        try:
            async with async_timeout.timeout(10):
                _LOGGER.info("Try connection to SmartThings TV with id [%s]", device_id)
                st_tv = SmartThingsTV(
                    api_key=api_key,
                    device_id=device_id,
                    session=session,
                )
                result = await st_tv.async_device_health()
                if result:
                    _LOGGER.info("Connection completed successfully.")
                    return RESULT_SUCCESS
                _LOGGER.error("Connection to SmartThings TV not available.")
                return RESULT_ST_DEVICE_NOT_FOUND
        except ClientResponseError as err:
            _LOGGER.error("Failed connecting to SmartThings TV, error: %s", err)
            if err.status == 400:  # Bad request, means that token is valid
                return RESULT_ST_DEVICE_NOT_FOUND
        except Exception as err:  # pylint: disable=broad-except
            _LOGGER.error("Failed connecting with SmartThings, error: %s", err)

        return RESULT_WRONG_APIKEY

    @staticmethod
    async def get_st_devices(api_key, session: ClientSession, st_device_label=""):
        """Get list of available ST devices"""

        try:
            async with async_timeout.timeout(4):
                devices = await SmartThingsTV.get_devices_list(
                    api_key, session, st_device_label
                )
        except Exception as err:  # pylint: disable=broad-except
            _LOGGER.error("Failed connecting with SmartThings, error: %s", err)
            return None

        return devices

    async def try_connect(
        self,
        session: ClientSession,
        api_key=None,
        st_device_id=None,
        *,
        ws_port=None,
        ws_token=None,
    ):
        """Try connect device"""
        if session is None:
            return RESULT_NOT_SUCCESSFUL

        # Accept ws_port alone (even without token) so the preferred port is
        # tried first — the fallback logic in _try_connect_ws will try the
        # alternate port automatically if the preferred one is unreachable.
        if ws_port:
            self._ws_port = ws_port
        if ws_token:
            self._ws_token = ws_token

        result = await self._hass.async_add_executor_job(self._try_connect_ws)
        if result == RESULT_SUCCESS:
            if api_key and st_device_id:
                result = await self._try_connect_st(api_key, st_device_id, session)

        return result


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Samsung TV integration."""
    # Clean up legacy translations_en.json (removed in b7, not deleted by HACS)
    _legacy = hass.config.path(
        "custom_components", "samsungtv_smart", "translations_en.json"
    )
    if os.path.isfile(_legacy):
        try:
            os.remove(_legacy)
            _LOGGER.info("Removed obsolete translations_en.json")
        except OSError as ex:
            _LOGGER.debug("Could not remove translations_en.json: %s", ex)

    if not is_valid_ha_version():
        msg = (
            "This integration require at least HomeAssistant version"
            f" {__min_ha_version__}, you are running version {__version__}."
            " Please upgrade HomeAssistant to continue use this integration."
        )
        _notify_message(hass, "inv_ha_version", "SamsungTV Smart", msg)
        _LOGGER.warning(msg)
        return True

    if DOMAIN in config:
        entries_list = hass.config_entries.async_entries(DOMAIN)
        for entry_config in config[DOMAIN]:
            # get ip address
            ip_address = entry_config[CONF_HOST]

            # check if already configured
            valid_entries = [
                entry.entry_id
                for entry in entries_list
                if entry.data[CONF_HOST] == ip_address
            ]
            if not valid_entries:
                _LOGGER.warning(
                    "Found yaml configuration for not configured device %s."
                    " Please use UI to configure",
                    ip_address,
                )
                continue

            data_yaml = {
                key: value
                for key, value in entry_config.items()
                if key in SAMSMART_SCHEMA and value
            }
            if data_yaml:
                if DOMAIN not in hass.data:
                    hass.data[DOMAIN] = {}
                hass.data[DOMAIN][valid_entries[0]] = {DATA_CFG_YAML: data_yaml}

    # Register path for local logo
    if local_logo_path := await _register_logo_paths(hass):
        hass.data.setdefault(DOMAIN, {})[LOCAL_LOGO_PATH] = local_logo_path

    # Register folder-gallery-card as Lovelace resource
    await _register_gallery_card(hass)

    # On-demand resized-thumbnail endpoint used by the gallery card so big
    # folders of full-size originals don't get downloaded at full resolution.
    try:
        from .http_thumbnail import SamsungTVThumbnailView

        hass.http.register_view(SamsungTVThumbnailView(hass))
    except Exception as exc:  # pylint: disable=broad-except
        _LOGGER.warning("Could not register thumbnail view: %s", exc)

    # One-click upload endpoint used by the art-upload card (pick a file on any
    # device → straight to the Frame, no folder sensor needed).
    try:
        from .http_upload import SamsungArtUploadView

        hass.http.register_view(SamsungArtUploadView(hass))
    except Exception as exc:  # pylint: disable=broad-except
        _LOGGER.warning("Could not register art upload view: %s", exc)

    # Register the art-upload Lovelace card (served + auto-added as a resource).
    await _register_bundled_card(hass, "samsung-art-upload-card.js")

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up the Samsung TV platform."""
    if not is_valid_ha_version():
        return False

    # migrate unique id to a accepted format
    _migrate_entry_unique_id(hass, entry)

    # migrate smartthings entry usage configuration
    _migrate_smartthings_config(hass, entry)

    # migrate old token file to registry entry if required
    if CONF_TOKEN not in entry.data:
        await hass.async_add_executor_job(
            _migrate_token, hass, entry, entry.data[CONF_HOST]
        )

    # migrate options to new format if required
    _migrate_options_format(hass, entry)

    # Dismiss any stale "local connection not authorized" / "IP Control
    # token problem" notifications left over from a previous session. Both
    # are only re-raised on a fresh auth rejection, so clearing them here
    # is safe even on a clean reload — they will be re-created if the
    # problem genuinely still exists, instead of staying stuck forever when
    # the TV was simply offline and never produced the one clean reconnect
    # that would otherwise dismiss them.
    clear_token_problem(hass, entry.entry_id, METHOD_LOCAL)
    clear_token_problem(hass, entry.entry_id, METHOD_IP_CONTROL)

    # setup entry
    if DOMAIN not in hass.data:
        hass.data[DOMAIN] = {}

    add_conf = None
    config = entry.data.copy()
    if entry.entry_id in hass.data[DOMAIN]:
        add_conf = hass.data[DOMAIN][entry.entry_id].get(DATA_CFG_YAML, {})
        for attr, value in add_conf.items():
            if value:
                config[attr] = value

    # setup entry
    hass.data[DOMAIN][entry.entry_id] = {
        DATA_CFG: config,
        DATA_OPTIONS: entry.options.copy(),
        # Snapshot used by the update listener to tell an options-only change
        # (applied live) from one that needs a reload (connection/auth data, or
        # a structural option that adds/removes platforms). Cheap to compare.
        DATA_ENTRY_DATA: _reload_fingerprint(entry),
    }
    if add_conf:
        hass.data[DOMAIN][entry.entry_id][DATA_CFG_YAML] = add_conf
    entry.async_on_unload(entry.add_update_listener(_update_listener))

    # Create the single shared Frame Art API instance BEFORE forwarding
    # platforms. Platform setups run concurrently and used to race on
    # hass.data[...][DATA_ART_API], each creating its own instance on a miss
    # (sensor.py even unconditionally). Multiple clients on the TV's
    # com.samsung.art-app channel make it route d2d_service_message responses
    # unpredictably and eventually stop handshaking new connections, so art
    # mode detection silently dies. One instance here, everyone reuses it.
    hass.data[DOMAIN][entry.entry_id][DATA_ART_API] = SamsungTVAsyncArt(
        host=config[CONF_HOST],
        port=config.get(CONF_PORT, DEFAULT_PORT),
        token=config.get(CONF_TOKEN),
        session=async_get_clientsession(hass),
        timeout=DEFAULT_TIMEOUT,
        name=f"{WS_PREFIX} {config.get(CONF_WS_NAME, 'HomeAssistant')} Art",
        supports_get_brightness=entry.data.get(CONF_SUPPORTS_GET_BRIGHTNESS),
        supports_get_color_temperature=entry.data.get(
            CONF_SUPPORTS_GET_COLOR_TEMPERATURE
        ),
    )

    await hass.config_entries.async_forward_entry_setups(entry, SAMSMART_PLATFORM)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(
        entry, SAMSMART_PLATFORM
    ):
        if art_api := hass.data[DOMAIN][entry.entry_id].pop(DATA_ART_API, None):
            await art_api.close()
        hass.data[DOMAIN][entry.entry_id].pop(DATA_CFG)
        hass.data[DOMAIN][entry.entry_id].pop(DATA_OPTIONS)
        if not hass.data[DOMAIN][entry.entry_id]:
            hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove a config entry."""
    await hass.async_add_executor_job(_remove_token_file, hass, entry.data[CONF_HOST])
    if DOMAIN in hass.data:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        if not hass.data[DOMAIN]:
            hass.data.pop(DOMAIN)
    # The entry is gone for good (unlike unload/reload), so any Repairs issue
    # keyed to its entry_id (e.g. oauth_auth_failed_<entry_id>) can never be
    # cleared by a future successful refresh on this entry — nothing will ever
    # call _clear_oauth_issue() for it again. Without this, dismissing the
    # issue in the UI only hides it; the registry entry itself outlives the
    # entry it refers to and clutters diagnostics indefinitely.
    ir.async_delete_issue(hass, DOMAIN, f"oauth_auth_failed_{entry.entry_id}")


# Options whose change adds/removes platforms or clients and therefore needs a
# full reload (everything else is applied live via SIGNAL_CONFIG_ENTITY).
# CONF_ST_POLL_ON_INTERVAL and CONF_IP_CONTROL_POLL_INTERVAL are included
# because their coordinators fix update_interval at creation time; a reload
# re-creates them with the new cadence (the media_player / selects read the
# SmartThings one live).
_RELOAD_OPTIONS = (
    CONF_ENABLE_IP_CONTROL,
    CONF_IP_CONTROL_ART_MODE,
    CONF_IP_CONTROL_POLL_INTERVAL,
    CONF_ST_POLL_ON_INTERVAL,
)

# entry.data keys persisted at RUNTIME as learned device facts, not connection
# settings: writing them must NOT reload the integration (a reload would tear
# down the very clients that just learned the fact — and reloads are exactly
# what the verify-and-fallback logic runs right after a user action).
_NO_RELOAD_DATA_KEYS = (
    CONF_ST_PICTURE_MODE_CAPABILITY,
    # Art-identification config read live by the service/sensor: persisting it
    # shouldn't tear the integration down. NOTE: CONF_ART_IDENTIFY_ENABLE is
    # deliberately NOT here — toggling it creates/removes the Art Metadata
    # sensor, which requires a reload.
    CONF_ART_IDENTIFY_PERSONAL,
    CONF_ART_VISION_API_KEY,
    CONF_ART_LLM_PROVIDER,
    CONF_ART_LLM_API_KEY,
    CONF_ART_LLM_MODEL,
    # Connection facts LEARNED and refreshed at runtime by the live clients,
    # which have already adapted by the time these are persisted — the write
    # only exists to survive a restart, so it must not reload (#12). The WS and
    # OAuth tokens rotate, and on ~2020 Frames the Art/REST ports re-learn on
    # reconnect; before this list, each such write reloaded the whole entry,
    # which on an unstable connection looped every few minutes (entity flapping
    # unavailable -> unknown -> restored). A reconfigure still reloads: it bumps
    # CONF_RECONFIGURE_GENERATION, which is NOT excluded here.
    CONF_TOKEN,
    CONF_OAUTH_TOKEN,
    CONF_PORT,
    CONF_REST_PORT,
    CONF_SUPPORTS_GET_BRIGHTNESS,
    CONF_SUPPORTS_GET_COLOR_TEMPERATURE,
    CONF_IS_FRAME_TV,
    CONF_SLIDESHOW_API,
    CONF_IP_CONTROL_TOKEN,
    CONF_IP_CONTROL_MODEL_ID,
    CONF_IP_CONTROL_FW_VERSION,
)


def _reload_fingerprint(entry: ConfigEntry) -> tuple:
    """Snapshot the parts of an entry whose change requires a reload."""
    return (
        {k: v for k, v in entry.data.items() if k not in _NO_RELOAD_DATA_KEYS},
        tuple(entry.options.get(key) for key in _RELOAD_OPTIONS),
    )


async def _update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """React to a config entry change.

    Most options (scan interval, app/source lists, ...) are applied live via the
    dispatcher signal — no costly full reload. Connection/auth *data* changes
    (host, port, token, API key) and structural options (IP Control enable /
    Art Mode) need the platforms or clients re-created, so those, and only
    those, schedule a reload. The reload is always scheduled from this listener
    (never from the config/options flow) per Home Assistant's deprecation of
    combining an update listener with in-flow reload methods.
    """
    store = hass.data[DOMAIN][entry.entry_id]
    store[DATA_OPTIONS] = entry.options.copy()
    async_dispatcher_send(hass, SIGNAL_CONFIG_ENTITY)

    fingerprint = _reload_fingerprint(entry)
    if store.get(DATA_ENTRY_DATA) != fingerprint:
        store[DATA_ENTRY_DATA] = fingerprint
        hass.config_entries.async_schedule_reload(entry.entry_id)
