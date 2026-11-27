"""Bundled cards are registered under a versioned URL, and www stays clean (#472).

The gallery card's Lovelace resource was the bare
/api/samsungtv_smart/folder-gallery-card.js. Browsers and the companion apps
kept the card they had cached from it, so after 9.6.0 dashboards still ran the
9.5 card: it ignores `file_urls`, and showed the right names over broken
pictures. The resource now carries `?v=<hash of the file>`, and an existing
entry is updated to it.

The custom logo folder www/samsungtv_smart_logos was created, empty, at every
start: deleted, it came back.
"""

import asyncio
import os
from types import SimpleNamespace

from custom_components.samsungtv_smart import (
    _async_upsert_card_resource,
    _card_version,
    _register_logo_paths,
)

URL = "/api/samsungtv_smart/folder-gallery-card.js"


class _Resources:
    def __init__(self, items):
        self.items = [dict(i) for i in items]
        self.calls = []

    async def async_get_info(self):
        return {}

    def async_items(self):
        return [dict(i) for i in self.items]

    async def async_create_item(self, data):
        self.calls.append(("create", data))
        self.items.append({"id": f"new{len(self.items)}", **data})

    async def async_update_item(self, item_id, updates):
        self.calls.append(("update", item_id, updates))
        for i in self.items:
            if i["id"] == item_id:
                i.update(updates)

    async def async_delete_item(self, item_id):
        self.calls.append(("delete", item_id))
        self.items = [i for i in self.items if i["id"] != item_id]


def _run(items):
    res = _Resources(items)
    hass = SimpleNamespace(data={"lovelace": SimpleNamespace(resources=res)})
    asyncio.run(_async_upsert_card_resource(hass, URL, "abc123"))
    return res


def test_a_new_install_gets_the_versioned_url():
    res = _run([])
    assert res.calls == [("create", {"res_type": "module", "url": f"{URL}?v=abc123"})]


def test_the_old_bare_url_is_updated_in_place():
    res = _run([{"id": "r1", "type": "module", "url": URL}])
    assert res.calls == [
        ("update", "r1", {"res_type": "module", "url": f"{URL}?v=abc123"})
    ]


def test_an_older_version_is_updated():
    res = _run([{"id": "r1", "type": "module", "url": f"{URL}?v=old"}])
    assert res.items[0]["url"] == f"{URL}?v=abc123"


def test_the_current_version_is_left_alone():
    res = _run([{"id": "r1", "type": "module", "url": f"{URL}?v=abc123"}])
    assert res.calls == []


def test_duplicates_are_removed_so_the_card_loads_once():
    res = _run(
        [
            {"id": "r1", "type": "module", "url": URL},
            {"id": "r2", "type": "module", "url": f"{URL}?v=old"},
        ]
    )
    assert [i["url"] for i in res.items] == [f"{URL}?v=abc123"]


def test_other_resources_are_not_touched():
    other = {"id": "x", "type": "module", "url": "/hacsfiles/button-card.js?v=1"}
    lookalike = {"id": "y", "type": "module", "url": f"{URL}x"}
    res = _run([other, lookalike])
    assert other in res.items and lookalike in res.items
    assert res.calls[0][0] == "create"


def test_no_lovelace_resources_is_not_an_error():
    asyncio.run(_async_upsert_card_resource(SimpleNamespace(data={}), URL, "v"))


def test_the_version_follows_the_file(tmp_path):
    js = tmp_path / "card.js"
    js.write_text("one")
    first = _card_version(js)
    assert first == _card_version(js)
    js.write_text("two")
    assert _card_version(js) != first


def test_the_shipped_cards_are_registered_through_the_versioned_path():
    import custom_components.samsungtv_smart as integ

    src = open(integ.__file__).read()
    assert '_register_bundled_card(hass, "folder-gallery-card.js")' in src
    assert '_register_bundled_card(hass, "samsung-art-upload-card.js")' in src
    assert "_register_gallery_card" not in src


# --- the custom logo folder ------------------------------------------------------


def _logo_hass(tmp_path):
    registered = []

    async def executor(func, *args):
        return func(*args)

    async def register(paths):
        registered.extend(paths)

    hass = SimpleNamespace(
        config=SimpleNamespace(path=lambda *p: os.path.join(str(tmp_path), *p)),
        async_add_executor_job=executor,
        http=SimpleNamespace(async_register_static_paths=register),
    )
    return hass, registered


def test_the_logo_folder_is_not_created_in_www(tmp_path):
    hass, registered = _logo_hass(tmp_path)
    assert asyncio.run(_register_logo_paths(hass)) is None
    assert not os.path.exists(os.path.join(str(tmp_path), "www"))
    # The bundled logos are still served.
    assert len(registered) == 1


def test_a_logo_folder_you_made_is_used(tmp_path):
    folder = tmp_path / "www" / "samsungtv_smart_logos"
    folder.mkdir(parents=True)
    hass, registered = _logo_hass(tmp_path)
    assert asyncio.run(_register_logo_paths(hass)) == str(folder)
    assert len(registered) == 2
