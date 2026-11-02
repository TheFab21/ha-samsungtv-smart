#!/usr/bin/env python3
"""Probe the TV's local ``com.samsung.tv.mobilebff`` WebSocket channel.

The channel and its requests come from the decompiled 2025 Frame firmware
(notes/QN55LS03FAFXZA/WEBSOCKET_DECOMPILED.md, "MobileBFF Channel"). Its reads
were confirmed on a Frame 2024: the quick settings (picture mode list per
input, sound mode, AI Mode, ...) and the input list. The open question is
which SmartThings-only actions it can replace: setting a quick setting
(sound mode) and switching input, plus whether any vconf key carries the
light-sensor reading or the power draw.

By default it only READS: it sends ``ask.service.status``,
``get.settings.data`` and ``get.extsources``, then listens. Writes happen only
with an explicit option, and each one is undone by the script:

  --toggle-sound-mode      set.settings.data: sound mode to another option, back
  --toggle-setting LAYER   the same for any quick setting (colortone, ...)
  --switch-source NAME     launch.extsource: to that input, then back
  --apps                   get.apptiles / recent / recommended (read-only)
  --icons                  fetch a few app/input icons, two ways (read-only)
  --extras                 tile functions/previews, Multi View, rotator (read-only)
  --launch-app NAME        launch.app: open that app tile, then back to TV
  --vconf KEY / --vconf-defaults
                           request.vconf.info reads (read-only)

e.g. ... probe_mobilebff.py 192.168.1.161 --toggle-sound-mode \\
         --switch-source "CINEMA 60" --vconf-defaults

Run it on the Home Assistant host, where aiohttp is already installed, e.g.:

    docker exec -it homeassistant python3 /config/probe_mobilebff.py 192.168.1.161

The container name depends on the install (`docker ps` lists it): a
Compose project named homeassistant gives homeassistant-home-assistant.

While it listens (90 s by default), use the remote: change the picture mode,
switch input (TV -> HDMI -> TV), open the quick settings. Everything received
is printed and saved to /config/mobilebff_probe_<time>.json (the token is not
written to it).

By default it connects without a token, like the integration's Art channel.
If the TV refuses that, it retries with the token and the name the integration
already uses, read from /config/.storage/core.config_entries, so the TV should
not ask for a new authorization.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
from pathlib import Path
import ssl
import sys
import time

import aiohttp

CHANNEL = "com.samsung.tv.mobilebff"
EVENT = "/sec/tv/appdata/mobilebff"
WS_PREFIX = "[Home Assistant]"
READ_REQUESTS = ("ask.service.status", "get.settings.data", "get.extsources")

# vconf keys to try with --vconf-defaults. The first four appear in the
# decompiled firmware (notes/QN55LS03FAFXZA); the rest are GUESSES at where a
# light-sensor reading or power state could live, from common Tizen key
# names. A missing key just gets an error or no answer.
VCONF_CANDIDATES = (
    "db/aitech/enable",
    "db/menu_widget/language",
    "memory/iot/thing/thing_status",
    "memory/sensor/mobile/illumination",
    "memory/pm/state",
    "memory/sensor/illumination",
    "memory/sensor/light",
    "memory/sensor/lux",
    "db/picture/brightness_sensor",
    "memory/tv/power_consumption",
)


def _b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


def _ssl_context() -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _integration_credentials(storage: Path, host: str) -> tuple[str | None, str]:
    """Return (token, client name) of the samsungtv_smart entry for host."""
    try:
        entries = json.loads(storage.read_text())["data"]["entries"]
    except (OSError, ValueError, KeyError) as ex:
        print(f"! Could not read {storage}: {ex}")
        return None, f"{WS_PREFIX} probe"
    for entry in entries:
        data = entry.get("data", {})
        if entry.get("domain") == "samsungtv_smart" and data.get("host") == host:
            ws_name = data.get("ws_name") or entry.get("title") or "probe"
            return data.get("token"), f"{WS_PREFIX} {ws_name}"
    print(f"! No samsungtv_smart entry with host {host} in {storage}")
    return None, f"{WS_PREFIX} probe"


def _decode(value):
    """Turn JSON carried as a string (at any depth) into objects."""
    if isinstance(value, str):
        text = value.strip()
        if text[:1] in "{[":
            try:
                return _decode(json.loads(text))
            except ValueError:
                return value
        return value
    if isinstance(value, dict):
        return {k: _decode(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_decode(v) for v in value]
    return value


def _image_ext(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG"):
        return "png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    head = data[:200].lstrip().lower()
    if head.startswith((b"<svg", b"<?xml")):
        return "svg"
    return None


def _find_image(value, path="") -> tuple[str, bytes] | None:
    """The first string in a decoded message that base64-decodes to an image."""
    if isinstance(value, dict):
        for key, item in value.items():
            found = _find_image(item, f"{path}.{key}")
            if found:
                return found
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found = _find_image(item, f"{path}[{index}]")
            if found:
                return found
    elif isinstance(value, str) and len(value) > 200:
        text = value.split(",", 1)[1] if value.startswith("data:") else value
        try:
            data = base64.b64decode(text, validate=False)
        except (ValueError, TypeError):
            return None
        if _image_ext(data):
            return path, data
    return None


class Probe:
    """One connection to the channel, recording everything it receives."""

    def __init__(self, host: str, port: int, listen: float, out: Path) -> None:
        self.host = host
        self.port = port
        self.listen = listen
        self.out = out
        self.records: list[dict] = []
        self._seq = 0
        self._t0 = time.monotonic()
        # Latest decoded "data" per response id (get.settings.data, ...).
        self.latest: dict[str, dict] = {}
        self._last_settings_read = 0.0

    def _record(self, direction: str, payload) -> None:
        item = {
            "t": round(time.monotonic() - self._t0, 2),
            "dir": direction,
            "msg": _decode(payload),
        }
        self.records.append(item)
        text = json.dumps(item["msg"], ensure_ascii=False)
        if len(text) > 600:
            text = text[:600] + f"... ({len(text)} chars, full text in the file)"
        print(f"[{item['t']:7.2f}s] {direction} {text}")

    async def _send(self, ws, request_id: str, params: dict | None = None) -> None:
        self._seq += 1
        inner = {
            "version": "0.2",
            "id": request_id,
            "seqId": str(self._seq),
            "params": params or {},
        }
        outer = {
            "method": "ms.channel.emit",
            "params": {"event": EVENT, "to": "host", "data": json.dumps(inner)},
        }
        self._record("->", outer)
        await ws.send_str(json.dumps(outer))

    async def connect(self, session, name: str, token: str | None):
        scheme = "wss" if self.port == 8002 else "ws"
        url = f"{scheme}://{self.host}:{self.port}/api/v2/channels/{CHANNEL}"
        url += f"?name={_b64(name)}"
        shown = url
        if token:
            url += f"&token={token}"
            shown += "&token=<redacted>"
        print(f"\n== Connecting {'with' if token else 'without'} token: {shown}")
        extra = {"ssl": _ssl_context()} if scheme == "wss" else {}
        try:
            ws = await asyncio.wait_for(
                session.ws_connect(url, heartbeat=20, **extra), timeout=10
            )
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as ex:
            print(f"! Connection failed: {ex!r}")
            return None
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                msg = await ws.receive(timeout=deadline - time.monotonic())
            except asyncio.TimeoutError:
                break
            if msg.type != aiohttp.WSMsgType.TEXT:
                print(f"! Socket closed during handshake: {msg.type} {msg.data!r}")
                return None
            self._record("<-", msg.data)
            event = (_decode(msg.data) or {}).get("event")
            if event == "ms.channel.connect":
                return ws
            if event in ("ms.channel.unauthorized", "ms.channel.timeOut", "ms.error"):
                break
        print("! No ms.channel.connect from the TV")
        await ws.close()
        return None

    async def _reader(self, ws) -> None:
        """Record every message; re-read settings at most every 20 s on notify."""
        async for msg in ws:
            if msg.type != aiohttp.WSMsgType.TEXT:
                print(f"! Socket closed: {msg.type} {msg.data!r}")
                return
            self._record("<-", msg.data)
            data = (_decode(msg.data) or {}).get("data")
            if not isinstance(data, dict):
                continue
            if data.get("id"):
                self.latest[data["id"]] = data
            if data.get("id") == "get.settings.data":
                self._last_settings_read = time.monotonic()
            if (
                data.get("id") == "notify.update"
                and "get.settings.data" in json.dumps(data)
                and time.monotonic() - self._last_settings_read > 20
            ):
                # The TV says its quick settings changed: read them again
                # (it repeats this every ~3 s, hence the 20 s floor).
                self._last_settings_read = time.monotonic()
                await self._send(ws, "get.settings.data")

    async def _wait_for(self, response_id: str, timeout: float = 8):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if response_id in self.latest:
                return self.latest[response_id]
            await asyncio.sleep(0.1)
        print(f"! No {response_id} response within {timeout:.0f} s")
        return None

    def _layer(self, layer_id: str) -> dict | None:
        settings = self.latest.get("get.settings.data") or {}
        for layer in settings.get("response") or ():
            if isinstance(layer, dict) and layer.get("id") == layer_id:
                return layer
        return None

    async def _set_element(self, ws, element: dict) -> None:
        params = {
            "id": element.get("id"),
            "title": element.get("title"),
            "subTitle": element.get("subTitle"),
        }
        print(f"\n== WRITE set.settings.data {params}")
        await self._send(ws, "set.settings.data", params)

    async def _fresh_settings(self, ws) -> None:
        """Re-read the quick settings, so a write uses the options offered now.

        The first capture of the write test used a list read 11 s earlier: the
        receiver had taken the sound meanwhile and the sound-mode layer had
        dropped to Standard alone.
        """
        self.latest.pop("get.settings.data", None)
        await self._send(ws, "get.settings.data")
        await self._wait_for("get.settings.data")

    async def toggle_setting(self, ws, name: str) -> None:
        """Switch a quick setting (layer ``s/setting.layer.<name>``) and back."""
        await self._fresh_settings(ws)
        layer = self._layer(f"s/setting.layer.{name}")
        elements = (layer or {}).get("elements") or []
        if len(elements) < 2:
            print(
                f"! The {name} layer offers "
                f"{[e.get('subTitle') for e in elements]}: nothing to switch to "
                "(sound mode with sound on an external receiver?); skipping"
            )
            return
        try:
            current = int(layer.get("current_element_index"))
        except (TypeError, ValueError):
            current = 0
        original = elements[current]
        other = elements[(current + 1) % len(elements)]
        print(
            f"\n== {name}: {original.get('subTitle')} -> "
            f"{other.get('subTitle')} -> back, 8 s each"
        )
        await self._set_element(ws, other)
        await asyncio.sleep(8)
        await self._set_element(ws, original)
        await asyncio.sleep(8)

    async def switch_source(self, ws, target: str) -> None:
        """Launch another input, then back.

        ``target`` matches a source id (``HDMI3``, ``TV``) or any part of a
        title: the TV renamed HDMI3 from "CINEMA 60" to "Périphérique externe
        avec CINEMA 60" once the receiver was on, so exact titles do not hold.
        """
        self.latest.pop("get.extsources", None)
        await self._send(ws, "get.extsources")
        await self._wait_for("get.extsources")
        sources = (self.latest.get("get.extsources") or {}).get("response") or []
        current = next(
            (s for s in sources if str(s.get("currentSource")) == "true"), None
        )
        wanted = target.casefold()
        dest = next(
            (
                s
                for s in sources
                if str(s.get("sourceId", "")).casefold().endswith(f"@{wanted}")
                or wanted in str(s.get("title", "")).casefold()
            ),
            None,
        )
        if dest is None:
            titles = [s.get("title") for s in sources]
            print(f"! No source matching {target!r} in {titles}; skipping")
            return
        print(
            f"\n== Source: {current and current.get('title')} -> "
            f"{dest.get('title')} -> back, 12 s each"
        )
        await self._send(ws, "launch.extsource", {"requestId": dest["sourceId"]})
        await asyncio.sleep(12)
        await self._send(ws, "get.extsources")
        await asyncio.sleep(1)
        back = current or next(
            (s for s in sources if str(s.get("sourceId", "")).endswith("@TV")), None
        )
        if back is not None and back is not dest:
            # No source is "current" while an app is on screen; go back to TV.
            await self._send(ws, "launch.extsource", {"requestId": back["sourceId"]})
            await asyncio.sleep(12)
            await self._send(ws, "get.extsources")
            await asyncio.sleep(1)

    async def read_apps(self, ws) -> None:
        """Read the app tiles (home screen, recent, recommended); read-only.

        The TV did not answer the remote channel's ed.installedApp.get on a
        Frame 2024 (no reply in 3 minutes), so the media player's source list
        had no apps. These requests may list them locally instead.
        """
        print("\n== App tiles: get.apptiles, get.recent.apptiles, ...")
        for request in (
            "get.apptiles",
            "get.recent.apptiles",
            "get.recommended.apptiles",
        ):
            self.latest.pop(request, None)
            await self._send(ws, request)
            await self._wait_for(request, timeout=6)
        tiles = (self.latest.get("get.apptiles") or {}).get("response")
        if isinstance(tiles, list):
            for tile in tiles[:40]:
                if isinstance(tile, dict):
                    short = {
                        k: v
                        for k, v in tile.items()
                        if "icon" not in k.lower() and not isinstance(v, (dict, list))
                    }
                    print(f"   tile: {short}")

    @staticmethod
    def _tile_request_id(tile: dict):
        for key in ("requestId", "tileId", "id", "appId"):
            if tile.get(key):
                return key, tile[key]
        return None, None

    def _save_image(self, label: str, message) -> None:
        found = _find_image(message)
        if found is None:
            print(f"   {label}: no image in the answer")
            return
        where, data = found
        folder = self.out.parent / "probe_icons"
        folder.mkdir(exist_ok=True)
        safe = "".join(c if c.isalnum() else "_" for c in label)[:40]
        target = folder / f"{safe}.{_image_ext(data)}"
        target.write_bytes(data)
        print(f"   {label}: {len(data)} bytes ({where}) -> {target}")

    async def icons_mobilebff(self, ws) -> None:
        """get.icon on the quick-settings channel for a few tiles; read-only."""
        tiles = (self.latest.get("get.apptiles") or {}).get("response") or []
        sources = (self.latest.get("get.extsources") or {}).get("response") or []
        print("\n== get.icon (quick-settings channel)")
        for item in [*tiles[:3], *sources[:2]]:
            request_id = item.get("appId") or item.get("sourceId")
            if not request_id:
                continue
            self.latest.pop("get.icon", None)
            await self._send(ws, "get.icon", {"requestId": request_id})
            answer = await self._wait_for("get.icon", timeout=5)
            label = item.get("title") or request_id
            if answer is not None:
                self._save_image(f"bff_{label}", answer)

    async def read_extras(self, ws) -> None:
        """The remaining read requests of the decompiled list; read-only.

        get.apptile.functions / get.apptile.previews per app tile (with the
        tile id as requestId, the shape launch.app accepted), the saved
        Multi View layouts, the rotating-accessory probe and the AI Mode
        flags. Nothing here changes the TV.
        """
        tiles = (self.latest.get("get.apptiles") or {}).get("response") or []
        wanted = [t for t in tiles if t.get("title") in ("Netflix", "YouTube")]
        wanted = wanted or tiles[:2]
        print("\n== Extras: tile functions/previews, Multi View, rotator, AI")
        for tile in wanted:
            for request in ("get.apptile.functions", "get.apptile.previews"):
                self.latest.pop(request, None)
                await self._send(ws, request, {"requestId": tile.get("appId")})
                answer = await self._wait_for(request, timeout=6)
                text = json.dumps((answer or {}).get("response"), ensure_ascii=False)
                print(f"   {request} {tile.get('title')}: {text[:300]}")
        for request in (
            "get.multiview.saved.tiles",
            "get.RotatorSupport",
            "get.aitech.support",
            "get.aitech.enable",
            "get.gamepad.support",
        ):
            self.latest.pop(request, None)
            await self._send(ws, request)
            answer = await self._wait_for(request, timeout=6)
            text = json.dumps((answer or {}).get("response"), ensure_ascii=False)
            print(f"   {request}: {text[:300]}")

    async def icon_variants(self, ws) -> None:
        """Try get.icon with several parameter shapes for one app tile.

        The first capture got {"requestId": "", "base64": ""} for a tile id
        as requestId: the answer has the right shape, but the TV did not
        recognise the parameter. The decompiled firmware only names
        "requestId", so try what it could hold, and a few other keys.
        """
        tiles = (self.latest.get("get.apptiles") or {}).get("response") or []
        tile = next((t for t in tiles if "webapp" in str(t.get("appId"))), None)
        if tile is None:
            print("! No web app tile to try; skipping --icon-variants")
            return
        app_id = tile["appId"]
        tizen_id = app_id.rpartition("@")[2]
        icon_path = tile.get("iconPath", "")
        variants = [
            {"requestId": icon_path},
            {"requestId": tizen_id},
            {"requestId": app_id, "iconPath": icon_path},
            {"iconPath": icon_path},
            {"path": icon_path},
            {"appId": app_id},
            {"id": app_id},
            {"requestId": app_id, "type": "icon"},
        ]
        print(f"\n== get.icon variants for {tile.get('title')!r}")
        for params in variants:
            self.latest.pop("get.icon", None)
            await self._send(ws, "get.icon", params)
            answer = await self._wait_for("get.icon", timeout=4)
            response = (answer or {}).get("response") or {}
            size = (
                len(response.get("base64") or "") if isinstance(response, dict) else 0
            )
            print(
                f"   {params} -> base64 {size} chars, echo {response.get('requestId')!r}"
                if isinstance(response, dict)
                else f"   {params} -> {answer}"
            )
            if size:
                self._save_image(f"variant_{tile.get('title')}", answer)

    async def icons_remote(self, session, name: str, token: str | None) -> None:
        """ed.apps.icon on the remote-control channel (needs the token); read-only.

        Asks for the iconPath the tiles report; the TV answers on the same
        event with the image in base64 when it supports it.
        """
        tiles = (self.latest.get("get.apptiles") or {}).get("response") or []
        sources = (self.latest.get("get.extsources") or {}).get("response") or []
        paths = [
            (t.get("title"), t.get("iconPath")) for t in tiles[:3] if t.get("iconPath")
        ] + [(x.get("title"), x.get("icon")) for x in sources[:2] if x.get("icon")]
        if not paths:
            print("! No icon paths to ask for; skipping ed.apps.icon")
            return
        if not token:
            print("! No token for the remote-control channel; skipping ed.apps.icon")
            return
        url = (
            f"wss://{self.host}:8002/api/v2/channels/samsung.remote.control"
            f"?name={_b64(name)}&token={token}"
        )
        print("\n== ed.apps.icon (remote-control channel, integration's token)")
        try:
            remote = await asyncio.wait_for(
                session.ws_connect(url, ssl=_ssl_context(), heartbeat=20), timeout=10
            )
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as ex:
            print(f"! Remote channel connection failed: {ex!r}")
            return
        try:
            msg = await remote.receive(timeout=10)
            event = (
                (_decode(msg.data) or {}).get("event")
                if msg.type == aiohttp.WSMsgType.TEXT
                else None
            )
            if event != "ms.channel.connect":
                print(f"! Remote channel did not connect: {msg.type} {event}")
                return
            for label, icon_path in paths:
                outer = {
                    "method": "ms.channel.emit",
                    "params": {
                        "event": "ed.apps.icon",
                        "to": "host",
                        "data": {"iconPath": icon_path},
                    },
                }
                self._record("->", outer)
                await remote.send_str(json.dumps(outer))
                end = time.monotonic() + 5
                while (left := end - time.monotonic()) > 0:
                    try:
                        reply = await remote.receive(timeout=left)
                    except asyncio.TimeoutError:
                        break
                    if reply.type != aiohttp.WSMsgType.TEXT:
                        break
                    decoded = _decode(reply.data)
                    if (decoded or {}).get("event") == "ed.apps.icon":
                        self._record("<-", reply.data)
                        self._save_image(f"remote_{label}", decoded)
                        break
                else:
                    print(f"   remote_{label}: no ed.apps.icon answer")
        finally:
            await remote.close()

    async def launch_app(self, ws, target: str) -> None:
        """Launch one app tile by (part of) its title, then go back to TV."""
        tiles = (self.latest.get("get.apptiles") or {}).get("response")
        if not isinstance(tiles, list):
            self.latest.pop("get.apptiles", None)
            await self._send(ws, "get.apptiles")
            await self._wait_for("get.apptiles", timeout=6)
            tiles = (self.latest.get("get.apptiles") or {}).get("response") or []
        wanted = target.casefold()
        tile = next(
            (
                t
                for t in tiles
                if isinstance(t, dict)
                and wanted in str(t.get("title") or t.get("name") or "").casefold()
            ),
            None,
        )
        if tile is None:
            titles = [
                t.get("title") or t.get("name") for t in tiles if isinstance(t, dict)
            ]
            print(f"! No app tile matching {target!r} in {titles}; skipping")
            return
        key, request_id = self._tile_request_id(tile)
        if request_id is None:
            print(f"! The tile has no id field to launch it with: {tile}; skipping")
            return
        print(f"\n== Launch app {tile.get('title')!r} (requestId from {key}), 12 s")
        await self._send(ws, "launch.app", {"requestId": request_id})
        await asyncio.sleep(12)
        sources = (self.latest.get("get.extsources") or {}).get("response") or []
        tv = next(
            (x for x in sources if str(x.get("sourceId", "")).endswith("@TV")), None
        )
        if tv is not None:
            await self._send(ws, "launch.extsource", {"requestId": tv["sourceId"]})
            await asyncio.sleep(5)

    async def read_vconf(self, ws, keys: list[str]) -> None:
        """Read each key as int, string and bool; the TV answers the right one."""
        print(f"\n== vconf reads: {keys}")
        for key in keys:
            for kind in ("int", "string", "bool"):
                await self._send(ws, f"request.vconf.info.{kind}", {"key": key})
                await asyncio.sleep(0.3)
        await asyncio.sleep(2)

    async def run(self, ws, actions: argparse.Namespace) -> None:
        reader = asyncio.ensure_future(self._reader(ws))
        try:
            for request_id in READ_REQUESTS:
                await self._send(ws, request_id)
                await asyncio.sleep(0.3)
            await self._wait_for("get.settings.data")
            await self._wait_for("get.extsources")
            if actions.vconf:
                await self.read_vconf(ws, actions.vconf)
            if actions.toggle_sound_mode:
                await self.toggle_setting(ws, "soundmode")
            for name in actions.toggle_setting or ():
                await self.toggle_setting(ws, name)
            if actions.switch_source:
                await self.switch_source(ws, actions.switch_source)
            if actions.apps or actions.launch_app or actions.icons:
                await self.read_apps(ws)
            if actions.extras:
                if not (actions.icons or actions.apps or actions.launch_app):
                    await self.read_apps(ws)
                await self.read_extras(ws)
            if actions.icon_variants:
                if not actions.icons and not actions.apps:
                    await self.read_apps(ws)
                await self.icon_variants(ws)
            if actions.icons:
                await self.icons_mobilebff(ws)
                await self.icons_remote(actions.session, actions.name, actions.token)
            if actions.launch_app:
                await self.launch_app(ws, actions.launch_app)
            print(
                f"\n== Listening {self.listen:.0f} s: change the picture mode, "
                "switch input, open the quick settings now.\n"
            )
            await asyncio.wait({reader}, timeout=self.listen)
        finally:
            reader.cancel()
            await ws.close()

    def save(self) -> None:
        self.out.write_text(
            json.dumps(
                {"host": self.host, "channel": CHANNEL, "records": self.records},
                ensure_ascii=False,
                indent=2,
            )
        )
        print(f"\n== Saved {len(self.records)} messages to {self.out}")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("host", help="TV IP address, e.g. 192.168.1.161")
    parser.add_argument("--port", type=int, default=8002, choices=(8001, 8002))
    parser.add_argument("--listen", type=float, default=90, help="seconds")
    parser.add_argument(
        "--storage",
        type=Path,
        default=Path("/config/.storage/core.config_entries"),
        help="Home Assistant config entries file (to reuse the token)",
    )
    parser.add_argument("--out-dir", type=Path, default=Path("/config"))
    writes = parser.add_argument_group(
        "writes (each one changes the TV, then puts it back)"
    )
    writes.add_argument(
        "--toggle-sound-mode",
        action="store_true",
        help="set.settings.data: switch the sound mode to another option, then back",
    )
    writes.add_argument(
        "--toggle-setting",
        metavar="LAYER",
        action="append",
        help="set.settings.data: switch the quick setting s/setting.layer.LAYER "
        "(colortone, automotion, contrastenhancer, ...) to another option, "
        "then back (repeatable)",
    )
    writes.add_argument(
        "--switch-source",
        metavar="NAME",
        help="launch.extsource: switch to this input (title or source id), then back",
    )
    writes.add_argument(
        "--launch-app",
        metavar="NAME",
        help="launch.app: open the app tile whose title contains NAME, then go "
        "back to the TV input",
    )
    parser.add_argument(
        "--extras",
        action="store_true",
        help="tile functions/previews, saved Multi View layouts, rotator and AI "
        "flags; read-only",
    )
    parser.add_argument(
        "--icon-variants",
        action="store_true",
        help="try get.icon with several parameter shapes for one app; read-only",
    )
    parser.add_argument(
        "--icons",
        action="store_true",
        help="fetch a few app and input icons (get.icon here, ed.apps.icon on the "
        "remote-control channel with the integration's token); read-only, saved "
        "to OUT_DIR/probe_icons",
    )
    parser.add_argument(
        "--apps",
        action="store_true",
        help="read the app tiles (get.apptiles, recent, recommended); read-only",
    )
    parser.add_argument(
        "--vconf",
        metavar="KEY",
        action="append",
        help="request.vconf.info: read this key (repeatable); read-only",
    )
    parser.add_argument(
        "--vconf-defaults",
        action="store_true",
        help=f"also read {len(VCONF_CANDIDATES)} candidate keys (see VCONF_CANDIDATES)",
    )
    args = parser.parse_args()
    if args.vconf_defaults:
        args.vconf = [*(args.vconf or []), *VCONF_CANDIDATES]

    out = args.out_dir / f"mobilebff_probe_{time.strftime('%Y%m%d_%H%M%S')}.json"
    probe = Probe(args.host, args.port, args.listen, out)
    token, name = _integration_credentials(args.storage, args.host)

    async with aiohttp.ClientSession() as session:
        ws = await probe.connect(session, f"{name} Probe", None)
        if ws is None and token and args.port == 8002:
            ws = await probe.connect(session, name, token)
        if ws is not None:
            args.session, args.name, args.token = session, name, token
            await probe.run(ws, args)
    probe.save()
    return 0 if ws is not None else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
