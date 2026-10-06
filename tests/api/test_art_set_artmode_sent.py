"""set_artmode says whether its request reached the socket.

_ensure_art_mode_ready holds a failed art-on against a retry (the write
guard) only if the write may have reached the TV. Asking whether the channel
is open after the call is not that: a TV can take the write and drop the
channel before replying. set_artmode records whether its own request was
handed to the socket.
"""

import asyncio

import aiohttp


class _RefusingSession:
    closed = False

    async def ws_connect(self, url, **_kwargs):
        await asyncio.sleep(0)
        raise aiohttp.ClientConnectionError("Connection refused")


class _OpenWS:
    closed = False

    def __init__(self):
        self.sent = []

    async def send_json(self, data):
        self.sent.append(data)


class _ResetWS(_OpenWS):
    """The TV reset the TCP connection; the receive loop has not noticed."""

    async def send_json(self, data):
        raise aiohttp.ClientConnectionResetError("Cannot write to closing transport")


async def test_nothing_sent_when_the_channel_cannot_open(art_client):
    art_client._external_session = _RefusingSession()
    art_client.last_set_artmode_sent = True

    assert await art_client.set_artmode(True) is False
    assert art_client.last_set_artmode_sent is False


async def test_sent_when_the_request_left_but_no_reply_came(art_client, monkeypatch):
    ws = _OpenWS()
    art_client._ws = ws
    art_client._connected = True

    async def no_reply(_key, _timeout):
        return None  # timed out, or the TV dropped the channel first

    monkeypatch.setattr(art_client, "_wait_for_response", no_reply)

    assert await art_client.set_artmode(True) is False
    assert art_client.last_set_artmode_sent is True
    assert '"set_artmode_status"' in ws.sent[0]["params"]["data"]


async def test_nothing_sent_when_send_json_fails(art_client):
    art_client._ws = _ResetWS()
    art_client._connected = True

    assert await art_client.set_artmode(True) is False
    assert art_client.last_set_artmode_sent is False
    assert art_client._connected is False
