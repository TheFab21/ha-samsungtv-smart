"""
Samsung Frame TV Art Mode API wrapper for Home Assistant.

Based on xchwarze/samsung-tv-ws-api art-updates branch
https://github.com/xchwarze/samsung-tv-ws-api/tree/art-updates

Copyright (C) 2019 DSR! <xchwarze@gmail.com>
Copyright (C) 2021 Matthew Garrett <mjg59@srcf.ucam.org>
Copyright (C) 2024 Nick Waterton <n.waterton@outlook.com>

Adapted for Home Assistant integration using aiohttp

SPDX-License-Identifier: LGPL-3.0
"""

from __future__ import annotations

import asyncio
import base64
from contextlib import suppress
from datetime import datetime
import io
import json
import logging
import os
import random
import ssl
import time
from typing import Any
import uuid

import aiohttp

from . import _image_prep

_LOGGER = logging.getLogger(__name__)


def _map_format_to_wire(pil_format: str | None) -> str | None:
    """Map a PIL image format to the Samsung wire file_type."""
    if not pil_format:
        return None
    fmt = pil_format.upper()
    if fmt in ("JPEG", "JPG", "MPO"):
        return "jpg"
    if fmt == "PNG":
        return "png"
    return fmt.lower()


def _detect_wire_type(data: bytes, hint: str | None = None) -> str:
    """Detect the real image format from bytes; fall back to the caller hint.

    Pillow is imported lazily here (not at module scope) so a missing or
    broken Pillow only degrades this best-effort upload-type detection — it
    must never break importing this module, which would take the whole
    integration (media_player, remote, buttons, ...) down with it. Every other
    PIL use in this repo is lazy for the same reason.
    """
    try:
        from PIL import Image  # noqa: PLC0415 - lazy: keep PIL out of import path

        with Image.open(io.BytesIO(data)) as img:
            wire = _map_format_to_wire(img.format)
            if wire:
                return wire
    except Exception:  # noqa: BLE001 - detection is best-effort; fall back to hint
        pass
    normalized = (hint or "png").lower()
    return "jpg" if normalized in ("jpg", "jpeg", "mpo") else normalized


class _DeviceLoggerAdapter(logging.LoggerAdapter):
    """Prefix every log line with the TV's host so multi-TV logs can be told apart."""

    def process(self, msg, kwargs):
        return f"[{self.extra['host']}] {msg}", kwargs


ART_ENDPOINT = "com.samsung.art-app"
D2D_SERVICE_MESSAGE_EVENT = "d2d_service_message"
MS_CHANNEL_CONNECT_EVENT = "ms.channel.connect"
MS_CHANNEL_READY_EVENT = "ms.channel.ready"

# aiohttp WebSocket heartbeat (seconds): aiohttp sends a PING this often and,
# if no PONG comes back in time, raises ServerTimeoutError on the receive loop.
# That is what kills a "zombie" socket — one the TV dropped without a TCP FIN
# (e.g. an abrupt power-off) so it still looks open but delivers nothing and
# would otherwise never reconnect. Kept above the 15s art coordinator poll so a
# genuinely live-but-quiet channel is not torn down unnecessarily.
ART_WS_HEARTBEAT = 20

# Auto-reconnect / keepalive (P4). When the Art WS receive loop exits because
# the TV dropped the socket mid-session (standby, network blip, missed
# heartbeat PONG on a zombie socket), we schedule a bounded exponential-backoff
# reconnect so the channel self-heals without waiting for the next lazy
# _send_art_request or an HA restart. Backoff is CAPPED so a persistently
# unreachable TV never turns into a hot reconnect loop.
_KEEPALIVE_INTERVAL = 60.0  # idle ping cadence: keep a quiet-but-live WS open
_MAX_BACKOFF = 60.0  # hard cap on the per-attempt reconnect delay (seconds)

# Upload d2d socket: write the image in chunks of this size, draining after
# each, so data really hits the wire before the socket is closed (see upload()).
# The drain-per-chunk is what matters, not the chunk size; 512K keeps the
# flush guarantee with far fewer syscalls than the reference's 64K.
_UPLOAD_CHUNK_SIZE = 512 * 1024

# Circuit breaker: consecutive request timeouts on a live socket before the
# channel is declared wedged (TV art APP dead while its network stack keeps
# the WS open and answering PINGs — heartbeat can't catch that) and the socket
# is force-closed to hand recovery to the auto-reconnect path (issue #153).
ART_WS_TIMEOUT_TRIP = 3

# Once the circuit breaker trips, suppress new requests long enough for the
# forced close and backed-off reconnect to settle.  Without this gate, the
# media player, sensor, number and select platforms can immediately enqueue a
# fresh burst against the same dying socket.
ART_WS_RECOVERY_COOLDOWN = 30.0

# How many times the recovery cooldown may double while the channel keeps
# wedging without ever answering a request: 30s, 60s, 120s, 240s. Caps the
# retry rate on a TV that accepts the socket and then kills it on a schedule,
# instead of re-arming into the same failure every cycle (#273).
ART_WS_RECOVERY_BACKOFF_MAX_DOUBLINGS = 3

# A wedge this soon after the previous one counts as a recurrence, and doubles
# the recovery cooldown even when the channel answered in between. A channel
# that answers some requests and times out on others — an art app polled on a
# TV showing HDMI — otherwise resets the escalation on every cycle and loops at
# the base cooldown indefinitely (measured: 10-11 wedges in ~1 h, every
# 4-8 min, with no backing-off line, on two consecutive mornings; #273).
ART_WS_WEDGE_RECURRENCE_WINDOW = 900.0

# Samsung firmware can leave a WebSocket transport half-open indefinitely.
# Integration unload (and therefore Home Assistant shutdown/restart) must not
# wait forever for aiohttp's close handshake.
ART_WS_CLOSE_TIMEOUT = 2.0

# Art-app error codes returned in {"event": "error", "error_code": N} replies,
# per the decompiled firmware (notes/QN55LS03FAFXZA/ART_MODE_DECOMPILED.md).
# Logged alongside the raw code so e.g. a thumbnail failure reads as
# "SYSTEM_FAIL (-1)" instead of a bare, otherwise meaningless "-1".
ART_ERROR_CODES = {
    -14: "INSUFFICIENT_SYSTEM_SPACE",
    -13: "PREVIEW_NOT_STARTED",
    -12: "CHECKOUT_IN_PROGRESS",
    -11: "INSUFFICIENT_SPACE",
    -10: "TEMPORARILY_UNAVAILABLE",
    -9: "NOT_SUPPORTED_API",
    -8: "SSO_REQUIRED",
    -7: "INVALID_PARAMETER",
    -6: "REQUEST_PARSE_FAIL",
    -5: "DB_ERROR",
    -4: "FILE_NOT_FOUND",
    -3: "NO_MEMORY",
    -2: "NO_PERMISSION",
    -1: "SYSTEM_FAIL",
    0: "NO_ERROR",
}


def _describe_art_error(error_code: Any) -> str:
    """Format an art-app error_code with its known name, if any."""
    try:
        name = ART_ERROR_CODES.get(int(error_code))
    except (TypeError, ValueError):
        name = None
    return f"{name} ({error_code})" if name else str(error_code)


def _build_ws_image_frame(envelope: dict, data: bytes) -> bytes:
    """Frame one WS-binary art upload: 2-byte big-endian header length, then
    the compact JSON ``art_app_request`` envelope, then the raw image bytes."""
    header = json.dumps(envelope, separators=(",", ":")).encode("utf-8")
    if len(header) > 0xFFFF:
        raise ValueError("Art upload header too large for a 2-byte length")
    return len(header).to_bytes(2, "big") + header + data


def _get_ssl_context() -> ssl.SSLContext:
    """Get SSL context for secure connections without blocking calls."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _serialize_string(string: str | bytes) -> str:
    """Serialize string to base64."""
    if isinstance(string, str):
        string = string.encode()
    return base64.b64encode(string).decode("utf-8")


class SamsungTVAsyncArt:
    """Async Samsung Frame TV Art Mode API class using aiohttp."""

    def __init__(
        self,
        host: str,
        port: int = 8001,
        token: str | None = None,
        session: aiohttp.ClientSession | None = None,
        timeout: int = 5,
        name: str = "HomeAssistant",
        supports_get_brightness: bool | None = None,
        supports_get_color_temperature: bool | None = None,
    ) -> None:
        """Initialize the Art API."""
        self._host = host
        self._log = _DeviceLoggerAdapter(_LOGGER, {"host": host})
        self._port = port
        self._token = token
        self._external_session = session
        self._session: aiohttp.ClientSession | None = None
        self._timeout = timeout
        self._name = name

        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._art_uuid: str = str(uuid.uuid4())

        # State
        self.art_mode: bool | None = None
        # art_mode_changed broadcasts, numbered as they arrive, and the number
        # of the latest one per state. The switch's write guard confirms a
        # write by a broadcast of the requested state that arrived AFTER the
        # write, rather than by the cached art_mode, which may already have
        # held that value before the write (#290).
        self.art_mode_broadcast_count = 0
        self.art_mode_broadcast_at: dict[bool, int] = {}
        # The latest art_mode_changed broadcast as (state, time.monotonic() of
        # arrival). The media player weighs it against the age of its cached
        # getTVStates.pictureMode snapshot: a broadcast heard AFTER that
        # snapshot was taken is the newer word on what the panel shows. Only the
        # broadcast is stamped — a get_artmode_status reply can be the stale
        # latch of #273, so it must never outrank the panel.
        self.art_mode_last_broadcast: tuple[bool, float] | None = None

        # Async handling
        self._pending_requests: dict[str, asyncio.Future] = {}
        # set_artmode() waiters: some Frames never echo the matching
        # request_id, only the art_mode_changed broadcast. Resolved as soon
        # as that broadcast arrives, instead of always paying the full
        # request timeout.
        self._art_mode_broadcast_waiters: list[asyncio.Future] = []
        # Whether the last set_artmode handed its request to the socket. False
        # when the channel could not be opened (failed, rate-limited, backing
        # off): nothing reached the TV. See set_artmode.
        self.last_set_artmode_sent = False
        self._recv_task: asyncio.Task | None = None
        self._connected = False

        # Auto-reconnect / keepalive tasks (P4). _keepalive_task pings an idle
        # channel; _reconnect_task runs the backoff loop after an unexpected
        # drop. Both are cancelled in close() so an intentional teardown never
        # races against a reconnect that could re-open the socket behind us.
        self._keepalive_task: asyncio.Task | None = None
        self._reconnect_task: asyncio.Task | None = None
        self._force_close_task: asyncio.Task | None = None

        # Connection failure tracking for exponential backoff (v6.3.5)
        self._connection_failures = 0
        self._max_connection_failures = 3  # Start backoff after 3 failures
        self._backoff_until: float | None = None
        self._last_connection_attempt: float = 0
        # Circuit breaker: consecutive request timeouts on a live socket
        # (zombie art app detection — see _note_request_timeout).
        self._timeout_streak = 0
        # Whether any request has been answered since the current connection was
        # established. A port that (re)connects but never answers is not healthy
        # — some 2020 Frames accept a bare ms.channel.connect on the plain-ws
        # port (8001) while the art app never becomes ready, so every request
        # times out. When a wedge trips with this still False, the port is a
        # zombie and _note_request_timeout flips to the alternate one (#12).
        self._got_response_since_connect = False
        # Suspends that breaker while an upload waits for image_added, so
        # unrelated thumbnail timeouts can't tear the socket down mid-upload.
        self._upload_in_progress = False

        # The TV's Art app is effectively a single-request protocol.  Several
        # HA platforms share this client and otherwise poll it concurrently,
        # overwriting event-keyed pending requests and amplifying a slow TV
        # into a reconnect storm.
        self._request_lock = asyncio.Lock()
        self._request_cooldown_until = 0.0
        # Consecutive wedges on connections that never answered anything. Each
        # one doubles the recovery cooldown, so a channel the TV keeps killing
        # is retried further and further apart instead of every cycle. Reset as
        # soon as a request is actually answered (#273: lifting the cooldown on
        # ms.channel.ready alone removed all spacing and took the wedge count
        # from 5 to 94 in a comparable window).
        self._unproductive_cycles = 0
        # Wedges that keep recurring within ART_WS_WEDGE_RECURRENCE_WINDOW,
        # whether or not the channel answered in between, and when the last one
        # was. Escalates the cooldown on their own, so a channel that is
        # partly answering cannot loop at the base cooldown forever (#273).
        self._recurring_wedges = 0
        self._last_wedge_at: float | None = None
        # True once the current port has actually answered a request. The art
        # port is a property of the TV/firmware, so it is discovered at connect
        # time (open() falls back to the alternate port when the connection
        # fails) and then treated as settled: a proven port is never swapped at
        # runtime on a wedge, and only a proven port is persisted (#273
        # follow-up — a runtime swap on transient Wi-Fi loss ping-ponged and
        # rewrote the stored port each time).
        self._proven_port: int | None = None

        # Connection lock to prevent concurrent connection attempts (v6.3.5)
        self._connection_lock = asyncio.Lock()

        # Short-lived cache for get_artmode_settings to dedupe the near-simultaneous
        # calls issued by the brightness and color-temperature NumberEntities every
        # 30 s. The Frame 2024 does not respond to the dedicated get_brightness /
        # get_color_temperature requests, so both entities fall through to
        # get_artmode_settings  -  without this cache, the TV receives two identical
        # WebSocket requests within ~1 ms of each other every cycle.
        # TTL is intentionally short (1.5 s): long enough to absorb concurrent
        # polls, short enough that a set_brightness / set_color_temperature
        # immediately reflects on the next poll.
        self._artmode_settings_cache: list | None = None
        self._artmode_settings_cache_ts: float = 0.0
        self._artmode_settings_cache_ttl: float = 1.5
        self._artmode_settings_lock = asyncio.Lock()

        # Capability flags: certain Frame TV models (e.g. QE55LS03DAUXXN / 2024)
        # do not respond to the dedicated get_brightness / get_color_temperature
        # WebSocket requests  -  the call times out silently and the integration
        # falls back to get_artmode_settings. To avoid paying the timeout cost
        # on every poll, we attempt the direct request once with a short timeout
        # and flip these flags off on the first miss. After that the entity
        # polls go straight to get_artmode_settings (cached above).
        # Flag = None means "unknown, probe once"; True/False is the learned state.
        # Seeded from persisted values (entry.data) when known, so the one-off
        # detection probe is not re-paid on every restart.
        self._supports_get_brightness: bool | None = supports_get_brightness
        self._supports_get_color_temperature: bool | None = (
            supports_get_color_temperature
        )
        # Fired (flag_name, value) when a capability is first determined from a
        # genuine signal, so the caller can persist it. Never fired on a
        # transport/connection failure (the result stays unknown then).
        self._capability_callback = None
        # Fired (port: int) when the runtime port fallback in open() finds
        # the TV listening on the alternate port, so the caller can persist
        # it (CONF_PORT) and avoid re-paying the failed attempt on the
        # configured port every reconnect.
        self._port_callback = None
        # Fired (no args) when the async art channel reports a panel transition
        # (art_mode_changed / go_to_standby), so the caller can refresh its
        # authoritative state (e.g. the IP Control pictureMode read) at once
        # instead of waiting for the next poll. A list, not a single slot,
        # because media_player.py and sensor.py each register their own.
        self._art_event_callbacks: list = []
        # Fired (no args) when the async art channel reports a content-state
        # broadcast (image_selected / matte_changed / slideshow_changed /
        # favorite_changed / rotation_changed), so a poll-based coordinator
        # can refresh immediately instead of waiting for its next interval.
        self._art_content_callbacks: list = []
        # Capability flag: True = TV supports get_thumbnail_list (pre-2024 TVs,
        # streams all thumbnails over a single socket connection — fast).
        # False = TV returns error -1 (2024-2025 Tizen), must use get_thumbnail
        # one-by-one. None = not yet probed (set on first call to get_thumbnail).
        self._supports_thumbnail_list: bool | None = None

    def _get_uuid(self) -> str:
        """Generate a new UUID for art requests."""
        self._art_uuid = str(uuid.uuid4())
        return self._art_uuid

    @property
    def recovery_remaining(self) -> float:
        """Seconds left in the post-wedge recovery cooldown (0 when requests flow).

        While it is positive every request is refused immediately, so a caller
        looping over many requests (thumbnail batch) should pause, not plough on.
        """
        return max(0.0, self._request_cooldown_until - time.monotonic())

    def register_capability_callback(self, func) -> None:
        """Register a callback fired when a get-capability is first learned.

        Signature: func(flag_name: str, value: bool), where flag_name is
        "brightness" or "color_temperature". Only called for a genuine signal
        (the TV responded -> True, or replied silently while connected -> False),
        never on a transport/connection failure.
        """
        self._capability_callback = func

    def _capability_probe_is_trustworthy(self) -> bool:
        """Whether silence to a capability probe really means "unsupported".

        A capability is latched permanently (persisted to entry.data, cleared
        only by a reconfigure), so it must not be decided by a channel that is
        merely busy. A 65" LS03A latched BOTH art get-capabilities to false
        from one 1 s timeout 12 s after a wedge-forced reconnect while its TV
        was waking; its same-model sibling reads true for both (#273).

        Trust the silence only on a channel that has answered something since
        it connected and is not in post-wedge recovery.
        """
        if not self._got_response_since_connect:
            return False
        return self._request_cooldown_until <= time.monotonic()

    def _learn_capability(self, flag_name: str, value: bool) -> None:
        """Notify the caller that a capability has been determined."""
        if self._capability_callback is not None:
            self._capability_callback(flag_name, value)

    def register_port_callback(self, func) -> None:
        """Register a callback fired when the runtime port fallback succeeds.

        Signature: func(port: int). Called when open() connects on the
        alternate port after the configured one failed, so the caller can
        persist the new port (CONF_PORT) for future restarts.
        """
        self._port_callback = func

    def _learn_port(self, port: int) -> None:
        """Notify the caller that the working port has changed."""
        if self._port_callback is not None:
            self._port_callback(port)

    def register_art_event_callback(self, func) -> None:
        """Register a callback fired on a panel-transition broadcast.

        Signature: func() with no arguments. Fired when the async art channel
        reports art_mode_changed or go_to_standby, so the caller can confirm
        the panel state (e.g. via IP Control) without waiting for its own poll.
        Multiple callers may register (media_player.py and sensor.py each do).
        """
        self._art_event_callbacks.append(func)

    def _fire_art_event(self) -> None:
        """Notify callers of a panel transition; never break the art loop."""
        for callback in self._art_event_callbacks:
            try:
                callback()
            except Exception:  # noqa: BLE001 - callback must not break the loop
                self._log.debug("Art API: art-event callback raised", exc_info=True)

    def register_art_content_callback(self, func) -> None:
        """Register a callback fired when the on-screen content/state changes.

        Signature: func() with no arguments. Fired on image_selected,
        matte_changed, slideshow_changed, favorite_changed, or
        rotation_changed broadcasts, so a poll-based coordinator (e.g. the
        Frame Art sensor) can refresh right away instead of waiting up to
        SCAN_INTERVAL for the change to show up.
        """
        self._art_content_callbacks.append(func)

    def _fire_art_content_event(self) -> None:
        """Notify callers of a content-state change; never break the art loop."""
        for callback in self._art_content_callbacks:
            try:
                callback()
            except Exception:  # noqa: BLE001 - callback must not break the loop
                self._log.debug("Art API: art-content callback raised", exc_info=True)

    @property
    def _ws_url(self) -> str:
        """Get the WebSocket URL for the art API.

        The art-app channel must be opened WITHOUT the remote-control token:
        2024 Frame TVs (e.g. QN55LS03DA, ws API 2.0.25) treat a token on this
        channel as a pending device authorization and never send
        ms.channel.connect — the connection idles until the TV drops it with
        ms.channel.timeOut. Tokenless connections complete the handshake
        immediately; the channel itself is unauthenticated.
        """
        scheme = "wss" if self._port == 8002 else "ws"
        name = _serialize_string(self._name)
        return f"{scheme}://{self._host}:{self._port}/api/v2/channels/{ART_ENDPOINT}?name={name}"

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session."""
        if self._external_session and not self._external_session.closed:
            return self._external_session
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def open(self, *, count_failure: bool = True) -> bool:
        """Open WebSocket connection and start listening.

        ``count_failure=False`` is for the reconnect loop, which spaces and
        bounds its own attempts. Counted, they put open() into its 2-minute
        backoff about 15 s after a drop, so the loop's later attempts were
        refused, and so was any art write once the TV was back.
        """
        # Acquire lock to prevent concurrent connection attempts (v6.3.5)
        async with self._connection_lock:
            # Reuse the existing connection only if it is genuinely alive.
            # `self._ws.closed` is unreliable on its own: when the receive
            # loop has exited because the TV went to standby, the WebSocket
            # object can still report closed=False momentarily even though
            # writes will raise "Cannot write to closing transport". We use
            # `_connected` (cleared by _receive_loop on exit) as the
            # authoritative liveness flag and drop the stale reference if
            # it disagrees with `_ws`.
            if self._ws and not self._ws.closed and self._connected:
                return True
            if self._ws and not self._connected:
                self._log.debug(
                    "Art API: Stale WebSocket reference detected, resetting"
                )
                try:
                    if not self._ws.closed:
                        await self._ws.close()
                except Exception:  # pylint: disable=broad-except
                    pass
                self._ws = None
                if self._recv_task and not self._recv_task.done():
                    self._recv_task.cancel()
                self._recv_task = None

            # Check if in backoff period (anti-saturation protection)
            if self._backoff_until is not None:
                if time.time() < self._backoff_until:
                    remaining = int(self._backoff_until - time.time())
                    self._log.debug(
                        "Art API: In backoff period, skipping connection attempt (%ds remaining)",
                        remaining,
                    )
                    return False
                else:
                    self._log.info(
                        "Art API: Backoff period expired, resuming connection attempts"
                    )
                    self._backoff_until = None
                    self._connection_failures = 0

            # Rate limit connection attempts (min 5 seconds between attempts)
            time_since_last = time.time() - self._last_connection_attempt
            if time_since_last < 5:
                self._log.debug(
                    "Art API: Too soon since last attempt (%.1fs ago), skipping",
                    time_since_last,
                )
                return False

            self._last_connection_attempt = time.time()

            # Try the configured port first, then fall back to the alternate
            # port (8001 <-> 8002) on the same connection attempt. The main
            # WebSocket connection (SamsungTVInfo._try_connect_ws) already
            # learns the working port during setup/reconfigure, but the Art
            # API is constructed once at startup from the static config value
            # and never re-learns it  -  a firmware update that filters the
            # configured port (e.g. 2024 Tizen filtering 8002) would otherwise
            # leave Art Mode permanently unreachable until the user manually
            # reconfigures. Mirror the same fallback here so it self-heals.
            # Once a port has answered this session it stays the reference:
            # a previous connect may have fallen back to the other port (a
            # waking TV that briefly refuses the proven one), so retry the
            # proven port first rather than staying on the fallback.
            if self._proven_port is not None and self._port != self._proven_port:
                self._port = self._proven_port

            if await self._connect_once(self._port):
                return True

            previous_port = self._port
            alternate_port = 8001 if self._port == 8002 else 8002
            self._log.debug(
                "Art API: Port %d failed, trying alternate port %d",
                previous_port,
                alternate_port,
            )
            # _connect_once already sets self._port to the port it succeeds on,
            # so log previous_port (not self._port) to avoid a nonsensical
            # "Port changed from N to N" line.
            if await self._connect_once(alternate_port):
                if previous_port != alternate_port:
                    self._log.info(
                        "Art API: port %d did not accept a connection, now on "
                        "port %d (not stored until it answers a request)",
                        previous_port,
                        alternate_port,
                    )
                # Switch in memory only. Connecting is not evidence the art app
                # will serve anything, and on a Wi-Fi Frame a single failed
                # connect to the good port at startup would otherwise rewrite
                # the stored port for good (measured on 192.168.1.31: one
                # "Port changed from 8002 to 8001" at start-up, persisted). The
                # port is written once it actually answers — see
                # _wait_for_response, which owns _proven_port.
                self._port = alternate_port
                return True

            if not count_failure:
                return False
            # Both ports failed: track the failure once for the whole attempt
            # (not once per port), so a temporarily unreachable TV does not
            # reach the backoff threshold twice as fast.
            self._connection_failures += 1
            # A TV that's simply off/unreachable trips this on every poll, and
            # the early attempts usually recover on their own, so keep them at
            # DEBUG. Surface a single INFO line only on the last attempt — i.e.
            # when we actually give up and back off — rather than WARNING, since
            # an unreachable TV is an expected condition, not a fault.
            reached_max = self._connection_failures >= self._max_connection_failures
            self._log.log(
                logging.INFO if reached_max else logging.DEBUG,
                "Art API: Connection failure %d/%d",
                self._connection_failures,
                self._max_connection_failures,
            )

            if reached_max:
                # Exponential backoff: 2, 5, 10, 20, 30 minutes (capped)
                backoff_minutes = min(
                    2
                    ** (self._connection_failures - self._max_connection_failures + 1),
                    30,
                )
                self._backoff_until = time.time() + (backoff_minutes * 60)
                self._log.debug(
                    "Art API: Too many connection failures (%d), entering %d minute backoff period",
                    self._connection_failures,
                    backoff_minutes,
                )

            return False

    async def _connect_once(self, port: int) -> bool:
        """Attempt a single WebSocket connection on the given port.

        Does not touch failure counters or backoff state — the caller (open())
        decides how to account for failure across both ports of a single
        connection attempt. On success, starts the receive loop and resets
        the failure counter.
        """
        original_port = self._port
        self._port = port
        try:
            session = await self._get_session()
            ssl_context = _get_ssl_context() if self._port == 8002 else None

            self._log.debug("Art API: Connecting to %s", self._ws_url)

            self._ws = await session.ws_connect(
                self._ws_url,
                timeout=aiohttp.ClientTimeout(total=self._timeout),
                ssl=ssl_context,
                heartbeat=ART_WS_HEARTBEAT,
            )

            # Wait for connection events
            # Frame TV 2024 sends "connect" but may never send "ready"
            connected = False
            for _ in range(3):  # Max 3 events (reduced from 5)
                try:
                    msg = await asyncio.wait_for(
                        self._ws.receive(), timeout=2
                    )  # Reduced timeout
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        response = json.loads(msg.data)
                        event = response.get("event", "")
                        self._log.debug("Art API: Connection event: %s", event)

                        if event == MS_CHANNEL_READY_EVENT:
                            # Got ready. On a channel that has been answering,
                            # lift the recovery cooldown now instead of leaving
                            # pollers idle for the rest of the 30s window: on a
                            # TV that drops the client on a schedule (a
                            # ~200-240s clientDisconnect cadence was measured on
                            # a 2020 Frame), reconnect + ready can land ~7s after
                            # the wedge and the fixed cooldown wasted ~23s of an
                            # already-working socket.
                            #
                            # But ready alone does NOT prove the app will serve
                            # requests: on a channel wedging without ever
                            # answering, lifting here re-armed straight back into
                            # the failure and removed all spacing (#273: 5 -> 94
                            # wedges). While unproductive cycles are being
                            # counted, keep the backed-off cooldown and let it
                            # expire on its own.
                            if self._unproductive_cycles == 0:
                                self._request_cooldown_until = 0.0
                            connected = True
                            break
                        elif event == MS_CHANNEL_CONNECT_EVENT:
                            # Frame TV 2024 often only sends connect, accept it!
                            self._log.debug(
                                "Art API: Accepting connect event (Frame TV 2024 compatible)"
                            )
                            connected = True
                            break
                    elif msg.type in (
                        aiohttp.WSMsgType.CLOSED,
                        aiohttp.WSMsgType.ERROR,
                    ):
                        break
                except asyncio.TimeoutError:
                    break

            if not connected:
                self._log.debug(
                    "Art API: Did not receive connect/ready event on port %d",
                    port,
                )
                await self._drop_failed_connect()
                self._port = original_port
                return False

            # Connection successful! Reset failure counter
            if self._connection_failures > 0:
                self._log.info(
                    "Art API: Connection successful, resetting failure counter"
                )
                self._connection_failures = 0

            self._connected = True
            # New connection: nothing has answered on it yet. If a wedge trips
            # before anything does, this port is a zombie (see
            # _note_request_timeout) and we flip to the alternate one.
            self._got_response_since_connect = False

            # Start the receive loop
            self._recv_task = asyncio.create_task(self._receive_loop())
            # Start the idle keepalive so a quiet-but-live channel is not
            # dropped by the TV. Cancelled in close() and on an unexpected
            # receive-loop exit (a fresh one starts on the next connect).
            self._keepalive_task = asyncio.create_task(self._keepalive())

            self._log.debug("Art API: Connected and listening on port %d", port)
            return True

        except Exception as ex:
            self._log.debug("Art API: Connection on port %d failed: %s", port, ex)
            await self._drop_failed_connect()
            self._port = original_port
            return False

    async def _drop_failed_connect(self) -> None:
        """Close the socket a failed _connect_once opened, and nothing else.

        Not close(): that is the intentional teardown, and it cancels the
        reconnect loop, which reaches _connect_once through open(). The loop
        then cancelled itself: the CancelledError surfaced on the alternate
        port's ws_connect, which ``except Exception`` does not catch, so no
        failure was counted, no backoff entered and no "reconnect gave up"
        logged (8.10.0 log: 8001 refused, 8002 tried, then nothing). A failed
        lazy open() from a poller killed a sleeping reconnect loop the same
        way. The receive loop and keepalive start only on success, so the
        half-open socket is all there is to drop.
        """
        ws, self._ws = self._ws, None
        if ws is not None and not ws.closed:
            await self._close_ws_bounded(ws)

    async def close(self) -> None:
        """Close the connection."""
        self._connected = False

        # Cancel the reconnect loop first: setting _connected = False above
        # marks this as an intentional teardown, but a reconnect task already
        # in flight would otherwise re-open the socket right after we close it.
        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
        self._reconnect_task = None

        if self._force_close_task and not self._force_close_task.done():
            self._force_close_task.cancel()
        self._force_close_task = None

        if self._keepalive_task:
            self._keepalive_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._keepalive_task
            self._keepalive_task = None

        if self._recv_task:
            self._recv_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._recv_task
            self._recv_task = None

        if self._ws and not self._ws.closed:
            await self._close_ws_bounded(self._ws)
        self._ws = None

        # Cancel all pending requests
        for future in self._pending_requests.values():
            if not future.done():
                future.cancel()
        self._pending_requests.clear()

        # Close own session if created
        if self._session and not self._external_session:
            await self._session.close()
            self._session = None

    @staticmethod
    def _backoff_delay(attempt: int) -> float:
        """Exponential backoff (1, 2, 4, ...) capped at _MAX_BACKOFF seconds."""
        return float(min(2**attempt, int(_MAX_BACKOFF)))

    async def _reconnect_with_backoff(self, max_attempts: int = 6) -> bool:
        """Try to reopen the WS with bounded exponential backoff.

        Returns True as soon as open() succeeds. Each attempt sleeps a capped
        delay first (so we never hammer a TV that's simply off), and open()
        applies its own 5s rate-limit on top, so this can never become a hot
        loop. Its failures are not counted towards open()'s backoff, which is
        there to space the pollers' attempts. Gives up after max_attempts; the
        next lazy _send_art_request will retry open() later if the TV comes
        back.
        """
        for attempt in range(max_attempts):
            await asyncio.sleep(self._backoff_delay(attempt))
            if await self.open(count_failure=False):
                self._log.debug("Art API: reconnected after %d attempt(s)", attempt + 1)
                return True
        # DEBUG: the socket dropped and the TV stayed off the network for the
        # whole minute this loop covers (network loss, reboot, deep standby).
        # A normal standby keeps the art socket open and is not seen here:
        # 4 standbys on a 55" LS03D left it up, it dropped once, when the TV
        # left the network. Unreachable is expected, not a fault (see open());
        # the next request retries.
        self._log.debug(
            "Art API: reconnect gave up after %d attempts; the next request "
            "will retry",
            max_attempts,
        )
        return False

    async def _keepalive(self) -> None:
        """Emit a periodic ping so an idle Art WS session is not dropped."""
        try:
            while self._connected and self._ws and not self._ws.closed:
                await asyncio.sleep(_KEEPALIVE_INTERVAL)
                if self._ws and not self._ws.closed:
                    await self._ws.ping()
        except asyncio.CancelledError:
            # Normal on close()/reconnect teardown — exit quietly.
            pass
        except Exception:  # noqa: BLE001 - a failed ping must not crash the task
            self._log.debug("Art API: keepalive ping failed", exc_info=True)

    async def _receive_loop(self) -> None:
        """Background task to receive and process WebSocket messages.

        When this loop exits (TV went to standby, network drop, transport
        closed by aiohttp, etc.), the WebSocket and the recv task references
        become stale. Previous versions only flipped `_connected` to False,
        which left `self._ws` pointing at a dead transport. That confused
        `open()` (which short-circuits when `self._ws and not self._ws.closed`
        looks truthy) and caused every subsequent `_send_art_request` to fail
        with `Cannot write to closing transport` until the integration was
        reloaded  -  sometimes many hours after the TV had come back online.

        We now clear the stale references and cancel any in-flight pending
        requests so the next `_send_art_request` triggers a fresh `open()`.

        Note: we do NOT call `close()` from here. `close()` cancels and awaits
        `self._recv_task`, and we ARE that task  -  calling it would deadlock.
        The cleanup below is the subset of `close()` that is safe to do from
        inside the task.
        """
        if not self._ws:
            return

        ws = self._ws
        cancelled = False
        try:
            async for msg in self._ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    try:
                        response = json.loads(msg.data)
                        event = response.get("event", "")
                        await self._process_event(event, response)
                    except json.JSONDecodeError:
                        self._log.debug("Art API: Failed to decode message")
                elif msg.type == aiohttp.WSMsgType.BINARY:
                    # API 0.97 Frames (e.g. the 2019 QN55LS03R, #311) answer a
                    # get_thumbnail over a binary WebSocket frame instead of
                    # handing back conn_info for a separate socket. The frame is
                    # the same 2-byte-length + JSON-header + raw-bytes envelope
                    # the 0.97 upload path uses (#307); _process_binary resolves
                    # the waiting request with the bytes attached. Newer Frames
                    # never send one, so this is dead weight for them, not a
                    # behaviour change.
                    self._process_binary(msg.data)
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    self._log.debug("Art API: WebSocket error")
                    break
                elif msg.type == aiohttp.WSMsgType.CLOSED:
                    self._log.debug("Art API: WebSocket closed")
                    break
        except asyncio.CancelledError:
            # Cancellation is the normal path used by close(), which is about
            # to bind `self._ws = None` etc. itself. Skip the cleanup in
            # finally to avoid stomping on close()'s state machine, but
            # re-raise so the task is marked cancelled rather than completed.
            cancelled = True
            raise
        except Exception as ex:
            self._log.debug("Art API: Receive loop error: %s", ex)
        finally:
            if not cancelled:
                # Receive loop is exiting on its own (TV standby, network
                # drop, transport closed, or a missed heartbeat PONG on a
                # zombie socket) and nobody else will clean up. Drop stale
                # references so the next _send_art_request triggers a fresh
                # open(). aiohttp ends the `async for` on CLOSE without
                # yielding it, so the CLOSED branch above never logs: say it
                # here.
                self._log.debug(
                    "Art API: receive loop ended (close code %s); reconnecting",
                    getattr(ws, "close_code", None),
                )
                self._connected = False
                self._ws = None
                self._recv_task = None
                # Invalidate the cached art_mode: the channel that keeps it
                # live is gone, so the last value (typically "on", since Art
                # Mode is a Frame's last state before power-off) is now stale
                # and must not keep being reported. Resetting to None makes
                # _art_mode_is_on() fall through to the independent power
                # sources (IP Control / SmartThings / REST PowerState) instead
                # of pinning a false "on". A reconnect restores the real value
                # from the first event received. The last broadcast goes with
                # it: it described a channel that is now dead.
                self.art_mode = None
                self.art_mode_last_broadcast = None
                # Fail in-flight pending requests immediately rather than
                # letting their callers block on the per-request timeout;
                # the response will never arrive on this dead channel.
                for future in self._pending_requests.values():
                    if not future.done():
                        future.cancel()
                self._pending_requests.clear()
                # Cancel the now-orphaned keepalive (it pinged THIS dead
                # socket); a fresh one is started by the next successful
                # connect, so we never end up with two pinging in parallel.
                if self._keepalive_task and not self._keepalive_task.done():
                    self._keepalive_task.cancel()
                self._keepalive_task = None
                # Schedule a bounded backoff reconnect so the Art channel
                # self-heals without waiting for the next lazy
                # _send_art_request or an HA restart. Fire-and-forget, but
                # tracked on self so close() can cancel it — that prevents an
                # intentional teardown from racing a reconnect. Skip if one is
                # already running (a reconnect that opened a socket which then
                # dropped again handles its own retries).
                if self._reconnect_task is None or self._reconnect_task.done():
                    self._reconnect_task = asyncio.create_task(
                        self._reconnect_with_backoff()
                    )

    async def _process_event(self, event: str, response: dict) -> None:
        """Process incoming WebSocket events."""
        self._log.debug("Art API: Received event '%s'", event)

        if event != D2D_SERVICE_MESSAGE_EVENT:
            return

        try:
            data_str = response.get("data", "{}")
            data = json.loads(data_str) if isinstance(data_str, str) else data_str
            self._log.debug("Art API: Event data: %s", data)
        except json.JSONDecodeError:
            return

        sub_event = data.get("event", "")

        # Update art mode status from events. Exclude set_artmode_status: it is
        # the reply to our OWN write, carrying 'status' (not 'value'), so reading
        # it here made data.get("value") None and forced art_mode False whatever
        # it reported. On some 2024 Frames (QA65LS03D) that reply trails the
        # authoritative art_mode_changed broadcast by up to ~13 s, so it
        # overwrote a correct "on" with False and — with no IP Control or
        # SmartThings to re-read — the switch and sensor stuck at off (#264). The
        # get_artmode_status status report (which does carry 'value') still
        # matches here; the write's reply is still resolved by request_id below.
        if "artmode_status" in sub_event and sub_event != "set_artmode_status":
            self.art_mode = data.get("value") == "on"
        elif sub_event == "art_mode_changed":
            self.art_mode = data.get("status") == "on"
            self.art_mode_broadcast_count += 1
            self.art_mode_broadcast_at[self.art_mode] = self.art_mode_broadcast_count
            self.art_mode_last_broadcast = (self.art_mode, time.monotonic())
            self._fire_art_event()
            for future in self._art_mode_broadcast_waiters:
                if not future.done():
                    future.set_result(None)
            self._art_mode_broadcast_waiters.clear()
        elif sub_event == "go_to_standby":
            # Ambiguous, not a definitive "art mode off": the panel also
            # fires this when it dims for its own power-save sleep timer
            # (e.g. no motion / low ambient light) while still considered
            # "in Art Mode" by the TV and other status checks. The legacy
            # sync WS handler (samsungws.py) already treats this event as
            # ArtModeStatus.Unavailable rather than Off for the same reason.
            # Forcing art_mode False here made it the priority-3 source for
            # extra_state_attributes on TVs without IP Control paired,
            # flipping art_mode_status to "off" while artwork was still on
            # screen. Leave self.art_mode untouched and let the event
            # callback trigger an authoritative re-check instead.
            self._fire_art_event()
        elif sub_event in (
            "image_selected",
            "matte_changed",
            "slideshow_changed",
            "favorite_changed",
            "rotation_changed",
            "image_added",
            "image_of_list_added",
        ):
            # Confirmed broadcasts (WEBSOCKET_DECOMPILED.md) for changes the
            # Frame Art sensor otherwise only learns about on its next poll.
            # image_added / image_of_list_added fire when the TV materializes
            # new content locally — that is when an Art Store (SAM-S*)
            # thumbnail finally becomes fetchable, so refreshing here retries
            # the thumbnail that was skipped while the content was uncached.
            self._fire_art_content_event()

        # Check for error
        if sub_event == "error":
            error_code = data.get("error_code", "unknown")
            self._log.debug("Art API: Error event: %s", _describe_art_error(error_code))

        # Resolve pending requests
        request_id = data.get("request_id", data.get("id"))
        self._log.debug(
            "Art API: Looking for request_id='%s' or sub_event='%s' in pending: %s",
            request_id,
            sub_event,
            list(self._pending_requests.keys()),
        )

        # Try to match by request_id first
        if request_id and request_id in self._pending_requests:
            future = self._pending_requests.get(request_id)
            if future and not future.done():
                self._log.debug("Art API: Matched by request_id '%s'", request_id)
                future.set_result(data)
                return

        # Try to match by sub_event
        if sub_event and sub_event in self._pending_requests:
            future = self._pending_requests.get(sub_event)
            if future and not future.done():
                self._log.debug("Art API: Matched by sub_event '%s'", sub_event)
                future.set_result(data)

    def _process_binary(self, raw: bytes) -> None:
        """Resolve a request answered over a binary WebSocket frame (#311).

        API 0.97 Frames return thumbnail bytes as a binary frame rather than
        the conn_info-plus-socket handshake newer Frames use. The frame mirrors
        the 0.97 upload envelope (#307): a 2-byte big-endian header length, a
        JSON header, then the raw bytes. The header is a d2d_service_message
        whose ``data`` is the JSON we would otherwise have received as text —
        for a thumbnail, ``{"event": "thumbnail", "content_id": ..., ...}``.

        The decoded payload is attached under ``binary`` and the pending request
        is resolved by whichever identifier the frame carries — the echoed
        request_id/id if present, else the content_id — so the normal
        request_id keying is never disturbed and get_thumbnail can also listen
        on a content_id alias. Anything undecodable or not a thumbnail is
        ignored rather than allowed to break the receive loop (its failure would
        tear down and reconnect the channel).
        """
        try:
            if len(raw) < 2:
                return
            header_len = int.from_bytes(raw[:2], "big")
            if header_len <= 0 or 2 + header_len > len(raw):
                self._log.debug(
                    "Art API: binary frame header length %d out of range (%d bytes)",
                    header_len,
                    len(raw),
                )
                return
            header = json.loads(raw[2 : 2 + header_len])
            payload = raw[2 + header_len :]
            data_str = header.get("data") if isinstance(header, dict) else None
            data = json.loads(data_str) if isinstance(data_str, str) else data_str
        except (ValueError, json.JSONDecodeError) as ex:
            self._log.debug("Art API: undecodable binary frame: %s", ex)
            return

        if not isinstance(data, dict):
            return
        if data.get("event") != "thumbnail":
            # Nothing else is expected over a binary frame today; don't guess.
            self._log.debug(
                "Art API: ignoring binary frame for event '%s'", data.get("event")
            )
            return

        result = {**data, "binary": payload}
        for key in (data.get("request_id"), data.get("id"), data.get("content_id")):
            if key and key in self._pending_requests:
                future = self._pending_requests.get(key)
                if future and not future.done():
                    self._log.debug(
                        "Art API: thumbnail binary frame (%d bytes) matched '%s'",
                        len(payload),
                        key,
                    )
                    future.set_result(result)
                return
        self._log.debug(
            "Art API: thumbnail binary frame for '%s' had no waiter",
            data.get("content_id"),
        )

    async def _wait_for_response(
        self,
        request_key: str,
        timeout: float = 5.0,
    ) -> dict[str, Any] | None:
        """Wait for a response matching the request key."""
        if request_key not in self._pending_requests:
            self._pending_requests[request_key] = (
                asyncio.get_event_loop().create_future()
            )

        try:
            result = await asyncio.wait_for(
                self._pending_requests[request_key],
                timeout=timeout,
            )
            # A real answer proves the art app is alive — reset the breaker and
            # mark this connection as one the current port actually serves. It
            # also ends any recovery backoff: the escalation only exists for
            # cycles that never got an answer (#273).
            self._timeout_streak = 0
            self._got_response_since_connect = True
            self._unproductive_cycles = 0
            # First real answer on this port proves it: settle it and persist
            # it. Persisting here rather than on a speculative switch means the
            # stored port only ever records a port the TV actually served.
            # Only the FIRST answering port of the session is proven: an answer
            # on a fallback port (a waking TV refusing the proven one for a few
            # seconds) serves the request but must not re-prove and persist it,
            # or the stored port flips at every wake (8002 -> 8001 measured on
            # four Frames in one evening, #273).
            if self._proven_port is None:
                self._proven_port = self._port
                self._learn_port(self._port)
            return result
        except asyncio.TimeoutError:
            self._log.debug("Art API: Timeout waiting for '%s'", request_key)
            self._note_request_timeout()
            return None
        except asyncio.CancelledError:
            # Channel teardown cancelled the future — the receive loop is
            # already handling recovery; not a zombie-app signal.
            return None
        finally:
            self._pending_requests.pop(request_key, None)

    def _note_request_timeout(self) -> None:
        """Circuit breaker for a zombie art channel (app dead, socket alive).

        The TV's art APP can crash while its network stack keeps the WebSocket
        open and answering PINGs: the aiohttp heartbeat sees a healthy socket,
        the receive loop never exits, and every request just times out forever
        — the integration stays wedged until a reload (issue #153). Detect it
        by counting CONSECUTIVE request timeouts (any real answer resets the
        count); at the threshold, force-close the socket so the receive loop
        exits through its normal unintentional-drop path, which cleans up and
        starts the bounded-backoff auto-reconnect.

        Occasional single timeouts (e.g. the capability probes on TVs that
        don't implement a request) don't trip it: interleaved successful
        requests keep resetting the streak.

        An upload in flight suspends the breaker entirely. On TVs whose
        get_thumbnail never answers (2024 Frames), the thumbnail coordinator
        piles up timeouts *while* an upload waits for ``image_added``; tripping
        here would force-close the socket, cancel that pending wait and report
        a bogus "no content_id returned" for an upload the TV was still
        processing. The channel is demonstrably alive during an upload anyway —
        the TV just answered send_image and accepted the d2d transfer.
        """
        if self._upload_in_progress:
            return
        self._timeout_streak += 1
        if self._timeout_streak < ART_WS_TIMEOUT_TRIP:
            return
        if not self._connected or not self._ws or self._ws.closed:
            # Already disconnected — lazy open()/auto-reconnect owns recovery.
            self._timeout_streak = 0
            return
        self._log.warning(
            "Art API: %d consecutive request timeouts on a live socket — "
            "art channel looks wedged, forcing reconnect",
            self._timeout_streak,
        )
        self._timeout_streak = 0
        # A connection that answered at least once was genuinely working, so its
        # wedge is a fresh fault and gets the base cooldown. One that never
        # answered is an unproductive cycle: double the cooldown each time so a
        # channel the TV keeps killing is retried further apart (#273).
        if self._got_response_since_connect:
            self._unproductive_cycles = 0
        else:
            self._unproductive_cycles = min(
                self._unproductive_cycles + 1, ART_WS_RECOVERY_BACKOFF_MAX_DOUBLINGS
            )
        # Independently of that: a wedge soon after the last one is a recurrence.
        # The reset above is right for a one-off fault on a working channel, but
        # a channel that answers some requests and times out on the rest resets
        # it on every cycle, so the spacing never grows. Count recurrences too
        # and take whichever escalation is larger; a quiet window clears it.
        now = time.monotonic()
        if (
            self._last_wedge_at is not None
            and now - self._last_wedge_at <= ART_WS_WEDGE_RECURRENCE_WINDOW
        ):
            self._recurring_wedges = min(
                self._recurring_wedges + 1, ART_WS_RECOVERY_BACKOFF_MAX_DOUBLINGS
            )
        else:
            self._recurring_wedges = 0
        self._last_wedge_at = now
        doublings = max(self._unproductive_cycles, self._recurring_wedges)
        cooldown = ART_WS_RECOVERY_COOLDOWN * (2**doublings)
        self._request_cooldown_until = max(
            self._request_cooldown_until,
            time.monotonic() + cooldown,
        )
        # A connection that wedged without ever answering a single request is a
        # zombie: on some 2020 Frames the plain-ws port (8001) accepts a bare
        # ms.channel.connect but its art app never becomes ready, so every
        # request times out — and because _connect_once "succeeded" there, each
        # reconnect keeps choosing the same dead port and never retries the
        # secure port (8002) that actually works, looping forever (#12). Flip to
        # the alternate port before the reconnect so open() tries the good one
        # first. A port that HAS answered is left alone (a transient wedge on a
        # genuinely-working port must not bounce it to the other one).
        if not self._got_response_since_connect and self._proven_port != self._port:
            alternate_port = 8001 if self._port == 8002 else 8002
            self._log.warning(
                "Art API: port %d has never answered a request — trying port %d "
                "for the reconnect",
                self._port,
                alternate_port,
            )
            # Not persisted: only a port that actually answers is learned (see
            # _wait_for_response). A speculative switch must not rewrite the
            # stored port.
            self._port = alternate_port
        elif not self._got_response_since_connect:
            # The port is known good — it has served this TV before. A wedge
            # with no answer here means the TV or the network is away (a Wi-Fi
            # Frame dropping off, a set powering down), not a wrong port, and
            # both ports look equally dead in that state. Switching would just
            # ping-pong: 55 flips each way were measured in one window on a
            # Wi-Fi Frame, each one also rewriting the stored port (#273
            # follow-up). Say so and let the backoff above space the retries.
            self._log.warning(
                "Art API: port %d unresponsive (the TV or network is likely "
                "away); keeping the port and backing off for %.0fs",
                self._port,
                cooldown,
            )
        elif self._recurring_wedges:
            # The channel answers some requests and times out on the others, so
            # neither branch above applies and nothing used to be said at all.
            # Usually the art app being polled on a TV that is on, but showing
            # an input rather than art.
            self._log.warning(
                "Art API: the channel answers but art requests keep timing out "
                "(%d wedges in a row; the TV may be on a non-art input); "
                "backing off for %.0fs",
                self._recurring_wedges + 1,
                cooldown,
            )
        # Close from a task: _ws.close() makes the receive loop's `async for`
        # terminate (CLOSED), and its cleanup/auto-reconnect machinery takes
        # over — one recovery path for every kind of dead channel.
        if self._force_close_task is None or self._force_close_task.done():
            self._force_close_task = asyncio.create_task(self._force_close_ws())

    async def _force_close_ws(self) -> None:
        """Close the current WS to trigger the receive-loop recovery path."""
        ws = self._ws
        if ws is None or ws.closed:
            return
        closed = await self._close_ws_bounded(ws)
        if closed:
            return

        # A transport that ignores close would otherwise leave _receive_loop
        # alive forever and block both recovery and integration unload.  Detach
        # it and start the same bounded reconnect path the receive loop would
        # normally schedule after observing CLOSED.
        if self._ws is ws:
            self._connected = False
            self._ws = None
            recv_task = self._recv_task
            self._recv_task = None
            if recv_task and recv_task is not asyncio.current_task():
                recv_task.cancel()
            if self._reconnect_task is None or self._reconnect_task.done():
                self._reconnect_task = asyncio.create_task(
                    self._reconnect_with_backoff()
                )

    async def _close_ws_bounded(self, ws: aiohttp.ClientWebSocketResponse) -> bool:
        """Close a WebSocket without ever blocking HA shutdown indefinitely."""
        close_task = asyncio.create_task(ws.close())
        done, _pending = await asyncio.wait({close_task}, timeout=ART_WS_CLOSE_TIMEOUT)
        if close_task not in done:
            close_task.cancel()

            # Do not await a cancellation-resistant transport.  Consume the
            # eventual result so a late exception cannot become an unhandled
            # task warning.
            def _consume_result(task: asyncio.Task) -> None:
                with suppress(BaseException):
                    task.result()

            close_task.add_done_callback(_consume_result)
            self._log.warning(
                "Art API: WebSocket close did not finish within %.1fs; "
                "detaching the stale transport",
                ART_WS_CLOSE_TIMEOUT,
            )
            return False
        try:
            close_task.result()
        except Exception as ex:  # noqa: BLE001 - best effort; caller recovers
            self._log.debug("Art API: WebSocket close failed: %s", ex)
            return False
        return True

    async def _send_art_request(
        self,
        request_data: dict[str, Any],
        wait_for_event: str | None = None,
        timeout: float = 5.0,
        bypass_cooldown: bool = False,
        alias_keys: list[str] | None = None,
    ) -> dict[str, Any] | None:
        """Serialize Art requests and suppress bursts during socket recovery.

        ``bypass_cooldown`` is for an explicit art-mode write. The cooldown
        exists to stop the POLLERS refilling a socket that is recovering; a
        write asked for by a user or an automation is not a poller, it is rare,
        and on a TV stuck on an input it is the very request that ends the
        wedge loop. Blocking it only made Art Mode unreachable for as long as
        the backoff lasted (#273: "Failed to turn Art Mode ON after 3 attempts"
        twice, five minutes apart, while the channel was backing off).
        """
        async with self._request_lock:
            cooldown_remaining = (
                0.0
                if bypass_cooldown
                else self._request_cooldown_until - time.monotonic()
            )
            if cooldown_remaining > 0:
                self._log.debug(
                    "Art API: recovery cooldown active; skipping request for %.1fs",
                    cooldown_remaining,
                )
                return None
            return await self._send_art_request_locked(
                request_data, wait_for_event, timeout, alias_keys
            )

    async def _send_art_request_locked(
        self,
        request_data: dict[str, Any],
        wait_for_event: str | None,
        timeout: float,
        alias_keys: list[str] | None = None,
    ) -> dict[str, Any] | None:
        """Send one request while the per-TV request lock is held."""
        # Ensure connected - also reconnect if WebSocket was closed by TV
        if not self._connected or not self._ws or self._ws.closed:
            if self._ws and self._ws.closed:
                self._log.debug("Art API: WebSocket was closed, reconnecting...")
                self._connected = False  # Reset flag to allow reconnection
            if not await self.open():
                self._log.debug("Art API: Failed to connect/reconnect")
                return None

        # Double-check connection after open()
        if not self._ws or self._ws.closed:
            self._log.debug("Art API: WebSocket still not connected after open()")
            return None

        # Set up request IDs (both old and new API style)
        if not request_data.get("id"):
            request_data["id"] = self._get_uuid()
        request_data["request_id"] = request_data["id"]

        request_key = wait_for_event or request_data["id"]

        # Create future before sending
        future = asyncio.get_event_loop().create_future()
        self._pending_requests[request_key] = future
        # Point each alias at the SAME future so an answer that arrives under a
        # different identifier (e.g. a 0.97 thumbnail binary frame keyed by
        # content_id, #311) still resolves this one await, with the normal
        # request_id keying left untouched. Never shadow the primary key.
        alias_keys = [k for k in (alias_keys or []) if k and k != request_key]
        for alias in alias_keys:
            self._pending_requests[alias] = future

        # Build command
        command = {
            "method": "ms.channel.emit",
            "params": {
                "event": "art_app_request",
                "to": "host",
                "data": json.dumps(request_data),
            },
        }

        sent = False
        try:
            await self._ws.send_json(command)
            sent = True
            self._log.debug(
                "Art API: Sent request '%s'", request_data.get("request", "unknown")
            )

            # Wait for response (_wait_for_response pops its own request_key)
            return await self._wait_for_response(request_key, timeout)

        except Exception as ex:
            self._log.debug("Art API: Error sending request: %s", ex)
            self._pending_requests.pop(request_key, None)
            if not sent:
                # Nothing left HA ("Cannot write to closing transport"):
                # set_artmode reads request_id as "handed to the socket".
                request_data.pop("request_id", None)
            # Mark as disconnected to force reconnection on next request
            self._connected = False
            return None
        finally:
            # Drop the aliases we added; _wait_for_response only pops the
            # primary key, so an orphaned alias would otherwise linger (and
            # could catch a later, unrelated frame).
            for alias in alias_keys:
                self._pending_requests.pop(alias, None)

    # ==================== REST API Methods ====================

    async def supported(self) -> bool:
        """Check if the TV supports Frame TV art mode."""
        try:
            session = await self._get_session()
            url = f"http://{self._host}:8001/api/v2/"
            async with asyncio.timeout(5):
                async with session.get(url) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        device = data.get("device", {})
                        return device.get("FrameTVSupport") == "true"
        except Exception as ex:
            self._log.debug("Art API: Error checking support: %s", ex)
        return False

    async def on(self) -> bool:
        """Check if the TV is on."""
        try:
            session = await self._get_session()
            url = f"http://{self._host}:8001/api/v2/"
            async with asyncio.timeout(5):
                async with session.get(url) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        device = data.get("device", {})
                        return device.get("PowerState", "off") == "on"
        except Exception:
            pass
        return False

    async def is_artmode(self) -> bool:
        """Check if currently in art mode."""
        return await self.on() and self.art_mode is True

    async def in_artmode(self) -> bool:
        """Authoritative art-mode check.

        Trust the Art WebSocket (get_artmode_status), not REST PowerState:
        2025 Frames report PowerState="standby" while Art Mode is ON.
        """
        if await self.on():
            return True
        return (await self.get_artmode()) == "on"

    # ==================== Art API Methods ====================

    async def get_api_version(self) -> str | None:
        """Get the art API version."""
        data = await self._send_art_request({"request": "get_api_version"})
        if not data:
            data = await self._send_art_request({"request": "api_version"})
        return data.get("version") if data else None

    async def available(
        self, category: str | None = None, *, strict: bool = False
    ) -> list | None:
        """Get list of available artwork.

        category: 'MY-C0002' for my pictures, 'MY-C0004' for favourites, 'MY-C0008' for store

        By default a failed read (no answer, unparsable reply) returns [] like an
        empty library. With strict=True it returns None instead, so a caller
        that acts on the list (deleting local files) can tell "the TV has no
        artwork" from "the TV did not answer".
        """
        failed: list | None = None if strict else []
        data = await self._send_art_request(
            {"request": "get_content_list", "category": category},
            timeout=15,
        )
        if not data:
            return failed

        content_list = data.get("content_list", "[]")
        if isinstance(content_list, str):
            try:
                content_list = json.loads(content_list)
            except json.JSONDecodeError:
                return failed

        if category:
            return [v for v in content_list if v.get("category_id") == category]
        return content_list

    async def get_current(self) -> dict[str, Any] | None:
        """Get information about the currently displayed artwork."""
        return await self._send_art_request({"request": "get_current_artwork"})

    async def get_thumbnail_list(self, content_id_list: list[dict]) -> dict[str, bytes]:
        """Get thumbnails for a list of content IDs (multi-download)."""
        self._log.debug(
            "Art API: Requesting get_thumbnail_list for %s", content_id_list
        )

        data = await self._send_art_request(
            {
                "request": "get_thumbnail_list",
                "content_id_list": content_id_list,
                "conn_info": {
                    "d2d_mode": "socket",
                    "connection_id": random.randrange(4 * 1024 * 1024 * 1024),
                    "id": self._get_uuid(),
                },
            },
            timeout=15,
        )

        if not data:
            self._log.debug("Art API: No response for get_thumbnail_list")
            return {}

        # Si la TV repond directement par un event d'erreur
        if data.get("event") == "error":
            self._log.debug(
                "Art API: get_thumbnail_list returned error: %s",
                _describe_art_error(data.get("error_code")),
            )
            return {}

        try:
            conn_info = data.get("conn_info", "{}")
            self._log.debug("Art API: get_thumbnail_list conn_info raw: %s", conn_info)

            if isinstance(conn_info, str):
                conn_info = json.loads(conn_info)

            ip = conn_info.get("ip")
            port = conn_info.get("port")
            secured = conn_info.get("secured", False)

            if not ip or not port:
                self._log.debug(
                    "Art API: Invalid conn_info for thumbnail_list - ip=%s, port=%s",
                    ip,
                    port,
                )
                return {}

            ssl_context = _get_ssl_context() if secured else None
            self._log.debug(
                "Art API: Opening thumbnail socket %s:%s (ssl=%s)",
                ip,
                port,
                ssl_context is not None,
            )

            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(ip, int(port), ssl=ssl_context),
                timeout=10,
            )
            self._log.debug("Art API: Connected to thumbnail socket")

            try:
                thumbnail_data_dict: dict[str, bytes] = {}
                total_num_thumbnails = 1
                current_thumb = -1

                while current_thumb + 1 < total_num_thumbnails:
                    self._log.debug("Art API: Reading thumbnail header.")
                    header_len = int.from_bytes(await reader.readexactly(4), "big")
                    self._log.debug("Art API: Header length: %d", header_len)

                    header_raw = await reader.readexactly(header_len)
                    header = json.loads(header_raw)
                    self._log.debug("Art API: Thumbnail header: %s", header)

                    thumbnail_data_len = int(header["fileLength"])
                    current_thumb = int(header["num"])
                    total_num_thumbnails = int(header["total"])
                    filename = "{}.{}".format(
                        header.get("fileID", header.get("content_id", "unknown")),
                        header.get("fileType", "jpg"),
                    )

                    self._log.debug(
                        "Art API: Reading %d bytes for thumbnail %d/%d (%s)",
                        thumbnail_data_len,
                        current_thumb + 1,
                        total_num_thumbnails,
                        filename,
                    )
                    thumbnail_data = await reader.readexactly(thumbnail_data_len)
                    thumbnail_data_dict[filename] = thumbnail_data
                    self._log.debug(
                        "Art API: Got thumbnail %s (%d bytes)",
                        filename,
                        len(thumbnail_data),
                    )

                return thumbnail_data_dict

            finally:
                writer.close()
                await writer.wait_closed()
                self._log.debug("Art API: Thumbnail socket closed")

        except asyncio.TimeoutError:
            self._log.debug("Art API: Timeout connecting to thumbnail socket")
            return {}
        except asyncio.IncompleteReadError as ex:
            self._log.debug(
                "Art API: Incomplete read in get_thumbnail_list "
                "(%d bytes read, %d expected)",
                len(ex.partial or b""),
                ex.expected,
            )
            return {}
        except Exception as ex:
            self._log.debug(
                "Art API: Error getting thumbnail_list: %s (type: %s)",
                ex,
                type(ex).__name__,
            )
            import traceback

            self._log.debug("Art API: Traceback: %s", traceback.format_exc())
            return {}

    async def get_thumbnail(self, content_id: str) -> bytes | None:
        """Get thumbnail for a specific piece of art.

        Strategy (learned once per session via _supports_thumbnail_list):
        - Unknown (None): probe get_thumbnail_list; cache the result for the session.
        - True (pre-2024 TVs): use get_thumbnail_list directly — fast single-socket path.
        - False (2024-2025 Tizen): skip get_thumbnail_list entirely and go straight to
          get_thumbnail, saving one useless WebSocket round-trip per image.
        """
        self._log.debug("Art API: Getting thumbnail for %s", content_id)

        # For SAM-S (Art Store) images, warm up the TV by calling get_content_list first
        # This seems to help the TV prepare the thumbnail data
        is_artstore = content_id.startswith("SAM-")
        if is_artstore:
            self._log.debug(
                "Art API: Art Store image detected, warming up with get_content_list"
            )
            await self._send_art_request(
                {
                    "request": "get_content_list",
                    "category": "MY-C0004",  # Favorites category
                },
                timeout=5,
            )
            # Small delay to let TV prepare
            await asyncio.sleep(0.1)

        # Only try get_thumbnail_list if the TV is known (or not yet probed) to support it.
        # Always try _get_thumbnail_via_list first — it works for both MY_F* and SAM-*
        # on most TV models. Only the direct socket fallback (get_thumbnail) fails for
        # SAM-* images. Never set the flag to False: a startup error (TV not ready)
        # must not permanently disable the fast path for the whole session.
        # Skip the list path entirely once we've learned this TV rejects it
        # with a definitive error -1 (2024-2025 Tizen): otherwise every single
        # thumbnail fetch logs a SYSTEM_FAIL and wastes a round-trip before the
        # direct get_thumbnail fallback below.
        if self._supports_thumbnail_list is not False:
            self._log.debug(
                "Art API: Trying get_thumbnail_list for %s (capability=%s)",
                content_id,
                self._supports_thumbnail_list,
            )
            result = await self._get_thumbnail_via_list(content_id)
            if result:
                if self._supports_thumbnail_list is None:
                    self._log.info(
                        "Art API: TV supports get_thumbnail_list — "
                        "fast path active for this session"
                    )
                    self._supports_thumbnail_list = True
                return result

        self._log.debug("Art API: Using get_thumbnail direct for %s", content_id)

        # Send the request and get connection info. Listen on the content_id
        # alias too: a 0.97 Frame answers over a binary frame keyed by
        # content_id rather than returning conn_info (#311), and _process_binary
        # resolves this same await with the bytes under "binary".
        data = await self._send_art_request(
            {
                "request": "get_thumbnail",
                "content_id": content_id,
                "conn_info": {
                    "d2d_mode": "socket",
                    "connection_id": random.randrange(4 * 1024 * 1024 * 1024),
                    "id": self._get_uuid(),
                },
            },
            timeout=10,
            alias_keys=[content_id],
        )

        if not data:
            self._log.debug("Art API: No response for get_thumbnail either")
            return None

        # API 0.97: the bytes came back on the WebSocket itself, no socket hop.
        binary = data.get("binary")
        if binary is not None:
            self._log.debug(
                "Art API: get_thumbnail for %s returned over a binary frame (%d bytes)",
                content_id,
                len(binary),
            )
            # This TV serves thumbnails over the binary frame, so the
            # get_thumbnail_list probe at the top is pure waste on it: learn to
            # skip straight to this path on the next fetch (same flag the
            # 2024-2025 Tizen direct-only Frames use).
            if self._supports_thumbnail_list is not False:
                self._supports_thumbnail_list = False
            return binary

        # Check for error
        if data.get("event") == "error":
            self._log.debug(
                "Art API: get_thumbnail error: %s",
                _describe_art_error(data.get("error_code")),
            )
            return None

        try:
            conn_info = data.get("conn_info", "{}")
            self._log.debug("Art API: get_thumbnail conn_info: %s", conn_info)

            if isinstance(conn_info, str):
                conn_info = json.loads(conn_info)

            ip = conn_info.get("ip")
            port = conn_info.get("port")

            if not ip or not port:
                self._log.debug("Art API: Invalid conn_info for thumbnail")
                return None

            self._log.debug("Art API: Connecting to %s:%s for thumbnail", ip, port)

            # Connect without SSL - reference implementation doesn't use SSL for thumbnail socket
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(ip, int(port)),
                timeout=10,
            )

            try:
                self._log.debug("Art API: Reading thumbnail header...")
                header_len = int.from_bytes(await reader.readexactly(4), "big")
                self._log.debug("Art API: Header length: %d", header_len)
                header = json.loads(await reader.readexactly(header_len))
                self._log.debug("Art API: Thumbnail header: %s", header)

                thumbnail_len = int(header["fileLength"])
                self._log.debug(
                    "Art API: Reading %d bytes of thumbnail data...", thumbnail_len
                )
                thumbnail_data = await reader.readexactly(thumbnail_len)
                self._log.debug(
                    "Art API: Got thumbnail (%d bytes)", len(thumbnail_data)
                )

                return thumbnail_data
            finally:
                writer.close()
                await writer.wait_closed()

        except asyncio.TimeoutError:
            self._log.debug("Art API: Timeout getting thumbnail")
            return None
        except Exception as ex:
            self._log.debug(
                "Art API: Error getting thumbnail: %s (type: %s)", ex, type(ex).__name__
            )
            return None

    async def _get_thumbnail_via_list(
        self,
        content_id: str,
        retry_count: int = 0,
    ) -> bytes | None:
        """Get thumbnail for a single content via get_thumbnail_list."""
        self._log.debug(
            "Art API: Trying get_thumbnail_list for %s (attempt %d)",
            content_id,
            retry_count + 1,
        )

        data = await self._send_art_request(
            {
                "request": "get_thumbnail_list",
                "content_id_list": [{"content_id": content_id}],
                "conn_info": {
                    "d2d_mode": "socket",
                    "connection_id": random.randrange(4 * 1024 * 1024 * 1024),
                    "id": self._get_uuid(),
                },
            },
            timeout=15,
        )

        if not data:
            self._log.debug("Art API: No response for get_thumbnail_list (single)")
            return None

        # Sur certains modeles, la TV repond directement "event: error" (code -1)
        if data.get("event") == "error":
            self._log.debug(
                "Art API: get_thumbnail_list error for %s: %s",
                content_id,
                _describe_art_error(data.get("error_code")),
            )
            # A definitive error_code -1 means this model doesn't support the
            # list path at all (2024-2025 Tizen) — remember it so we stop
            # re-probing it on every single thumbnail and go straight to the
            # direct get_thumbnail. We only latch on this explicit code, NOT on
            # a missing/transient response (handled above as "no response"),
            # so a TV-not-ready blip can't permanently disable the fast path.
            try:
                if int(data.get("error_code")) == -1:
                    if self._supports_thumbnail_list is not False:
                        self._log.info(
                            "Art API: TV does not support get_thumbnail_list "
                            "(error -1) — using direct get_thumbnail from now on"
                        )
                    self._supports_thumbnail_list = False
            except (TypeError, ValueError):
                pass
            return None

        try:
            conn_info = data.get("conn_info", "{}")
            self._log.debug(
                "Art API: get_thumbnail_list (single) conn_info: %s", conn_info
            )

            if isinstance(conn_info, str):
                conn_info = json.loads(conn_info)

            ip = conn_info.get("ip")
            port = conn_info.get("port")
            secured = conn_info.get("secured", False)

            if not ip or not port:
                self._log.debug("Art API: Invalid conn_info for %s", content_id)
                return None

            ssl_context = _get_ssl_context() if secured else None
            self._log.debug(
                "Art API: Opening connection for %s to %s:%s (ssl=%s)",
                content_id,
                ip,
                port,
                ssl_context is not None,
            )

            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(ip, int(port), ssl=ssl_context),
                    timeout=10,
                )
                self._log.debug("Art API: Connected successfully for %s", content_id)
            except ConnectionResetError as ex:
                self._log.debug("Art API: Connection reset for %s: %s", content_id, ex)
                # Retry uniquement pour le Art Store (SAM-), comme discute
                if content_id.startswith("SAM-") and retry_count < 2:
                    self._log.debug(
                        "Art API: Art Store image %s, retrying after delay", content_id
                    )
                    await asyncio.sleep(0.5 * (retry_count + 1))
                    return await self._get_thumbnail_via_list(
                        content_id, retry_count + 1
                    )
                return None

            try:
                self._log.debug("Art API: Reading thumbnail header for %s", content_id)
                header_len = int.from_bytes(await reader.readexactly(4), "big")
                self._log.debug(
                    "Art API: Header length for %s: %d", content_id, header_len
                )

                header_raw = await reader.readexactly(header_len)
                header = json.loads(header_raw)
                self._log.debug(
                    "Art API: Thumbnail header for %s: %s", content_id, header
                )

                thumbnail_len = int(header["fileLength"])
                self._log.debug(
                    "Art API: Reading %d bytes of thumbnail data for %s",
                    thumbnail_len,
                    content_id,
                )
                thumbnail_data = await reader.readexactly(thumbnail_len)
                self._log.debug(
                    "Art API: Got thumbnail for %s (%d bytes)",
                    content_id,
                    len(thumbnail_data),
                )

                return thumbnail_data

            except asyncio.IncompleteReadError as ex:
                self._log.debug(
                    "Art API: Incomplete read for %s: %d bytes read on %d expected",
                    content_id,
                    len(ex.partial or b""),
                    ex.expected,
                )
                # meme logique : petit retry pour les images SAM- si besoin
                if content_id.startswith("SAM-") and retry_count < 2:
                    self._log.debug(
                        "Art API: Art Store image %s, retrying after incomplete read",
                        content_id,
                    )
                    await asyncio.sleep(0.5 * (retry_count + 1))
                    return await self._get_thumbnail_via_list(
                        content_id, retry_count + 1
                    )
                return None

            finally:
                writer.close()
                await writer.wait_closed()
                self._log.debug(
                    "Art API: Thumbnail connection closed for %s", content_id
                )

        except Exception as ex:
            self._log.debug(
                "Art API: Error in _get_thumbnail_via_list for %s: %s (type: %s)",
                content_id,
                ex,
                type(ex).__name__,
            )
            return None

    async def select_image(
        self,
        content_id: str,
        category: str | None = None,
        show: bool = True,
    ) -> bool:
        """Select and display a piece of art."""
        data = await self._send_art_request(
            {
                "request": "select_image",
                "category_id": category,
                "content_id": content_id,
                "show": show,
            }
        )
        return data is not None

    async def get_artmode(self) -> str | None:
        """Get current art mode status."""
        data = await self._send_art_request({"request": "get_artmode_status"})
        if data is not None:
            value = data.get("value", data.get("status", "off"))
            self.art_mode = value == "on"
            return value
        return None

    def art_mode_broadcast_since(self, mark: int, turn_on: bool) -> bool:
        """Whether an art_mode_changed broadcast to this state arrived after mark.

        ``mark`` is ``art_mode_broadcast_count`` read before a write.
        """
        return self.art_mode_broadcast_at.get(turn_on, 0) > mark

    async def set_artmode(self, mode: str | bool) -> bool:
        """Set art mode on or off."""
        if isinstance(mode, bool):
            mode = "on" if mode else "off"
        desired = mode == "on"

        # Some Frames (e.g. 2020/2021) never echo the matching request_id for
        # set_artmode_status, only the art_mode_changed broadcast — and that
        # broadcast carries no request_id either, so it can't be awaited via
        # _send_art_request's normal matching. Race both: whichever confirms
        # first (the direct response, or the broadcast updating self.art_mode)
        # wins, instead of always paying the full request timeout.
        broadcast_future = asyncio.get_event_loop().create_future()
        self._art_mode_broadcast_waiters.append(broadcast_future)
        request = {"request": "set_artmode_status", "value": mode}
        self.last_set_artmode_sent = False
        request_task = asyncio.ensure_future(
            self._send_art_request(request, bypass_cooldown=True)
        )
        try:
            done, _ = await asyncio.wait(
                {request_task, broadcast_future},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if request_task in done and request_task.result() is not None:
                return True
            if self.art_mode == desired:
                if broadcast_future in done:
                    self._log.debug(
                        "Art API: set_artmode(%s) confirmed early by an "
                        "art_mode_changed broadcast",
                        mode,
                    )
                return True
            if request_task not in done:
                await request_task
                if request_task.result() is not None:
                    return True
        finally:
            if broadcast_future in self._art_mode_broadcast_waiters:
                self._art_mode_broadcast_waiters.remove(broadcast_future)
            if not request_task.done():
                request_task.cancel()
            # _send_art_request_locked stamps request_id only once the channel
            # is open, right before send_json, and drops it again if send_json
            # fails: without it nothing left HA.
            self.last_set_artmode_sent = "request_id" in request

        # Final fallback: the request timed out and no broadcast arrived in
        # time either. If the TV's state already matches what we asked for
        # (e.g. a late broadcast resolved it after our wait above), treat it
        # as success; otherwise report a genuine failure.
        if self.art_mode == desired:
            self._log.debug(
                "Art API: set_artmode(%s) timed out, but an art_mode_changed "
                "broadcast confirms state=%s; treating as success",
                mode,
                self.art_mode,
            )
            return True
        return False

    async def set_favourite(self, content_id: str, status: str = "on") -> bool:
        """Add or remove artwork from favorites."""
        data = await self._send_art_request(
            {
                "request": "change_favorite",
                "content_id": content_id,
                "status": status,
            },
            wait_for_event="favorite_changed",
        )
        return data is not None

    async def get_photo_filter_list(self) -> list[str]:
        """Get list of available photo filters."""
        data = await self._send_art_request({"request": "get_photo_filter_list"})
        if data:
            filter_list = data.get("filter_list", "[]")
            if isinstance(filter_list, str):
                try:
                    return json.loads(filter_list)
                except json.JSONDecodeError:
                    pass
        return []

    async def set_photo_filter(self, content_id: str, filter_id: str) -> bool:
        """Apply a photo filter to artwork."""
        data = await self._send_art_request(
            {
                "request": "set_photo_filter",
                "content_id": content_id,
                "filter_id": filter_id,
            }
        )
        return data is not None

    async def get_matte_list(self, include_color: bool = False) -> list | tuple:
        """Get list of available matte types."""
        data = await self._send_art_request({"request": "get_matte_list"})
        if data:
            matte_types = data.get("matte_list", data.get("matte_type_list", "[]"))
            if isinstance(matte_types, str):
                try:
                    matte_types = json.loads(matte_types)
                except json.JSONDecodeError:
                    matte_types = []

            if include_color:
                matte_colors = data.get("matte_color_list", "[]")
                if isinstance(matte_colors, str):
                    try:
                        matte_colors = json.loads(matte_colors)
                    except json.JSONDecodeError:
                        matte_colors = []
                return matte_types, matte_colors
            return matte_types
        return ([], []) if include_color else []

    async def change_matte(
        self,
        content_id: str,
        matte_id: str | None = None,
        portrait_matte: str | None = None,
    ) -> bool:
        """Set the matte for a piece of artwork."""
        request = {
            "request": "change_matte",
            "content_id": content_id,
            "matte_id": matte_id or "none",
        }
        if portrait_matte:
            request["portrait_matte_id"] = portrait_matte
        data = await self._send_art_request(request)
        return data is not None

    async def get_artmode_settings(self, setting: str = "") -> dict | list | None:
        """Get art mode settings.

        Uses a short-lived cache (~1.5 s) to dedupe the near-simultaneous calls
        issued by the brightness and color-temperature NumberEntities on every
        polling cycle. The cache is invalidated by set_brightness and
        set_color_temperature so a freshly-applied value is read back on the
        very next poll.
        """
        async with self._artmode_settings_lock:
            now = time.time()
            cached = self._artmode_settings_cache
            cache_fresh = (
                cached is not None
                and (now - self._artmode_settings_cache_ts)
                < self._artmode_settings_cache_ttl
            )

            if cache_fresh:
                self._log.debug(
                    "Art API: get_artmode_settings served from cache (age=%.2fs)",
                    now - self._artmode_settings_cache_ts,
                )
                settings_data = cached
            else:
                data = await self._send_art_request({"request": "get_artmode_settings"})
                if not data:
                    return None
                settings_data = data.get("data", "[]")
                if isinstance(settings_data, str):
                    try:
                        settings_data = json.loads(settings_data)
                    except json.JSONDecodeError:
                        return None
                # Only cache lists  -  refuse to cache malformed payloads.
                if isinstance(settings_data, list):
                    self._artmode_settings_cache = settings_data
                    self._artmode_settings_cache_ts = now

        if setting:
            if not isinstance(settings_data, list):
                return None
            return next(
                (item for item in settings_data if item.get("item") == setting),
                None,
            )
        return settings_data

    def _invalidate_artmode_settings_cache(self) -> None:
        """Drop the cached art mode settings.

        Called after a set_* operation so that the next read reflects the new
        value rather than the pre-set snapshot.
        """
        self._artmode_settings_cache = None
        self._artmode_settings_cache_ts = 0.0

    async def get_brightness(self) -> dict | None:
        """Get current art mode brightness.

        On TVs that respond to the dedicated `get_brightness` WebSocket request
        (older firmware on some models), use it directly. On TVs that don't
        respond  -  observed on the QE55LS03DAUXXN / Frame 2024  -  fall back to
        `get_artmode_settings("brightness")`, which returns the same value as
        part of the consolidated settings payload.

        A capability flag is learned at runtime: after the first timeout we
        stop attempting the direct request and go straight to the cached
        consolidated path, eliminating the 5 s timeout cost per poll.
        """
        if self._supports_get_brightness is not False:
            data = await self._send_art_request(
                {"request": "get_brightness"},
                timeout=1.0 if self._supports_get_brightness is None else 2.0,
            )
            if data:
                if self._supports_get_brightness is None:
                    self._log.debug(
                        "Art API: TV supports direct get_brightness, "
                        "enabling fast path"
                    )
                    self._supports_get_brightness = True
                    self._learn_capability("brightness", True)
                return data
            if (
                self._supports_get_brightness is None
                and self._connected
                and self._capability_probe_is_trustworthy()
            ):
                # Connected but silent to this specific request => genuine
                # "unsupported" signal. (A falsy result while NOT connected is a
                # transport issue, not a capability answer — stay unknown.)
                self._log.info(
                    "Art API: TV did not respond to get_brightness within 1 s; "
                    "falling back to get_artmode_settings for future polls"
                )
                self._supports_get_brightness = False
                self._learn_capability("brightness", False)

        return await self.get_artmode_settings("brightness")

    async def set_brightness(self, value: int) -> bool:
        """Set art mode brightness."""
        data = await self._send_art_request(
            {
                "request": "set_brightness",
                "value": value,
            }
        )
        if data is not None:
            self._invalidate_artmode_settings_cache()
        return data is not None

    async def get_color_temperature(self) -> dict | None:
        """Get current art mode color temperature.

        See `get_brightness` for the capability-detection rationale. The Frame
        2024 does not respond to the dedicated request and is served from the
        consolidated `get_artmode_settings` payload.
        """
        if self._supports_get_color_temperature is not False:
            data = await self._send_art_request(
                {"request": "get_color_temperature"},
                timeout=1.0 if self._supports_get_color_temperature is None else 2.0,
            )
            if data:
                if self._supports_get_color_temperature is None:
                    self._log.debug(
                        "Art API: TV supports direct get_color_temperature, "
                        "enabling fast path"
                    )
                    self._supports_get_color_temperature = True
                    self._learn_capability("color_temperature", True)
                return data
            if (
                self._supports_get_color_temperature is None
                and self._connected
                and self._capability_probe_is_trustworthy()
            ):
                self._log.info(
                    "Art API: TV did not respond to get_color_temperature within "
                    "1 s; falling back to get_artmode_settings for future polls"
                )
                self._supports_get_color_temperature = False
                self._learn_capability("color_temperature", False)

        return await self.get_artmode_settings("color_temperature")

    async def set_color_temperature(self, value: int) -> bool:
        """Set art mode color temperature."""
        data = await self._send_art_request(
            {
                "request": "set_color_temperature",
                "value": value,
            }
        )
        if data is not None:
            self._invalidate_artmode_settings_cache()
        return data is not None

    async def set_motion_sensitivity(self, value: str) -> bool:
        """Set Art Mode motion sensitivity.

        Only supported on Frame models with a motion sensor (reported by
        `get_artmode_settings("motion_sensitivity")`). No dedicated get
        request exists; read the current value back via get_artmode_settings.
        """
        data = await self._send_art_request(
            {"request": "set_motion_sensitivity", "value": value}
        )
        if data is not None:
            self._invalidate_artmode_settings_cache()
        return data is not None

    async def set_motion_timer(self, value: str) -> bool:
        """Set Art Mode motion timer.

        Only supported on Frame models that report
        `get_artmode_settings("motion_timer")`. No dedicated get request
        exists; read the current value back via get_artmode_settings.
        """
        data = await self._send_art_request(
            {"request": "set_motion_timer", "value": value}
        )
        if data is not None:
            self._invalidate_artmode_settings_cache()
        return data is not None

    async def set_brightness_sensor_setting(self, value: str) -> bool:
        """Enable/disable the Art Mode ambient brightness sensor.

        Accepts ``"on"`` / ``"off"`` (per the decompiled firmware, calls
        ScreenManagerSetBrightnessSensorEnableValue). Only supported on Frame
        models that report `get_artmode_settings("brightness_sensor_setting")`.
        No dedicated get request exists; read the current value back via
        get_artmode_settings.
        """
        data = await self._send_art_request(
            {"request": "set_brightness_sensor_setting", "value": value}
        )
        if data is not None:
            self._invalidate_artmode_settings_cache()
        return data is not None

    async def get_auto_rotation_status(self) -> dict | None:
        """Get auto rotation settings."""
        return await self._send_art_request({"request": "get_auto_rotation_status"})

    async def set_auto_rotation_status(
        self,
        duration: int = 0,
        shuffle: bool = True,
        category: int = 2,
    ) -> bool:
        """Configure auto rotation."""
        data = await self._send_art_request(
            {
                "request": "set_auto_rotation_status",
                "value": str(duration) if duration > 0 else "off",
                "category_id": f"MY-C000{category}",
                "type": "shuffleslideshow" if shuffle else "slideshow",
            }
        )
        return data is not None

    async def get_slideshow_status(self) -> dict | None:
        """Get slideshow settings."""
        return await self._send_art_request({"request": "get_slideshow_status"})

    async def set_slideshow_status(
        self,
        duration: int = 0,
        shuffle: bool = True,
        category: int = 2,
    ) -> bool:
        """Configure slideshow settings."""
        data = await self._send_art_request(
            {
                "request": "set_slideshow_status",
                "value": str(duration) if duration > 0 else "off",
                "category_id": f"MY-C000{category}",
                "type": "shuffleslideshow" if shuffle else "slideshow",
            }
        )
        return data is not None

    async def detect_slideshow_api(self, timeout: float = 3.0) -> str | None:
        """Detect which slideshow API the TV speaks.

        Samsung split Frame TV slideshow control across firmware
        generations: newer models (2024+) typically respond to
        ``slideshow_status``, while some older Frames respond only to
        the parallel ``auto_rotation_status`` API. Both APIs accept the
        same (duration, shuffle, category) payload.

        Probes both endpoints read-only (no side effects on the TV's
        slideshow state) and returns:
          * ``"slideshow"`` if only ``get_slideshow_status`` returned
            a usable response, or if both did (slideshow is the
            canonical newer-firmware path)
          * ``"auto_rotation"`` if only ``get_auto_rotation_status``
            returned a usable response
          * ``None`` if neither responded — caller should retry later
            and fall back to the default in the meantime
        """

        def _is_usable(response) -> bool:
            return isinstance(response, dict) and "value" in response

        slideshow_ok = False
        try:
            async with asyncio.timeout(timeout):
                slideshow_ok = _is_usable(await self.get_slideshow_status())
        except (asyncio.TimeoutError, Exception) as ex:  # noqa: BLE001
            self._log.debug(
                "Art API: detect_slideshow_api: get_slideshow_status probe failed: %s",
                ex,
            )

        auto_rotation_ok = False
        try:
            async with asyncio.timeout(timeout):
                auto_rotation_ok = _is_usable(await self.get_auto_rotation_status())
        except (asyncio.TimeoutError, Exception) as ex:  # noqa: BLE001
            self._log.debug(
                "Art API: detect_slideshow_api: get_auto_rotation_status probe failed: %s",
                ex,
            )

        if slideshow_ok:
            return "slideshow"
        if auto_rotation_ok:
            return "auto_rotation"
        return None

    async def upload(
        self,
        file: str | bytes,
        matte: str = "shadowbox_polar",
        portrait_matte: str = "shadowbox_polar",
        file_type: str = "png",
        date: str | None = None,
        timeout: int = 30,
        hass=None,
        panel: tuple[int, int] | None = None,
    ) -> str | None:
        """Upload a new image to the TV.

        The image is normalised (baseline JPEG, EXIF stripped, fitted to
        ``panel``) before it is sent — see ``_image_prep``. Every upload path
        funnels through here, so the card, ``art_upload`` and
        ``art_upload_batch`` all get the same treatment.
        """
        self._log.debug("Art API: Starting upload, file type: %s", type(file))

        if isinstance(file, str):
            self._log.debug("Art API: Loading file from path: %s", file)
            file_name, file_extension = os.path.splitext(file)
            file_type = file_extension[1:].lower()
            try:
                # Use executor to avoid blocking the event loop
                def read_file(path):
                    with open(path, "rb") as f:
                        return f.read()

                if hass:
                    file = await hass.async_add_executor_job(read_file, file)
                else:
                    # Fallback for non-HA usage
                    file = await asyncio.get_event_loop().run_in_executor(
                        None, read_file, file
                    )

                self._log.debug("Art API: File loaded, size: %d bytes", len(file))
            except Exception as ex:
                self._log.error("Art API: Failed to read file: %s", ex)
                return None

        # Normalise to something the TV can actually decode. Done here rather
        # than per-caller so the service and batch paths get it too; the batch
        # fingerprints (mtime / perceptual hash) are computed on the SOURCE
        # file before this, so skip-unchanged and dedup are unaffected.
        try:
            if hass is not None:
                file, _suffix = await hass.async_add_executor_job(
                    _image_prep.normalise_for_frame,
                    file,
                    panel or _image_prep.DEFAULT_PANEL,
                )
            else:
                file, _suffix = _image_prep.normalise_for_frame(
                    file, panel or _image_prep.DEFAULT_PANEL
                )
            file_type = "jpg"
        except Exception as ex:  # noqa: BLE001 - unsupported/corrupt image
            self._log.error("Art API: Could not prepare image for upload: %s", ex)
            return None

        file_size = len(file)
        file_type = _detect_wire_type(file, hint=file_type)

        self._log.debug(
            "Art API: Upload - file_size=%d, file_type=%s, matte=%s",
            file_size,
            file_type,
            matte,
        )

        if date is None:
            date = datetime.now().strftime("%Y:%m:%d %H:%M:%S")

        request_id = self._get_uuid()
        self._log.debug(
            "Art API: Sending send_image request, request_id=%s", request_id
        )

        # Hold the timeout circuit breaker off for the whole transfer: on TVs
        # whose get_thumbnail never answers, the coordinator's timeouts would
        # otherwise trip it and force-close the socket while we wait for
        # image_added, killing a perfectly good upload.
        self._upload_in_progress = True
        try:
            return await self._upload_locked(
                file, matte, portrait_matte, file_type, date, timeout, request_id
            )
        finally:
            self._upload_in_progress = False
            self._timeout_streak = 0

    async def _upload_locked(  # noqa: PLR0913 - mirrors upload()'s parameters
        self,
        file: bytes,
        matte: str,
        portrait_matte: str,
        file_type: str,
        date: str,
        timeout: int,
        request_id: str,
    ) -> str | None:
        """Do the send_image handshake and d2d transfer (breaker suspended)."""
        file_size = len(file)

        data = await self._send_art_request(
            {
                "request": "send_image",
                "file_type": file_type,
                "request_id": request_id,
                "id": request_id,
                "conn_info": {
                    "d2d_mode": "socket",
                    "connection_id": random.randrange(4 * 1024 * 1024 * 1024),
                    "id": request_id,
                },
                "image_date": date,
                "matte_id": matte or "none",
                "portrait_matte_id": portrait_matte or "none",
                "file_size": file_size,
            },
            timeout=15,
        )

        self._log.debug("Art API: send_image response: %s", data)

        if not data:
            self._log.error("Art API: No response from send_image request")
            return None

        if data.get("event") == "error":
            error_code = data.get("error_code")
            self._log.error("Art API: send_image error: %s", error_code)
            # A 2019 Frame (art API "0.97") rejects the D2D socket handshake
            # with SYSTEM_FAIL (-1) and takes the image as a binary WebSocket
            # frame instead (#307). Fall back to that transport only on this
            # exact refusal, so every TV that accepts D2D is untouched.
            if str(error_code) == "-1":
                self._log.debug(
                    "Art API: D2D refused with SYSTEM_FAIL; retrying over the "
                    "WS-binary transport"
                )
                return await self._upload_ws_binary(
                    file, matte, file_type, self._get_uuid(), timeout
                )
            return None

        try:
            conn_info = data.get("conn_info", "{}")
            self._log.debug("Art API: Upload conn_info (raw): %s", conn_info)

            if isinstance(conn_info, str):
                conn_info = json.loads(conn_info)

            self._log.debug("Art API: Upload conn_info (parsed): %s", conn_info)

            if not conn_info.get("ip") or not conn_info.get("port"):
                self._log.error("Art API: Invalid conn_info - missing ip or port")
                return None

            header = json.dumps(
                {
                    "num": 0,
                    "total": 1,
                    "fileLength": file_size,
                    "fileName": "dummy",
                    "fileType": file_type,
                    "secKey": conn_info["key"],
                    "version": "0.0.1",
                }
            )

            self._log.debug(
                "Art API: Connecting to %s:%s for upload (secured=%s)",
                conn_info["ip"],
                conn_info["port"],
                conn_info.get("secured"),
            )

            ssl_context = _get_ssl_context() if conn_info.get("secured") else None

            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(
                        conn_info["ip"],
                        int(conn_info["port"]),
                        ssl=ssl_context,
                    ),
                    timeout=10,
                )
                self._log.debug("Art API: Connected for upload")
            except asyncio.TimeoutError:
                self._log.error("Art API: Timeout connecting for upload")
                return None
            except Exception as ex:
                self._log.error("Art API: Failed to connect for upload: %s", ex)
                return None

            try:
                self._log.debug("Art API: Sending header (%d bytes)", len(header))
                writer.write(len(header).to_bytes(4, "big"))
                writer.write(header.encode("ascii"))

                self._log.debug("Art API: Sending file data (%d bytes)", file_size)
                # Send in chunks, draining after each one, so the bytes are
                # actually flushed to the wire as we go. Writing the whole file
                # in one call only fills the asyncio buffer ("sent" in ms), and
                # the immediate TLS close below then races with the buffered
                # data — 2024 Frames (LS03D) end up with a truncated image:
                # entry created, grey thumbnail, and no image_added event.
                # Matches the reference implementation (samsung-tv-ws-api).
                for pos in range(0, file_size, _UPLOAD_CHUNK_SIZE):
                    writer.write(file[pos : pos + _UPLOAD_CHUNK_SIZE])
                    await writer.drain()
                self._log.debug("Art API: Data sent successfully")
            finally:
                writer.close()
                await writer.wait_closed()

            # Wait for image_added event
            self._log.debug(
                "Art API: Waiting for image_added event (timeout=%ds)", timeout
            )
            result = await self._wait_for_response("image_added", timeout=timeout)

            if result:
                content_id = result.get("content_id")
                self._log.info("Art API: Upload successful, content_id=%s", content_id)
                return content_id
            else:
                self._log.error("Art API: No image_added event received")
                return None

        except Exception as ex:
            self._log.error("Art API: Error uploading image: %s", ex)
            import traceback

            self._log.debug("Art API: Upload traceback: %s", traceback.format_exc())
            return None

    async def _upload_ws_binary(
        self,
        file: bytes,
        matte: str,
        file_type: str,
        request_id: str,
        timeout: int,
    ) -> str | None:
        """Upload an image as a binary WebSocket frame (2019 Frames, #307).

        The art app takes the image over the existing WebSocket as one binary
        frame — a 2-byte big-endian header length, the ``art_app_request``
        envelope, then the raw bytes — and replies under the same id with the
        new content_id. The inner payload is exactly the one the #307 reporter
        verified on a 2019 Frame (returning MY_F0096): a richer payload risks
        re-triggering the SYSTEM_FAIL this path exists to avoid, and the frame
        boundary already delimits the bytes, so no file_size is sent.
        """
        ft = file_type.lower()
        wire_type = "JPEG" if ft in ("jpg", "jpeg") else ft.upper()
        envelope = {
            "method": "ms.channel.emit",
            "params": {
                "event": "art_app_request",
                "to": "host",
                "data": json.dumps(
                    {
                        "request": "send_image",
                        "file_type": wire_type,
                        "matte_id": matte or "none",
                        "id": request_id,
                    }
                ),
            },
        }
        try:
            frame = _build_ws_image_frame(envelope, file)
        except ValueError as ex:
            self._log.error("Art API: %s", ex)
            return None

        # Serialize the send with other art requests (one ws, no concurrent
        # writes); the reply is awaited after the lock, like the D2D path.
        async with self._request_lock:
            if not self._connected or not self._ws or self._ws.closed:
                if not await self.open() or not self._ws or self._ws.closed:
                    self._log.error("Art API: not connected for WS-binary upload")
                    return None
            # Register before sending so a fast reply isn't missed.
            self._pending_requests[request_id] = (
                asyncio.get_event_loop().create_future()
            )
            try:
                await self._ws.send_bytes(frame)
            except Exception as ex:  # noqa: BLE001 - surfaced; socket recovers
                self._pending_requests.pop(request_id, None)
                self._connected = False
                self._log.error("Art API: failed to send image frame: %s", ex)
                return None

        self._log.debug(
            "Art API: sent %d-byte WS-binary upload, waiting for reply", len(frame)
        )
        result = await self._wait_for_response(request_id, timeout=timeout)
        if not result:
            self._log.error("Art API: no reply to WS-binary upload")
            return None
        if result.get("event") == "error":
            self._log.error(
                "Art API: WS-binary send_image error: %s",
                _describe_art_error(result.get("error_code")),
            )
            return None
        content_id = result.get("content_id")
        if content_id:
            self._log.info(
                "Art API: Upload successful (WS-binary), content_id=%s", content_id
            )
            return content_id
        self._log.error("Art API: WS-binary upload returned no content_id")
        return None

    async def upload_batch(
        self,
        files: list[str],
        *,
        matte: str = "shadowbox_polar",
        hass=None,
        throttle: float = 2.0,
        sidecar_path: str | None = None,
        dedup_dir: str | None = None,
        panel: tuple[int, int] | None = None,
    ) -> dict:
        """Upload a list of image files, cheaply and idempotently.

        - **Skip-unchanged**: with a ``sidecar_path``, files whose mtime is
          unchanged since the last run are skipped, so re-running a folder
          uploads 0 files instead of duplicating everything on the TV.
        - **Perceptual dedup (opt-in)**: with a ``dedup_dir`` (the already
          downloaded TV thumbnails), a candidate that is a confident near-match
          of an image already on the TV is skipped — robust to the TV's
          re-encode. Strict threshold + "can't fingerprint → never skip" keep
          false skips (a photo silently never uploading) out.
        - **Throttle**: uploads are spaced ``throttle`` seconds apart. Large
          batches (>~25) reliably fail partway through without this pacing.

        All filesystem access runs in the executor. Returns
        ``{"uploaded": [...], "skipped": n, "skipped_duplicate": n,
        "failed": [...]}``.
        """
        from . import _upload_sidecar as sc

        sidecar: dict = {}
        if sidecar_path and hass is not None:
            sidecar = await hass.async_add_executor_job(sc.load_sidecar, sidecar_path)

        # Reference fingerprints of what is already on the TV (opt-in). New
        # uploads are appended so near-identical files within THIS batch are
        # also caught.
        ref_hashes: list[int] = []
        if dedup_dir and hass is not None:
            ref_hashes = await hass.async_add_executor_job(
                sc.fingerprint_dir, dedup_dir
            )

        uploaded: list[str] = []
        failed: list[str] = []
        skipped = 0
        skipped_dup = 0
        did_upload = False

        for path in files:
            name = os.path.basename(path)
            mtime = (
                await hass.async_add_executor_job(sc.safe_mtime, path)
                if hass is not None
                else 0.0
            )
            if sidecar_path and not sc.needs_upload(name, mtime, sidecar):
                skipped += 1
                continue

            candidate_hash = None
            if dedup_dir and hass is not None:
                candidate_hash = await hass.async_add_executor_job(sc.hash_file, path)
                if sc.is_duplicate_hash(candidate_hash, ref_hashes):
                    self._log.info(
                        "Art API: batch upload skipping %s — perceptual "
                        "duplicate of art already on the TV",
                        name,
                    )
                    skipped_dup += 1
                    continue

            # Space out real uploads only (skips are free).
            if did_upload and throttle > 0:
                await asyncio.sleep(throttle)
            did_upload = True

            content_id = await self.upload(path, matte=matte, hass=hass, panel=panel)
            if content_id:
                uploaded.append(content_id)
                sidecar[name] = {"content_id": content_id, "modified": mtime}
                if candidate_hash is not None:
                    ref_hashes.append(candidate_hash)  # catch within-batch dupes
            else:
                failed.append(path)

        if sidecar_path and hass is not None:
            await hass.async_add_executor_job(sc.save_sidecar, sidecar_path, sidecar)

        self._log.info(
            "Art API: batch upload done — %d uploaded, %d skipped, "
            "%d duplicate, %d failed",
            len(uploaded),
            skipped,
            skipped_dup,
            len(failed),
        )
        return {
            "uploaded": uploaded,
            "skipped": skipped,
            "skipped_duplicate": skipped_dup,
            "failed": failed,
        }

    async def delete(self, content_id: str) -> bool:
        """Delete an uploaded piece of art."""
        return await self.delete_list([content_id])

    async def delete_list(self, content_ids: list[str]) -> bool:
        """Delete multiple uploaded pieces of art."""
        content_id_list = [{"content_id": cid} for cid in content_ids]
        await self._send_art_request(
            {
                "request": "delete_image_list",
                "content_id_list": content_id_list,
            }
        )
        # The TV broadcasts image_added when art appears but nothing when it is
        # removed, so tell the listeners ourselves: the Frame Art coordinator
        # caches the library size and would otherwise keep publishing the old
        # count until its refresh interval elapsed.
        self._fire_art_content_event()
        return True

    # ==================== Context Manager ====================

    async def __aenter__(self) -> "SamsungTVAsyncArt":
        """Async context manager entry."""
        await self.open()
        return self

    async def __aexit__(self, *args) -> None:
        """Async context manager exit."""
        await self.close()
