"""Art writes report whether the TV confirmed, not just that a request was sent.

#401: ``delete_list`` returned True unconditionally and ``set_favourite`` is
honest; both must return False when the channel is silent (``_send_art_request``
returns None in its recovery cooldown or when the confirmation never comes), so
a service / card can tell a real change from a no-op.
"""


async def test_delete_list_is_false_when_the_tv_does_not_confirm(
    art_client, monkeypatch
):
    sent = {}

    async def fake_send(request_data, *a, **k):
        sent["request"] = request_data.get("request")
        sent["wait_for_event"] = k.get("wait_for_event")
        return None  # silent channel / cooldown

    monkeypatch.setattr(art_client, "_send_art_request", fake_send)
    monkeypatch.setattr(art_client, "_fire_art_content_event", lambda: None)

    assert await art_client.delete_list(["MY-001"]) is False
    # It waits for the TV's own removal broadcast.
    assert sent["request"] == "delete_image_list"
    assert sent["wait_for_event"] == "image_deleted"


async def test_delete_list_is_true_once_the_tv_confirms(art_client, monkeypatch):
    fired = []

    async def fake_send(request_data, *a, **k):
        return {"event": "image_deleted"}

    monkeypatch.setattr(art_client, "_send_art_request", fake_send)
    monkeypatch.setattr(art_client, "_fire_art_content_event", lambda: fired.append(1))

    assert await art_client.delete_list(["MY-001"]) is True
    # Listeners are still told, so the library count refreshes at once.
    assert fired == [1]


async def test_set_favourite_is_false_when_the_tv_does_not_confirm(
    art_client, monkeypatch
):
    async def fake_send(request_data, *a, **k):
        return None

    monkeypatch.setattr(art_client, "_send_art_request", fake_send)
    assert await art_client.set_favourite("SAM-001", "off") is False


async def test_set_favourite_is_true_when_the_tv_confirms(art_client, monkeypatch):
    async def fake_send(request_data, *a, **k):
        return {"event": "favorite_changed"}

    monkeypatch.setattr(art_client, "_send_art_request", fake_send)
    assert await art_client.set_favourite("SAM-001", "on") is True
