"""CONF_ART_ON_NON_FRAME is wired through the integration.

The option only does something if every stage agrees: the options flow offers
it (advanced page, saved with the advanced options), the entry setup hands it
to the one shared art client, a change reloads the entry (it creates or
removes the art entities), and the UI has a label and a description for it.
The integration package needs Home Assistant to import, so this reads the
sources, the way the other option tests here do.
"""

import json
from pathlib import Path
import re

ROOT = Path(__file__).parents[1] / "custom_components" / "samsungtv_smart"
KEY = "art_on_non_frame"
MARKER = "art_app"  # IS_FRAME_TV_ART_APP: CONF_IS_FRAME_TV written by the probe


def _src(name):
    return (ROOT / name).read_text()


def test_constant():
    assert f'CONF_ART_ON_NON_FRAME = "{KEY}"' in _src("const.py")


def test_options_flow_offers_and_saves_it():
    flow = _src("config_flow.py")
    advanced = flow[flow.index("ADVANCED_OPTIONS = [") : flow.index("ENUM_OPTIONS = [")]
    assert "CONF_ART_ON_NON_FRAME," in advanced
    form = flow[flow.index("def _async_adv_opt_form") :]
    assert re.search(
        r"vol\.Required\(\s*CONF_ART_ON_NON_FRAME,\s*default=options\.get\(CONF_ART_ON_NON_FRAME, False\),\s*\): bool",
        form,
    )


def test_shared_art_client_gets_the_option_and_a_change_reloads():
    init = _src("__init__.py")
    shared = init[init.index("[DATA_ART_API] = SamsungTVAsyncArt(") :]
    shared = shared[: shared.index("async_forward_entry_setups")]
    assert "art_on_non_frame=entry.options.get(CONF_ART_ON_NON_FRAME, False)" in shared
    reload = init[
        init.index("_RELOAD_OPTIONS = (") : init.index("_NO_RELOAD_DATA_KEYS = (")
    ]
    assert "CONF_ART_ON_NON_FRAME," in reload


def test_media_player_fallback_client_gets_the_option():
    player = _src("media_player.py")
    fallback = player[player.index("self._art_api = SamsungTVAsyncArt(") :]
    fallback = fallback[: fallback.index("register_capability_callback")]
    assert "CONF_ART_ON_NON_FRAME" in fallback


def test_ui_strings():
    for name in ("strings.json", "translations/en.json"):
        adv = json.loads(_src(name))["options"]["step"]["adv_opt"]
        assert adv["data"][KEY]
        assert "R95H" in adv["data_description"][KEY]


def test_persisted_flag_records_the_art_app_source():
    """sensor.py persists the marker, not True, when the probe confirmed art.

    Every reader of CONF_IS_FRAME_TV tests it for truth, so the marker must be
    a non-empty string; only __init__.py compares it.
    """
    assert f'IS_FRAME_TV_ART_APP = "{MARKER}"' in _src("const.py")
    sensor = _src("sensor.py")
    assert (
        "flag = IS_FRAME_TV_ART_APP if art_api.art_channel_confirmed else True"
        in sensor
    )
    assert "CONF_IS_FRAME_TV: flag" in sensor


def test_turning_the_option_off_drops_the_art_app_flag_before_the_platforms():
    """__init__.py removes the probe's flag (never a Frame's True) at setup.

    It must run before the art client is created and the platforms are
    forwarded, since those read the flag to decide on the art entities, and
    the key must be exempt from the reload fingerprint so the write does not
    schedule a second reload.
    """
    init = _src("__init__.py")
    cleanup = init.index("entry.data.get(CONF_IS_FRAME_TV) == IS_FRAME_TV_ART_APP")
    assert cleanup < init.index("[DATA_ART_API] = SamsungTVAsyncArt(")
    assert cleanup < init.index("async_forward_entry_setups(entry, SAMSMART_PLATFORM)")
    assert "not entry.options.get(CONF_ART_ON_NON_FRAME, False)" in init
    assert "if k != CONF_IS_FRAME_TV" in init
    no_reload = init[init.index("_NO_RELOAD_DATA_KEYS = (") :]
    no_reload = no_reload[: no_reload.index("\n)\n")]  # comments mention "(#...)"
    assert "CONF_IS_FRAME_TV," in no_reload


def test_persisted_marker_seeds_the_shared_client():
    """__init__.py hands the persisted confirmation to the shared art client.

    The platforms skip the live check on the marker; the media player's own
    check does not read the entry, so the client is told after it is created
    and before the platforms are forwarded.
    """
    init = _src("__init__.py")
    seed = init.index("mark_art_channel_confirmed()")
    assert init.index("[DATA_ART_API] = SamsungTVAsyncArt(") < seed
    assert seed < init.index("async_forward_entry_setups(entry, SAMSMART_PLATFORM)")
    guard = init.rindex(
        "entry.data.get(CONF_IS_FRAME_TV) == IS_FRAME_TV_ART_APP", 0, seed
    )
    assert guard < seed
