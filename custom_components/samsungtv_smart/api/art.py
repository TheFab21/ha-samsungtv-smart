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
from datetime import datetime
import json
import logging
import os
import random
import ssl
import time
from typing import Any
import uuid

import aiohttp

_LOGGER = logging.getLogger(__name__)

_UPLOAD_THROTTLE_SECONDS = 2.0

try:
    from . import _dedup  # normal package import inside HA
except ImportError:  # test/standalone: conftest.py put the api dir on sys.path
    import _dedup


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

        # Async handling
        self._pending_requests: dict[str, asyncio.Future] = {}
        # set_artmode() waiters: some Frames never echo the matching
        # request_id, only the art_mode_changed broadcast. Resolved as soon
        # as that broadcast arrives, instead of always paying the full
        # request timeout.
        self._art_mode_broadcast_waiters: list[asyncio.Future] = []
        self._recv_task: asyncio.Task | None = None
        self._connected = False

        # Connection failure tracking for exponential backoff (v6.3.5)
        self._connection_failures = 0
        self._max_connection_failures = 3  # Start backoff after 3 failures
        self._backoff_until: float | None = None
        self._last_connection_attempt: float = 0

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

    def register_capability_callback(self, func) -> None:
        """Register a callback fired when a get-capability is first learned.

        Signature: func(flag_name: str, value: bool), where flag_name is
        "brightness" or "color_temperature". Only called for a genuine signal
        (the TV responded -> True, or replied silently while connected -> False),
        never on a transport/connection failure.
        """
        self._capability_callback = func

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

    async def open(self) -> bool:
        """Open WebSocket connection and start listening."""
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
                    "Art API: Too soon since last attempt (%.1fs ago), waiting",
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
                    self._log.warning(
                        "Art API: Port changed from %d to %d "
                        "(likely a firmware update filtered the previous port)",
                        previous_port,
                        alternate_port,
                    )
                self._port = alternate_port
                self._learn_port(alternate_port)
                return True

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
                            # Perfect! Got ready event
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
                await self.close()
                self._port = original_port
                return False

            # Connection successful! Reset failure counter
            if self._connection_failures > 0:
                self._log.info(
                    "Art API: Connection successful, resetting failure counter"
                )
                self._connection_failures = 0

            self._connected = True

            # Start the receive loop
            self._recv_task = asyncio.create_task(self._receive_loop())

            self._log.debug("Art API: Connected and listening on port %d", port)
            return True

        except Exception as ex:
            self._log.debug("Art API: Connection on port %d failed: %s", port, ex)
            await self.close()
            self._port = original_port
            return False

    async def close(self) -> None:
        """Close the connection."""
        self._connected = False

        if self._recv_task:
            self._recv_task.cancel()
            try:
                await self._recv_task
            except asyncio.CancelledError:
                pass
            self._recv_task = None

        if self._ws and not self._ws.closed:
            await self._ws.close()
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
                # open().
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
                # from the first event received.
                self.art_mode = None
                # Fail in-flight pending requests immediately rather than
                # letting their callers block on the per-request timeout;
                # the response will never arrive on this dead channel.
                for future in self._pending_requests.values():
                    if not future.done():
                        future.cancel()
                self._pending_requests.clear()

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

        # Update art mode status from events
        if "artmode_status" in sub_event:
            self.art_mode = data.get("value") == "on"
        elif sub_event == "art_mode_changed":
            self.art_mode = data.get("status") == "on"
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
            return result
        except asyncio.TimeoutError:
            self._log.debug("Art API: Timeout waiting for '%s'", request_key)
            return None
        except asyncio.CancelledError:
            return None
        finally:
            self._pending_requests.pop(request_key, None)

    async def _send_art_request(
        self,
        request_data: dict[str, Any],
        wait_for_event: str | None = None,
        timeout: float = 5.0,
    ) -> dict[str, Any] | None:
        """Send an art API request and wait for response."""
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
        self._pending_requests[request_key] = asyncio.get_event_loop().create_future()

        # Build command
        command = {
            "method": "ms.channel.emit",
            "params": {
                "event": "art_app_request",
                "to": "host",
                "data": json.dumps(request_data),
            },
        }

        try:
            await self._ws.send_json(command)
            self._log.debug(
                "Art API: Sent request '%s'", request_data.get("request", "unknown")
            )

            # Wait for response
            return await self._wait_for_response(request_key, timeout)

        except Exception as ex:
            self._log.debug("Art API: Error sending request: %s", ex)
            self._pending_requests.pop(request_key, None)
            # Mark as disconnected to force reconnection on next request
            self._connected = False
            return None

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

    # ==================== Art API Methods ====================

    async def get_api_version(self) -> str | None:
        """Get the art API version."""
        data = await self._send_art_request({"request": "get_api_version"})
        if not data:
            data = await self._send_art_request({"request": "api_version"})
        return data.get("version") if data else None

    async def available(self, category: str | None = None) -> list:
        """Get list of available artwork.

        category: 'MY-C0002' for my pictures, 'MY-C0004' for favourites, 'MY-C0008' for store
        """
        data = await self._send_art_request(
            {"request": "get_content_list", "category": category},
            timeout=15,
        )
        if not data:
            return []

        content_list = data.get("content_list", "[]")
        if isinstance(content_list, str):
            try:
                content_list = json.loads(content_list)
            except json.JSONDecodeError:
                return []

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

        # Send the request and get connection info
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
        )

        if not data:
            self._log.debug("Art API: No response for get_thumbnail either")
            return None

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
        if data:
            value = data.get("value")
            self.art_mode = value == "on"
            return value
        return None

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
        request_task = asyncio.ensure_future(
            self._send_art_request(
                {
                    "request": "set_artmode_status",
                    "value": mode,
                }
            )
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
            matte_types = data.get("matte_type_list", "[]")
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
            if self._supports_get_brightness is None and self._connected:
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
            if self._supports_get_color_temperature is None and self._connected:
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
    ) -> str | None:
        """Upload a new image to the TV."""
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

        file_size = len(file)
        if file_type == "jpeg":
            file_type = "jpg"

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
            self._log.error("Art API: send_image error: %s", data.get("error_code"))
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
                writer.write(file)
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

    async def upload_batch(
        self,
        files: list[str],
        hass=None,
        throttle: float = _UPLOAD_THROTTLE_SECONDS,
        sidecar_path: str | None = None,
    ) -> list[str]:
        """Upload files with ~2s spacing; skip unchanged files via the sidecar.

        A second run of an unchanged folder uploads 0 files (criterion 12).
        """
        sidecar = _dedup.load_sidecar(sidecar_path) if sidecar_path else {}
        content_ids: list[str] = []
        did_upload = False
        for file in files:
            name = os.path.basename(file)
            mtime = os.path.getmtime(file) if os.path.exists(file) else 0.0
            if sidecar_path and not _dedup.needs_upload(name, mtime, sidecar):
                continue  # unchanged since last run -> skip
            if did_upload:
                await asyncio.sleep(throttle)
            cid = await self.upload(file, hass=hass)
            did_upload = True
            if cid:
                content_ids.append(cid)
                sidecar[name] = {"content_id": cid, "modified": mtime}
        if sidecar_path:
            _dedup.save_sidecar(sidecar_path, sidecar)
        return content_ids

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
        return True

    # ==================== Context Manager ====================

    async def __aenter__(self) -> "SamsungTVAsyncArt":
        """Async context manager entry."""
        await self.open()
        return self

    async def __aexit__(self, *args) -> None:
        """Async context manager exit."""
        await self.close()
