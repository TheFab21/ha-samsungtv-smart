"""Pre-4.x Frames upload over a binary WebSocket frame, not D2D (#307).

A 2019 Frame (art API "0.97") answers the D2D ``send_image`` handshake with
SYSTEM_FAIL (-1). Those Frames take the image as one binary WS frame instead.
The transport is version-gated: 4.x and up (and unknown versions) keep D2D.
"""

import asyncio
import json

import pytest


def test_art_api_major(art_client):
    import art

    assert art._art_api_major("0.97") == 0
    assert art._art_api_major("4.3.4.0") == 4
    assert art._art_api_major("2.03") == 2
    assert art._art_api_major(None) is None
    assert art._art_api_major("") is None
    assert art._art_api_major("weird") is None


def test_which_versions_use_ws_binary(art_client):
    import art

    assert art._art_uses_ws_binary_upload("0.97") is True
    assert art._art_uses_ws_binary_upload("2.03") is True
    assert art._art_uses_ws_binary_upload("3.1.0") is True
    assert art._art_uses_ws_binary_upload("4.3.4.0") is False
    assert art._art_uses_ws_binary_upload("4.0") is False
    # Unknown -> keep the current (D2D) transport.
    assert art._art_uses_ws_binary_upload(None) is False
    assert art._art_uses_ws_binary_upload("junk") is False


def test_build_ws_image_frame(art_client):
    import art

    envelope = {"method": "ms.channel.emit", "params": {"event": "art_app_request"}}
    data = b"\xff\xd8\xff\xe0JPEGBYTES"
    frame = art._build_ws_image_frame(envelope, data)

    header_len = int.from_bytes(frame[:2], "big")
    header = frame[2 : 2 + header_len]
    assert frame[2 + header_len :] == data  # raw bytes appended verbatim
    assert json.loads(header) == envelope  # compact JSON round-trips

    with pytest.raises(ValueError):
        art._build_ws_image_frame({"x": "y" * 70000}, data)


class _FakeWS:
    """A ws whose send_bytes captures the frame and answers the pending future."""

    closed = False

    def __init__(self, client, reply):
        self._client = client
        self._reply = reply
        self.sent = None

    async def send_bytes(self, frame):
        self.sent = frame
        # The TV echoes the request id with the result; resolve it like the
        # receive loop would.
        req_id = self._reply.get("id")
        fut = self._client._pending_requests.get(req_id)
        if fut and not fut.done():
            fut.set_result(self._reply)


async def _run_ws_binary(art_client, reply):
    art_client._connected = True
    art_client._ws = _FakeWS(art_client, reply)
    return await art_client._upload_ws_binary(
        b"IMAGEDATA",
        "shadowbox_polar",
        "none",
        "jpg",
        "2026:10:07 00:00:00",
        reply["id"],
        5,
    )


async def test_ws_binary_upload_returns_content_id(art_client):
    reply = {"id": "req-1", "event": "image_added", "content_id": "MY_F0096"}
    result = await _run_ws_binary(art_client, reply)

    assert result == "MY_F0096"
    frame = art_client._ws.sent
    header_len = int.from_bytes(frame[:2], "big")
    envelope = json.loads(frame[2 : 2 + header_len])
    inner = json.loads(envelope["params"]["data"])
    assert envelope["params"]["event"] == "art_app_request"
    assert inner["request"] == "send_image"
    assert inner["file_type"] == "JPEG"  # jpg is sent as JPEG
    assert inner["id"] == "req-1"
    assert "conn_info" not in inner  # the D2D socket handshake is NOT used
    assert frame[2 + header_len :] == b"IMAGEDATA"


async def test_ws_binary_upload_error_reply_returns_none(art_client):
    reply = {"id": "req-2", "event": "error", "error_code": "-1"}
    assert await _run_ws_binary(art_client, reply) is None


async def test_upload_locked_routes_old_api_to_ws_binary(art_client, monkeypatch):
    calls = {}

    async def fake_version():
        return "0.97"

    async def fake_ws_binary(*args, **kwargs):
        calls["ws_binary"] = True
        return "MY_F0001"

    async def fail_send(*a, **k):
        calls["d2d"] = True
        return None

    monkeypatch.setattr(art_client, "_resolve_api_version", fake_version)
    monkeypatch.setattr(art_client, "_upload_ws_binary", fake_ws_binary)
    monkeypatch.setattr(art_client, "_send_art_request", fail_send)

    out = await art_client._upload_locked(
        b"IMG", "none", "none", "jpg", "2026:10:07 00:00:00", 5, "req-3"
    )
    assert out == "MY_F0001"
    assert calls == {"ws_binary": True}  # D2D send_image never attempted


async def test_upload_locked_keeps_d2d_for_new_api(art_client, monkeypatch):
    calls = {}

    async def fake_version():
        return "4.3.4.0"

    async def fake_ws_binary(*a, **k):
        calls["ws_binary"] = True
        return "X"

    async def fake_send(*a, **k):
        calls["d2d"] = True
        return None  # no conn_info -> upload returns None, fine for this test

    monkeypatch.setattr(art_client, "_resolve_api_version", fake_version)
    monkeypatch.setattr(art_client, "_upload_ws_binary", fake_ws_binary)
    monkeypatch.setattr(art_client, "_send_art_request", fake_send)

    await art_client._upload_locked(
        b"IMG", "none", "none", "jpg", "2026:10:07 00:00:00", 5, "req-4"
    )
    assert calls == {"d2d": True}  # WS-binary not used on 4.x


async def test_resolve_api_version_is_cached(art_client, monkeypatch):
    n = {"calls": 0}

    async def one_version():
        n["calls"] += 1
        return "0.97"

    monkeypatch.setattr(art_client, "get_api_version", one_version)
    assert await art_client._resolve_api_version() == "0.97"
    assert await art_client._resolve_api_version() == "0.97"
    assert n["calls"] == 1  # queried once, then cached
