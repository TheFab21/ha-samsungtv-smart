"""Local quick-settings channel of the TV (``com.samsung.tv.mobilebff``).

The TV's launcher publishes its quick settings on this MSF WebSocket channel:
each setting is a "layer" with its elements and the index of the current one.
The picture-mode layer is the only local source of the modes the CURRENT
INPUT offers — IP Control can set a mode but cannot list them, and the
SmartThings cloud list lagged or missed modes (FILMMAKER MODE absent on a
Frame 2024 while the TV offered it).

Measured on a Frame 2024 (QE55LS03D, two probes, Nov 2026):

* the channel accepts a connection WITHOUT the remote-control token, like the
  Art channel, and answers at once — no on-screen authorization;
* ``get.settings.data`` returns 22 layers (~40 kB); the picture-mode one is
  ``s/setting.layer.picturemode`` with elements
  ``s/setting.layer.picturemode@<Id>`` (``Dynamic``, ``Standard``, ``Eco``,
  ``Movie``, ``FilmMaker``), the localized label in ``subTitle`` and the
  current one at ``current_element_index`` (followed Dynamic -> Movie ->
  Standard as the mode was changed with the remote);
* a change is pushed as ``event.setting.update`` carrying only the changed
  layers, within a second of the remote press; an input switch pushed the
  new list (TV: 5 modes; HDMI: 4, no FILMMAKER MODE);
* ``notify.update`` arrives every ~3 s while the TV is on, whether anything
  changed or not, so it is NOT used as a trigger to re-read the 40 kB list.

Only reads are sent. ``set.settings.data`` exists in the firmware but is not
used: IP Control already sets the picture mode with a verified answer.
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Callable
from dataclasses import dataclass
import json
import logging
import ssl
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

CHANNEL = "com.samsung.tv.mobilebff"
EVENT = "/sec/tv/appdata/mobilebff"
PICTURE_MODE_LAYER = "s/setting.layer.picturemode"

# Seconds to wait for ms.channel.connect after the socket opens.
CONNECT_TIMEOUT = 10
# Reconnect backoff after a lost or refused connection.
RETRY_MIN = 5
RETRY_MAX = 120
# How often to re-check whether the TV is on while it is not.
IDLE_CHECK = 15
# Handshakes the TV answered without accepting (unauthorized, timeOut, no
# connect) before giving up until the entry is reloaded. Measured TVs accept
# at once; one that refuses may be showing an authorization prompt for each
# attempt, which must not repeat every couple of minutes.
MAX_REFUSALS = 3

# Quick-settings ids that differ from the OCF name pictureModeControl takes.
_OCF_BY_SETTING_ID = {"FilmMaker": "FilmmakerMode"}


@dataclass(frozen=True)
class PictureModeLayer:
    """The picture modes the current input offers, as the TV lists them."""

    modes: tuple[tuple[str, str], ...]  # (setting id, localized label)
    current: str | None  # setting id of the current mode

    @property
    def labels(self) -> list[str]:
        return [label for _, label in self.modes]

    @property
    def current_label(self) -> str | None:
        for mode_id, label in self.modes:
            if mode_id == self.current:
                return label
        return None


def picture_mode_ocf(setting_id: str) -> str:
    """Return the pictureModeControl value for a quick-settings mode id."""
    return _OCF_BY_SETTING_ID.get(setting_id, setting_id)


def parse_picture_mode_layer(layer: Any) -> PictureModeLayer | None:
    """Parse the picture-mode layer; None when it is not one or is unusable."""
    if not isinstance(layer, dict) or layer.get("id") != PICTURE_MODE_LAYER:
        return None
    prefix = f"{PICTURE_MODE_LAYER}@"
    modes: list[tuple[str, str]] = []
    for element in layer.get("elements") or ():
        if not isinstance(element, dict):
            continue
        element_id = element.get("id")
        if not isinstance(element_id, str) or not element_id.startswith(prefix):
            continue
        mode_id = element_id[len(prefix) :]
        label = element.get("subTitle")
        if not mode_id:
            continue
        if not isinstance(label, str) or not label.strip():
            label = mode_id
        modes.append((mode_id, label.strip()))
    if not modes:
        return None
    current = None
    try:
        index = int(layer.get("current_element_index"))
    except (TypeError, ValueError):
        index = -1
    if 0 <= index < len(modes):
        current = modes[index][0]
    return PictureModeLayer(tuple(modes), current)


def picture_mode_from_message(message: Any) -> PictureModeLayer | None:
    """Return the picture-mode layer carried by a channel message, if any.

    ``get.settings.data`` carries every layer as a list in ``response``;
    ``event.setting.update`` carries the changed ones in
    ``response.updateElement``. ``data`` may arrive as a JSON string.
    """
    if not isinstance(message, dict) or message.get("event") != EVENT:
        return None
    data = message.get("data")
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except ValueError:
            return None
    if not isinstance(data, dict):
        return None
    response = data.get("response")
    if data.get("id") == "get.settings.data":
        layers = response
    elif data.get("id") == "event.setting.update" and isinstance(response, dict):
        layers = response.get("updateElement")
    else:
        return None
    if not isinstance(layers, list):
        return None
    for layer in layers:
        parsed = parse_picture_mode_layer(layer)
        if parsed is not None:
            return parsed
    return None


def _ssl_context() -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


class SamsungMobileBFF:
    """Keep the quick-settings channel open while the TV is on.

    ``should_connect`` is asked before every connection attempt, so nothing
    is sent to a TV that is off. The current picture-mode layer is kept while
    connected and cleared when the connection drops, so callers never act on
    a list from before the TV went away.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        name: str,
        should_connect: Callable[[], bool],
    ) -> None:
        self._session = session
        self._host = host
        self._name = base64.b64encode(name.encode()).decode()
        self._should_connect = should_connect
        self._listeners: list[Callable[[], None]] = []
        self._task: asyncio.Task | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._seq = 0
        self.picture_modes: PictureModeLayer | None = None

    def add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Call ``listener`` whenever the picture-mode layer changes."""
        self._listeners.append(listener)

        def remove() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return remove

    def start(self, create_task: Callable[..., asyncio.Task]) -> None:
        if self._task is None or self._task.done():
            self._task = create_task(self._run())

    async def stop(self) -> None:
        task, self._task = self._task, None
        if self._ws is not None and not self._ws.closed:
            await self._ws.close()
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._set_picture_modes(None)

    def _set_picture_modes(self, layer: PictureModeLayer | None) -> None:
        if layer == self.picture_modes:
            return
        self.picture_modes = layer
        for listener in list(self._listeners):
            try:
                listener()
            except Exception:  # noqa: BLE001 - one listener must not stop others
                _LOGGER.debug("mobilebff listener raised", exc_info=True)

    async def _run(self) -> None:
        delay = RETRY_MIN
        refusals = 0
        while True:
            if not self._should_connect():
                await asyncio.sleep(IDLE_CHECK)
                continue
            connected = False
            refused = False
            for port in (8002, 8001):
                try:
                    connected = await self._session_once(port)
                    refused = refused or not connected
                except asyncio.CancelledError:
                    raise
                except Exception as ex:  # noqa: BLE001
                    _LOGGER.debug(
                        "Quick-settings channel on %s:%s failed: %r",
                        self._host,
                        port,
                        ex,
                    )
                finally:
                    self._ws = None
                    self._set_picture_modes(None)
                if connected:
                    break
            if connected:
                refusals = 0
            elif refused:
                refusals += 1
                if refusals >= MAX_REFUSALS:
                    _LOGGER.info(
                        "%s did not accept its quick-settings channel %d times: "
                        "picture modes will come from SmartThings or the fixed "
                        "list until the integration is reloaded",
                        self._host,
                        refusals,
                    )
                    return
            delay = RETRY_MIN if connected else min(delay * 2, RETRY_MAX)
            await asyncio.sleep(delay)

    async def _session_once(self, port: int) -> bool:
        """Run one connection until it closes; True if the TV accepted it."""
        scheme = "wss" if port == 8002 else "ws"
        url = (
            f"{scheme}://{self._host}:{port}/api/v2/channels/{CHANNEL}"
            f"?name={self._name}"
        )
        extra = {"ssl": _ssl_context()} if scheme == "wss" else {}
        ws = await asyncio.wait_for(
            self._session.ws_connect(url, heartbeat=20, **extra),
            timeout=CONNECT_TIMEOUT,
        )
        self._ws = ws
        try:
            if not await self._wait_connected(ws):
                return False
            _LOGGER.debug("Quick-settings channel open on %s:%s", self._host, port)
            await self._send(ws, "get.settings.data")
            async for msg in ws:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    break
                try:
                    message = json.loads(msg.data)
                except ValueError:
                    continue
                layer = picture_mode_from_message(message)
                if layer is not None:
                    self._set_picture_modes(layer)
            return True
        finally:
            if not ws.closed:
                await ws.close()

    async def _wait_connected(self, ws: aiohttp.ClientWebSocketResponse) -> bool:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + CONNECT_TIMEOUT
        while (left := deadline - loop.time()) > 0:
            msg = await ws.receive(timeout=left)
            if msg.type != aiohttp.WSMsgType.TEXT:
                return False
            try:
                event = json.loads(msg.data).get("event")
            except (ValueError, AttributeError):
                continue
            if event == "ms.channel.connect":
                return True
            if event in ("ms.channel.unauthorized", "ms.channel.timeOut", "ms.error"):
                return False
        return False

    async def _send(self, ws: aiohttp.ClientWebSocketResponse, request: str) -> None:
        self._seq += 1
        inner = {"version": "0.2", "id": request, "seqId": str(self._seq)}
        inner["params"] = {}
        await ws.send_str(
            json.dumps(
                {
                    "method": "ms.channel.emit",
                    "params": {"event": EVENT, "to": "host", "data": json.dumps(inner)},
                }
            )
        )
