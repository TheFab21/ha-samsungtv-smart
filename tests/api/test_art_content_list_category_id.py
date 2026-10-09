"""get_content_list: the art-app reads category_id, not category.

Decompiled MobileGetContentListCommand (art-app 3.50.106): without
category_id the TV answers recents (relabelled MY-C0008) + MY-C0002 +
MY-C0004 + MY-C0001. Sending only ``category`` therefore listed every recent
artwork twice and returned nothing for any other category.
"""

import json


def _reply(items):
    return {"content_list": json.dumps(items)}


async def test_category_is_sent_as_category_id(art_client, monkeypatch):
    sent = []

    async def fake_send(request_data, *a, **k):
        sent.append(request_data)
        return _reply([{"content_id": "SAM-1", "category_id": "MY-C0001"}])

    monkeypatch.setattr(art_client, "_send_art_request", fake_send)
    assert await art_client.available("MY-C0001") == [
        {"content_id": "SAM-1", "category_id": "MY-C0001"}
    ]
    assert sent[0]["category_id"] == "MY-C0001"
    assert sent[0]["category"] == "MY-C0001"


async def test_no_category_sends_no_category_id(art_client, monkeypatch):
    sent = []

    async def fake_send(request_data, *a, **k):
        sent.append(request_data)
        return _reply([])

    monkeypatch.setattr(art_client, "_send_art_request", fake_send)
    await art_client.available()
    assert "category_id" not in sent[0]


async def test_unfiltered_list_drops_recent_duplicates(art_client, monkeypatch):
    async def fake_send(request_data, *a, **k):
        return _reply(
            [
                {"content_id": "MY_F0002", "category_id": "MY-C0008"},
                {"content_id": "SAM-9", "category_id": "MY-C0008"},
                {"content_id": "MY_F0001", "category_id": "MY-C0002"},
                {"content_id": "MY_F0002", "category_id": "MY-C0002"},
                {"content_id": "MY_F0002", "category_id": "MY-C0004"},
            ]
        )

    monkeypatch.setattr(art_client, "_send_art_request", fake_send)
    result = await art_client.available()
    assert [(a["content_id"], a["category_id"]) for a in result] == [
        ("MY_F0002", "MY-C0002"),
        ("SAM-9", "MY-C0008"),
        ("MY_F0001", "MY-C0002"),
    ]


async def test_recents_honoured_by_the_tv_are_not_filtered_out(
    art_client, monkeypatch
):
    # With category_id=MY-C0008 the TV labels each recent with its real
    # category, so filtering on MY-C0008 would empty the list.
    async def fake_send(request_data, *a, **k):
        return _reply([{"content_id": "MY_F0002", "category_id": "MY-C0002"}])

    monkeypatch.setattr(art_client, "_send_art_request", fake_send)
    assert await art_client.available("MY-C0008") == [
        {"content_id": "MY_F0002", "category_id": "MY-C0002"}
    ]
