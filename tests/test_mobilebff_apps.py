"""Apps from the TV's home-screen tiles over the mobilebff channel.

Measured on a Frame 2024 (QE55LS03D): the remote channel's
ed.installedApp.get got no reply in three minutes, so the media player's
source list had no apps. get.apptiles listed 15 tiles at once; each web app
carries its Tizen id (d/webapp@3201907018807 is Netflix), and launch.app
with that tile id opened Netflix ("ok" in 0.2 s).
"""

import asyncio
import json
from types import SimpleNamespace

from custom_components.samsungtv_smart import media_player as mp
from custom_components.samsungtv_smart.api import mobilebff as bff
from custom_components.samsungtv_smart.const import DATA_MOBILEBFF, DOMAIN

TILES = [
    {"appId": "d/webapp@3202311033054", "title": "SmartThings", "iconPath": "/x"},
    {"appId": "s/livetv@TVPLUS", "title": "Samsung TV Plus"},
    {"appId": "d/webapp@3201907018807", "title": "Netflix"},
    {"appId": "d/webapp@111299001912", "title": "YouTube"},
]


def _msg(kind, response, seq=None):
    data = {"version": "0.2", "id": kind, "seqId": seq, "response": response}
    return {"event": bff.EVENT, "data": json.dumps(data)}


def _client():
    client = bff.SamsungMobileBFF(None, "192.0.2.1", "t", lambda: True)
    client._handle(_msg("get.apptiles", TILES))
    return client


def test_web_app_tiles_carry_their_tizen_id():
    client = _client()
    assert [t.title for t in client.apps] == [
        "SmartThings",
        "Samsung TV Plus",
        "Netflix",
        "YouTube",
    ]
    assert client.find_app("3201907018807").tile_id == "d/webapp@3201907018807"
    tv_plus = client.apps[1]
    assert tv_plus.app_id is None


def test_the_app_list_survives_a_dropped_connection():
    client = _client()
    client._clear()
    assert len(client.apps) == 4


class _Log:
    def debug(self, *args):
        pass

    info = warning = error = debug


class _WS:
    installed_app = {}


def _device(client, connected=True):
    if connected:
        client._ws = SimpleNamespace(closed=False)
    device = object.__new__(mp.SamsungTVDevice)
    device._log = _Log()
    device._entry_id = "e1"
    device.hass = SimpleNamespace(data={DOMAIN: {"e1": {DATA_MOBILEBFF: client}}})
    device._ws = _WS()
    device._app_list = None
    device._app_list_st = None
    device._dump_apps = False
    device._options = {}
    device._get_option = lambda key, default=None: default
    return device


def test_the_source_list_gets_the_home_screen_apps_when_the_tv_lists_none():
    device = _device(_client(), connected=False)
    device._gen_installed_app_list()
    assert device._app_list == {
        "SmartThings": "3202311033054",
        "Netflix": "3201907018807",
        "YouTube": "111299001912",
    }


def test_the_remote_channel_list_still_wins_when_the_tv_sends_it():
    device = _device(_client())
    device._ws.installed_app = {
        "3201907018807": mp.App("3201907018807", "Netflix", 2),
    }
    device._gen_installed_app_list()
    assert device._app_list == {"Netflix": "3201907018807"}


def test_an_app_opens_locally_and_skips_the_remote_channel():
    client = _client()
    opened = []

    async def launch(tile_id):
        opened.append(tile_id)
        return True

    client.async_launch_app = launch
    device = _device(client)
    device._app_list = {"Netflix": "3201907018807"}
    sent = []

    async def send(*args, **kwargs):
        sent.append(args)

    device.async_send_command = send
    asyncio.run(device._async_launch_app("Netflix"))
    assert opened == ["d/webapp@3201907018807"]
    assert sent == []


def test_a_deep_link_keeps_the_remote_channel():
    device = _device(_client())
    device._app_list = {"Netflix": "3201907018807"}
    device._app_launch_ignored_logged = True
    sent = []

    async def send(*args, **kwargs):
        sent.append(args)

    device.async_send_command = send
    asyncio.run(device._async_launch_app("Netflix", meta_data="m=1"))
    assert sent and sent[0][0] == "3201907018807,,m=1"


def test_an_app_without_a_tile_falls_back():
    device = _device(_client())
    assert asyncio.run(device._async_local_app("3201234567890")) is False
