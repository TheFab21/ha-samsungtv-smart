"""App icons fetched from the TV over the quick-settings channel.

Measured on a Frame 2024 (QE55LS03D): get.apptiles names each app's icon
file (/opt/share/webappservice/apps_icon/all/<id>/152x152.png), and get.icon
returns it in base64 when that PATH is the requestId - the SmartThings
tile's icon came back as a 152x152 PNG, 15 kB. A tile id or a Tizen id as
requestId returned an empty base64; ed.apps.icon on the remote channel got
no answer at all. The online logo match it replaces had picked "awesometv"
for Swisscom blue TV.
"""

import asyncio
import base64
import json
from types import SimpleNamespace

from custom_components.samsungtv_smart import media_player as mp
from custom_components.samsungtv_smart.api import mobilebff as bff
from custom_components.samsungtv_smart.const import DATA_MOBILEBFF, DOMAIN

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
NETFLIX_ICON = "/opt/share/webappservice/apps_icon/all/3201907018807/152x152.png"


def _msg(kind, response, seq=None):
    data = {"version": "0.2", "id": kind, "seqId": seq, "response": response}
    return {"event": bff.EVENT, "data": json.dumps(data)}


class _FakeWS:
    closed = False

    def __init__(self):
        self.sent = []

    async def send_str(self, text):
        self.sent.append(json.loads(json.loads(text)["params"]["data"]))


def _client():
    client = bff.SamsungMobileBFF(None, "192.0.2.1", "t", lambda: True)
    client._ws = _FakeWS()
    client._handle(
        _msg(
            "get.apptiles",
            [
                {
                    "appId": "d/webapp@3201907018807",
                    "title": "Netflix",
                    "iconPath": NETFLIX_ICON,
                },
                {"appId": "d/webapp@111299001912", "title": "YouTube"},
            ],
        )
    )
    return client


def test_tiles_keep_their_icon_path():
    client = _client()
    assert client.find_app("3201907018807").icon_path == NETFLIX_ICON
    assert client.find_app("111299001912").icon_path is None


def _answer(client, encoded):
    async def tv():
        while not client._ws.sent:
            await asyncio.sleep(0.001)
        request = client._ws.sent[-1]
        client._handle(
            _msg(
                "get.icon",
                {"requestId": request["params"]["requestId"], "base64": encoded},
                request["seqId"],
            )
        )

    return tv()


def test_get_icon_sends_the_path_and_decodes_the_image():
    client = _client()

    async def run():
        tv = asyncio.ensure_future(_answer(client, base64.b64encode(PNG).decode()))
        data = await client.async_get_icon(NETFLIX_ICON)
        await tv
        return data

    assert asyncio.run(run()) == PNG
    assert client._ws.sent[-1]["params"] == {"requestId": NETFLIX_ICON}


def test_an_empty_answer_is_no_icon():
    client = _client()

    async def run():
        tv = asyncio.ensure_future(_answer(client, ""))
        data = await client.async_get_icon(NETFLIX_ICON)
        await tv
        return data

    assert asyncio.run(run()) is None


def _device(client):
    device = object.__new__(mp.SamsungTVDevice)
    device._entry_id = "e1"
    device.hass = SimpleNamespace(data={DOMAIN: {"e1": {DATA_MOBILEBFF: client}}})
    device._app_list = {"Netflix": "3201907018807", "YouTube": "111299001912"}
    device._running_app = "Netflix"
    device._tv_app_icons = {}
    return device


def test_the_running_app_gets_its_tv_icon_once():
    client = _client()
    fetched = []

    async def get_icon(path):
        fetched.append(path)
        return PNG

    client.async_get_icon = get_icon
    device = _device(client)
    url = asyncio.run(device._tv_app_icon_url())
    assert url == f"{mp.TV_APP_ICON_URL}3201907018807"
    assert asyncio.run(device._tv_app_icon_url()) == url
    assert fetched == [NETFLIX_ICON]

    device._attr_media_image_url = url
    assert asyncio.run(device.async_get_media_image()) == (PNG, "image/png")


def test_no_icon_without_a_tile_icon_or_while_the_channel_is_closed():
    client = _client()
    device = _device(client)
    device._running_app = "YouTube"
    assert asyncio.run(device._tv_app_icon_url()) is None
    device._running_app = "Netflix"
    client._ws = None
    assert asyncio.run(device._tv_app_icon_url()) is None
    device._running_app = mp.DEFAULT_APP
    assert asyncio.run(device._tv_app_icon_url()) is None


def test_a_cached_icon_survives_the_channel_closing():
    client = _client()
    device = _device(client)
    device._tv_app_icons["3201907018807"] = PNG
    client._ws = None
    assert asyncio.run(device._tv_app_icon_url()) == (
        f"{mp.TV_APP_ICON_URL}3201907018807"
    )


def test_the_icon_follows_the_title_not_the_lagging_running_app():
    # Reported: SmartThings -> Swisscom blue TV kept SmartThings' icon, then
    # YouTube showed Swisscom's: the running-app scan lags a switch by up to
    # ~9 s, and the icon was taken from it while the title had moved on.
    client = _client()
    fetched = []

    async def get_icon(path):
        fetched.append(path)
        return PNG

    client.async_get_icon = get_icon
    device = _device(client)
    device._running_app = "YouTube"  # stale scan
    url = asyncio.run(device._tv_app_icon_url("Netflix"))
    assert url == f"{mp.TV_APP_ICON_URL}3201907018807"
    assert fetched == [NETFLIX_ICON]


def test_a_title_from_smartthings_matches_its_app_whatever_the_case():
    device = _device(_client())
    device._running_app = mp.DEFAULT_APP
    assert device._media_icon_app("NETFLIX") == "Netflix"


def test_without_an_app_title_the_running_app_is_used():
    device = _device(_client())
    assert device._media_icon_app("TV Plus") == "Netflix"
    device._running_app = mp.DEFAULT_APP
    assert device._media_icon_app("TV Plus") is None
