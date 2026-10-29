"""AI Mode reports one option, not a new one per frame of content.

While AI Mode is on, ``pictureModeControl`` stops naming a mode and reports a
generated id that follows the content. Over two days on a Frame Pro (#352):
``Gen_589828`` most of the time, ``Gen_327705`` alternating with it on
successive polls, plus ``Gen_589826`` and ``Gen_589829`` — and ``Intelligent``
for the single poll in which AI Mode was switched on.

``_apply_tv_value`` learns whatever the TV reports as its own option, so each
new id became a raw, untranslated entry in the select and the list grew and
shrank with the content. The ids are not modes: nothing selects them.

They are now all reported as ``Intelligent``, the firmware's own OCF name for
the mode, which is also the name the TV reported itself. The raw id stays on
the entity's ``tv_picture_mode`` attribute, where a diagnostic needs it.
"""

import asyncio
import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "custom_components" / "samsungtv_smart"

# picture_mode_keys is pure standard library; load it straight from its file
# like test_picture_mode_ip_value does, rather than pulling in the package.
_spec = importlib.util.spec_from_file_location(
    "picture_mode_keys", PKG / "picture_mode_keys.py"
)
_pmk = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_pmk)

reported = _pmk.picture_mode_ip_reported
ip_base = _pmk.picture_mode_ip_base

# Every value the reporter saw, in the order their history showed them.
OBSERVED_IDS = ("Gen_589828", "Gen_327705", "Gen_589826", "Gen_589829")


if "pysmartthings" not in sys.modules:
    pysmartthings = ModuleType("pysmartthings")

    class _Names:
        def __getattr__(self, name):
            return name

    pysmartthings.Attribute = _Names()
    pysmartthings.Capability = _Names()
    pysmartthings.Command = _Names()
    pysmartthings.SmartThings = object
    sys.modules["pysmartthings"] = pysmartthings

from custom_components.samsungtv_smart import select as sel  # noqa: E402
from custom_components.samsungtv_smart.api.ipcontrol import (  # noqa: E402
    PICTURE_MODE_OPTIONS,
)

PictureModeSelect = sel.SamsungTVIPControlPictureModeSelect


# --- the rule itself -------------------------------------------------------


def test_every_observed_id_reports_the_mode_the_tv_named():
    for value in OBSERVED_IDS:
        assert reported(value) == "Intelligent", value


def test_the_name_the_tv_reported_itself_passes_through():
    assert reported("Intelligent") == "Intelligent"


def test_a_decorated_generated_id_is_still_the_same_mode():
    # Unobserved, but the getter decorates the predefined modes with HDR / PC
    # suffixes, so the pattern deliberately does not anchor the end.
    assert reported("Gen_589828HDR") == "Intelligent"
    assert reported("Gen_327705PCHDR") == "Intelligent"


def test_the_predefined_modes_are_untouched():
    for value in ("Dynamic", "Standard", "Movie", "FilmmakerMode", "Eco", "Natural"):
        assert reported(value) == value, value
    assert reported("CAL-DAY") == "CAL-DAY"


def test_hdr_and_pc_variants_still_collapse_to_their_mode():
    assert reported("StandardHDR") == "Standard"
    assert reported("MoviePC") == "Movie"
    assert reported("StandardPCHDR") == "Standard"


def test_a_mode_that_merely_contains_the_prefix_is_not_swallowed():
    # Anchored at the start, and the digits are required: only the generated
    # ids match, never a mode name that happens to read like one.
    for value in ("Generic", "PreGen_1", "Gen_", "Gen_abc", "NextGen_4"):
        assert reported(value) == ip_base(value), value


# --- the select ------------------------------------------------------------


def _select() -> PictureModeSelect:
    """A picture mode select with only what _apply_tv_value touches."""
    entity = object.__new__(PictureModeSelect)
    entity._ocf_by_option = {
        _pmk.picture_mode_slug(v): v for v in PICTURE_MODE_OPTIONS
    }
    entity._attr_options = list(entity._ocf_by_option)
    entity._attr_current_option = None
    entity._tv_value = None
    return entity


def test_the_whole_run_of_ids_lands_on_one_option():
    entity = _select()
    before = list(entity._attr_options)

    for value in OBSERVED_IDS * 3:
        entity._apply_tv_value(value)

    assert entity._attr_options == [*before, "intelligent"]
    assert entity._attr_current_option == "intelligent"


def test_no_generated_id_becomes_an_option():
    entity = _select()
    for value in OBSERVED_IDS:
        entity._apply_tv_value(value)
    assert not [option for option in entity._attr_options if option.startswith("gen")]


def test_the_option_sends_the_name_the_tv_takes():
    entity = _select()
    entity._apply_tv_value("Gen_589828")
    # async_select_option sends _ocf_by_option[option], so this IS what the
    # TV receives: the OCF name, never the generated id.
    assert entity._ocf_by_option["intelligent"] == "Intelligent"


def test_the_raw_id_stays_for_diagnostics():
    entity = _select()
    entity._apply_tv_value("Gen_327705")
    assert entity._tv_value == "Gen_327705"
    assert entity.extra_state_attributes == {"tv_picture_mode": "Gen_327705"}


def test_switching_into_ai_mode_and_back_out_tracks_the_tv():
    entity = _select()
    entity._apply_tv_value("Standard")
    assert entity._attr_current_option == "standard"
    entity._apply_tv_value("Intelligent")
    assert entity._attr_current_option == "intelligent"
    entity._apply_tv_value("Gen_589828")
    assert entity._attr_current_option == "intelligent"
    entity._apply_tv_value("MovieHDR")
    assert entity._attr_current_option == "movie"
    # One option added across the whole sequence, and it is not a raw id.
    assert entity._attr_options.count("intelligent") == 1


def test_a_real_mode_the_tv_offers_is_still_learned():
    entity = _select()
    assert "natural" not in entity._attr_options
    entity._apply_tv_value("Natural")
    assert entity._attr_current_option == "natural"
    assert entity._ocf_by_option["natural"] == "Natural"


def test_selecting_ai_mode_sends_the_ocf_name(monkeypatch):
    entity = _select()
    entity.hass = MagicMock()
    entity._entry_id = "entry"
    entity.async_write_ha_state = lambda: None
    entity._apply_tv_value("Gen_589828")

    sent = []

    class _Client:
        async def async_set_picture_mode(self, value, fallback=None):
            sent.append((value, fallback))
            return "Intelligent"

    monkeypatch.setattr(entity, "_get_ip_control", lambda: _Client())
    monkeypatch.setattr(sel, "clear_token_problem", lambda *a, **k: None)

    asyncio.run(entity.async_select_option("intelligent"))

    # No HDR / PC suffix is inferred from a generated id, so no second try.
    assert sent == [("Intelligent", None)]


def test_a_tv_that_refuses_the_name_says_so(monkeypatch):
    # A Frame 2024 and a 2026 S95H both answer -32002 for Intelligent, so the
    # option has to fail loudly rather than look applied.
    entity = _select()
    entity.hass = MagicMock()
    entity._entry_id = "entry"
    entity.async_write_ha_state = lambda: None
    entity._apply_tv_value("Gen_589828")

    class _Refusing:
        async def async_set_picture_mode(self, value, fallback=None):
            raise sel.SamsungIPControlModeLockedError("-32002 not offered")

    monkeypatch.setattr(entity, "_get_ip_control", lambda: _Refusing())

    with pytest.raises(sel.HomeAssistantError) as caught:
        asyncio.run(entity.async_select_option("intelligent"))
    assert "intelligent" in str(caught.value)
    # The reading is untouched: the TV is still in AI Mode.
    assert entity._tv_value == "Gen_589828"


# --- the other reader of the same value ------------------------------------


def test_the_media_player_attribute_reports_the_mode_too():
    # Same getTVStates value reaches media_player's picture_mode attribute; it
    # must not publish a generated id either.
    source = (PKG / "media_player.py").read_text()
    assert "data[ATTR_PICTURE_MODE] = picture_mode_ip_reported(local_mode)" in source
    assert "picture_mode_ip_base(local_mode)" not in source


# --- the label -------------------------------------------------------------


def test_every_locale_that_names_the_modes_names_this_one():
    translated = 0
    for path in sorted((PKG / "translations").glob("*.json")):
        state = (
            json.loads(path.read_text(encoding="utf-8"))
            .get("entity", {})
            .get("select", {})
            .get("picture_mode", {})
            .get("state")
        )
        if not state:
            continue
        translated += 1
        assert "intelligent" in state, path.name
        assert state["intelligent"].strip(), path.name
    assert translated >= 8
