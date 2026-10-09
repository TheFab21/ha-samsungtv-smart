"""Art-app requests wired from the 3.50.106 decompile.

get_device_info, get_current_rotation, get/set_art_picture_mode and
reset_brightness: request shapes and reply parsing.
"""


def _fake(replies, sent):
    async def fake_send(request_data, *a, **k):
        sent.append(request_data)
        return replies.get(request_data["request"])

    return fake_send


async def test_device_info_drops_envelope_keys(art_client, monkeypatch):
    sent = []
    reply = {
        "event": "get_device_info",
        "request_id": "1",
        "id": "1",
        "target": "x",
        "support_motion_sensor": "TRUE",
        "support_brightness_sensor": "FALSE",
        "tv_flash_size": "16",
    }
    monkeypatch.setattr(
        art_client, "_send_art_request", _fake({"get_device_info": reply}, sent)
    )
    assert await art_client.get_device_info() == {
        "support_motion_sensor": "TRUE",
        "support_brightness_sensor": "FALSE",
        "tv_flash_size": "16",
    }


async def test_rotation_maps_status(art_client, monkeypatch):
    sent = []
    replies = {"get_current_rotation": {"current_rotation_status": 2}}
    monkeypatch.setattr(art_client, "_send_art_request", _fake(replies, sent))
    assert await art_client.get_current_rotation() == "portrait"
    replies["get_current_rotation"] = {"current_rotation_status": "1"}
    assert await art_client.get_current_rotation() == "landscape"
    replies["get_current_rotation"] = None
    assert await art_client.get_current_rotation() is None


async def test_art_picture_mode_get_and_set(art_client, monkeypatch):
    sent = []
    replies = {
        "get_art_picture_mode": {"art_picture_mode": 3},
        "set_art_picture_mode": {"value": "4"},
    }
    monkeypatch.setattr(art_client, "_send_art_request", _fake(replies, sent))
    assert await art_client.get_art_picture_mode() == 3
    assert await art_client.set_art_picture_mode(4) is True
    assert sent[-1] == {"request": "set_art_picture_mode", "art_picture_mode": 4}


async def test_reset_brightness_invalidates_settings_cache(art_client, monkeypatch):
    sent = []
    replies = {"reset_brightness": {"brightness_value": "10"}}
    monkeypatch.setattr(art_client, "_send_art_request", _fake(replies, sent))
    art_client._artmode_settings_cache = [{"item": "brightness", "value": "40"}]
    art_client._artmode_settings_cache_ts = 1e12
    assert await art_client.reset_brightness() == {"brightness_value": "10"}
    assert art_client._artmode_settings_cache is None


async def test_reset_brightness_without_reply(art_client, monkeypatch):
    sent = []
    monkeypatch.setattr(art_client, "_send_art_request", _fake({}, sent))
    art_client._artmode_settings_cache = [{"item": "brightness", "value": "40"}]
    assert await art_client.reset_brightness() is None
    assert art_client._artmode_settings_cache is not None
