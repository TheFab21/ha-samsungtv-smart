"""Picture modes from the TV's local quick-settings channel (mobilebff).

The messages below are trimmed from two probes of a Frame 2024 (QE55LS03D):
on the TV input it listed five modes, FILMMAKER MODE included, which the
SmartThings select did not show; on HDMI it pushed four. The current mode is
``current_element_index``: it followed Dynamic -> Movie -> Standard as the
mode was changed with the remote, each pushed within a second.
"""

import asyncio
import json
import time
from unittest.mock import MagicMock

from custom_components.samsungtv_smart import select as sel
from custom_components.samsungtv_smart.api import mobilebff as bff

LAYER = bff.PICTURE_MODE_LAYER
TV_INPUT_MODES = [
    ("Dynamic", "Dynamique"),
    ("Standard", "Standard"),
    ("Eco", "Éco"),
    ("Movie", "Cinéma"),
    ("FilmMaker", "FILMMAKER MODE"),
]


def _layer(modes, current, layer_id=LAYER):
    return {
        "id": layer_id,
        "current_element_index": str(current),
        "child": [],
        "elements": [
            {
                "id": f"{layer_id}@{mode_id}",
                "title": "Mode Image",
                "subTitle": label,
                "dim": "false",
                "actionType": "toggle",
                "type": "SettingPreview",
            }
            for mode_id, label in modes
        ],
    }


def _message(kind, layers, as_string=False):
    response = layers if kind == "get.settings.data" else {"updateElement": layers}
    data = {"version": "0.2", "id": kind, "seqId": "2", "response": response}
    return {
        "event": bff.EVENT,
        "data": json.dumps(data) if as_string else data,
        "from": "host",
    }


# --- parsing -----------------------------------------------------------------


def test_full_settings_read_gives_the_list_and_the_current_mode():
    other = {"id": "s/setting.layer.soundmode", "elements": []}
    layer = bff.picture_mode_from_message(
        _message("get.settings.data", [other, _layer(TV_INPUT_MODES, 0)])
    )
    assert layer.labels == [label for _, label in TV_INPUT_MODES]
    assert layer.current == "Dynamic"
    assert layer.current_label == "Dynamique"


def test_pushed_update_follows_the_remote():
    layer = bff.picture_mode_from_message(
        _message("event.setting.update", [_layer(TV_INPUT_MODES, 3)])
    )
    assert layer.current == "Movie"
    assert layer.current_label == "Cinéma"


def test_input_switch_pushes_the_shorter_list():
    layer = bff.picture_mode_from_message(
        _message("event.setting.update", [_layer(TV_INPUT_MODES[:4], 0)])
    )
    assert "FILMMAKER MODE" not in layer.labels


def test_data_sent_as_a_json_string_is_read_too():
    layer = bff.picture_mode_from_message(
        _message("get.settings.data", [_layer(TV_INPUT_MODES, 1)], as_string=True)
    )
    assert layer.current == "Standard"


def test_updates_of_other_settings_carry_no_picture_modes():
    brightness = {"id": "s/setting.layer.picture_setup", "elements": []}
    assert (
        bff.picture_mode_from_message(_message("event.setting.update", [brightness]))
        is None
    )


def test_the_three_second_notify_is_not_a_picture_mode_message():
    notify = {
        "event": bff.EVENT,
        "data": {"id": "notify.update", "response": {"reqMessage": "x"}},
    }
    assert bff.picture_mode_from_message(notify) is None
    assert bff.picture_mode_from_message({"event": "ms.channel.connect"}) is None


def test_out_of_range_index_leaves_the_current_mode_unknown():
    layer = bff.parse_picture_mode_layer(_layer(TV_INPUT_MODES, 9))
    assert layer.current is None
    assert layer.current_label is None
    assert bff.parse_picture_mode_layer(_layer([], 0)) is None


def test_quick_settings_ids_map_to_the_ocf_names_ip_control_takes():
    assert bff.picture_mode_ocf("FilmMaker") == "FilmmakerMode"
    assert bff.picture_mode_ocf("Movie") == "Movie"


# --- client ------------------------------------------------------------------


def _client(should_connect=lambda: True):
    return bff.SamsungMobileBFF(MagicMock(), "192.0.2.1", "probe", should_connect)


def test_listeners_hear_changes_only():
    client = _client()
    calls = []
    remove = client.add_listener(lambda: calls.append(client.picture_modes))
    first = bff.parse_picture_mode_layer(_layer(TV_INPUT_MODES, 0))
    client._set_picture_modes(first)
    client._set_picture_modes(bff.parse_picture_mode_layer(_layer(TV_INPUT_MODES, 0)))
    assert calls == [first]
    remove()
    client._set_picture_modes(None)
    assert len(calls) == 1


def test_nothing_is_opened_while_the_tv_is_not_in_normal_viewing(monkeypatch):
    client = _client(should_connect=lambda: False)
    monkeypatch.setattr(bff, "IDLE_CHECK", 0)
    opened = []
    client._session_once = lambda port: opened.append(port)

    async def run_briefly():
        task = asyncio.ensure_future(client._run())
        await asyncio.sleep(0.01)
        task.cancel()

    asyncio.run(run_briefly())
    assert opened == []


def test_a_dropped_connection_clears_the_list(monkeypatch):
    client = _client()
    monkeypatch.setattr(bff, "RETRY_MIN", 0)
    seen = []
    client.add_listener(lambda: seen.append(client.picture_modes))

    async def session(port):
        client._set_picture_modes(
            bff.parse_picture_mode_layer(_layer(TV_INPUT_MODES, 0))
        )
        raise ConnectionResetError

    client._session_once = session

    async def run_briefly():
        task = asyncio.ensure_future(client._run())
        await asyncio.sleep(0.01)
        task.cancel()

    asyncio.run(run_briefly())
    assert seen[0] is not None
    assert seen[1] is None
    assert client.picture_modes is None


# --- SmartThings picture mode select ----------------------------------------


def _st_select(layer):
    entity = object.__new__(sel.SamsungTVPictureModeSelect)
    entity._mobilebff = MagicMock(picture_modes=layer)
    entity._attr_options = ["Cinéma", "Dynamique", "Standard", "Éco"]
    entity._attr_current_option = "Standard"
    entity._pending_mode = None
    entity._pending_until = 0
    entity._skip_poll_until = 0
    entity._capability = "samsungvd.pictureMode"
    return entity


def test_st_select_shows_filmmaker_mode_when_the_tv_offers_it():
    entity = _st_select(bff.parse_picture_mode_layer(_layer(TV_INPUT_MODES, 4)))
    assert entity._apply_local() is True
    assert "FILMMAKER MODE" in entity._attr_options
    assert entity._attr_current_option == "FILMMAKER MODE"
    assert entity._apply_local() is False


def test_st_select_skips_the_cloud_read_while_the_local_list_is_there():
    entity = _st_select(bff.parse_picture_mode_layer(_layer(TV_INPUT_MODES, 3)))
    entity._rest_get_capability_status = MagicMock(
        side_effect=AssertionError("cloud read")
    )
    asyncio.run(entity.async_update())
    assert entity._attr_current_option == "Cinéma"


def test_st_select_falls_back_to_the_cloud_without_the_channel():
    entity = _st_select(None)
    assert entity._apply_local() is False
    assert entity._attr_options == ["Cinéma", "Dynamique", "Standard", "Éco"]


def test_st_select_holds_a_just_set_mode_until_the_tv_reports_it():
    entity = _st_select(bff.parse_picture_mode_layer(_layer(TV_INPUT_MODES, 0)))
    entity._pending_mode = "Cinéma"
    entity._pending_until = time.time() + 60
    entity._apply_local()
    assert entity._attr_current_option == "Cinéma"
    entity._mobilebff.picture_modes = bff.parse_picture_mode_layer(
        _layer(TV_INPUT_MODES, 3)
    )
    entity._apply_local()
    assert entity._pending_mode is None
    assert entity._attr_current_option == "Cinéma"


# --- IP Control picture mode select ------------------------------------------


def _ip_select(layer, current="standard"):
    entity = object.__new__(sel.SamsungTVIPControlPictureModeSelect)
    entity._mobilebff = MagicMock(picture_modes=layer)
    entity._ocf_by_option = {"standard": "Standard", "movie": "Movie"}
    entity._attr_options = ["standard", "movie"]
    entity._attr_current_option = current
    return entity


def test_ip_select_offers_the_modes_the_input_offers():
    entity = _ip_select(bff.parse_picture_mode_layer(_layer(TV_INPUT_MODES, 0)))
    assert entity._apply_local_modes() is True
    assert entity._attr_options == [
        "dynamic",
        "standard",
        "eco",
        "movie",
        "filmmaker_mode",
    ]
    assert entity._ocf_by_option["filmmaker_mode"] == "FilmmakerMode"


def test_ip_select_keeps_the_current_option_the_list_omits():
    entity = _ip_select(
        bff.parse_picture_mode_layer(_layer(TV_INPUT_MODES[:4], 0)),
        current="ai_customization",
    )
    entity._apply_local_modes()
    assert entity._attr_options[-1] == "ai_customization"
    assert "filmmaker_mode" not in entity._attr_options


def test_ip_select_reoffers_a_known_mode_the_tv_reports_after_a_shorter_list():
    entity = _ip_select(bff.parse_picture_mode_layer(_layer(TV_INPUT_MODES, 0)))
    entity._apply_local_modes()
    entity._mobilebff.picture_modes = bff.parse_picture_mode_layer(
        _layer(TV_INPUT_MODES[:4], 0)
    )
    entity._apply_local_modes()
    entity._tv_value = None
    entity._apply_tv_value("FilmmakerMode")
    assert entity._attr_current_option == "filmmaker_mode"
    assert "filmmaker_mode" in entity._attr_options


def test_a_tv_that_keeps_refusing_is_left_alone(monkeypatch):
    # A refused handshake may be an on-screen authorization prompt: three per
    # reload at most, not one every two minutes forever.
    client = _client()
    monkeypatch.setattr(bff, "RETRY_MIN", 0)
    monkeypatch.setattr(bff, "RETRY_MAX", 0)
    attempts = []

    async def refused(port):
        attempts.append(port)
        return False

    client._session_once = refused
    asyncio.run(asyncio.wait_for(client._run(), timeout=2))
    assert len(attempts) == 2 * bff.MAX_REFUSALS
