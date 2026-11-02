#!/usr/bin/env python3
"""Probe the TV's local ``com.samsung.tv.mobilebff`` WebSocket channel.

Question it answers: does the TV publish its picture mode (and the modes the
current input offers) locally, through the launcher's quick settings, so the
integration would not need the SmartThings cloud for that list?

The channel and its requests come from the decompiled 2025 Frame firmware
(notes/QN55LS03FAFXZA/WEBSOCKET_DECOMPILED.md, "MobileBFF Channel"). Nothing in
it has been tried on a live TV yet, which is what this script is for.

It only READS: it sends ``ask.service.status``, ``get.settings.data`` and
``get.extsources``, never a ``set.*`` or ``launch.*`` request. It then listens
and asks for the settings again each time the TV announces they changed.

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

    async def run(self, ws) -> None:
        for request_id in READ_REQUESTS:
            await self._send(ws, request_id)
            await asyncio.sleep(0.3)
        print(
            f"\n== Listening {self.listen:.0f} s: change the picture mode, switch "
            "input, open the quick settings now.\n"
        )
        end = time.monotonic() + self.listen
        while (left := end - time.monotonic()) > 0:
            try:
                msg = await ws.receive(timeout=left)
            except asyncio.TimeoutError:
                break
            if msg.type != aiohttp.WSMsgType.TEXT:
                print(f"! Socket closed: {msg.type} {msg.data!r}")
                break
            self._record("<-", msg.data)
            data = (_decode(msg.data) or {}).get("data")
            if (
                isinstance(data, dict)
                and data.get("id") == "notify.update"
                and "get.settings.data" in json.dumps(data)
            ):
                # The TV says its quick settings changed: read them again.
                await self._send(ws, "get.settings.data")
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
    args = parser.parse_args()

    out = args.out_dir / f"mobilebff_probe_{time.strftime('%Y%m%d_%H%M%S')}.json"
    probe = Probe(args.host, args.port, args.listen, out)
    token, name = _integration_credentials(args.storage, args.host)

    async with aiohttp.ClientSession() as session:
        ws = await probe.connect(session, f"{name} Probe", None)
        if ws is None and token and args.port == 8002:
            ws = await probe.connect(session, name, token)
        if ws is not None:
            await probe.run(ws)
    probe.save()
    return 0 if ws is not None else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
