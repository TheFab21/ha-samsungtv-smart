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
  --switch-source NAME     launch.extsource: to that input, then back
  --vconf KEY / --vconf-defaults
                           request.vconf.info reads (read-only)

e.g. ... probe_mobilebff.py 192.168.1.161 --toggle-sound-mode \\
         --switch-source "CINEMA 60" --vconf-defaults

Run it on the Home Assistant host, where aiohttp is already installed, e.g.:

    docker exec -it homeassistant python3 /config/probe_mobilebff.py 192.168.1.161

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

    async def toggle_sound_mode(self, ws) -> None:
        """Switch the sound mode to another option, then back."""
        layer = self._layer("s/setting.layer.soundmode")
        elements = (layer or {}).get("elements") or []
        if len(elements) < 2:
            print("! No sound-mode layer with two options; skipping")
            return
        try:
            current = int(layer.get("current_element_index"))
        except (TypeError, ValueError):
            current = 0
        original = elements[current]
        other = elements[(current + 1) % len(elements)]
        print(
            f"\n== Sound mode: {original.get('subTitle')} -> "
            f"{other.get('subTitle')} -> back, 8 s each"
        )
        await self._set_element(ws, other)
        await asyncio.sleep(8)
        await self._set_element(ws, original)
        await asyncio.sleep(8)

    async def switch_source(self, ws, target: str) -> None:
        """Launch another input (matched by title or source id), then back."""
        sources = (self.latest.get("get.extsources") or {}).get("response") or []
        current = next(
            (s for s in sources if str(s.get("currentSource")) == "true"), None
        )
        wanted = target.casefold()
        dest = next(
            (
                s
                for s in sources
                if wanted
                in (
                    str(s.get("title", "")).casefold(),
                    str(s.get("sourceId", "")).casefold(),
                )
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
        if current is not None:
            await self._send(ws, "launch.extsource", {"requestId": current["sourceId"]})
            await asyncio.sleep(12)
            await self._send(ws, "get.extsources")
            await asyncio.sleep(1)

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
                await self.toggle_sound_mode(ws)
            if actions.switch_source:
                await self.switch_source(ws, actions.switch_source)
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
        "--switch-source",
        metavar="NAME",
        help="launch.extsource: switch to this input (title or source id), then back",
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
            await probe.run(ws, args)
    probe.save()
    return 0 if ws is not None else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
