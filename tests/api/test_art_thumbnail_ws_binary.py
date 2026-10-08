"""A 0.97 Frame returns a thumbnail over a binary WebSocket frame (#311).

The 2019 Frame (art API "0.97") answers ``get_thumbnail`` with the image bytes
as a single binary WebSocket frame — the same 2-byte-length + JSON-header + raw
-bytes envelope the 0.97 upload path uses (#307) — instead of handing back
conn_info for a separate socket. ``_process_binary`` resolves the waiting
request with the bytes attached, and ``get_thumbnail`` returns them. Newer
Frames never send a binary frame, so the conn_info path is untouched.
"""

import asyncio
import json

import pytest


def _thumbnail_frame(content_id, payload, *, request_id=None):
    """Build the binary frame a 0.97 Frame sends for a thumbnail."""
    import art

    inner = {"event": "thumbnail", "content_id": content_id}
    if request_id is not None:
        inner["request_id"] = request_id
        inner["id"] = request_id
    header = {"event": "d2d_service_message", "data": json.dumps(inner)}
    return art._build_ws_image_frame(header, payload)


def test_process_binary_resolves_a_content_id_waiter(art_client):
    loop = asyncio.get_event_loop()
    fut = loop.create_future()
    art_client._pending_requests["MY_F0096"] = fut

    art_client._process_binary(_thumbnail_frame("MY_F0096", b"JPEGBYTES"))

    assert fut.done()
    assert fut.result()["binary"] == b"JPEGBYTES"
    assert fut.result()["content_id"] == "MY_F0096"


def test_process_binary_prefers_the_echoed_request_id(art_client):
    loop = asyncio.get_event_loop()
    by_id = loop.create_future()
    by_cid = loop.create_future()
    art_client._pending_requests["req-1"] = by_id
    art_client._pending_requests["MY_F0096"] = by_cid

    art_client._process_binary(
        _thumbnail_frame("MY_F0096", b"BYTES", request_id="req-1")
    )

    # request_id is matched first; the content_id waiter is left for its own
    # resolver so a single frame never resolves two different requests.
    assert by_id.done() and by_id.result()["binary"] == b"BYTES"
    assert not by_cid.done()


def test_process_binary_ignores_a_non_thumbnail_frame(art_client):
    import art

    loop = asyncio.get_event_loop()
    fut = loop.create_future()
    art_client._pending_requests["MY_F0096"] = fut

    header = {"event": "d2d_service_message", "data": json.dumps({"event": "other"})}
    art_client._process_binary(art._build_ws_image_frame(header, b"x"))

    assert not fut.done()


@pytest.mark.parametrize(
    "raw",
    [
        b"",  # too short for a length prefix
        b"\x00",  # one byte
        b"\xff\xff\x01\x02",  # header length overruns the frame
        b"\x00\x04not-json-at-all-just-filler",  # header is not JSON
    ],
)
def test_process_binary_tolerates_a_malformed_frame(art_client, raw):
    # A raise here would kill the receive loop and force a reconnect.
    art_client._process_binary(raw)


class _ThumbnailWS:
    """A ws whose send_json answers the request with a 0.97 binary frame."""

    closed = False

    def __init__(self, client, payload, *, echo_request_id):
        self._client = client
        self._payload = payload
        self._echo = echo_request_id

    async def send_json(self, command):
        request = json.loads(command["params"]["data"])
        content_id = request["content_id"]
        rid = request["request_id"] if self._echo else None
        self._client._process_binary(
            _thumbnail_frame(content_id, self._payload, request_id=rid)
        )


async def _run_get_thumbnail(art_client, *, echo_request_id):
    art_client._connected = True
    art_client._supports_thumbnail_list = False  # skip the list probe
    art_client._ws = _ThumbnailWS(
        art_client, b"THUMBNAILBYTES", echo_request_id=echo_request_id
    )
    return await art_client.get_thumbnail("MY_F0096")


async def test_get_thumbnail_returns_bytes_from_a_binary_frame(art_client):
    result = await _run_get_thumbnail(art_client, echo_request_id=False)

    assert result == b"THUMBNAILBYTES"
    # Having proven this TV serves thumbnails over the binary frame, the list
    # probe is skipped from now on — and no alias is left dangling.
    assert art_client._supports_thumbnail_list is False
    assert "MY_F0096" not in art_client._pending_requests


async def test_get_thumbnail_binary_frame_keyed_by_request_id(art_client):
    # Same outcome whether the frame echoes our request_id or only the
    # content_id — both resolve the one await.
    result = await _run_get_thumbnail(art_client, echo_request_id=True)
    assert result == b"THUMBNAILBYTES"


async def test_alias_does_not_disturb_normal_request_id_resolution(art_client):
    """A conn_info-style answer (newer Frames) still resolves by request_id,
    and the content_id alias is cleaned up afterwards."""
    art_client._connected = True

    class _ConnInfoWS:
        closed = False

        async def send_json(self, command):
            request = json.loads(command["params"]["data"])
            # Resolve by request_id, exactly as _process_event would for a
            # text conn_info reply on a D2D-capable TV.
            fut = art_client._pending_requests.get(request["request_id"])
            if fut and not fut.done():
                fut.set_result({"event": "ready", "conn_info": "{}"})

    art_client._ws = _ConnInfoWS()

    data = await art_client._send_art_request(
        {"request": "get_thumbnail", "content_id": "MY_F0096"},
        timeout=5,
        alias_keys=["MY_F0096"],
    )

    assert data == {"event": "ready", "conn_info": "{}"}
    assert "MY_F0096" not in art_client._pending_requests  # alias cleaned up
