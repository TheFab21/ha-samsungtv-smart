"""The app list must not stay empty after one empty get.apptiles answer.

get.apptiles was sent once per quick-settings connection. On a 2025 Frame
(QE43LS03FAUXXH) Home Assistant restarted while the panel was switching out of
Art Mode: the channel opened, the tiles came back empty, and the source list
stayed ``['ТВ']`` until the channel reconnected. Every app button then logged
"Unsupported source 'A1 Xplore TV'. Known sources: ТВ" and did nothing; a
config-entry reload brought all eleven sources back at once.

The channel now asks again after a few delays while the list is empty.
"""

import asyncio

from custom_components.samsungtv_smart.api import mobilebff as bff


class _Ws:
    closed = False


def _client(monkeypatch, answers):
    """A client whose get.apptiles answers come from ``answers`` in order."""
    monkeypatch.setattr(bff, "APP_TILES_RETRY_DELAYS", (0, 0, 0, 0))
    client = bff.SamsungMobileBFF(None, "192.0.2.1", "t", lambda: True)
    client._ws = _Ws()
    sent = []

    async def fake_send(request, params=None, seq=None):
        sent.append(request)
        tiles = answers.pop(0) if answers else []
        client._handle(
            {
                "event": bff.EVENT,
                "data": bff.json.dumps(
                    {"version": "0.2", "id": request, "response": tiles}
                ),
            }
        )
        return "1"

    client._send = fake_send
    return client, sent


NETFLIX = [{"appId": "d/webapp@3201907018807", "title": "Netflix"}]


def test_empty_answer_is_retried_until_tiles_arrive(monkeypatch):
    client, sent = _client(monkeypatch, [[], NETFLIX])
    asyncio.run(client._ensure_app_tiles())
    assert sent == ["get.apptiles", "get.apptiles"]
    assert [t.title for t in client.apps] == ["Netflix"]


def test_no_retry_when_tiles_already_listed(monkeypatch):
    client, sent = _client(monkeypatch, [])
    client._handle(
        {
            "event": bff.EVENT,
            "data": bff.json.dumps({"id": "get.apptiles", "response": NETFLIX}),
        }
    )
    asyncio.run(client._ensure_app_tiles())
    assert sent == []


def test_gives_up_after_the_last_delay(monkeypatch):
    client, sent = _client(monkeypatch, [])
    asyncio.run(client._ensure_app_tiles())
    assert sent == ["get.apptiles"] * len(bff.APP_TILES_RETRY_DELAYS)
    assert client.apps == ()


def test_stops_when_the_channel_closes(monkeypatch):
    client, sent = _client(monkeypatch, [])
    client._ws.closed = True
    asyncio.run(client._ensure_app_tiles())
    assert sent == []
