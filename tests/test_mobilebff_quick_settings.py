"""Quick settings, input switching and sound mode over the mobilebff channel.

Measured on a Frame 2024 (QE55LS03D), Nov 2026:

* ``set.settings.data`` colour tone Standard -> Warm 1 -> Standard: each
  answered ``status: ok`` in 0.7 s and the TV pushed the new value within a
  second;
* ``set.settings.data`` to a sound mode the input no longer offered (a
  receiver had taken the sound) was ALSO answered ``ok``, and nothing was
  pushed - so a write counts as applied only once the TV reports it;
* ``launch.extsource`` TV -> HDMI3 -> TV: ``ok``, then shown as the current
  input; HDMI3 was listed as "CINEMA 60", then "Périphérique externe avec
  CINEMA 60" once the receiver was on (CEC);
* the AI Mode and energy-saving layers list both options under one element
  id, told apart by their label only.
"""

import asyncio
import json
from types import SimpleNamespace

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.samsungtv_smart import media_player as mp
from custom_components.samsungtv_smart import select as sel
from custom_components.samsungtv_smart.api import mobilebff as bff
from custom_components.samsungtv_smart.const import DATA_MOBILEBFF, DOMAIN

COLORTONE = "s/setting.layer.colortone"
SOUNDMODE = "s/setting.layer.soundmode"


def _element(layer_id, suffix, label, title="T", dim="false"):
    element_id = f"{layer_id}@{suffix}" if suffix else layer_id
    return {"id": element_id, "title": title, "subTitle": label, "dim": dim}


def _layer(layer_id, options, current, **kw):
    return {
        "id": layer_id,
        "current_element_index": str(current),
        "elements": [_element(layer_id, s, label, **kw) for s, label in options],
    }


COLORTONE_OPTIONS = [
    ("Cool", "Froide"),
    ("Standard", "Standard"),
    ("Warm1", "Chaude 1"),
    ("Warm2", "Chaude 2"),
]
SOURCES = [
    {"sourceId": "s/source.layer.tv@TV", "title": "TV", "currentSource": "true"},
    {
        "sourceId": "s/source.layer.hdmi@HDMI3",
        "title": "Périphérique externe avec CINEMA 60",
        "currentSource": "false",
    },
]


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
    return client


# --- parsing -----------------------------------------------------------------


def test_options_sharing_one_element_id_are_kept_apart_by_label():
    ai_mode = _layer("s/setting.layer.aitech", [("", "Désactivé"), ("", "Activé")], 1)
    layer = bff.parse_setting_layer(ai_mode)
    assert [e.label for e in layer.elements] == ["Désactivé", "Activé"]
    assert layer.elements[0].element_id == layer.elements[1].element_id
    assert layer.current_element.params["subTitle"] == "Activé"


def test_greyed_out_options_are_marked():
    game = _layer(
        "s/setting.layer.gamemode", [("Off", "Off"), ("On", "On")], 0, dim="true"
    )
    assert all(e.dim for e in bff.parse_setting_layer(game).elements)


# --- 2026 firmware (26_RSM_FTV): nested list tiles and shared-subtitle toggles


def _tile(suffix, title, selected=False, dim="false"):
    return {
        "id": f"s/setting.layer.picturemodetile@{suffix}",
        "elements": [
            {
                "title": title,
                "is_selected": "true" if selected else "false",
                "dim": dim,
                "type": "SecondDepthOption",
            }
        ],
    }


def _nested_list_layer():
    """A 2026 list setting: one preview element over a Commonlayer of tiles."""
    return {
        "id": "s/setting.layer.picturemode",
        "current_element_index": "0",
        "elements": [
            {
                "id": "s/setting.layer.picturemode",
                "title": "Picture Mode",
                "subTitle": "Standard",
                "actionType": "list",
                "type": "SettingPreview",
            }
        ],
        "child": [
            {
                "id": "s/setting.layer.picturemodelayer",
                "current_element_index": "0",
                "elements": [
                    {
                        "id": "s/setting.layer.picturemodelayer",
                        "title": "Commonlayer",
                        "hasListInfo": "true",
                        "type": "Layer",
                    }
                ],
                "child": [
                    _tile(0, "Dynamic"),
                    _tile(1, "Standard", selected=True),
                    _tile(2, "Eco"),
                    _tile(3, "Movie (Calibrated)"),
                    _tile(4, "FILMMAKER MODE"),
                ],
            }
        ],
    }


def _toggle_layer(off_subtitle, on_subtitle):
    return {
        "id": "s/setting.layer.aitech",
        "current_element_index": "1",
        "elements": [
            {
                "id": "s/setting.layer.aitech@Off",
                "title": "AI Mode",
                "subTitle": off_subtitle,
                "actionType": "toggle",
                "dim": "false",
            },
            {
                "id": "s/setting.layer.aitech@On",
                "title": "AI Mode",
                "subTitle": on_subtitle,
                "actionType": "toggle",
                "dim": "false",
            },
        ],
    }


def test_a_2026_list_setting_reads_its_options_from_the_grandchild_tiles():
    layer = bff.parse_setting_layer(_nested_list_layer())
    assert [e.label for e in layer.elements] == [
        "Dynamic",
        "Standard",
        "Eco",
        "Movie (Calibrated)",
        "FILMMAKER MODE",
    ]
    # is_selected, not current_element_index, marks the current option.
    assert layer.current_element.label == "Standard"
    # The value the write carries is the id after '@'.
    assert layer.elements[4].element_id.endswith("@4")
    assert sel.quick_setting_options(layer) == [
        "Dynamic",
        "Standard",
        "Eco",
        "Movie (Calibrated)",
        "FILMMAKER MODE",
    ]


def test_a_2026_list_tile_that_is_greyed_out_is_marked():
    raw = _nested_list_layer()
    raw["child"][0]["child"][0]["elements"][0]["dim"] = "true"
    layer = bff.parse_setting_layer(raw)
    assert layer.elements[0].dim is True
    assert layer.elements[1].dim is False


def test_a_2026_shared_subtitle_toggle_is_labelled_and_read_by_id_suffix():
    # Both elements say "Off" and current_element_index is a constant "1";
    # the suffix gives the labels and the subtitle picks the current one.
    layer = bff.parse_setting_layer(_toggle_layer("Off", "Off"))
    assert [e.label for e in layer.elements] == ["Off", "On"]
    assert layer.current == 0
    assert sel.quick_setting_options(layer) == ["Off", "On"]

    on = bff.parse_setting_layer(_toggle_layer("On", "On"))
    assert on.current == 1


def test_a_shared_subtitle_toggle_sends_the_tvs_own_subtitle_on_the_wire():
    # #404: 9.0.8 labelled the elements by their id suffix and sent that suffix
    # as subTitle too; the TV answered ok and did not apply it. set.settings.data
    # is echo-based, so the label HA shows and the subTitle sent must differ:
    # the label is the suffix, the wire carries what the TV reported.
    layer = bff.parse_setting_layer(_toggle_layer("Off", "Off"))
    on = layer.elements[1]
    assert on.label == "On"  # what HA shows
    assert on.params == {
        "id": "s/setting.layer.aitech@On",
        "title": "AI Mode",
        "subTitle": "Off",  # what the TV sent for this element
    }
    # The current element is unaffected by the split.
    assert layer.current == 0
    assert layer.elements[0].params["subTitle"] == "Off"


def test_a_list_tile_still_sends_its_own_title():
    # The tile path has no synthesised label, so the wire payload is unchanged
    # (contrast enhancer / colour space set fine on the 2026 firmware).
    layer = bff.parse_setting_layer(_nested_list_layer())
    assert layer.elements[4].params == {
        "id": "s/setting.layer.picturemodetile@4",
        "title": "FILMMAKER MODE",
        "subTitle": "FILMMAKER MODE",
    }


def test_a_generic_element_still_sends_its_subtitle():
    layer = bff.parse_setting_layer(_layer(COLORTONE, COLORTONE_OPTIONS, 1))
    assert layer.current_element.params == {
        "id": f"{COLORTONE}@Standard",
        "title": "T",
        "subTitle": "Standard",
    }


def test_a_toggle_with_distinct_subtitles_is_left_to_the_generic_reader():
    # Energy saving is the same shape but its subtitles already differ, so the
    # subtitles are the labels and current_element_index is right.
    layer = bff.parse_setting_layer(
        {
            "id": "s/setting.layer.energysavingsolution",
            "current_element_index": "0",
            "elements": [
                {
                    "id": "s/setting.layer.energysavingsolution@Off",
                    "title": "Energy Saving",
                    "subTitle": "Off",
                    "actionType": "toggle",
                    "dim": "false",
                },
                {
                    "id": "s/setting.layer.energysavingsolution@On",
                    "title": "Energy Saving",
                    "subTitle": "On",
                    "actionType": "toggle",
                    "dim": "false",
                },
            ],
        }
    )
    assert [e.label for e in layer.elements] == ["Off", "On"]
    assert layer.current == 0


def test_inputs_match_by_id_before_the_cec_name():
    client = _client()
    client._handle(_msg("get.extsources", SOURCES))
    assert client.find_source("HDMI3").title.startswith("Périphérique")
    assert client.find_source("", "TV").short_id == "TV"
    assert client.find_source("HDMI1", "Netflix") is None


# --- client state --------------------------------------------------------------


def test_a_push_updates_one_layer_and_keeps_the_others():
    client = _client()
    sound = _layer(SOUNDMODE, [("Standard", "Standard")], 0)
    client._handle(
        _msg("get.settings.data", [_layer(COLORTONE, COLORTONE_OPTIONS, 1), sound])
    )
    client._handle(
        _msg(
            "event.setting.update",
            {"updateElement": [_layer(COLORTONE, COLORTONE_OPTIONS, 2)]},
        )
    )
    assert client.layers[COLORTONE].current == 2
    assert SOUNDMODE in client.layers


def test_layer_listeners_hear_each_reported_layer():
    client = _client()
    seen = []
    client.add_layer_listener(lambda layer: seen.append(layer.layer_id))
    client._handle(_msg("get.settings.data", [_layer(COLORTONE, COLORTONE_OPTIONS, 1)]))
    assert seen == [COLORTONE]


def test_a_drop_clears_settings_and_inputs():
    client = _client()
    client._handle(_msg("get.settings.data", [_layer(COLORTONE, COLORTONE_OPTIONS, 1)]))
    client._handle(_msg("get.extsources", SOURCES))
    client._clear()
    assert client.layers == {} and client.sources == ()


# --- writes --------------------------------------------------------------------


def _answer_then(client, push=None):
    """Answer the next request ok, then optionally push a layer update."""

    async def tv():
        while not client._ws.sent:
            await asyncio.sleep(0.001)
        request = client._ws.sent[-1]
        client._handle(_msg(request["id"], {"status": "ok"}, request["seqId"]))
        if push is not None:
            await asyncio.sleep(0.05)
            client._handle(_msg("event.setting.update", {"updateElement": [push]}))

    return tv()


def test_a_setting_counts_as_set_once_the_tv_reports_it():
    client = _client()
    client._handle(_msg("get.settings.data", [_layer(COLORTONE, COLORTONE_OPTIONS, 1)]))

    async def run():
        tv = asyncio.ensure_future(
            _answer_then(client, _layer(COLORTONE, COLORTONE_OPTIONS, 2))
        )
        done = await client.async_set_setting(COLORTONE, 2)
        await tv
        return done

    assert asyncio.run(run()) is True
    sent = client._ws.sent[-1]
    assert sent["id"] == "set.settings.data"
    assert sent["params"] == {
        "id": f"{COLORTONE}@Warm1",
        "title": "T",
        "subTitle": "Chaude 1",
    }


def test_ok_without_the_tv_reporting_it_is_not_success():
    client = _client()
    client._handle(_msg("get.settings.data", [_layer(COLORTONE, COLORTONE_OPTIONS, 1)]))

    async def run():
        tv = asyncio.ensure_future(_answer_then(client))
        done = await client.async_set_setting(COLORTONE, 2, confirm_timeout=0.2)
        await tv
        return done

    assert asyncio.run(run()) is False


def test_no_answer_is_not_success():
    client = _client()
    client._handle(_msg("get.settings.data", [_layer(COLORTONE, COLORTONE_OPTIONS, 1)]))

    async def run():
        return await asyncio.wait_for(
            client._request("set.settings.data", {}, timeout=0.1), 1
        )

    assert asyncio.run(run()) is None


def test_switching_input_sends_launch_extsource():
    client = _client()

    async def run():
        tv = asyncio.ensure_future(_answer_then(client))
        done = await client.async_launch_source("s/source.layer.hdmi@HDMI3")
        await tv
        return done

    assert asyncio.run(run()) is True
    assert client._ws.sent[0] == {
        "version": "0.2",
        "id": "launch.extsource",
        "seqId": "1",
        "params": {"requestId": "s/source.layer.hdmi@HDMI3"},
    }


# --- quick-setting select ----------------------------------------------------


def _qs_select(client, layer_id=COLORTONE):
    entity = sel.SamsungTVQuickSettingSelect(
        client, layer_id, "qs_color_tone", "TV", "uid"
    )
    entity.async_write_ha_state = lambda: None
    return entity


def test_duplicate_labels_become_distinct_options():
    layer = bff.parse_setting_layer(
        _layer("x", [("0", "Indéfini"), ("1", "Indéfini")], 0)
    )
    assert sel.quick_setting_options(layer) == ["Indéfini", "Indéfini (2)"]


def test_select_shows_the_tv_labels_and_current_option():
    client = _client()
    client._handle(_msg("get.settings.data", [_layer(COLORTONE, COLORTONE_OPTIONS, 1)]))
    entity = _qs_select(client)
    assert entity.options == ["Froide", "Standard", "Chaude 1", "Chaude 2"]
    assert entity.current_option == "Standard"
    assert entity.available is True
    assert entity.unique_id == "uid_qs_color_tone"


def test_select_is_unavailable_without_the_channel_or_when_greyed_out():
    client = _client()
    client._handle(
        _msg(
            "get.settings.data",
            [_layer(COLORTONE, COLORTONE_OPTIONS, 1, dim="true")],
        )
    )
    assert _qs_select(client).available is False
    client._handle(_msg("get.settings.data", [_layer(COLORTONE, COLORTONE_OPTIONS, 1)]))
    client._ws = None
    assert _qs_select(client).available is False


def test_select_raises_when_the_tv_does_not_apply_the_option():
    client = _client()
    client._handle(_msg("get.settings.data", [_layer(COLORTONE, COLORTONE_OPTIONS, 1)]))
    entity = _qs_select(client)

    async def refuse(layer_id, index):
        return False

    client.async_set_setting = refuse
    with pytest.raises(HomeAssistantError):
        asyncio.run(entity.async_select_option("Chaude 1"))


def test_select_follows_the_tv_after_a_confirmed_write():
    client = _client()
    client._handle(_msg("get.settings.data", [_layer(COLORTONE, COLORTONE_OPTIONS, 1)]))
    entity = _qs_select(client)

    async def apply(layer_id, index):
        client._handle(
            _msg(
                "event.setting.update",
                {"updateElement": [_layer(COLORTONE, COLORTONE_OPTIONS, index)]},
            )
        )
        return True

    client.async_set_setting = apply
    asyncio.run(entity.async_select_option("Chaude 1"))
    assert entity.current_option == "Chaude 1"


# --- media player ------------------------------------------------------------


class _Log:
    def debug(self, *args):
        pass

    info = warning = error = debug


def _device(client):
    device = object.__new__(mp.SamsungTVDevice)
    device._log = _Log()
    device._entry_id = "e1"
    device.hass = SimpleNamespace(data={DOMAIN: {"e1": {DATA_MOBILEBFF: client}}})
    device._st = None
    return device


def test_media_player_switches_input_locally_by_its_id():
    client = _client()
    client._handle(_msg("get.extsources", SOURCES))
    launched = []

    async def launch(source_id):
        launched.append(source_id)
        return True

    client.async_launch_source = launch
    device = _device(client)
    assert asyncio.run(device._async_local_source("CINEMA 60", "ST_HDMI3")) is True
    assert launched == ["s/source.layer.hdmi@HDMI3"]


def test_media_player_falls_back_when_the_input_is_unknown_or_closed():
    client = _client()
    client._handle(_msg("get.extsources", SOURCES))
    device = _device(client)
    assert asyncio.run(device._async_local_source("Xbox", "KEY_HDMI1")) is False
    client._ws = None
    assert asyncio.run(device._async_local_source("CINEMA 60", "ST_HDMI3")) is False


def test_media_player_sound_mode_comes_from_the_tv_and_is_set_locally():
    client = _client()
    client._handle(
        _msg(
            "get.settings.data",
            [
                _layer(
                    SOUNDMODE, [("Standard", "Standard"), ("Amplify", "Amplifier")], 0
                )
            ],
        )
    )
    device = _device(client)
    assert device.sound_mode_list == ["Standard", "Amplifier"]
    assert device.sound_mode == "Standard"
    asked = []

    async def apply(layer_id, index):
        asked.append((layer_id, index))
        return True

    client.async_set_setting = apply
    device.async_write_ha_state = lambda: None
    asyncio.run(device.async_select_sound_mode("Amplifier"))
    assert asked == [(SOUNDMODE, 1)]


def test_media_player_sound_mode_not_offered_without_smartthings_is_an_error():
    client = _client()
    client._handle(
        _msg("get.settings.data", [_layer(SOUNDMODE, [("Standard", "Standard")], 0)])
    )
    device = _device(client)
    with pytest.raises(HomeAssistantError):
        asyncio.run(device.async_select_sound_mode("Amplifier"))


def test_select_writes_only_when_what_it_shows_changes():
    client = _client()
    client._handle(_msg("get.settings.data", [_layer(COLORTONE, COLORTONE_OPTIONS, 1)]))
    entity = _qs_select(client)
    writes = []
    entity.async_write_ha_state = lambda: writes.append(entity.current_option)
    entity._changed()  # first push: availability becomes known
    entity._changed()  # nothing new
    client._handle(
        _msg(
            "event.setting.update",
            {"updateElement": [_layer(SOUNDMODE, [("Standard", "Standard")], 0)]},
        )
    )
    entity._changed()  # another setting changed
    client._handle(
        _msg(
            "event.setting.update",
            {"updateElement": [_layer(COLORTONE, COLORTONE_OPTIONS, 0)]},
        )
    )
    entity._changed()
    assert writes == ["Standard", "Froide"]
