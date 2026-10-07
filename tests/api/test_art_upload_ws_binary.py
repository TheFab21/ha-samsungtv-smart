"""A 2019 Frame falls back to a binary WebSocket upload on SYSTEM_FAIL (#307).

The 2019 Frame (art API "0.97") answers the D2D ``send_image`` handshake with
SYSTEM_FAIL (-1). The upload then retries over the legacy binary-WS transport
the reporter verified on-device. Every other refusal, and every TV that
accepts D2D, is untouched — there is no version guessing.
"""

import json

import pytest


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
        req_id = self._reply.get("id")
        fut = self._client._pending_requests.get(req_id)
        if fut and not fut.done():
            fut.set_result(dict(self._reply))


async def _run_ws_binary(art_client, reply):
    art_client._connected = True
    art_client._ws = _FakeWS(art_client, reply)
    return await art_client._upload_ws_binary(
        b"IMAGEDATA", "shadowbox_polar", "jpg", reply["id"], 5
    )


async def test_ws_binary_payload_is_the_verified_minimal_set(art_client):
    reply = {"id": "req-1", "event": "image_added", "content_id": "MY_F0096"}
    result = await _run_ws_binary(art_client, reply)

    assert result == "MY_F0096"
    frame = art_client._ws.sent
    header_len = int.from_bytes(frame[:2], "big")
    envelope = json.loads(frame[2 : 2 + header_len])
    inner = json.loads(envelope["params"]["data"])
    assert envelope["params"]["event"] == "art_app_request"
    # Exactly the four fields the reporter verified on the 2019 Frame — no
    # conn_info, portrait_matte_id, file_size or image_date.
    assert inner == {
        "request": "send_image",
        "file_type": "JPEG",
        "matte_id": "shadowbox_polar",
        "id": "req-1",
    }
    assert frame[2 + header_len :] == b"IMAGEDATA"


async def test_ws_binary_error_reply_returns_none(art_client):
    reply = {"id": "req-2", "event": "error", "error_code": "-1"}
    assert await _run_ws_binary(art_client, reply) is None


async def _run_upload_locked(art_client, monkeypatch, send_image_reply):
    """Drive _upload_locked with a scripted send_image response and a fake ws
    that answers the WS-binary fallback with a content_id."""
    sent = {}

    async def fake_send_art_request(payload, timeout=15):
        sent["d2d"] = payload
        return send_image_reply

    monkeypatch.setattr(art_client, "_send_art_request", fake_send_art_request)
    art_client._connected = True

    # The fallback resolves its own fresh request id; capture it from the frame.
    class _WS:
        closed = False

        async def send_bytes(self, frame):
            header_len = int.from_bytes(frame[:2], "big")
            inner = json.loads(json.loads(frame[2 : 2 + header_len])["params"]["data"])
            sent["ws_binary"] = inner
            fut = art_client._pending_requests.get(inner["id"])
            if fut and not fut.done():
                fut.set_result({"id": inner["id"], "content_id": "MY_F0096"})

    art_client._ws = _WS()
    out = await art_client._upload_locked(
        b"IMG", "none", "none", "jpg", "2026:10:07 00:00:00", 5, "req-d2d"
    )
    return out, sent


async def test_d2d_system_fail_falls_back_to_ws_binary(art_client, monkeypatch):
    out, sent = await _run_upload_locked(
        art_client, monkeypatch, {"event": "error", "error_code": "-1"}
    )
    assert out == "MY_F0096"
    assert "d2d_mode" in sent["d2d"]["conn_info"]  # D2D was tried first
    assert sent["ws_binary"]["request"] == "send_image"  # then WS-binary
    assert sent["ws_binary"]["id"] != "req-d2d"  # a fresh id for the retry


async def test_other_d2d_errors_do_not_fall_back(art_client, monkeypatch):
    out, sent = await _run_upload_locked(
        art_client, monkeypatch, {"event": "error", "error_code": "7"}
    )
    assert out is None
    assert "ws_binary" not in sent  # only SYSTEM_FAIL (-1) triggers the retry
