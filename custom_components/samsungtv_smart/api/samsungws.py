"""
SamsungTVWS - Samsung Smart TV WS API wrapper

Copyright (C) 2019 Xchwarze
Copyright (C) 2020 Ollo69

    This library is free software; you can redistribute it and/or
    modify it under the terms of the GNU Lesser General Public
    License as published by the Free Software Foundation; either
    version 2.1 of the License, or (at your option) any later version.

    This library is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
    Lesser General Public License for more details.

    You should have received a copy of the GNU Lesser General Public
    License along with this library; if not, write to the Free Software
    Foundation, Inc., 51 Franklin Street, Fifth Floor,
    Boston, MA  02110-1335  USA

"""

from __future__ import annotations

import base64
from datetime import datetime, timezone
from enum import Enum
import json
import logging
import socket
import ssl
import subprocess
import sys
from threading import Lock, Thread
import time
from typing import Any, Callable
from urllib.parse import urlencode, urljoin
import uuid

import aiohttp
import requests
import websocket
from websocket._exceptions import WebSocketProtocolException

from .shared_read import shared_session_read
from .shortcuts import SamsungTVShortcuts

DEFAULT_POWER_ON_DELAY = 120
MIN_APP_SCAN_INTERVAL = 9
MAX_APP_VALIDITY_SEC = 60
MAX_WS_PING_INTERVAL = 10
PING_TIMEOUT = 3
# After this many consecutive new-token issuances (i.e. the TV rejecting the
# stored token in a row, with no clean reuse in between), stop relaunching the
# remote thread to avoid an endless on-screen authorization-prompt loop.
MAX_CONSECUTIVE_NEW_TOKENS = 5
TYPE_DEEP_LINK = "DEEP_LINK"
TYPE_NATIVE_LAUNCH = "NATIVE_LAUNCH"

_WS_ENDPOINT_REMOTE_CONTROL = "/api/v2/channels/samsung.remote.control"
_WS_ENDPOINT_APP_CONTROL = "/api/v2"
_WS_ENDPOINT_ART = "/api/v2/channels/com.samsung.art-app"
_WS_LOG_NAME = "websocket"

_LOG_PING_PONG = False
_LOGGING = logging.getLogger(__name__)


class _DeviceLoggerAdapter(logging.LoggerAdapter):
    """Prefix every log line with the TV's host so multi-TV logs can be told apart."""

    def process(self, msg, kwargs):
        return f"[{self.extra['host']}] {msg}", kwargs


def _set_ws_logger_level(level: int = logging.CRITICAL) -> None:
    """Set the websocket library logging level."""
    ws_logger = logging.getLogger(_WS_LOG_NAME)
    if ws_logger.level < level:
        ws_logger.setLevel(level)


def _format_rest_url(host: str, append: str = "", port: int = 8001) -> str:
    """Return URL used for rest commands.

    Newer (2024+) Frame TVs only expose the REST API on the SSL port (8002)
    and reset the connection on a plain HTTP request, so the scheme has to
    follow the port instead of being hardcoded to http.
    """
    scheme = "https" if port == 8002 else "http"
    return f"{scheme}://{host}:{port}/api/v2/{append}"


def gen_uuid() -> str:
    """Generate new uuid."""
    return str(uuid.uuid4())


def kill_subprocess(
    process: subprocess.Popen[Any],
) -> None:
    """Force kill a subprocess and wait for it to exit."""
    process.kill()
    process.communicate()
    process.wait()

    del process


def _process_api_response(response, *, raise_error=True):
    """Process response received by TV."""
    try:
        return json.loads(response)
    except json.JSONDecodeError as exc:
        _LOGGING.debug("Failed to parse response from TV. response text: %s", response)
        if raise_error:
            raise ResponseError(
                "Failed to parse response from TV. Maybe feature not supported on this model"
            ) from exc
    return response


def _log_ping_pong(msg, *args):
    """Log ping pong message if enabled."""
    if not _LOG_PING_PONG:
        return
    _LOGGING.debug(msg=msg, args=args)


class Ping:
    """Class for handling Ping to a specific host."""

    def __init__(self, host):
        """Initialize the object."""
        self._ip_address = host
        if sys.platform == "win32":
            self._ping_cmd = ["ping", "-n", "1", "-w", "2000", host]
        else:
            self._ping_cmd = ["ping", "-n", "-q", "-c1", "-W2", host]

    def ping(self, port=0):
        """Check if IP is available using ICMP or trying open a specific port."""
        if port > 0:
            return self._ping_socket(port)
        return self._ping()

    def _ping(self):
        """Send ICMP echo request and return True if success."""
        with subprocess.Popen(
            self._ping_cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
        ) as pinger:
            try:
                pinger.communicate(timeout=1 + PING_TIMEOUT)
                return pinger.returncode == 0
            except subprocess.TimeoutExpired:
                kill_subprocess(pinger)
                return False
            except subprocess.CalledProcessError:
                return False

    def _ping_socket(self, port):
        """Check if port is available and return True if success."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(PING_TIMEOUT - 1)
            return sock.connect_ex((self._ip_address, port)) == 0


class ConnectionFailure(Exception):
    """Error during connection."""


class ResponseError(Exception):
    """Error in response."""


class HttpApiError(Exception):
    """Error using HTTP API."""


class App:
    """Define a TV Application."""

    def __init__(self, app_id, app_name, app_type):
        self.app_id = app_id
        self.app_name = app_name
        self.app_type = app_type


class ArtModeStatus(Enum):
    """Define possible ArtMode status."""

    Unsupported = 0
    Unavailable = 1
    Off = 2
    On = 3


class SamsungTVAsyncRest:
    """Class that implement rest request in async."""

    def __init__(
        self,
        host: str,
        session: aiohttp.ClientSession,
        timeout=None,
        port: int = 8001,
    ) -> None:
        """Initialize the class."""
        self._host = host
        self._log = _DeviceLoggerAdapter(_LOGGING, {"host": host})
        self._port = port
        self._session = session
        self._timeout = None if timeout == 0 else timeout
        self._port_callback: Callable[[int], None] | None = None
        # The port is "proven" the first time it answers a request. A proven
        # port is never swapped/persisted on a transient failure — on a sleeping
        # panel both REST ports flap and the old always-switch rule ping-ponged
        # (57 <-> 58 flips in ~50 h). Only an unproven port is adopted.
        self._proven_port: int | None = None

    def register_port_callback(self, func: Callable[[int], None]) -> None:
        """Register a callback invoked when the working REST port changes.

        Mirrors api.art's port self-heal: lets the caller persist the
        learned port (entry.data) so a misconfigured/changed port doesn't
        keep failing on every restart.
        """
        self._port_callback = func

    def _learn_port(self, port: int) -> None:
        """Switch to the alternate port and report it via the callback."""
        self._port = port
        if self._port_callback is not None:
            self._port_callback(port)

    async def _rest_request_once(
        self, target: str, method: str, port: int
    ) -> dict[str, Any]:
        """Perform a single async rest request against a specific port."""
        url = _format_rest_url(self._host, target, port)

        async def read():
            if method == "POST":
                req = self._session.post(url, timeout=self._timeout, verify_ssl=False)
            elif method == "PUT":
                req = self._session.put(url, timeout=self._timeout, verify_ssl=False)
            elif method == "DELETE":
                req = self._session.delete(url, timeout=self._timeout, verify_ssl=False)
            else:
                req = self._session.get(url, timeout=self._timeout, verify_ssl=False)
            async with req as resp:
                return _process_api_response(await resp.text())

        if method == "GET":
            return await shared_session_read(
                self._session, ("TV_REST", url, self._timeout), read
            )
        return await read()

    async def _rest_request(self, target: str, method: str = "GET") -> dict[str, Any]:
        """Perform async rest request.

        Tries the configured port first, then falls back to the other REST
        port (8001/8002) on a connection failure — some firmwares (e.g.
        2024+ Frame) only serve the REST API on 8002, while older sets may
        only answer on 8001.

        The port is only *adopted* (switched and persisted) while it is
        unproven — genuine discovery of the port this TV actually serves. Once
        a port has answered a request it is proven and kept: a proven port that
        merely fails transiently (a sleeping panel, where both ports flap) is
        NOT swapped, so the request is still served from the alternate for this
        call but the stored port does not ping-pong (57 <-> 58 flips in ~50 h
        before this).
        """
        try:
            result = await self._rest_request_once(target, method, self._port)
        except aiohttp.ClientConnectionError:
            alternate_port = 8001 if self._port == 8002 else 8002
            try:
                result = await self._rest_request_once(target, method, alternate_port)
            except aiohttp.ClientConnectionError as ex:
                raise HttpApiError(
                    "TV unreachable or feature not supported on this model."
                ) from ex
            if self._proven_port != self._port:
                # The configured port has never answered — adopt the one that
                # does. It is persisted below once it answers as the primary.
                self._log.info(
                    "REST API: port %s did not answer for %s, now trying %s "
                    "(not stored until it answers as the primary)",
                    self._port,
                    target or "device info",
                    alternate_port,
                )
                self._port = alternate_port
            else:
                # A proven port is only momentarily down (e.g. TV asleep);
                # serve this call from the alternate but keep the proven port.
                self._log.debug(
                    "REST API: proven port %s momentarily down, served %s from "
                    "%s; keeping the proven port",
                    self._port,
                    target or "device info",
                    alternate_port,
                )
            return result
        # The primary answered: prove and persist it the first time.
        if self._proven_port != self._port:
            self._proven_port = self._port
            self._learn_port(self._port)
        return result

    async def async_rest_device_info(self) -> dict[str, Any]:
        """Get device info using rest api call."""
        self._log.debug("Get device info via rest api")
        return await self._rest_request("")

    async def async_rest_app_status(self, app_id: str) -> dict[str, Any]:
        """Get app status using rest api call."""
        self._log.debug("Get app %s status via rest api", app_id)
        return await self._rest_request("applications/" + app_id)

    async def async_rest_app_run(self, app_id: str) -> dict[str, Any]:
        """Run an app using rest api call."""
        self._log.debug("Run app %s via rest api", app_id)
        return await self._rest_request("applications/" + app_id, "POST")

    async def async_rest_app_close(self, app_id: str) -> dict[str, Any]:
        """Close an app using rest api call."""
        self._log.debug("Close app %s via rest api", app_id)
        return await self._rest_request("applications/" + app_id, "DELETE")

    async def async_rest_app_install(self, app_id: str) -> dict[str, Any]:
        """Install a new app using rest api call."""
        self._log.debug("Install app %s via rest api", app_id)
        return await self._rest_request("applications/" + app_id, "PUT")


class SamsungTVWS:
    """Class to manage websocket communication with tizen TV."""

    def __init__(
        self,
        host: str,
        *,
        token: str | None = None,
        token_file: str | None = None,
        port: int | None = 8001,
        timeout: int | None = None,
        key_press_delay: float | None = 1.0,
        name: str | None = "SamsungTvRemote",
        app_list: dict | None = None,
        ping_port: int | None = 0,
    ):
        """Initialize SamsungTVWS object."""
        self.host = host
        self._log = _DeviceLoggerAdapter(_LOGGING, {"host": host})
        self.token = token
        self.token_file = token_file
        self.port = port or 8001
        self.timeout = None if timeout == 0 else timeout
        self.key_press_delay = 1.0 if key_press_delay is None else key_press_delay
        self.name = name or "SamsungTvRemote"
        self._app_list = dict(app_list) if app_list else None
        self._ping_port = ping_port or 0

        self.connection = None
        self._artmode_status = ArtModeStatus.Unsupported
        self._power_on_requested = False
        self._power_on_requested_time = datetime.min.replace(tzinfo=timezone.utc)
        self._power_on_delay = DEFAULT_POWER_ON_DELAY
        self._power_on_artmode = False

        self._installed_app = {}
        # App ids the TV answered "404 Not found" for: not installed, so we stop
        # re-querying them every scan (otherwise a single missing app in the
        # configured app_list is polled forever — observed 1900+ times in one
        # session). Cleared whenever the TV reports a fresh installed-app list.
        self._app_not_found: set[str] = set()
        self._running_apps: dict[str, datetime] = {}
        self._running_app: str | None = None
        self._running_app_changed: bool | None = None
        self._last_running_scan = datetime.now(timezone.utc)
        self._app_type = {}
        self._sync_lock = Lock()
        self._last_app_scan = datetime.min.replace(tzinfo=timezone.utc)

        self._ping_thread = None
        self._ping_thread_run = False

        self._ws_remote = None
        self._client_remote = None
        self._last_ping = datetime.min.replace(tzinfo=timezone.utc)
        self._is_connected = False

        self._ws_control = None
        self._client_control = None
        self._last_control_ping = datetime.min.replace(tzinfo=timezone.utc)
        self._is_control_connected = False

        self._ws_art = None
        self._client_art = None
        self._last_art_ping = datetime.min.replace(tzinfo=timezone.utc)
        self._client_art_supported = 2
        self._art_thread_disabled = (
            False  # Set True when async Art API (art.py) is active
        )

        self._ping = Ping(self.host)
        self._status_callback = None
        self._new_token_callback = None
        # Auth-failure guard: a Samsung TV issues a brand-new token (and re-arms
        # the on-screen authorization prompt) whenever it receives a connection
        # without a valid token. If the stored token is bad, the reconnect loop
        # would re-prompt forever. We count consecutive new-token issuances; a
        # clean reconnect that REUSES the token resets the counter. Past the
        # threshold we stop relaunching the remote thread and notify the caller.
        self._auth_error_callback = None
        self._auth_recovered_callback = None
        self._consecutive_new_tokens = 0
        self._auth_blocked = False
        # Port self-heal for the remote channel. A TokenAuthSupport TV that ends
        # up configured on the unencrypted 8001 channel rejects every connect
        # with ms.channel.unauthorized and never shows an on-screen prompt — the
        # prompt + token flow only exists on the secure 8002 channel. Before
        # pausing reconnection for good we flip 8001 -> 8002 once and retry.
        self._port_changed_callback = None
        self._tried_alt_port = False

    @property
    def auth_blocked(self) -> bool:
        """True when repeated token rejections have paused reconnection."""
        return self._auth_blocked

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, exc_traceback):
        self.close()

    @staticmethod
    def ping_probe(host):
        """Try to ping device and return usable port."""
        ping = Ping(host)
        for port in (9197, 0):
            try:
                if ping.ping(port):
                    return port
            except Exception:  # pylint: disable=broad-except
                _LOGGING.debug("Failed to ping device using port %s", port)

        return None

    @staticmethod
    def _serialize_string(string):
        if isinstance(string, str):
            string = str.encode(string)
        return base64.b64encode(string).decode("utf-8")

    def _is_ssl_connection(self):
        return self.port == 8002

    def _format_websocket_url(self, path, is_ssl=False, use_token=True):
        scheme = "wss" if is_ssl else "ws"

        base_uri = f"{scheme}://{self.host}:{self.port}"
        ws_uri = urljoin(base_uri, path)
        query = {"name": self._serialize_string(self.name)}
        if is_ssl and use_token:
            if token := self._get_token():
                query["token"] = token
        ws_query = urlencode(query)
        return f"{ws_uri}?{ws_query}"

    def set_ping_port(self, port: int):
        """Set a new ping port."""
        self._ping_port = port

    def update_app_list(self, app_list: dict | None):
        """Update application list."""
        self._app_list = dict(app_list) if app_list else None

    def register_new_token_callback(self, func):
        """Register a callback function."""
        self._new_token_callback = func

    def register_auth_error_callback(self, func):
        """Register a callback fired when the TV keeps rejecting the token.

        Called (on the WS thread) once reconnection is paused after
        ``MAX_CONSECUTIVE_NEW_TOKENS`` consecutive rejections.
        """
        self._auth_error_callback = func

    def register_auth_recovered_callback(self, func):
        """Register a callback fired when a connection authorizes cleanly again."""
        self._auth_recovered_callback = func

    def register_port_changed_callback(self, func):
        """Register a callback fired when the WS port self-heals (8001<->8002).

        Receives the new port so the caller can persist it to the config entry,
        so the next restart connects on the working port directly.
        """
        self._port_changed_callback = func

    def _try_alternate_port(self) -> bool:
        """Flip the remote channel from the 8001 to the 8002 port once.

        Returns True if the port was switched (caller should retry instead of
        tripping the auth guard). A TokenAuth TV stuck on the unencrypted 8001
        channel rejects every connect with no on-screen prompt; the secure 8002
        channel is where the prompt + token actually happen. We only flip from
        8001 -> 8002 and only once per session: on a genuine 2024 model (whose
        8002 is filtered) the 8002 attempt simply fails and the guard trips as
        before, just one port-flip later — no ping-pong.
        """
        if self.port != 8001 or self._tried_alt_port:
            return False
        # Expected, self-correcting transition on Frames whose token flow only
        # lives on the secure channel: INFO, not WARNING. Only the case where
        # BOTH ports reject (below) is an actionable problem.
        self._log.info(
            "TV %s rejected the unencrypted 8001 remote channel repeatedly "
            "(no on-screen prompt) — switching to the secure 8002 channel",
            self.host,
        )
        self.port = 8002
        self._tried_alt_port = True
        self._consecutive_new_tokens = 0
        if self._port_changed_callback is not None:
            self._port_changed_callback(8002)
        return True

    def register_status_callback(self, func):
        """Register callback function used on status change."""
        self._status_callback = func

    def _bump_auth_failure(self, reason: str) -> None:
        """Count one more rejected-connection event and trip the auth guard.

        Shared by every code path that signals a rejected token (new-token
        issuance on ``ms.channel.connect``, ``ms.error`` "No Authorized", and
        the bare ``ms.channel.unauthorized`` event) so they all feed the same
        ``MAX_CONSECUTIVE_NEW_TOKENS`` threshold instead of each needing its
        own counter.
        """
        # A rejected channel is not a usable connection, whatever a previous
        # connect left behind. Saying so here is what lets a caller tell a key
        # that was merely written to the socket from one the TV accepted:
        # send_key() returns True as soon as the frame goes out, while this
        # event arrives asynchronously on the socket thread and never reaches
        # that result (a power-on to a rejecting Frame therefore looked like a
        # success and skipped the configured wake method entirely). The
        # ms.channel.connect path sets _is_connected back to True right after
        # calling this, so a new-token issuance is unaffected.
        self._is_connected = False
        self._consecutive_new_tokens += 1
        if (
            not self._auth_blocked
            and self._consecutive_new_tokens >= MAX_CONSECUTIVE_NEW_TOKENS
        ):
            # Before giving up, a TV stuck on the unencrypted 8001 channel may
            # just need the secure 8002 channel (where the prompt + token live).
            # Flip once and let the reconnect retry there instead of pausing.
            if self._try_alternate_port():
                return
            self._auth_blocked = True
            if self.port == 8002 and self._tried_alt_port:
                # We already flipped 8001 -> 8002 and the secure channel is
                # ALSO rejecting. Per the decompiled msf-server
                # (notes/QN55LS03FAFXZA/PORTS.md), some firmwares fail to bring
                # up the 8002 TLS vhost at all ("can't create vhost for '8002'
                # port") — observed on ~2020 Frames — so neither the plain 8001
                # nor the secure 8002 channel can complete the token handshake.
                # Surface that explicitly instead of a generic "re-pair", which
                # won't help when the TV simply has no working secure channel.
                self._log.warning(
                    "TV %s rejected both the 8001 and the secure 8002 remote "
                    "channels (%s) — this TV may not expose a working TLS "
                    "(8002) channel (firmware failed to create the vhost, seen "
                    "on some 2020 Frames); remote control over this channel "
                    "cannot authorize. Pausing remote reconnection.",
                    self.host,
                    reason,
                )
            else:
                self._log.warning(
                    "TV %s repeatedly rejected the connection (%s) — pausing "
                    "remote reconnection; re-pair required",
                    self.host,
                    reason,
                )
            if self._auth_error_callback is not None:
                self._auth_error_callback()

    def unregister_status_callback(self):
        """Unregister callback function used on status change."""
        self._status_callback = None

    def _get_token(self):
        """Get current token.

        Only ever returns a non-empty ``str``. The stored token must be a plain
        PAT string; if it was polluted with non-string data (e.g. an OAuth token
        dict written under the same config key), we return ``None`` rather than
        urlencode a dict into the WebSocket URL — which the TV rejects, causing
        an endless new-token / authorization-prompt loop.
        """
        if self.token_file is not None:
            try:
                with open(self.token_file, "r", encoding="utf-8") as token_file:
                    return token_file.readline()
            except Exception as exc:  # pylint: disable=broad-except
                self._log.error("Failed to read TV token file: %s", str(exc))
                return ""
        if isinstance(self.token, str) and self.token:
            return self.token
        if self.token is not None:
            self._log.warning(
                "Ignoring non-string local token (%s) — connecting without it",
                type(self.token).__name__,
            )
        return None

    def _set_token(self, token):
        """Save new token."""
        self._log.debug("New token %s", token)
        if self.token_file is not None:
            self._log.debug("Save new token to file %s", self.token_file)
            with open(self.token_file, "w", encoding="utf-8") as token_file:
                token_file.write(token)
            return

        if self.token is not None and self.token == token:
            return
        self.token = token
        if self._new_token_callback is not None:
            self._new_token_callback()

    def _ws_send(
        self,
        command,
        key_press_delay=None,
        *,
        use_control=False,
        ws_socket=None,
        raise_on_closed=False,
    ):
        """Send a command using the appropriate websocket."""
        using_remote = False
        if not use_control:
            if self._ws_remote:
                connection = self._ws_remote
                using_remote = True
            else:
                connection = self.open()
        elif ws_socket:
            connection = ws_socket
        else:
            self._start_client(start_all=True)
            return False

        payload = json.dumps(command)
        try:
            connection.send(payload)
        except (
            websocket.WebSocketConnectionClosedException,
            WebSocketProtocolException,
        ) as exc:
            # Gérer à la fois les fermetures normales et les codes 1005 invalides
            if isinstance(exc, WebSocketProtocolException):
                error_msg = str(exc)
                if "1005" in error_msg:
                    self._log.warning(
                        "_ws_send: Samsung TV sent invalid close code 1005, "
                        "connection will be reset"
                    )
            if raise_on_closed:
                raise
            self._log.warning("_ws_send: connection is closed, send command failed")
            if using_remote or use_control:
                self._log.info("_ws_send: try to restart communication threads")
                self._start_client(start_all=use_control)
            return False
        except websocket.WebSocketTimeoutException:
            self._log.warning("_ws_send: timeout error sending command %s", payload)
            return False

        if using_remote:
            # we consider a message sent valid as a ping
            self._last_ping = datetime.now(timezone.utc)

        if key_press_delay is None:
            if self.key_press_delay > 0:
                time.sleep(self.key_press_delay)
        elif key_press_delay > 0:
            time.sleep(key_press_delay)

        # Note: On ne ferme PAS systématiquement self.connection ici car cela peut
        # causer des problèmes si plusieurs commandes sont envoyées en séquence rapide.
        # La fermeture sera gérée par le garbage collector ou explicitement par le code appelant.
        # Pour éviter la saturation, il faut plutôt espacer les commandes dans les automatisations.

        return True

    def _rest_request(self, target, method="GET"):
        """Send a rest command using http protocol."""
        url = _format_rest_url(self.host, target, self.port)
        verify = not self._is_ssl_connection()
        try:
            if method == "POST":
                response = requests.post(url, timeout=self.timeout, verify=verify)
            elif method == "PUT":
                response = requests.put(url, timeout=self.timeout, verify=verify)
            elif method == "DELETE":
                response = requests.delete(url, timeout=self.timeout, verify=verify)
            else:
                response = requests.get(url, timeout=self.timeout, verify=verify)
        except requests.ConnectionError as exc:
            raise HttpApiError(
                "TV unreachable or feature not supported on this model."
            ) from exc
        return _process_api_response(response.text, raise_error=False)

    def _check_conn_id(self, resp_data):
        """Check if returned connection id from WS server is valid for this TV."""
        if not resp_data:
            return False

        msg_id = resp_data.get("id")
        if not msg_id:
            return False

        clients_info = resp_data.get("clients")
        for client in clients_info:
            device_name = client.get("deviceName")
            if device_name:
                if device_name == self._serialize_string(self.name):
                    conn_id = client.get("id", "")
                    if conn_id == msg_id:
                        return True
        return False

    @staticmethod
    def _run_forever(
        ws_app: websocket.WebSocketApp, *, sslopt: dict = None, ping_interval: int = 0
    ) -> None:
        """Call method run_forever changing library log level before."""
        _set_ws_logger_level()
        ws_app.run_forever(sslopt=sslopt, ping_interval=ping_interval)

    def _client_remote_thread(self):
        """Start the main client WS thread that connect to the remote TV."""
        if self._ws_remote:
            return

        is_ssl = self._is_ssl_connection()
        url = self._format_websocket_url(_WS_ENDPOINT_REMOTE_CONTROL, is_ssl=is_ssl)
        sslopt = {"cert_reqs": ssl.CERT_NONE} if is_ssl else {}

        websocket.setdefaulttimeout(self.timeout)
        self._ws_remote = websocket.WebSocketApp(
            url,
            on_message=self._on_message_remote,
            on_ping=self._on_ping_remote,
        )
        self._log.debug("Thread SamsungRemote started")
        # Réduire le ping_interval de 3600s (1h) à 300s (5min) pour détecter plus rapidement
        # les connexions mortes et éviter la saturation SmartThings.
        self._run_forever(self._ws_remote, sslopt=sslopt, ping_interval=300)
        self._is_connected = False
        if self._status_callback is not None:
            self._status_callback()
        if self._ws_art:
            self._ws_art.close()
        if self._ws_control:
            self._ws_control.close()
        if self._ws_remote:
            self._ws_remote.close()
        self._ws_remote = None
        self._log.debug("Thread SamsungRemote terminated")

    def _on_ping_remote(self, _, payload):
        """Manage ping message received by remote WS connection."""
        _log_ping_pong("Received WS remote ping %s, sending pong", payload)
        self._last_ping = datetime.now(timezone.utc)
        if self._ws_remote.sock:
            try:
                self._ws_remote.sock.pong(payload)
            except Exception as ex:  # pylint: disable=broad-except
                self._log.warning("WS remote send_pong failed, %s", ex)

    def _on_message_remote(self, _, message):
        """Manage messages received by remote WS connection."""
        response = _process_api_response(message)
        self._log.debug(response)
        event = response.get("event")
        if not event:
            return

        # we consider a message valid as a ping
        self._last_ping = datetime.now(timezone.utc)

        if event == "ms.channel.connect":
            conn_data = response.get("data")
            if not self._check_conn_id(conn_data):
                return
            self._log.debug("Message remote: received connect")
            token = conn_data.get("token")
            # Some firmwares echo back the SAME token on every successful
            # connect as a confirmation, not just when issuing a genuinely new
            # one. Only count it as a "new" (i.e. rejected-old-token) issuance
            # when it actually differs from what we have stored — otherwise
            # normal periodic reconnects with an accepted token would
            # themselves trip the 5-in-a-row threshold below and pause
            # reconnection of the control channel (apps) for good.
            token_changed = bool(token) and token != self.token
            if token:
                self._set_token(token)
            if token_changed:
                self._bump_auth_failure("new token issued")
            else:
                # Connected with no token, or the same token we already had =>
                # the stored token was accepted. Always signal recovery
                # (dismiss is idempotent) so a notification left over from a
                # previous, now-reloaded instance is cleared too.
                self._consecutive_new_tokens = 0
                self._auth_blocked = False
                if self._auth_recovered_callback is not None:
                    self._auth_recovered_callback()
            self._is_connected = True
            self._request_apps_list()
            self._start_client(start_all=True)
            if self._status_callback is not None:
                self._status_callback()
        elif event == "ms.error":
            data = response.get("data") or {}
            self._log.debug("Message remote: error %s", data)
            if "authoriz" in str(data.get("message", "")).lower():
                # "No Authorized": the TV refused this connection's token.
                self._bump_auth_failure("'No Authorized' (ms.error)")
        elif event == "ms.channel.unauthorized":
            # Some firmwares reject the connection with this bare event
            # instead of ms.channel.connect (new token) or ms.error
            # ("No Authorized") — neither of which fires on this path, so
            # without this branch the reconnect loop hammered the TV every
            # ~1s forever instead of ever tripping the auth-blocked guard.
            self._bump_auth_failure("ms.channel.unauthorized")
        elif event == "ed.installedApp.get":
            self._log.debug("Message remote: received installedApp")
            self._handle_installed_app(response)
        elif event == "ed.edenTV.update":
            self._log.debug("Message remote: received edenTV")
            self._get_running_app(force_scan=True)

    def _request_apps_list(self):
        """Request to the TV the list of installed apps."""
        self._log.debug("Request app list")
        self._ws_send(
            {
                "method": "ms.channel.emit",
                "params": {"event": "ed.installedApp.get", "to": "host"},
            },
            key_press_delay=0,
        )

    def _handle_installed_app(self, response):
        """Manage the list of installed apps received from the TV."""
        list_app = response.get("data", {}).get("data")
        installed_app = {}
        for app_info in list_app:
            app_id = app_info["appId"]
            self._log.debug("Found app: %s", app_id)
            app = App(app_id, app_info["name"], app_info["app_type"])
            installed_app[app_id] = app
        self._installed_app = installed_app
        # Fresh authoritative list from the TV — re-evaluate previously
        # not-found apps (one may have been installed since).
        self._app_not_found.clear()

    def _client_control_thread(self):
        """Start the client control WS thread used to manage running apps."""
        if self._ws_control:
            return

        is_ssl = self._is_ssl_connection()
        url = self._format_websocket_url(
            _WS_ENDPOINT_APP_CONTROL, is_ssl=is_ssl, use_token=True
        )
        sslopt = {"cert_reqs": ssl.CERT_NONE} if is_ssl else {}

        websocket.setdefaulttimeout(self.timeout)
        self._ws_control = websocket.WebSocketApp(
            url,
            on_message=self._on_message_control,
            on_ping=self._on_ping_control,
        )
        self._log.debug("Thread SamsungControl started")
        # we set ping interval (1 hour) only to enable multi-threading mode
        # on socket. TV do not answer to ping but send ping to client
        self._run_forever(self._ws_control, sslopt=sslopt, ping_interval=3600)
        self._is_control_connected = False
        if self._ws_control:
            self._ws_control.close()
        self._ws_control = None
        self._running_app_changed = None
        self._log.debug("Thread SamsungControl terminated")

    def _on_ping_control(self, _, payload):
        """Manage ping message received by control WS channel."""
        _log_ping_pong("Received WS control ping %s, sending pong", payload)
        self._last_control_ping = datetime.now(timezone.utc)
        if self._ws_control.sock:
            try:
                self._ws_control.sock.pong(payload)
            except Exception as ex:  # pylint: disable=broad-except
                self._log.warning("WS control send_pong failed, %s", ex)

    def _on_message_control(self, _, message):
        """Manage messages received by control WS channel."""
        response = _process_api_response(message)
        self._log.debug(response)
        result = response.get("result")
        if result:
            self._set_running_app(response)
            return
        error = response.get("error")
        if error:
            self._manage_control_err(response)
            return
        event = response.get("event")
        if not event:
            return
        if event == "ms.channel.connect":
            conn_data = response.get("data")
            if not self._check_conn_id(conn_data):
                return
            self._log.debug("Message control: received connect")
            self._is_control_connected = True
            self._get_running_app()
        elif event == "ed.installedApp.get":
            self._log.debug("Message control: received installedApp")
            self._handle_installed_app(response)

    def _set_running_app(self, response):
        """Set the current running app based on received message."""
        if not (app_id := response.get("id")):
            return
        if (result := response.get("result")) is None:
            return
        if isinstance(result, bool):
            is_running = result
        elif (is_running := result.get("visible")) is None:
            return

        call_time = datetime.now(timezone.utc)
        self._last_running_scan = call_time
        self._running_apps[app_id] = call_time
        if self._running_app:
            if is_running and app_id != self._running_app:
                self._log.debug("app running: %s", app_id)
                self._running_app = app_id
                self._running_app_changed = True
            elif not is_running and app_id == self._running_app:
                self._log.debug("app stopped: %s", app_id)
                self._running_app = None
                self._running_app_changed = True
        elif is_running:
            self._log.debug("app running: %s", app_id)
            self._running_app = app_id
            self._running_app_changed = True

        if self._running_app_changed is None:
            self._running_app_changed = True

    def _manage_control_err(self, response):
        """Manage errors from control WS channel."""
        app_id = response.get("id")
        if not app_id:
            return
        error_code = response.get("error", {}).get("code", 0)
        if error_code == 404:  # Not found error
            # Remember this app is not installed so we stop polling it every
            # scan. Without this a single missing app in the configured
            # app_list is queried on every cycle forever (TVs that never report
            # an installed-app list, e.g. some 2020 Frames, hit this hard).
            if app_id not in self._app_not_found:
                self._app_not_found.add(app_id)
                self._log.debug(
                    "App ID %s not found on TV — skipping it until next "
                    "installed-app refresh",
                    app_id,
                )

    def _get_app_status(self, app_id, app_type):
        """Send a message to control WS channel to get the app status."""
        if app_id in self._app_not_found:
            # Already known not installed — don't re-query (and don't log).
            return

        self._log.debug("Get app status: AppID: %s, AppType: %s", app_id, app_type)

        if not (self._ws_control and self._is_control_connected):
            return

        if app_type == 4:  # app type 4 always return not found error
            return

        method = "ms.application.get"
        try:
            self._ws_send(
                {
                    "id": app_id,
                    "method": method,
                    "params": {"id": app_id},
                },
                key_press_delay=0,
                use_control=True,
                ws_socket=self._ws_control,
                raise_on_closed=True,
            )
        except websocket.WebSocketConnectionClosedException:
            self._log.debug("Get app status aborted: connection closed")

    def _client_art_thread(self):
        """Start the client art WS thread used to manage art mode status."""
        if self._ws_art:
            return

        is_ssl = self._is_ssl_connection()
        # use_token=False: the art-app channel is unauthenticated; sending the
        # remote-control token makes 2024 Frame TVs hold the handshake and
        # close with ms.channel.timeOut instead of ms.channel.connect.
        url = self._format_websocket_url(
            _WS_ENDPOINT_ART, is_ssl=is_ssl, use_token=False
        )
        sslopt = {"cert_reqs": ssl.CERT_NONE} if is_ssl else {}

        websocket.setdefaulttimeout(self.timeout)
        self._ws_art = websocket.WebSocketApp(
            url,
            on_message=self._on_message_art,
            on_ping=self._on_ping_art,
        )
        self._log.debug("Thread SamsungArt started")
        # we set ping interval (1 hour) only to enable multi-threading mode
        # on socket. TV do not answer to ping but send ping to client
        self._run_forever(self._ws_art, sslopt=sslopt, ping_interval=3600)
        if self._ws_art:
            self._ws_art.close()
        self._ws_art = None
        self._log.debug("Thread SamsungArt terminated")

    def _on_ping_art(self, _, payload):
        """Manage ping message received by art WS channel."""
        _log_ping_pong("Received WS art ping %s, sending pong", payload)
        self._last_art_ping = datetime.now(timezone.utc)
        if self._ws_art.sock:
            try:
                self._ws_art.sock.pong(payload)
            except Exception as ex:  # pylint: disable=broad-except
                self._log.warning("WS art send_pong failed: %s", ex)

    def _on_message_art(self, _, message):
        """Manage messages received by art WS channel."""
        response = _process_api_response(message)
        self._log.debug(response)
        event = response.get("event")
        if not event:
            return

        # we consider a message valid as a ping
        self._last_art_ping = datetime.now(timezone.utc)

        if event == "ms.channel.connect":
            conn_data = response.get("data")
            if not self._check_conn_id(conn_data):
                return
            self._log.debug("Message art: received connect")
            self._client_art_supported = 1
        elif event == "ms.channel.ready":
            self._log.debug("Message art: channel ready")
            self._get_artmode_status()
        elif event == "d2d_service_message":
            self._log.debug("Message art: d2d message")
            self._handle_artmode_status(response)

    def _get_artmode_status(self):
        """Detect current art mode based on received message."""
        self._log.debug("Sending get_art_status")
        msg_data = {
            "request": "get_artmode_status",
            "id": gen_uuid(),
        }
        self._ws_send(
            {
                "method": "ms.channel.emit",
                "params": {
                    "data": json.dumps(msg_data),
                    "to": "host",
                    "event": "art_app_request",
                },
            },
            key_press_delay=0,
            use_control=True,
            ws_socket=self._ws_art,
        )

    def _handle_artmode_status(self, response):
        """Handle received art mode status."""
        data_str = response.get("data")
        if not data_str:
            return
        data = _process_api_response(data_str)
        event = data.get("event", "")
        if event == "art_mode_changed":
            status = data.get("status", "")
            if status == "on":
                artmode_status = ArtModeStatus.On
            else:
                artmode_status = ArtModeStatus.Off
        elif event == "artmode_status":
            value = data.get("value", "")
            if value == "on":
                artmode_status = ArtModeStatus.On
            else:
                artmode_status = ArtModeStatus.Off
        elif event == "go_to_standby":
            artmode_status = ArtModeStatus.Unavailable
        elif event == "wakeup":
            self._get_artmode_status()
            return
        else:
            # Unknown message
            return

        if self._power_on_requested and artmode_status != ArtModeStatus.Unavailable:
            if artmode_status == ArtModeStatus.On and not self._power_on_artmode:
                self.send_key("KEY_POWER", key_press_delay=0)
            elif artmode_status == ArtModeStatus.Off and self._power_on_artmode:
                self.send_key("KEY_POWER", key_press_delay=0)
            self._power_on_requested = False

        self._artmode_status = artmode_status

    @property
    def is_connected(self):
        """Return if WS connection is open."""
        return self._is_connected

    @property
    def artmode_status(self):
        """Return current art mode status."""
        return self._artmode_status

    @property
    def installed_app(self):
        """Return a list of installed apps."""
        return self._installed_app

    @property
    def running_app(self):
        """Return current running app."""
        return self._running_app

    def is_app_running(
        self, app_id: str, *, max_age: float | None = None
    ) -> bool | None:
        """Return whether app_id is running, or None without a usable reading."""
        if max_age is not None:
            seen = self._running_apps.get(app_id)
            if seen is None or not (
                0 <= (datetime.now(timezone.utc) - seen).total_seconds() <= max_age
            ):
                return None
        if app_id == self._running_app:
            return True
        if (last_seen := self._running_apps.get(app_id)) is None:
            return None
        app_age = (self._last_running_scan - last_seen).total_seconds()
        if app_age >= MAX_APP_VALIDITY_SEC:
            self._running_apps.pop(app_id)
            return None
        return False

    def _ping_thread_method(self):
        """Start the ping thread that check the TV status."""
        ping = Ping(self.host)
        while self._ping_thread_run:
            if ping.ping(self._ping_port):
                if not self._is_connected:
                    self._start_client()
                    # Notify HA immediately when TV comes back online
                    if self._status_callback:
                        self._status_callback()
                else:
                    self._check_remote()
            else:
                if self._is_connected:
                    self.stop_client()
                    # Notify HA immediately when TV goes offline
                    if self._status_callback:
                        self._status_callback()
            time.sleep(1.0)

    def _check_remote(self):
        """Check current remote thread status."""
        call_time = datetime.now(timezone.utc)
        if self._ws_remote:
            difference = (call_time - self._last_ping).total_seconds()
            if difference >= MAX_WS_PING_INTERVAL:
                self.stop_client()
                if self._artmode_status != ArtModeStatus.Unsupported:
                    self._artmode_status = ArtModeStatus.Unavailable
            else:
                self._check_art_mode()
                self._get_running_app()
                self._notify_app_change()

        if self._power_on_requested:
            difference = (call_time - self._power_on_requested_time).total_seconds()
            if difference > self._power_on_delay:
                self._power_on_requested = False

    def _check_art_mode(self):
        """Check current art mode and start related control thread if required."""
        if self._artmode_status == ArtModeStatus.Unsupported:
            return
        if self._ws_art:
            difference = (
                datetime.now(timezone.utc) - self._last_art_ping
            ).total_seconds()
            if difference >= MAX_WS_PING_INTERVAL:
                self._artmode_status = ArtModeStatus.Unavailable
                self._ws_art.close()
        elif self._ws_remote:
            self._start_client(start_all=True)

    def _notify_app_change(self):
        """Notify that running app is changed."""
        if not self._running_app_changed:
            return
        if not self._status_callback:
            self._running_app_changed = False
            return
        last_change = (
            datetime.now(timezone.utc) - self._last_running_scan
        ).total_seconds()
        if last_change >= 2:  # delay 2 seconds before calling
            self._running_app_changed = False
            self._status_callback()

    def _get_running_app(self, *, force_scan=False):
        """Query current running app using control channel."""
        if not (self._ws_control and self._is_control_connected):
            return

        scan_interval = 1 if force_scan else MIN_APP_SCAN_INTERVAL
        with self._sync_lock:
            call_time = datetime.now(timezone.utc)
            difference = (call_time - self._last_app_scan).total_seconds()
            if difference < scan_interval:
                return
            self._last_app_scan = call_time

        if self._app_list is not None:
            app_to_check = {}
            for app_name, app_id in self._app_list.items():
                app = None
                if self._installed_app:
                    app = self._installed_app.get(app_id)
                else:
                    app_type = self._app_type.get(app_id, 2)
                    if app_type <= 4:
                        app = App(app_id, app_name, app_type)
                if app:
                    app_to_check[app_id] = app
        else:
            app_to_check = self._installed_app

        for app in app_to_check.values():
            self._get_app_status(app.app_id, app.app_type)

    def set_power_on_request(self, set_art_mode=False, power_on_delay=0):
        """Set a power on request status and save the time of the rquest."""
        self._power_on_requested = True
        self._power_on_requested_time = datetime.now(timezone.utc)
        self._power_on_artmode = set_art_mode
        self._power_on_delay = max(power_on_delay, 0) or DEFAULT_POWER_ON_DELAY

    def set_power_off_request(self):
        """Remove a previous power on request."""
        self._power_on_requested = False

    def start_poll(self):
        """Start polling the TV for status."""
        if self._ping_thread is None or not self._ping_thread.is_alive():
            self._ping_thread = Thread(target=self._ping_thread_method)
            self._ping_thread.name = "SamsungPing"
            self._ping_thread.daemon = True
            self._ping_thread_run = True
            self._ping_thread.start()

    def stop_poll(self):
        """Stop polling the TV for status."""
        # NOTE: the guard must check that the thread IS alive (a previous
        # inverted check made this a no-op in the normal case, leaking the
        # ping thread and its WebSocket client threads on every reload —
        # which kept stale clients connected to the TV's art-app channel in
        # parallel with the new entry instance).
        if self._ping_thread is not None and self._ping_thread.is_alive():
            self._ping_thread_run = False
            self._ping_thread.join()
            if self._is_connected:
                self.stop_client()
        self._ping_thread = None

    def disable_art_thread(self):
        """Disable the SamsungArt WebSocket thread.

        Called when the async Art API (art.py) handles art mode,
        to prevent multiple clients on the same art-app channel
        which causes the TV to route d2d_service_message responses
        unpredictably — resulting in 100% timeout on art.py requests.
        """
        self._art_thread_disabled = True
        self._log.debug("SamsungArt thread disabled (async Art API active)")
        # Stop existing art thread if already running
        if self._ws_art:
            try:
                self._ws_art.close()
            except Exception:
                pass
            self._ws_art = None

    def _start_client(self, *, start_all=False):
        """Start all thread that connect to the TV websocket"""

        if self._auth_blocked:
            # Reconnection is paused after repeated token rejections; relaunching
            # would only re-arm the on-screen authorization prompt. Stays paused
            # until the user re-pairs and a clean reconnect resets the flag.
            return

        if self._client_remote is None or not self._client_remote.is_alive():
            self._client_remote = Thread(target=self._client_remote_thread)
            self._client_remote.name = "SamsungRemote"
            self._client_remote.daemon = True
            self._client_remote.start()

            return

        if start_all:
            if self._client_control is None or not self._client_control.is_alive():
                self._client_control = Thread(target=self._client_control_thread)
                self._client_control.name = "SamsungControl"
                self._client_control.daemon = True
                self._client_control.start()

            if (
                self._client_art_supported > 0
                and not self._art_thread_disabled
                and (self._client_art is None or not self._client_art.is_alive())
            ):
                if self._client_art_supported > 1:
                    self._client_art_supported = 0
                self._client_art = Thread(target=self._client_art_thread)
                self._client_art.name = "SamsungArt"
                self._client_art.daemon = True
                self._client_art.start()

    def stop_client(self):
        """Stop the ws remote client thread and cleanup all connections."""
        if self._ws_remote:
            try:
                self._ws_remote.close()
            except Exception as ex:
                self._log.debug("Error closing ws_remote: %s", ex)
            self._ws_remote = None

        # Nettoyer aussi la connexion simple pour éviter la saturation
        if self.connection:
            try:
                self.connection.close()
            except Exception as ex:
                self._log.debug("Error closing simple connection: %s", ex)
            self.connection = None

    def open(self):
        """Open a WS client connection with the TV."""
        if self.connection is not None:
            return self.connection

        is_ssl = self._is_ssl_connection()
        url = self._format_websocket_url(_WS_ENDPOINT_REMOTE_CONTROL, is_ssl=is_ssl)
        sslopt = {"cert_reqs": ssl.CERT_NONE} if is_ssl else {}

        self._log.debug("WS url %s", url)
        connection = websocket.create_connection(url, self.timeout, sslopt=sslopt)
        completed = False
        response = ""

        for _ in range(3):
            try:
                response = _process_api_response(connection.recv())
            except WebSocketProtocolException as exc:
                # Samsung TV envoie parfois un code de fermeture 1005 invalide
                # Le code 1005 est réservé et ne devrait pas être envoyé par le serveur
                # selon la RFC 6455. Cela indique généralement une SATURATION des connexions
                # côté SmartThings (trop de connexions WebSocket ouvertes simultanément).
                error_msg = str(exc)
                if "1005" in error_msg:
                    self._log.warning(
                        "Samsung TV sent invalid close code 1005 - likely connection saturation. "
                        "Forcing cleanup of all connections to allow TV to recover."
                    )
                    # Nettoyage complet de toutes les connexions
                    try:
                        connection.close()
                    except Exception:
                        pass

                    # Forcer l'arrêt de la connexion persistante si elle existe
                    if self._ws_remote:
                        try:
                            self._ws_remote.close()
                        except Exception:
                            pass
                        self._ws_remote = None

                    self.connection = None

                    raise ConnectionFailure(
                        "Connection closed by TV with code 1005 - TV may be saturated with connections. "
                        "All connections have been cleaned up."
                    )
                raise

            self._log.debug(response)
            event = response.get("event", "-")
            if event != "ms.channel.connect":
                break
            conn_data = response.get("data")
            if self._check_conn_id(conn_data):
                completed = True
                token = conn_data.get("token")
                if token:
                    self._set_token(token)
                break

        if not completed:
            self.close()
            raise ConnectionFailure(response)

        self.connection = connection
        return connection

    def close(self):
        """Close WS connection."""
        if self.connection:
            self.connection.close()
            self._log.debug("Connection closed.")
        self.connection = None

    def send_key(self, key, key_press_delay=None, cmd="Click"):
        """Send a key to the TV using appropriate WS connection."""
        self._log.debug("Sending key %s", key)
        return self._ws_send(
            {
                "method": "ms.remote.control",
                "params": {
                    "Cmd": cmd,
                    "DataOfCmd": key,
                    "Option": "false",
                    "TypeOfRemote": "SendRemoteKey",
                },
            },
            key_press_delay,
        )

    def hold_key(self, key, seconds):
        """Send a key to the TV and keep it pressed for specific number of seconds"""
        if self.send_key(key, key_press_delay=0, cmd="Press"):
            time.sleep(seconds)
            return self.send_key(key, key_press_delay=0, cmd="Release")
        return False

    def send_text(self, text, send_delay=None):
        """Send a text string to the TV."""
        if not text:
            return False

        base64_text = self._serialize_string(text)
        if self._ws_send(
            {
                "method": "ms.remote.control",
                "params": {
                    "Cmd": f"{base64_text}",
                    "DataOfCmd": "base64",
                    "TypeOfRemote": "SendInputString",
                },
            },
            key_press_delay=send_delay,
        ):
            self._ws_send(
                {
                    "method": "ms.remote.control",
                    "params": {
                        "TypeOfRemote": "SendInputEnd",
                    },
                },
                key_press_delay=0,
            )
            return True

        return False

    def move_cursor(self, x, y, duration=0):
        """Move the cursor in the TV to specific coordinate."""
        self._ws_send(
            {
                "method": "ms.remote.control",
                "params": {
                    "Cmd": "Move",
                    "Position": {"x": x, "y": y, "Time": str(duration)},
                    "TypeOfRemote": "ProcessMouseDevice",
                },
            },
            key_press_delay=0,
        )

    def run_app(self, app_id, action_type="", meta_tag="", *, use_remote=False):
        """Launch an app using appropriate WS channel."""
        if not action_type:
            app = self._installed_app.get(app_id)
            if app:
                app_type = app.app_type
            else:
                app_type = self._app_type.get(app_id, 2)
            action_type = TYPE_DEEP_LINK if app_type == 2 else TYPE_NATIVE_LAUNCH
        elif action_type != TYPE_NATIVE_LAUNCH:
            action_type = TYPE_DEEP_LINK

        self._log.debug(
            "Sending run app app_id: %s app_type: %s meta_tag: %s",
            app_id,
            action_type,
            meta_tag,
        )

        if self._ws_control and action_type == TYPE_DEEP_LINK and not use_remote:
            return self._ws_send(
                {
                    "id": app_id,
                    "method": "ms.application.start",
                    "params": {"id": app_id},
                },
                key_press_delay=0,
                use_control=True,
                ws_socket=self._ws_control,
            )

        return self._ws_send(
            {
                "method": "ms.channel.emit",
                "params": {
                    "event": "ed.apps.launch",
                    "to": "host",
                    "data": {
                        # action_type: NATIVE_LAUNCH / DEEP_LINK
                        # app_type == 2 ? 'DEEP_LINK' : 'NATIVE_LAUNCH',
                        "action_type": action_type,
                        "appId": app_id,
                        "metaTag": meta_tag,
                    },
                },
            },
            key_press_delay=0,
        )

    def _send_app_lifecycle(self, method, app_id):
        """Send an ms.application.* lifecycle command on the control channel.

        Unlike run_app there is no ed.apps.launch fallback: that event only
        knows how to launch, so without the control channel these cannot be
        sent at all. Say so rather than returning a silent False.
        """
        if not (self._ws_control and self._is_control_connected):
            self._log.warning(
                "Cannot send %s for %s: the control channel is not connected",
                method,
                app_id,
            )
            return False

        self._log.debug("Sending %s for app_id: %s", method, app_id)
        return self._ws_send(
            {
                "id": app_id,
                "method": method,
                "params": {"id": app_id},
            },
            key_press_delay=0,
            use_control=True,
            ws_socket=self._ws_control,
        )

    def stop_app(self, app_id):
        """Stop a running app on the TV."""
        return self._send_app_lifecycle("ms.application.stop", app_id)

    def start_app(self, app_id):
        """Launch an app by id, skipping the app-type lookup run_app does.

        run_app picks its channel from the installed-app list, which a
        built-in app is usually missing from; this always sends the
        documented ms.application.start.
        """
        return self._send_app_lifecycle("ms.application.start", app_id)

    def open_browser(self, url):
        """Launch the browser app on the TV."""
        self._log.debug("Opening url in browser %s", url)
        return self.run_app("org.tizen.browser", TYPE_NATIVE_LAUNCH, url)

    def rest_device_info(self):
        """Get device info using rest api call."""
        self._log.debug("Get device info via rest api")
        return self._rest_request("")

    def rest_app_status(self, app_id):
        """Get app status using rest api call."""
        self._log.debug("Get app %s status via rest api", app_id)
        return self._rest_request("applications/" + app_id)

    def rest_app_run(self, app_id):
        """Run an app using rest api call."""
        self._log.debug("Run app %s via rest api", app_id)
        return self._rest_request("applications/" + app_id, "POST")

    def rest_app_close(self, app_id):
        """Close an app using rest api call."""
        self._log.debug("Close app %s via rest api", app_id)
        return self._rest_request("applications/" + app_id, "DELETE")

    def rest_app_install(self, app_id):
        """Install a new app using rest api call."""
        self._log.debug("Install app %s via rest api", app_id)
        return self._rest_request("applications/" + app_id, "PUT")

    def shortcuts(self):
        """Return a list of available shortcuts."""
        return SamsungTVShortcuts(self)
