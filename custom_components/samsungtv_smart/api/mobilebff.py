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
    data = _message_data(message)
    if data is None:
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


@dataclass(frozen=True)
class SettingElement:
    """One option of a quick setting, as the TV lists it."""

    element_id: str  # e.g. s/setting.layer.colortone@Warm1 (not always unique)
    title: str  # the setting's name in the menu language
    label: str  # the option's label in the menu language (subTitle)
    dim: bool  # greyed out on the TV's own menu

    @property
    def params(self) -> dict[str, str]:
        """What set.settings.data takes to select this option."""
        return {"id": self.element_id, "title": self.title, "subTitle": self.label}


@dataclass(frozen=True)
class SettingLayer:
    """A quick setting: its options and the index of the current one."""

    layer_id: str
    elements: tuple[SettingElement, ...]
    current: int | None

    @property
    def current_element(self) -> SettingElement | None:
        if self.current is None:
            return None
        return self.elements[self.current]


@dataclass(frozen=True)
class ExtSource:
    """An input the TV lists (TV, HDMI, DLNA), with the name it shows."""

    source_id: str  # e.g. s/source.layer.hdmi@HDMI3
    title: str  # e.g. CINEMA 60 - renamed by CEC once the receiver is on
    current: bool

    @property
    def short_id(self) -> str:
        """``HDMI3`` for ``s/source.layer.hdmi@HDMI3``."""
        return self.source_id.rpartition("@")[2]


def parse_setting_layer(layer: Any) -> SettingLayer | None:
    """Parse any quick-settings layer; None when it has no usable element."""
    if not isinstance(layer, dict) or not isinstance(layer.get("id"), str):
        return None
    elements: list[SettingElement] = []
    for element in layer.get("elements") or ():
        if not isinstance(element, dict) or not isinstance(element.get("id"), str):
            continue
        label = element.get("subTitle")
        title = element.get("title")
        elements.append(
            SettingElement(
                element["id"],
                title if isinstance(title, str) else "",
                label.strip() if isinstance(label, str) else "",
                str(element.get("dim")).lower() == "true",
            )
        )
    if not elements:
        return None
    try:
        index = int(layer.get("current_element_index"))
    except (TypeError, ValueError):
        index = -1
    current = index if 0 <= index < len(elements) else None
    return SettingLayer(layer["id"], tuple(elements), current)


def parse_sources(response: Any) -> tuple[ExtSource, ...] | None:
    """Parse a get.extsources response."""
    if not isinstance(response, list):
        return None
    sources = []
    for item in response:
        if not isinstance(item, dict) or not isinstance(item.get("sourceId"), str):
            continue
        title = item.get("title")
        sources.append(
            ExtSource(
                item["sourceId"],
                title if isinstance(title, str) else item["sourceId"],
                str(item.get("currentSource")).lower() == "true",
            )
        )
    return tuple(sources)


def _message_data(message: Any) -> dict | None:
    """The inner ``data`` of a channel message (it may arrive as a string)."""
    if not isinstance(message, dict) or message.get("event") != EVENT:
        return None
    data = message.get("data")
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except ValueError:
            return None
    return data if isinstance(data, dict) else None


def _ssl_context() -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


class SamsungMobileBFF:
    """Keep the quick-settings channel open while the TV is on.

    ``should_connect`` is asked before every connection attempt, so nothing
    is sent to a TV that is off. The quick settings and the input list are
    kept while connected and cleared when the connection drops, so callers
    never act on a list from before the TV went away.

    Writes were measured on a Frame 2024: ``set.settings.data`` (colour tone
    Standard -> Warm 1 -> Standard) and ``launch.extsource`` (TV -> HDMI3 ->
    TV) each answered ``status: ok`` within 0.7 s, and the TV pushed the new
    setting within a second. A write counts as applied only once the TV
    reports the new value, since ``ok`` alone was also answered for a sound
    mode the input no longer offered.
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
        self._layer_listeners: list[Callable[[SettingLayer], None]] = []
        self._task: asyncio.Task | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._seq = 0
        self._pending: dict[str, asyncio.Future] = {}
        self.picture_modes: PictureModeLayer | None = None
        self.layers: dict[str, SettingLayer] = {}
        self.sources: tuple[ExtSource, ...] = ()

    @property
    def connected(self) -> bool:
        return self._ws is not None and not self._ws.closed

    def add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Call ``listener`` whenever the settings or the inputs change."""
        self._listeners.append(listener)

        def remove() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return remove

    def add_layer_listener(
        self, listener: Callable[[SettingLayer], None]
    ) -> Callable[[], None]:
        """Call ``listener(layer)`` for each quick setting the TV reports."""
        self._layer_listeners.append(listener)

        def remove() -> None:
            if listener in self._layer_listeners:
                self._layer_listeners.remove(listener)

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
        self._clear()

    def _notify(self) -> None:
        for listener in list(self._listeners):
            try:
                listener()
            except Exception:  # noqa: BLE001 - one listener must not stop others
                _LOGGER.debug("mobilebff listener raised", exc_info=True)

    def _set_picture_modes(self, layer: PictureModeLayer | None) -> None:
        if layer == self.picture_modes:
            return
        self.picture_modes = layer
        self._notify()

    def _clear(self) -> None:
        for future in self._pending.values():
            if not future.done():
                future.cancel()
        self._pending.clear()
        changed = bool(self.layers or self.sources)
        self.layers = {}
        self.sources = ()
        if self.picture_modes is not None:
            self._set_picture_modes(None)
        elif changed:
            self._notify()

    def _apply_layers(self, layers: Any, replace: bool) -> None:
        if not isinstance(layers, list):
            return
        new = {} if replace else dict(self.layers)
        if replace:
            # A full read without a picture-mode layer means there is none now.
            self.picture_modes = None
        parsed_layers = []
        for raw in layers:
            parsed = parse_setting_layer(raw)
            if parsed is not None:
                new[parsed.layer_id] = parsed
                parsed_layers.append(parsed)
            picture = parse_picture_mode_layer(raw)
            if picture is not None:
                self.picture_modes = picture
        changed = new != self.layers
        self.layers = new
        for parsed in parsed_layers:
            for listener in list(self._layer_listeners):
                try:
                    listener(parsed)
                except Exception:  # noqa: BLE001
                    _LOGGER.debug("mobilebff layer listener raised", exc_info=True)
        if changed:
            self._notify()

    def _handle(self, message: Any) -> None:
        data = _message_data(message)
        if data is None:
            return
        kind = data.get("id")
        response = data.get("response")
        seq = data.get("seqId")
        if isinstance(seq, str) and seq in self._pending:
            future = self._pending.pop(seq)
            if not future.done():
                future.set_result(data)
        if kind == "get.settings.data":
            self._apply_layers(response, replace=True)
        elif kind == "event.setting.update" and isinstance(response, dict):
            self._apply_layers(response.get("updateElement"), replace=False)
        elif kind == "get.extsources":
            sources = parse_sources(response)
            if sources is not None and sources != self.sources:
                self.sources = sources
                self._notify()

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
                    self._clear()
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
            await self._send("get.settings.data")
            await self._send("get.extsources")
            async for msg in ws:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    break
                try:
                    message = json.loads(msg.data)
                except ValueError:
                    continue
                self._handle(message)
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

    def _next_seq(self) -> str:
        self._seq += 1
        return str(self._seq)

    async def _send(
        self, request: str, params: dict | None = None, seq: str | None = None
    ) -> str:
        """Send a request; return its seqId (the TV echoes it in the answer)."""
        ws = self._ws
        if ws is None or ws.closed:
            raise ConnectionError("quick-settings channel is not open")
        seq = seq or self._next_seq()
        inner = {"version": "0.2", "id": request, "seqId": seq}
        inner["params"] = params or {}
        await ws.send_str(
            json.dumps(
                {
                    "method": "ms.channel.emit",
                    "params": {"event": EVENT, "to": "host", "data": json.dumps(inner)},
                }
            )
        )
        return seq

    async def _request(
        self, request: str, params: dict | None = None, timeout: float = 5
    ) -> dict | None:
        """Send and wait for the answer with the same seqId; None on timeout."""
        future = asyncio.get_running_loop().create_future()
        seq = self._next_seq()
        self._pending[seq] = future
        try:
            await self._send(request, params, seq)
            return await asyncio.wait_for(future, timeout)
        except (asyncio.TimeoutError, asyncio.CancelledError, ConnectionError):
            return None
        finally:
            self._pending.pop(seq, None)

    @staticmethod
    def _ok(answer: dict | None) -> bool:
        response = (answer or {}).get("response")
        return isinstance(response, dict) and response.get("status") == "ok"

    async def async_set_setting(
        self, layer_id: str, index: int, confirm_timeout: float = 4
    ) -> bool:
        """Select option ``index`` of a quick setting; True once the TV shows it."""
        layer = self.layers.get(layer_id)
        if layer is None or not 0 <= index < len(layer.elements):
            return False
        if layer.current == index:
            return True
        answer = await self._request("set.settings.data", layer.elements[index].params)
        if not self._ok(answer):
            _LOGGER.debug("Quick setting %s[%d] refused: %s", layer_id, index, answer)
            return False
        loop = asyncio.get_running_loop()
        deadline = loop.time() + confirm_timeout
        while loop.time() < deadline:
            current = self.layers.get(layer_id)
            if current is not None and current.current == index:
                return True
            await asyncio.sleep(0.1)
        _LOGGER.debug(
            "Quick setting %s[%d] answered ok but the TV did not report it",
            layer_id,
            index,
        )
        return False

    async def async_launch_source(self, source_id: str) -> bool:
        """Switch to an input; True when the TV accepted it."""
        answer = await self._request("launch.extsource", {"requestId": source_id})
        if not self._ok(answer):
            _LOGGER.debug("Input %s refused: %s", source_id, answer)
            return False
        # The input list has no change broadcast: read it again once the TV
        # has had time to switch (it showed the new input within 12 s).
        asyncio.get_running_loop().call_later(
            3, lambda: asyncio.ensure_future(self._refresh_sources())
        )
        return True

    async def _refresh_sources(self) -> None:
        try:
            await self._send("get.extsources")
        except (ConnectionError, aiohttp.ClientError, RuntimeError):
            pass

    def find_source(self, *names: str) -> ExtSource | None:
        """The input whose short id (``HDMI3``) or title matches one of names."""
        wanted = [n.casefold() for n in names if n]
        for source in self.sources:
            if source.short_id.casefold() in wanted:
                return source
        for source in self.sources:
            if source.title.casefold() in wanted:
                return source
        return None
