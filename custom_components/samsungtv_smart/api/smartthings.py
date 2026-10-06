"""SmartThings TV integration using pysmartthings library (v6.0)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import timedelta
from enum import Enum
import logging
from typing import NoReturn

from aiohttp import ClientResponse, ClientResponseError, ClientSession
from pysmartthings import SmartThings

from homeassistant.util import Throttle

# Capability names as strings (pysmartthings v6.0+ compatibility)
CAP_SWITCH = "switch"
CAP_AUDIO_VOLUME = "audioVolume"
CAP_AUDIO_MUTE = "audioMute"
CAP_TV_CHANNEL = "tvChannel"
CAP_MEDIA_INPUT_SOURCE = "mediaInputSource"
CAP_LIGHT_CONTROL = "samsungvd.lightControl"

HUE_SYNC_MODE_OFF = "TurnOff"
HUE_SYNC_MODE_ON = "TurnOn"


class SmartThingsCapabilityUnsupported(Exception):
    """The TV does not expose the capability a command needs.

    SmartThings answers 422 Unprocessable Entity for a command sent against a
    capability absent from the device profile — the same code seen when
    samsungvd.pictureMode is addressed on a model that only has
    custom.picturemode. It is not a transient failure, so retrying is
    pointless; but "absent" does not always mean "unsupported by the model",
    since some capabilities only appear once the matching feature is set up on
    the TV. Callers should say both rather than declare the model incapable.
    """


_LOGGER = logging.getLogger(__name__)


def _has_content(value) -> bool:
    """Whether a capability attribute value says anything.

    Empty strings and lists of them are placeholders (see
    async_hue_sync_session_active), not values.
    """
    if isinstance(value, (list, tuple)):
        return any(_has_content(item) for item in value)
    if isinstance(value, str):
        return bool(value.strip())
    return bool(value)


class _STLoggerAdapter(logging.LoggerAdapter):
    """Prefix every log line with the TV's host so multi-TV logs can be told apart.

    Mirrors api.art._DeviceLoggerAdapter. SmartThings instances are keyed by
    device_id, not host, so fall back to the (short) device_id when no host
    was supplied (e.g. the transient config-flow validation instance).
    """

    def process(self, msg, kwargs):
        ident = self.extra.get("host") or self.extra.get("device_id")
        return f"[{ident}] {msg}", kwargs


# SmartThings REST API
API_BASEURL = "https://api.smartthings.com/v1"
API_DEVICES = f"{API_BASEURL}/devices"

# Seconds to wait after a setPictureMode + refresh before reading the mode
# back to check the TV actually applied it. Long enough for the refresh to
# propagate a fresh panel read to the cloud in the common case, short enough
# that a wrongly-unconfirmed retry (a harmless duplicate send of the same
# mode via the other capability) doesn't feel laggy.
PICTURE_MODE_VERIFY_DELAY = 5

# Device types
DEVICE_TYPE_OCF = "OCF"
DEVICE_TYPE_NAME_TV = "Samsung OCF TV"
DEVICE_TYPE_NAMES = ["Samsung OCF TV", "x.com.st.d.monitor"]

# Component name
COMPONENT_MAIN = "main"


class STStatus(Enum):
    """SmartThings status values."""

    STATE_ON = "on"
    STATE_OFF = "off"
    STATE_UNKNOWN = "unknown"


class SmartThingsTV:
    """Class to read status for TV registered in SmartThings cloud using pysmartthings."""

    def __init__(
        self,
        api_key: str,
        device_id: str,
        use_channel_info: bool = True,
        session: ClientSession | None = None,
        api_key_callback: Callable[[], str | None] | None = None,
        host: str | None = None,
    ):
        """Initialize SmartThingsTV with pysmartthings."""
        self._api_key = api_key
        self._device_id = device_id
        self._use_channel_info = use_channel_info
        self._api_key_callback = api_key_callback
        # Per-TV log prefix, consistent with api.art / media_player. Falls back
        # to the device_id when no host is supplied (config-flow validation).
        self._log = _STLoggerAdapter(_LOGGER, {"host": host, "device_id": device_id})

        # Store session for direct REST API calls (source selection)
        self._session = session

        # Initialize pysmartthings for status reading
        self._st = SmartThings(session=session)
        self._st.authenticate(api_key)

        # State tracking
        self._device_name = None
        self._state = STStatus.STATE_UNKNOWN
        self._prev_state = STStatus.STATE_UNKNOWN
        self._muted = False
        self._volume = 10
        self._source_list = None
        self._source_list_map = None
        # Last source-list snapshot that was logged. _update_source_list runs on
        # every SmartThings poll and its diagnostics almost never change, so they
        # are emitted only when this signature moves (measured: 3 DEBUG lines
        # ~2x/min, 1,974 lines in 5.5 h on one TV, burying real events). Same
        # treatment the device-info payload already gets.
        self._source_list_log_state: tuple | None = None
        self._source = ""
        self._channel = ""
        self._channel_name = ""
        self._sound_mode = None
        self._sound_mode_list = None
        self._picture_mode = None
        self._picture_mode_list = None
        # Bidirectional mapping between display names and API ids for picture modes
        # e.g. {"Standard": "modeStandard", "Éco": "modeEco", ...}
        self._picture_mode_map: dict[str, str] = {}
        # Track which SmartThings capability the TV uses for picture/sound mode
        # (varies by model: some use custom.picturemode, others samsungvd.pictureMode)
        self._picture_mode_capability = None
        self._sound_mode_capability = None
        # Capability VERIFIED to actually actuate setPictureMode on this panel
        # (learned by the verify-and-fallback in async_set_picture_mode; some
        # TVs answer 200 COMPLETED on one capability without applying it).
        # Seeded from persisted entry data at startup; tried first on every
        # change, with the other capability kept as fallback.
        self._verified_picture_mode_capability: str | None = None
        # Optional callback (set by the media_player) invoked with the verified
        # capability name so it can be persisted across HA restarts.
        self.picture_mode_capability_persist_cb: Callable[[str], None] | None = None

        self._is_forced_val = False
        self._forced_count = 0
        # True when the last poll read the current input from the device status.
        # Some TVs (e.g. 2022 Frame) report mediaInputSource.inputSource as null
        # and supportedInputSources as [], so the input is only available from
        # the samsungvd.mediaInputSource REST status and has to be re-read on
        # every poll — otherwise the reported source stays frozen (#230).
        self._source_from_status = False

    def set_verified_picture_mode_capability(self, capability: str | None) -> None:
        """Seed the verified setPictureMode capability (from persisted data).

        Accepts either the legacy bare capability ("custom.picturemode") or the
        newer "capability|form" pair (form: "name" or "id"), e.g.
        "custom.picturemode|name".
        """
        if capability is None:
            return
        cap, _, form = capability.partition("|")
        if cap in ("custom.picturemode", "samsungvd.pictureMode") and form in (
            "",
            "name",
            "id",
        ):
            self._verified_picture_mode_capability = capability
        else:
            self._log.debug(
                "Ignoring unknown persisted picture mode capability: %s", capability
            )

    def _get_api_key(self) -> str:
        """Get API key used to connect to SmartThings."""
        if self._api_key_callback is not None:
            if api_key := self._api_key_callback():
                self._api_key = api_key
                self._st.authenticate(api_key)
        return self._api_key

    # ──────────────────────────────────────────────────────────────────────────
    # Properties
    # ──────────────────────────────────────────────────────────────────────────

    @property
    def api_key(self) -> str:
        """Return current api_key."""
        return self._api_key

    @property
    def device_id(self) -> str:
        """Return current device_id."""
        return self._device_id

    @property
    def device_name(self) -> str | None:
        """Return device name."""
        return self._device_name

    @property
    def state(self) -> STStatus:
        """Return current state."""
        return self._state

    @property
    def prev_state(self) -> STStatus:
        """Return previous state."""
        return self._prev_state

    @property
    def muted(self) -> bool:
        """Return mute state."""
        return self._muted

    @property
    def volume(self) -> int:
        """Return volume level."""
        return self._volume

    @property
    def source(self) -> str:
        """Return current source."""
        return self._source

    @property
    def source_list(self) -> dict | None:
        """Return source list."""
        return self._source_list

    @property
    def channel(self) -> str:
        """Return current channel."""
        return self._channel

    @property
    def channel_name(self) -> str:
        """Return current channel name."""
        return self._channel_name

    @property
    def sound_mode(self) -> str | None:
        """Return current sound mode."""
        return self._sound_mode

    @property
    def sound_mode_list(self) -> list | None:
        """Return sound mode list."""
        return self._sound_mode_list

    @property
    def picture_mode(self) -> str | None:
        """Return current picture mode."""
        return self._picture_mode

    @property
    def picture_mode_list(self) -> list | None:
        """Return picture mode list."""
        return self._picture_mode_list

    @property
    def picture_mode_map(self) -> dict[str, str]:
        """Return the display name -> internal id map for picture modes.

        The ids (``modeStandard``, ``modeMovie``, ...) are the only
        language-independent handle on a picture mode; the display names are
        localized by the TV.
        """
        return dict(self._picture_mode_map)

    def get_source_name(self, source_key: str) -> str:
        """Get source name from key."""
        if not self._source_list_map or source_key not in self._source_list_map:
            return source_key
        return self._source_list_map[source_key]

    # ──────────────────────────────────────────────────────────────────────────
    # Helper methods
    # ──────────────────────────────────────────────────────────────────────────

    def _set_source(self, source: str):
        """Set source value."""
        if self._state != STStatus.STATE_OFF:
            if source != self._source:
                self._source = source
                self._channel = ""
                self._channel_name = ""
                self._is_forced_val = True
                self._forced_count = 0

    def set_application(self, app_id: str):
        """Set running application info."""
        if self._use_channel_info:
            self._channel = ""
            self._channel_name = app_id
            self._is_forced_val = True
            self._forced_count = 0

    def _get_source_list_from_map(self) -> list:
        """Return source list from source map."""
        if not self._source_list_map:
            return []
        return list(self._source_list_map.keys())

    async def _send_rest_command(
        self,
        capability: str,
        command: str,
        arguments: list | None = None,
    ) -> None:
        """Send a command via direct REST API.

        Used instead of pysmartthings Command class, which is an Enum in
        v6.x and cannot be instantiated with keyword arguments.
        """
        if not self._device_id or not self._session:
            self._log.error("Cannot send REST command: device_id or session missing")
            return

        api_key = self._get_api_key()
        url = f"{API_DEVICES}/{self._device_id}/commands"
        cmd: dict = {
            "component": COMPONENT_MAIN,
            "capability": capability,
            "command": command,
        }
        if arguments:
            cmd["arguments"] = arguments

        async with self._session.post(
            url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            json={"commands": [cmd]},
        ) as resp:
            if resp.status >= 400:
                await self._raise_command_refused(resp, capability, command)
            result = await resp.json()
            self._log.debug(
                "REST command %s/%s sent, status: %s, response: %s",
                capability,
                command,
                resp.status,
                result,
            )
            # HTTP 200 only means SmartThings accepted the request; the body
            # says whether the device executed it. A FAILED result here means
            # the TV is registered in the cloud but not reachable by it --
            # typically because the TV itself cannot reach Samsung's servers
            # (blocked DNS, firewall). That looked like success in the log
            # while nothing happened on the panel (#197), so say it plainly.
            if any(
                (item or {}).get("status") == "FAILED"
                for item in (result or {}).get("results", [])
            ):
                self._log.warning(
                    "SmartThings accepted %s/%s but the TV did not execute it "
                    "(result: FAILED) — the TV is registered in the cloud but "
                    "the cloud cannot reach it. Check the TV's own internet "
                    "access (DNS/ad-blocking rules on samsung* domains).",
                    capability,
                    command,
                )

    async def _raise_command_refused(
        self, resp: ClientResponse, capability: str, command: str
    ) -> NoReturn:
        """Raise a refused command's error, keeping what SmartThings said.

        SmartThings explains a refusal in the body ({"requestId": ..., "error":
        {"code", "message", "details"}}), but aiohttp's raise_for_status keeps
        only the status line, so a 409 on Hue Sync reached the log as
        "409, message='Conflict'" and nothing more (#298). The body's code and
        message go into the ClientResponseError message, so every caller that
        logs the error now logs them, and err.status is unchanged for the
        callers that branch on it.
        """
        try:
            body = await resp.json(content_type=None)
        except Exception:  # noqa: BLE001 - an unreadable body still raises below
            body = None
        error = body.get("error") if isinstance(body, dict) else None
        request_id = body.get("requestId") if isinstance(body, dict) else None
        reason = []
        if isinstance(error, dict):
            reason = [str(p) for p in (error.get("code"), error.get("message")) if p]
        message = resp.reason or ""
        if reason:
            message = f"{message} ({': '.join(reason)})"
        if request_id:
            message = f"{message} [requestId {request_id}]"
        self._log.debug(
            "REST command %s/%s refused, status: %s, body: %s",
            capability,
            command,
            resp.status,
            body,
        )
        raise ClientResponseError(
            resp.request_info,
            resp.history,
            status=resp.status,
            message=message.strip(),
            headers=resp.headers,
        )

    async def _update_source_list(self, main_comp: dict) -> None:
        """Update source list from device status, with custom name support.

        Reads supportedInputSources for the basic list, then checks
        supportedInputSourcesMap for custom device names (e.g. "PlayStation"
        for HDMI1). Falls back to REST API if pysmartthings doesn't expose
        the capability or the map attribute.
        """
        has_media_input = "mediaInputSource" in main_comp
        has_supported = False
        supported_val = None
        if has_media_input:
            media_input = main_comp["mediaInputSource"]

            has_supported = "supportedInputSources" in media_input
            supported_val = (
                media_input["supportedInputSources"].value if has_supported else None
            )

            if has_supported and supported_val:
                supported_inputs = supported_val
                if supported_inputs:
                    self._source_list = {}
                    self._source_list_map = {}
                    for source in supported_inputs:
                        if isinstance(source, str):
                            source_id = source
                            source_name = source
                        elif isinstance(source, dict):
                            source_id = source.get("id", "")
                            source_name = source.get("name", source_id)
                        else:
                            continue
                        if source_id:
                            self._source_list[source_id] = source_name
                            self._source_list_map[source_id] = source_name

                    # Try to get custom names from supportedInputSourcesMap
                    _mk = "supportedInputSourcesMap"
                    if _mk in media_input:
                        sources_map_raw = media_input[_mk].value
                        if sources_map_raw:
                            self._apply_source_name_map(sources_map_raw)

        # Fallback: fetch via REST when pysmartthings gave us no source list,
        # or when it gave us no current input this poll. The second case is not
        # a one-off: on TVs whose mediaInputSource attributes are always null,
        # this REST read is the ONLY place the current input comes from, so it
        # has to run on every poll or the source never changes again (#230).
        if self._state == STStatus.STATE_ON and (
            not self._source_list or not self._source_from_status
        ):
            self._log.debug(
                "Samsung TV: reading input source via REST (list empty: %s, "
                "input in status: %s)",
                not self._source_list,
                self._source_from_status,
            )
            await self._fetch_input_source_map()

        self._log_source_list_state(has_media_input, has_supported, supported_val)

    def _log_source_list_state(
        self, has_media_input: bool, has_supported: bool, supported_val
    ) -> None:
        """Emit the source-list diagnostics only when the snapshot changes.

        These three lines used to be logged on every SmartThings poll while
        describing a list that essentially never moves.
        """
        signature = (
            has_media_input,
            has_supported,
            repr(supported_val),
            tuple(sorted((self._source_list_map or {}).items())),
        )
        if signature == self._source_list_log_state:
            return
        self._source_list_log_state = signature

        self._log.debug(
            "Samsung TV: source list updated (mediaInputSource in comp: %s, "
            "supportedInputSources present=%s, value=%s)",
            has_media_input,
            has_supported,
            supported_val,
        )
        if self._source_list:
            self._log.debug(
                "Samsung TV: sources loaded: %s",
                dict(self._source_list_map or {}),
            )
        else:
            self._log.debug("Samsung TV: no sources available after update")

    def _apply_source_name_map(self, sources_map_raw: list) -> None:
        """Apply custom names from supportedInputSourcesMap."""
        for entry in sources_map_raw:
            if isinstance(entry, dict):
                s_id = entry.get("id", "")
                s_name = entry.get("name", s_id)
            elif isinstance(entry, str):
                s_id = s_name = entry
            else:
                continue
            if s_id and s_name and s_id in self._source_list_map:
                self._source_list_map[s_id] = s_name
                self._source_list[s_id] = s_name

    async def _fetch_input_source_map(self) -> None:
        """Fetch input sources and custom names via direct REST GET.

        Builds both the source list and the name map from REST API.
        Tries both standard and Samsung-specific capabilities.
        """
        if not self._device_id or not self._session:
            self._log.debug("Cannot fetch input sources: missing device_id or session")
            return
        api_key = self._get_api_key()

        # Try multiple capabilities — Samsung TVs may use different ones
        capabilities = [
            "samsungvd.mediaInputSource",
            "mediaInputSource",
        ]

        for cap_name in capabilities:
            url = (
                f"{API_DEVICES}/{self._device_id}"
                f"/components/main/capabilities/{cap_name}/status"
            )
            self._log.debug("Samsung TV: fetching input sources via REST: %s", cap_name)
            try:
                async with self._session.get(
                    url,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Accept": "application/json",
                    },
                ) as resp:
                    if resp.status != 200:
                        self._log.debug(
                            "Samsung TV: %s returned status %s", cap_name, resp.status
                        )
                        continue
                    data = await resp.json()
                    self._log.debug("Samsung TV: %s REST response: %s", cap_name, data)

                    # Try supportedInputSourcesMap first (has custom names)
                    raw_map = data.get("supportedInputSourcesMap", {}).get("value")
                    if raw_map:
                        self._source_list = {}
                        self._source_list_map = {}
                        for entry in raw_map:
                            if isinstance(entry, dict):
                                s_id = entry.get("id", "")
                                s_name = entry.get("name", s_id)
                            elif isinstance(entry, str):
                                s_id = s_name = entry
                            else:
                                continue
                            if s_id:
                                self._source_list[s_id] = s_name
                                self._source_list_map[s_id] = s_name
                        self._log.debug(
                            "Samsung TV: sources from %s map: %s",
                            cap_name,
                            self._source_list_map,
                        )
                        # Also read current inputSource from same response
                        input_val = data.get("inputSource", {}).get("value")
                        if input_val:
                            self._source = input_val
                        return

                    # Fallback to supportedInputSources (plain list)
                    raw_sources = data.get("supportedInputSources", {}).get("value")
                    if raw_sources:
                        self._source_list = {}
                        self._source_list_map = {}
                        for source in raw_sources:
                            if isinstance(source, str):
                                self._source_list[source] = source
                                self._source_list_map[source] = source
                            elif isinstance(source, dict):
                                s_id = source.get("id", "")
                                s_name = source.get("name", s_id)
                                if s_id:
                                    self._source_list[s_id] = s_name
                                    self._source_list_map[s_id] = s_name
                        if self._source_list:
                            self._log.debug(
                                "Samsung TV: sources from %s list: %s",
                                cap_name,
                                self._source_list_map,
                            )
                            return

            except Exception as err:
                self._log.debug("Error fetching %s: %s", cap_name, err)

    async def _update_picture_mode(self, main_comp: dict) -> None:
        """Update picture mode from device status or REST fallback.

        Supports both capability names:
        - samsungvd.pictureMode (newer models)
        - custom.picturemode (older models)

        Samsung SmartThings uses two attributes:
        - supportedPictureModesMap: [{"id":"modeStandard","name":"Standard"}, ...]
        - supportedPictureModes: ["Standard", "Eco"] (localized names only)
        - pictureMode: current mode as an id (e.g. "modeStandard")

        We use the Map to build a name<->id mapping so that:
        - _picture_mode_list shows localized names to the user
        - _picture_mode stores the localized name (not the raw id)
        - async_set_picture_mode resolves name -> id before sending
        """
        for _pic_cap in ("samsungvd.pictureMode", "custom.picturemode"):
            if _pic_cap not in main_comp:
                continue

            self._picture_mode_capability = _pic_cap
            picture_mode_cap = main_comp[_pic_cap]

            # Build name<->id map from supportedPictureModesMap if available
            _mk = "supportedPictureModesMap"
            if _mk in picture_mode_cap:
                modes_map_raw = picture_mode_cap[_mk].value
                if modes_map_raw:
                    new_map: dict[str, str] = {}
                    for entry in modes_map_raw:
                        if isinstance(entry, dict):
                            m_id = entry.get("id", "")
                            m_name = entry.get("name", m_id)
                        elif isinstance(entry, str):
                            m_id = m_name = entry
                        else:
                            continue
                        # Samsung's localized names arrive padded in some
                        # locales (" Prirodzený", " Dynamický" -- see #197).
                        # A leading space is not part of the name: it would
                        # show in the UI and be sent back as the argument.
                        m_name = m_name.strip() or m_id
                        if m_id:
                            new_map[m_name] = m_id
                    if new_map:
                        self._picture_mode_map = new_map
                        self._picture_mode_list = list(new_map.keys())

            # Fallback: use supportedPictureModes (names only, no id mapping)
            if not self._picture_mode_list:
                _sk = "supportedPictureModes"
                if _sk in picture_mode_cap:
                    self._picture_mode_list = [
                        m.strip() if isinstance(m, str) else m
                        for m in (picture_mode_cap[_sk].value or [])
                    ] or None

            # If pysmartthings did not expose supportedPictureModesMap,
            # fetch it directly from the REST capability status endpoint.
            if not self._picture_mode_map:
                await self._fetch_picture_mode_map()

            # Current mode: convert raw id -> display name if map available
            _pk = "pictureMode"
            if _pk in picture_mode_cap:
                raw_mode = picture_mode_cap[_pk].value
                if self._picture_mode_map:
                    reverse = {v: k for k, v in self._picture_mode_map.items()}
                    self._picture_mode = reverse.get(raw_mode, raw_mode)
                else:
                    self._picture_mode = raw_mode
            else:
                # pysmartthings v6 may not expose pictureMode attribute;
                # fall back to REST API to get current picture mode
                await self._fetch_picture_mode_map()
            return

        # No picture mode capability found in main_comp at all —
        # try REST API directly (some models / pysmartthings versions
        # don't expose the capability through get_device_status)
        if self._picture_mode is None and self._state == STStatus.STATE_ON:
            if not self._picture_mode_capability:
                for cap_name in ("samsungvd.pictureMode", "custom.picturemode"):
                    self._picture_mode_capability = cap_name
                    await self._fetch_picture_mode_map()
                    if self._picture_mode is not None:
                        return
                self._picture_mode_capability = None
            else:
                await self._fetch_picture_mode_map()

    async def _fetch_picture_mode_map(self) -> None:
        """Fetch supportedPictureModesMap and current mode via direct REST GET.

        pysmartthings v6 does not expose all capability attributes; in
        particular supportedPictureModesMap (which maps display names to
        internal ids like modeStandard / modeEco) is missing.  We fetch the
        raw capability status directly to build the name<->id mapping and
        also read the current picture mode in the same request.
        """
        if not self._device_id or not self._session:
            return
        api_key = self._get_api_key()
        capability = self._picture_mode_capability or "samsungvd.pictureMode"
        url = (
            f"{API_DEVICES}/{self._device_id}"
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
                    self._log.debug(
                        "Could not fetch picture mode map (status %s)", resp.status
                    )
                    return
                data = await resp.json()
                # data looks like:
                # {"supportedPictureModesMap": {"value": [{"id": "modeStandard",
                #   "name": "Standard"}, ...]}, "pictureMode": {"value": "modeStandard"}}
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
                        # Samsung's localized names arrive padded in some
                        # locales (" Prirodzený", " Dynamický" -- see #197).
                        # A leading space is not part of the name: it would
                        # show in the UI and be sent back as the argument.
                        m_name = m_name.strip() or m_id
                        if m_id:
                            new_map[m_name] = m_id
                    if new_map:
                        self._picture_mode_map = new_map
                        self._picture_mode_list = list(new_map.keys())
                        self._log.debug(
                            "Picture mode map loaded via REST: %s", list(new_map.keys())
                        )

                # Also read current picture mode from the same response
                raw_mode = data.get("pictureMode", {}).get("value")
                if raw_mode:
                    if self._picture_mode_map:
                        reverse = {v: k for k, v in self._picture_mode_map.items()}
                        self._picture_mode = reverse.get(raw_mode, raw_mode)
                    else:
                        self._picture_mode = raw_mode
                    self._log.debug(
                        "Picture mode from REST: %s (raw: %s)",
                        self._picture_mode,
                        raw_mode,
                    )

        except Exception as err:
            self._log.debug("Error fetching picture mode map: %s", err)

    # ──────────────────────────────────────────────────────────────────────────
    # Device discovery
    # ──────────────────────────────────────────────────────────────────────────

    @staticmethod
    async def get_devices_list(
        api_key: str,
        session: ClientSession,
        device_label: str = "",
    ) -> dict:
        """Get list of available SmartThings devices using pysmartthings."""
        result = {}

        try:
            st = SmartThings(session=session)
            st.authenticate(api_key)
            devices = await st.get_devices()

            for dev in devices:
                if dev.type != DEVICE_TYPE_OCF:
                    continue

                if device_label and dev.label != device_label:
                    continue
                elif not device_label and dev.device_type_name not in DEVICE_TYPE_NAMES:
                    continue

                result[dev.device_id] = {
                    "name": dev.name or f"TV ID {dev.device_id}",
                    "label": dev.label or "",
                }

            _LOGGER.info("SmartThings discovered TV devices: %s", str(result))

        except Exception as err:
            _LOGGER.error("Error getting devices list: %s", err)

        return result

    # ──────────────────────────────────────────────────────────────────────────
    # Status update (uses pysmartthings for reading — untouched)
    # ──────────────────────────────────────────────────────────────────────────

    @Throttle(timedelta(seconds=1))
    async def async_device_update(self, use_channel_info: bool = True):
        """Update device status using pysmartthings."""
        self._get_api_key()

        # Periodically send a refresh command to force the TV to report
        # its actual state to SmartThings cloud. Without this, values like
        # pictureMode remain stale (e.g. always "Standard") until the
        # SmartThings app is opened. Throttled to once per 60 seconds.
        if self._state == STStatus.STATE_ON:
            await self._periodic_refresh()

        try:
            # get_device_status() returns .components (dict, not DeviceStatus object)
            components = await self._st.get_device_status(self._device_id)

            if COMPONENT_MAIN not in components:
                self._log.warning("Main component not found in device status")
                return

            main_comp = components[COMPONENT_MAIN]

            # Update device name (once)
            if not self._device_name:
                try:
                    device = await self._st.get_device(self._device_id)
                    self._device_name = device.label or device.name
                except Exception as err:
                    self._log.debug("Could not get device name: %s", err)

            # Update state
            self._prev_state = self._state
            if "switch" in main_comp and "switch" in main_comp["switch"]:
                switch_value = main_comp["switch"]["switch"].value
                if switch_value == "on":
                    self._state = STStatus.STATE_ON
                elif switch_value == "off":
                    self._state = STStatus.STATE_OFF
                else:
                    self._state = STStatus.STATE_UNKNOWN
            else:
                self._state = STStatus.STATE_UNKNOWN

            # Update volume and mute
            if "audioVolume" in main_comp and "volume" in main_comp["audioVolume"]:
                self._volume = main_comp["audioVolume"]["volume"].value

            if "audioMute" in main_comp and "mute" in main_comp["audioMute"]:
                self._muted = main_comp["audioMute"]["mute"].value == "muted"

            # Update source — standard capability first, then the Samsung one.
            self._source_from_status = False
            for _src_cap in ("mediaInputSource", "samsungvd.mediaInputSource"):
                if _src_cap in main_comp and "inputSource" in main_comp[_src_cap]:
                    input_val = main_comp[_src_cap]["inputSource"].value
                    if input_val:
                        self._source = input_val
                        self._source_from_status = True
                        break
            # If neither capability carried a value, _update_source_list falls
            # back to reading samsungvd.mediaInputSource over REST — on every
            # poll, not only the first one, or the source would stay frozen at
            # whatever input the TV was on when the list was first built (#230).

            # Update channel info if enabled
            if use_channel_info and self._state == STStatus.STATE_ON:
                if "tvChannel" in main_comp:
                    tv_channel = main_comp["tvChannel"]
                    if "tvChannel" in tv_channel:
                        self._channel = tv_channel["tvChannel"].value
                    if "tvChannelName" in tv_channel:
                        self._channel_name = tv_channel["tvChannelName"].value

            # Update source list
            # FIX: Samsung SmartThings returns supportedInputSources as a plain
            # list of strings (e.g. ["digitalTv", "HDMI1", "HDMI2", "HDMI3"]),
            # NOT a list of dicts.  Handle both formats defensively.
            # Also check supportedInputSourcesMap for custom device names
            # (e.g. [{"id": "HDMI1", "name": "PlayStation"}]).
            await self._update_source_list(main_comp)

            # Update sound mode — support both capability names
            for _snd_cap in ("samsungvd.soundMode", "custom.soundmode"):
                if _snd_cap in main_comp:
                    self._sound_mode_capability = _snd_cap
                    sound_mode_cap = main_comp[_snd_cap]
                    if "soundMode" in sound_mode_cap:
                        self._sound_mode = sound_mode_cap["soundMode"].value
                    if "supportedSoundModes" in sound_mode_cap:
                        self._sound_mode_list = sound_mode_cap[
                            "supportedSoundModes"
                        ].value
                    break

            # Update picture mode
            await self._update_picture_mode(main_comp)

        except Exception as err:
            self._log.error("Error updating SmartThings status: %s", err)
            raise

    # ──────────────────────────────────────────────────────────────────────────
    # Device health
    # ──────────────────────────────────────────────────────────────────────────

    async def async_device_health(self) -> str:
        """Get device health status using pysmartthings."""
        self._get_api_key()
        try:
            health = await self._st.get_device_health(self._device_id)
            return health.state
        except Exception as err:
            self._log.error("Error getting device health: %s", err)
            return "UNKNOWN"

    # ──────────────────────────────────────────────────────────────────────────
    # Power commands — REST API (fixes EnumType / component_id bug)
    # ──────────────────────────────────────────────────────────────────────────

    async def async_turn_on(self):
        """Turn device on via direct REST API."""
        self._get_api_key()
        try:
            await self._send_rest_command(capability=CAP_SWITCH, command="on")
            self._state = STStatus.STATE_ON
        except Exception as err:
            # 409 Conflict typically means the device is offline in the
            # SmartThings cloud (TV in deep sleep, off the network, or
            # hardware unresponsive). This is a normal, expected case —
            # not an integration bug — so we log it as WARNING instead of
            # ERROR to reduce noise in logs.
            err_str = str(err)
            if "409" in err_str or "Conflict" in err_str:
                self._log.warning(
                    "Cannot turn on device via SmartThings "
                    "(device appears offline): %s",
                    err,
                )
            else:
                self._log.error("Error turning on device: %s", err)
            raise

    async def async_turn_off(self):
        """Turn device off via direct REST API."""
        self._get_api_key()
        try:
            await self._send_rest_command(capability=CAP_SWITCH, command="off")
            self._state = STStatus.STATE_OFF
        except Exception as err:
            self._log.error("Error turning off device: %s", err)
            raise

    # ──────────────────────────────────────────────────────────────────────────
    # Other commands — REST API (fixes EnumType / component_id bug)
    # ──────────────────────────────────────────────────────────────────────────

    async def async_send_command(self, cmd_type: str, command: str = ""):
        """Send a command to the device via direct REST API."""
        self._get_api_key()

        try:
            if cmd_type == "setvolume":
                await self._send_rest_command(
                    capability=CAP_AUDIO_VOLUME,
                    command="setVolume",
                    arguments=[int(command)],
                )
            elif cmd_type == "stepvolume":
                cmd_name = "volumeUp" if command == "up" else "volumeDown"
                await self._send_rest_command(
                    capability=CAP_AUDIO_VOLUME,
                    command=cmd_name,
                )
            elif cmd_type == "audiomute":
                cmd_name = "mute" if command == "on" else "unmute"
                await self._send_rest_command(
                    capability=CAP_AUDIO_MUTE,
                    command=cmd_name,
                )
            elif cmd_type == "selectchannel":
                await self._send_rest_command(
                    capability=CAP_TV_CHANNEL,
                    command="setTvChannel",
                    arguments=[command],
                )
            elif cmd_type == "stepchannel":
                cmd_name = "channelUp" if command == "up" else "channelDown"
                await self._send_rest_command(
                    capability=CAP_TV_CHANNEL,
                    command=cmd_name,
                )
            else:
                self._log.warning("Unknown command type: %s", cmd_type)
                return

        except Exception as err:
            self._log.error("Error sending command %s: %s", cmd_type, err)
            raise

    # ──────────────────────────────────────────────────────────────────────────
    # Source selection — REST API (fixes EnumType / component_id bug)
    # ──────────────────────────────────────────────────────────────────────────

    async def async_select_source(self, source: str):
        """Select input source via direct REST API.

        The pysmartthings Command class is an Enum in v6.x and cannot be
        instantiated with keyword arguments (component_id=...).  We bypass
        it here and call the SmartThings REST API directly.
        """
        self._get_api_key()
        try:
            await self._send_rest_command(
                capability=CAP_MEDIA_INPUT_SOURCE,
                command="setInputSource",
                arguments=[source],
            )
            self._set_source(source)
        except Exception as err:
            self._log.error("Error selecting source: %s", err)
            raise

    async def async_select_vd_source(self, source: str):
        """Select Samsung VD source via direct REST API."""
        self._get_api_key()
        try:
            await self._send_rest_command(
                capability="samsungvd.mediaInputSource",
                command="setInputSource",
                arguments=[source],
            )
        except Exception as err:
            self._log.error("Error selecting VD source: %s", err)
            raise

    async def async_hue_sync_session_active(self) -> bool | None:
        """Whether a Hue Sync session is currently running, or None if unknown.

        samsungvd.lightControl only steers an ALREADY-running Hue Sync session:
        while a session exists the capability reports supportedModes /
        streamControl / selectedAppId, and while none exists it is empty and
        setLightControlMode returns COMPLETED without doing anything (#266). So
        this is the signal for whether setLightControlMode will have any effect.
        Returns True when a session is running, False when the capability is
        empty (no session), and None when it can't be read.

        "Empty" includes placeholders: a 55" Frame 2024 (QE55LS03DAUXXN, the
        pysmartthings vd_frame_2024 fixture) idles at supportedModes [""],
        selectedAppId "" and streamControl false. A plain truthiness test read
        that [""] as a running session, so start_hue_sync never launched the app
        and stop_hue_sync sent its command instead of reporting nothing to stop
        (#298).
        """
        if not self._device_id or not self._session:
            return None
        api_key = self._get_api_key()
        url = (
            f"{API_DEVICES}/{self._device_id}"
            f"/components/main/capabilities/{CAP_LIGHT_CONTROL}/status"
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
                    self._log.debug(
                        "Could not read %s status (HTTP %s)",
                        CAP_LIGHT_CONTROL,
                        resp.status,
                    )
                    return None
                data = await resp.json()
        except Exception as err:  # noqa: BLE001 - best-effort probe
            self._log.debug("Could not read %s status: %s", CAP_LIGHT_CONTROL, err)
            return None

        active = any(
            _has_content((data.get(attr) or {}).get("value"))
            for attr in ("supportedModes", "streamControl", "selectedAppId")
        )
        self._log.debug(
            "%s status: %s -> Hue Sync session %s",
            CAP_LIGHT_CONTROL,
            data,
            "running" if active else "not running",
        )
        return active

    async def async_set_hue_sync(self, enabled: bool) -> None:
        """Turn a running Hue Sync session on or off (samsungvd.lightControl).

        This does not launch the TV app; the media player does that first when
        no session is running (#266).
        """
        mode = HUE_SYNC_MODE_ON if enabled else HUE_SYNC_MODE_OFF
        try:
            await self._send_rest_command(
                capability=CAP_LIGHT_CONTROL,
                command="setLightControlMode",
                arguments=[mode],
            )
        except ClientResponseError as err:
            if err.status == 422:
                # The capability is not in the device profile as SmartThings
                # sees it. Reported on a 2022 Frame (QE65LS03BAUXXH); the
                # feature was developed against an S95C. Whether it is missing
                # for good or only until the Hue Sync TV app is set up is not
                # something the error distinguishes, so the message says both.
                self._log.warning(
                    "SmartThings rejected %s with 422: this TV does not expose "
                    "that capability right now. It may be absent on the model, "
                    "or only appear once the Hue Sync TV app is installed and "
                    "paired with a bridge",
                    CAP_LIGHT_CONTROL,
                )
                raise SmartThingsCapabilityUnsupported(CAP_LIGHT_CONTROL) from err
            self._log.error("Error setting Hue Sync mode: %s", err)
            raise
        except Exception as err:
            self._log.error("Error setting Hue Sync mode: %s", err)
            raise

    # ──────────────────────────────────────────────────────────────────────────
    # Sound / picture mode (pysmartthings — unchanged)
    # ──────────────────────────────────────────────────────────────────────────

    async def async_set_sound_mode(self, mode: str):
        """Select sound mode using direct REST API."""
        if self._state != STStatus.STATE_ON:
            self._log.debug(
                "Cannot set sound mode: TV state is %s (not ON)", self._state
            )
            return
        if self._sound_mode_list and mode not in self._sound_mode_list:
            self._log.warning(
                "Sound mode '%s' not in known list %s — sending anyway",
                mode,
                self._sound_mode_list,
            )

        capability = self._sound_mode_capability or "custom.soundmode"
        try:
            await self._send_rest_command(
                capability=capability,
                command="setSoundMode",
                arguments=[mode],
            )
            self._sound_mode = mode
        except Exception as err:
            self._log.error("Error setting sound mode: %s", err)
            raise

    async def async_set_picture_mode(self, mode: str) -> bool | None:
        """Select picture mode using direct REST API.

        Returns True when the panel was READ BACK as having applied the mode,
        None when a send was accepted but could not be verified, and False when
        nothing was applied (no attempt, every attempt rejected, or accepted
        and demonstrably not applied). The caller uses this to decide whether
        the WebSocket key is still needed — see async_select_picture_mode.

        Tries both capability variants: some Samsung TVs accept commands
        on custom.picturemode but not samsungvd.pictureMode, or vice versa.
        We try the detected capability first, then fall back to the other.

        A successful HTTP send is NOT enough to stop: some TVs answer
        ``200 COMPLETED`` and then silently drop the command — observed with
        ``custom.picturemode`` under self-published OAuth app clients (the
        official app / a PAT actuate the same panel fine, issue #116). So
        after each accepted send the mode is read back, and if the TV did
        not apply it the other capability is tried too. A redundant send of
        the same target mode is harmless.
        """
        if self._state != STStatus.STATE_ON:
            self._log.debug(
                "Cannot set picture mode: TV state is %s (not ON)", self._state
            )
            return False

        # Resolve display name -> internal API id using the map.
        mode_id = self._picture_mode_map.get(mode, mode)

        if self._picture_mode_list and mode not in self._picture_mode_list:
            self._log.warning(
                "Picture mode '%s' not in known list %s — sending anyway",
                mode,
                self._picture_mode_list,
            )

        # Attempt matrix: (capability × argument form). Field testing on #116
        # proved the argument FORM matters as much as the capability: on an
        # S90C, custom.picturemode answers 200 COMPLETED for the internal id
        # ("modeMovie") while doing NOTHING, but actuates the panel when sent
        # the display NAME ("Movie") — and samsungvd.pictureMode 422s outright.
        # (This is also why the legacy PAT/REST era "worked": it sent names;
        # the name->id map landed together with OAuth support.) So each
        # capability is tried with the NAME first, then the id, each send
        # verified; the verified (capability, form) pair is memorized.
        primary = self._picture_mode_capability or "samsungvd.pictureMode"
        fallback = (
            "custom.picturemode"
            if primary == "samsungvd.pictureMode"
            else "samsungvd.pictureMode"
        )
        forms = {"name": mode, "id": mode_id}
        attempts: list[tuple[str, str]] = []  # (capability, form)

        def _add(capability: str | None, form: str) -> None:
            if not capability:
                return
            if forms["name"] == forms["id"] and form == "id":
                return  # no map: both forms identical, one attempt is enough
            if (capability, form) not in attempts:
                attempts.append((capability, form))

        verified = self._verified_picture_mode_capability
        if verified:
            v_cap, _, v_form = verified.partition("|")
            if v_form in forms:
                _add(v_cap, v_form)
            else:  # legacy memory: bare capability, form unknown
                _add(v_cap, "name")
                _add(v_cap, "id")
        for capability in (primary, fallback):
            _add(capability, "name")
            _add(capability, "id")

        any_sent = False
        failures: list[str] = []
        # Capabilities this device has already refused as unsupported. The
        # matrix tries each capability twice (name form, id form); a 422 is a
        # verdict on the capability itself, so the second form is a wasted
        # request -- and wasted requests are what push SmartThings into rate
        # limiting (#197: a UE50RU7172 answered 422 for samsungvd.pictureMode
        # and 409 for custom.picturemode, then 429 for everything once the
        # four-request matrix had run a few times).
        unsupported: set[str] = set()
        for capability, form in attempts:
            if capability in unsupported:
                continue
            argument = forms[form]
            try:
                await self._send_rest_command(
                    capability=capability,
                    command="setPictureMode",
                    arguments=[argument],
                )
            except Exception as err:
                self._log.debug(
                    "setPictureMode via %s (%s=%s) failed: %s, trying next",
                    capability,
                    form,
                    argument,
                    err,
                )
                # Kept so the final error can say WHY every attempt failed.
                # Reporting only "failed via any capability/form" (#197) left
                # nothing to act on without turning on debug logging first.
                failures.append(f"{capability} ({form}={argument!r}): {err}")
                status = (
                    getattr(err, "status", None)
                    if isinstance(err, ClientResponseError)
                    else None
                )
                if status == 429:
                    # Rate limited. Every remaining attempt would fail the same
                    # way and dig the hole deeper, so stop here.
                    self._log.warning(
                        "SmartThings is rate limiting this device (429) — "
                        "abandoning the remaining picture mode attempts"
                    )
                    break
                if status == 422:
                    unsupported.add(capability)
                continue
            self._log.debug(
                "Picture mode '%s' sent via %s (%s form: %s)",
                mode,
                capability,
                form,
                argument,
            )
            any_sent = True
            self._picture_mode = mode

            # Send a refresh command to force the TV to update its
            # cached state in SmartThings cloud. Without this, the API
            # returns stale values (e.g. always "Standard") until the
            # SmartThings app is opened. Samsung confirmed this behavior
            # and recommended the refresh workaround.
            await self._async_refresh_device_status()

            # Read the mode back: COMPLETED does not guarantee the panel
            # applied it (see docstring). None = could not verify — assume
            # applied rather than blind-firing the remaining attempts.
            applied = await self._async_verify_picture_mode(mode, mode_id)
            if applied:
                # Confirmed on the panel: remember this (capability, form) so
                # later changes go straight through (persisted across restarts).
                self._remember_picture_mode_capability(f"{capability}|{form}")
                return True
            if applied is None:
                return None
            self._log.warning(
                "setPictureMode via %s (%s form) was accepted (COMPLETED) but "
                "the TV still reports another mode — trying the next variant",
                capability,
                form,
            )

        if not any_sent:
            self._log.error(
                "Failed to set picture mode '%s' — every attempt was rejected "
                "by SmartThings: %s",
                mode,
                "; ".join(failures) or "no attempt was made",
            )
            return False
        else:
            # All accepted-but-unapplied (or the last one unverifiable).
            # Keep the optimistic value: the select entity holds the pending
            # mode and reconciles with the cloud on its own grace window.
            self._log.warning(
                "Picture mode '%s' could not be confirmed on the TV via any "
                "capability/argument form; if it did not change, the cloud "
                "command channel likely cannot actuate this panel (see #116)",
                mode,
            )
            return False

    def _remember_picture_mode_capability(self, capability: str) -> None:
        """Memorize (and persist) the capability confirmed to actuate the panel.

        Only called after a VERIFIED apply — never on an unverifiable send — so
        a flaky cloud read can't memorize the wrong capability. If the panel's
        behaviour ever changes (e.g. a firmware update), a later verified apply
        through the other capability simply overwrites the memory.
        """
        if capability == self._verified_picture_mode_capability:
            return
        self._log.debug(
            "Picture mode capability verified working: %s (was: %s) — memorizing",
            capability,
            self._verified_picture_mode_capability,
        )
        self._verified_picture_mode_capability = capability
        if self.picture_mode_capability_persist_cb is not None:
            try:
                self.picture_mode_capability_persist_cb(capability)
            except Exception as err:  # pylint: disable=broad-except
                self._log.debug("Could not persist picture mode capability: %s", err)

    async def _async_verify_picture_mode(self, mode: str, mode_id: str) -> bool | None:
        """Return whether the TV now reports the requested mode, None if unknown.

        Waits a few seconds after the refresh command so the cloud has a
        chance to re-read the panel, then GETs the capability status
        directly. The ``pictureMode`` attribute reports the display NAME on
        some models (Frame 2024: "Dynamique") and the internal id on others —
        both representations of the target are accepted (normalized through
        the name<->id map). Returns None when the read fails — the caller
        must not treat that as "not applied".
        """
        if not self._device_id or not self._session:
            return None
        await asyncio.sleep(PICTURE_MODE_VERIFY_DELAY)
        capability = self._picture_mode_capability or "samsungvd.pictureMode"
        url = (
            f"{API_DEVICES}/{self._device_id}"
            f"/components/main/capabilities/{capability}/status"
        )
        try:
            async with self._session.get(
                url,
                headers={
                    "Authorization": f"Bearer {self._get_api_key()}",
                    "Accept": "application/json",
                },
            ) as resp:
                if resp.status != 200:
                    self._log.debug(
                        "Picture mode verify read failed (status %s)", resp.status
                    )
                    return None
                data = await resp.json()
        except Exception as err:
            self._log.debug("Picture mode verify read failed: %s", err)
            return None
        raw_mode = data.get("pictureMode", {}).get("value")
        if not raw_mode:
            return None
        self._log.debug(
            "Picture mode verify: TV reports '%s' (wanted '%s' / id '%s')",
            raw_mode,
            mode,
            mode_id,
        )
        if raw_mode in (mode, mode_id):
            return True
        # Normalize through the map: raw may be a name where we hold the id,
        # or vice versa.
        if self._picture_mode_map:
            if self._picture_mode_map.get(raw_mode) == mode_id:
                return True  # raw is the name of our target id
            reverse = {v: k for k, v in self._picture_mode_map.items()}
            if reverse.get(raw_mode) == mode:
                return True  # raw is the id of our target name
        return False

    async def _async_refresh_device_status(self) -> None:
        """Send a 'refresh' command to force SmartThings to re-read TV state.

        The SmartThings app does this when opened, which is why the API
        returns correct values after opening the app. Samsung confirmed
        this is the recommended workaround for stale capability values.
        """
        try:
            await self._send_rest_command(
                capability="refresh",
                command="refresh",
            )
            self._log.debug("Sent refresh command to update SmartThings state")
        except Exception as err:
            self._log.debug("Refresh command failed (non-critical): %s", err)

    @Throttle(timedelta(seconds=60))
    async def _periodic_refresh(self) -> None:
        """Periodically refresh SmartThings state during polling.

        Ensures that changes made via the TV remote control or other
        sources are reflected in the SmartThings API. Throttled to
        once per 60 seconds to avoid API rate limiting.
        """
        await self._async_refresh_device_status()


class InvalidSmartThingsSoundMode(RuntimeError):
    """Selected sound mode is invalid."""


class InvalidSmartThingsPictureMode(RuntimeError):
    """Selected picture mode is invalid."""
