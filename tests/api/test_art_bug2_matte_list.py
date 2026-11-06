"""Bug 2: field matte_type_list was renamed matte_list on some firmware."""

import json


async def test_matte_list_new_field_name(art_client, monkeypatch):
    async def fake_send(request_data, *a, **k):
        return {
            "matte_list": json.dumps(["none", "shadowbox_polar", "modernthin_black"])
        }

    monkeypatch.setattr(art_client, "_send_art_request", fake_send)
    result = await art_client.get_matte_list()
    assert result == ["none", "shadowbox_polar", "modernthin_black"]


async def test_matte_list_old_field_name(art_client, monkeypatch):
    async def fake_send(request_data, *a, **k):
        return {"matte_type_list": json.dumps(["none", "flexible_polar"])}

    monkeypatch.setattr(art_client, "_send_art_request", fake_send)
    result = await art_client.get_matte_list()
    assert result == ["none", "flexible_polar"]


async def test_matte_list_none_when_the_tv_does_not_answer(art_client, monkeypatch):
    # #402: a silent channel returns None, so the caller can tell it apart from
    # a real empty list and retry / fall back instead of logging "no mattes".
    async def fake_send(request_data, *a, **k):
        return None

    monkeypatch.setattr(art_client, "_send_art_request", fake_send)
    assert await art_client.get_matte_list() is None
    assert await art_client.get_matte_list(include_color=True) is None


async def test_matte_list_empty_answer_is_a_real_empty_not_none(art_client, monkeypatch):
    # A dict with empty lists is a genuine answer: ([], []), never None.
    async def fake_send(request_data, *a, **k):
        return {"matte_list": "[]", "matte_color_list": "[]"}

    monkeypatch.setattr(art_client, "_send_art_request", fake_send)
    assert await art_client.get_matte_list(include_color=True) == ([], [])
