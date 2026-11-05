"""The home-screen app list survives a restart via the on-disk cache.

On a Frame the app list comes only from the quick-settings channel, which is
open only in normal viewing. After a Home Assistant restart with the TV in Art
Mode or off, the apps were unknown until the TV was next watched, so they
dropped out of the source list. The client is now seeded from a cached list and
re-saves whenever a live get.apptiles brings a different one.
"""

import json
from unittest.mock import MagicMock

from custom_components.samsungtv_smart.api import mobilebff as bff


def _client(initial_apps=(), on_apps_changed=None):
    return bff.SamsungMobileBFF(
        MagicMock(),
        "192.0.2.1",
        "probe",
        lambda: True,
        initial_apps=initial_apps,
        on_apps_changed=on_apps_changed,
    )


NETFLIX = {
    "appId": "d/webapp@3201907018807",
    "title": "Netflix",
    "iconPath": "/x/n.png",
}
CANAL = {"appId": "d/webapp@3202010022094", "title": "CANAL+"}


def _apptiles_message(items):
    data = {"version": "0.2", "id": "get.apptiles", "response": items}
    return {"event": bff.EVENT, "data": data, "from": "host"}


def test_round_trip_serialization():
    apps = bff.parse_app_tiles([NETFLIX, CANAL])
    restored = bff.apps_from_data(bff.apps_to_data(apps))
    assert restored == apps
    assert restored[0].app_id == "3201907018807"
    assert restored[0].icon_path == "/x/n.png"


def test_data_is_json_safe():
    apps = bff.parse_app_tiles([NETFLIX, CANAL])
    json.dumps(bff.apps_to_data(apps))  # must not raise


def test_apps_from_data_tolerates_garbage():
    assert bff.apps_from_data(None) == ()
    assert bff.apps_from_data("nope") == ()
    assert bff.apps_from_data([{"no_tile_id": 1}, 42]) == ()


def test_client_is_seeded_from_the_cache():
    cached = bff.apps_from_data(
        [{"tile_id": "d/webapp@3201907018807", "title": "Netflix"}]
    )
    client = _client(initial_apps=cached)
    # Available before any connection — this is what fills the source list with
    # the TV in Art Mode or off.
    assert client.apps == cached
    assert client.find_app("3201907018807") is not None


def test_a_live_read_replaces_and_saves():
    saved = []
    client = _client(on_apps_changed=lambda apps: saved.append(apps))
    client._handle(_apptiles_message([NETFLIX, CANAL]))
    assert [t.title for t in client.apps] == ["Netflix", "CANAL+"]
    assert len(saved) == 1 and saved[0] == client.apps


def test_an_unchanged_live_read_does_not_resave():
    seed = bff.parse_app_tiles([NETFLIX, CANAL])
    saved = []
    client = _client(initial_apps=seed, on_apps_changed=lambda apps: saved.append(apps))
    client._handle(_apptiles_message([NETFLIX, CANAL]))  # same list as the cache
    assert saved == []  # nothing new to persist
