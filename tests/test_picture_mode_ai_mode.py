"""AI Optimised and AI Customisation are two modes, and must stay two options.

With AI Mode on, a TV's Picture Mode menu offers exactly those two and nothing
else. Over ``pictureModeControl`` the first is the predefined OCF name
``Intelligent``; the second has no name and is reported as a generated id that
follows what is on screen. Measured on a Frame Pro (26_RSM_FTV) in #352:

    set Intelligent    -> accepted, read back Intelligent     -> AI Optimised
    set Gen_589828     -> accepted (the id then reported)     -> AI Customisation
    set Gen_327705     -> -32002   (an id for another screen) -> unchanged

8.15.0 reported every generated id as ``Intelligent``, on the strength of one
poll seen as AI Mode came on. That showed AI Customisation under AI Optimised's
name — and picking that option sent ``Intelligent``, which this TV accepts, so
it moved the user from one mode to the other. These tests hold the two apart,
and hold the one thing that makes AI Customisation selectable at all: the value
behind it is the id the TV is reporting right now, refreshed on every poll.
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

is_generated = _pmk.picture_mode_ip_is_generated
AI_CUSTOM = _pmk.PICTURE_MODE_GENERATED_OPTION

# What the TV reported, with what each id meant on screen.
SDR_VIDEO = "Gen_589828"
HDR_VIDEO = "Gen_589829"
HOME_SCREEN = "Gen_327705"
UNEXPLAINED = "Gen_589826"
OBSERVED_IDS = (SDR_VIDEO, HOME_SCREEN, UNEXPLAINED, HDR_VIDEO)


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


# --- telling the two apart -------------------------------------------------


def test_every_observed_id_is_an_ai_customisation_reading():
    for value in OBSERVED_IDS:
        assert is_generated(value), value


def test_ai_optimised_is_not_one_of_them():
    # The whole point: Intelligent is a mode of its own, not the family's name.
    assert not is_generated("Intelligent")


def test_a_decorated_generated_id_still_counts():
    # Unobserved, but the getter decorates the predefined modes with HDR / PC
    # suffixes, so the pattern deliberately does not anchor the end.
    assert is_generated("Gen_589828HDR")
    assert is_generated("Gen_327705PCHDR")


def test_the_predefined_modes_are_not_generated():
    for value in PICTURE_MODE_OPTIONS + ("Natural", "CAL-DAY", "StandardHDR"):
        assert not is_generated(value), value


def test_a_mode_that_merely_reads_like_one_is_not_swallowed():
    for value in ("Generic", "PreGen_1", "Gen_", "Gen_abc", "NextGen_4"):
        assert not is_generated(value), value


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


def test_ai_customisation_does_not_report_as_ai_optimised():
    # The 8.15.0 regression, in one test.
    entity = _select()
    entity._apply_tv_value(SDR_VIDEO)
    assert entity._attr_current_option == AI_CUSTOM
    assert entity._attr_current_option != "intelligent"
    assert "intelligent" not in entity._attr_options


def test_ai_optimised_reports_as_itself():
    entity = _select()
    entity._apply_tv_value("Intelligent")
    assert entity._attr_current_option == "intelligent"
    assert entity._ocf_by_option["intelligent"] == "Intelligent"
    assert AI_CUSTOM not in entity._attr_options


def test_the_two_modes_keep_two_options():
    entity = _select()
    entity._apply_tv_value(SDR_VIDEO)
    entity._apply_tv_value("Intelligent")
    entity._apply_tv_value(HDR_VIDEO)

    assert entity._attr_options.count(AI_CUSTOM) == 1
    assert entity._attr_options.count("intelligent") == 1
    assert entity._attr_current_option == AI_CUSTOM
    # And the one thing the old code got wrong: the value behind AI
    # Customisation is never AI Optimised's name.
    assert entity._ocf_by_option[AI_CUSTOM] != "Intelligent"


def test_the_whole_run_of_ids_lands_on_one_option():
    entity = _select()
    before = list(entity._attr_options)
    for value in OBSERVED_IDS * 3:
        entity._apply_tv_value(value)
    assert entity._attr_options == [*before, AI_CUSTOM]
    assert not [o for o in entity._attr_options if o.startswith("gen")]


def test_the_value_behind_it_follows_what_is_on_screen():
    # Only the id the TV reports right now is accepted, so the stored value has
    # to be refreshed on every poll, not learned once.
    entity = _select()
    for value in (SDR_VIDEO, HDR_VIDEO, HOME_SCREEN):
        entity._apply_tv_value(value)
        assert entity._ocf_by_option[AI_CUSTOM] == value
        assert entity._tv_value == value


def test_the_raw_id_stays_for_diagnostics():
    entity = _select()
    entity._apply_tv_value(HOME_SCREEN)
    assert entity.extra_state_attributes == {"tv_picture_mode": HOME_SCREEN}


def test_a_predefined_mode_and_its_hdr_variant_are_unchanged():
    entity = _select()
    entity._apply_tv_value("StandardHDR")
    assert entity._attr_current_option == "standard"
    assert entity._ocf_by_option["standard"] == "Standard"
    entity._apply_tv_value("MoviePC")
    assert entity._attr_current_option == "movie"


def test_a_real_mode_the_tv_offers_is_still_learned():
    entity = _select()
    entity._apply_tv_value("Natural")
    assert entity._attr_current_option == "natural"
    assert entity._ocf_by_option["natural"] == "Natural"


# --- setting ---------------------------------------------------------------


def _wired(entity, monkeypatch, client):
    entity.hass = MagicMock()
    entity._entry_id = "entry"
    entity.async_write_ha_state = lambda: None
    monkeypatch.setattr(entity, "_get_ip_control", lambda: client)
    monkeypatch.setattr(sel, "clear_token_problem", lambda *a, **k: None)


class _Recording:
    def __init__(self, echo=None):
        self.sent = []
        self.echo = echo

    async def async_set_picture_mode(self, value, fallback=None):
        self.sent.append((value, fallback))
        return self.echo if self.echo is not None else value


def test_selecting_ai_customisation_sends_the_current_id(monkeypatch):
    entity = _select()
    client = _Recording()
    _wired(entity, monkeypatch, client)
    entity._apply_tv_value(SDR_VIDEO)
    entity._apply_tv_value(HDR_VIDEO)

    asyncio.run(entity.async_select_option(AI_CUSTOM))

    # The latest id, never AI Optimised's name and never a stale one.
    assert client.sent == [(HDR_VIDEO, None)]


def test_selecting_ai_optimised_sends_its_ocf_name(monkeypatch):
    entity = _select()
    client = _Recording()
    _wired(entity, monkeypatch, client)
    entity._apply_tv_value("Intelligent")

    asyncio.run(entity.async_select_option("intelligent"))

    assert client.sent == [("Intelligent", None)]


def test_a_successful_set_follows_the_echo(monkeypatch):
    # Switching from AI Customisation to AI Optimised must move the reading.
    entity = _select()
    client = _Recording(echo="Intelligent")
    _wired(entity, monkeypatch, client)
    entity._apply_tv_value(SDR_VIDEO)

    asyncio.run(entity.async_select_option("intelligent"))

    assert entity._attr_current_option == "intelligent"
    assert entity._tv_value == "Intelligent"


def test_a_refused_ai_customisation_explains_why(monkeypatch):
    # -32002 for an id the TV is not reporting is the normal answer here, so
    # the error has to say what would have worked instead of just failing.
    entity = _select()

    class _Refusing:
        async def async_set_picture_mode(self, value, fallback=None):
            raise sel.SamsungIPControlModeLockedError("-32002 not offered")

    _wired(entity, monkeypatch, _Refusing())
    entity._apply_tv_value("Standard")
    entity._ocf_by_option[AI_CUSTOM] = SDR_VIDEO
    entity._attr_options = [*entity._attr_options, AI_CUSTOM]

    with pytest.raises(sel.HomeAssistantError) as caught:
        asyncio.run(entity.async_select_option(AI_CUSTOM))

    message = str(caught.value)
    assert "on screen" in message
    assert "Standard" in message  # the last reading, so the user can see why
    assert "AI Mode settings" in message
    # The reading is untouched by a refusal.
    assert entity._tv_value == "Standard"


def test_another_refused_mode_keeps_the_plain_message(monkeypatch):
    entity = _select()

    class _Refusing:
        async def async_set_picture_mode(self, value, fallback=None):
            raise sel.SamsungIPControlModeLockedError("-32002 not offered")

    _wired(entity, monkeypatch, _Refusing())
    entity._apply_tv_value("Standard")

    with pytest.raises(sel.HomeAssistantError) as caught:
        asyncio.run(entity.async_select_option("movie"))

    message = str(caught.value)
    assert "The TV refused picture mode 'movie'" in message
    assert "on screen" not in message


# --- the other reader of the same value ------------------------------------


def test_the_media_player_attribute_publishes_the_id_it_was_given():
    # No OCF name exists for AI Customisation, so picture_mode carries the raw
    # id rather than another mode's name.
    source = (PKG / "media_player.py").read_text()
    assert "data[ATTR_PICTURE_MODE] = picture_mode_ip_base(local_mode)" in source
    assert "picture_mode_ip_reported" not in source


# --- the labels ------------------------------------------------------------


def test_every_locale_that_names_the_modes_names_both_of_these():
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
        for key in ("intelligent", "ai_customization"):
            assert key in state, f"{path.name}: {key}"
            assert state[key].strip(), f"{path.name}: {key}"
        assert state["intelligent"] != state["ai_customization"], path.name
    assert translated >= 8
