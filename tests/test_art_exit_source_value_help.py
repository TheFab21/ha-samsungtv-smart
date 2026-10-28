"""A key in a field that wants a source name must say so, and the form should offer the names.

Reported on a QE55LS03D, 28 October. The art exit source option held the raw
key `KEY_HDMI3`:

    07:27:39.872  INFO  Art Mode turned off: switching to 'KEY_HDMI3' (art exit source option)
    07:27:39.872  ERROR Unsupported source

The panel left Art Mode and went nowhere. Two things let that happen.

The option takes a source NAME, which _async_select_source looks up in the
source / app / channel lists; a key is the right-hand side of a source list
entry, not a name. "Unsupported source" named neither the value it refused nor
anything that would have worked, so there was nothing to act on.

And the form that accepted it: _art_exit_source_selector offers a dropdown
built from the CONFIGURED lists, which are empty whenever the source list is
left blank and discovered from SmartThings at runtime -- that is, for exactly
the users who most need to be shown the names. They got a bare text box.
"""

import pathlib
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

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

from custom_components.samsungtv_smart import config_flow as cf  # noqa: E402
from custom_components.samsungtv_smart import media_player as mp  # noqa: E402

# --- the message -----------------------------------------------------------


def _log_for(source, sources=("TV", "CINEMA 60")):
    """The error lines the real method emits for this value.

    source_list is a property with side effects (it refreshes the ST and app
    lists); the message only needs its value, so the method is called against
    a stand-in carrying just what it reads.
    """
    log = MagicMock()
    mp.SamsungTVDevice._log_unsupported_source(
        SimpleNamespace(_log=log, source_list=list(sources)), source
    )
    return [str(c) for c in log.error.call_args_list]


def test_a_key_is_named_as_a_key_and_the_field_is_pointed_at():
    errors = _log_for("KEY_HDMI3")

    assert len(errors) == 1
    message = errors[0]
    assert "KEY_HDMI3" in message
    assert "is a key, not a source name" in message
    assert "Source list" in message


def test_the_st_and_ip_prefixes_get_the_same_help():
    for key in ("ST_HDMI3", "IP_HDMI3"):
        message = _log_for(key)[0]
        assert "is a key, not a source name" in message, key
        assert key in message, key


def test_a_plain_typo_is_refused_with_the_value_and_the_alternatives():
    message = _log_for("Cinema60")[0]

    assert "Cinema60" in message
    assert "is a key, not a source name" not in message
    assert "CINEMA 60" in message, "the names that would have worked are listed"


def test_the_known_sources_are_always_listed():
    for source in ("KEY_HDMI3", "whatever"):
        assert "CINEMA 60" in _log_for(source)[0], source


def test_an_empty_source_list_says_none_rather_than_nothing():
    message = _log_for("KEY_HDMI3", sources=())[0]

    assert "none" in message


# --- the form --------------------------------------------------------------


class _Flow:
    """Only what _art_exit_source_selector and _live_source_names touch."""

    def __init__(self, configured=None, published=None, entity="media_player.frame"):
        self._source_list = configured
        self._app_list = None
        self._entry_id = "entry-1"
        self._entity = entity
        self._published = published
        states = SimpleNamespace(
            get=lambda eid: (
                SimpleNamespace(attributes={"source_list": self._published})
                if eid == self._entity and self._published is not None
                else None
            )
        )
        self.hass = SimpleNamespace(states=states)

    _live_source_names = cf.OptionsFlowHandler._live_source_names
    _art_exit_source_selector = cf.OptionsFlowHandler._art_exit_source_selector


def _with_entry_entities(entities):
    original = cf._async_get_entry_entities
    cf._async_get_entry_entities = lambda hass, entry_id: entities
    return original


def _restore(original):
    cf._async_get_entry_entities = original


def test_the_dropdown_is_built_from_the_live_list_when_none_is_configured():
    # The reported case: nothing configured, SmartThings discovered the names
    # at runtime. That is who needs the list most.
    flow = _Flow(configured={}, published=["TV", "CINEMA 60", "Netflix"])
    original = _with_entry_entities(["media_player.frame", "switch.frame_art_mode"])
    try:
        selector = flow._art_exit_source_selector()
    finally:
        _restore(original)

    assert isinstance(selector, cf.SelectSelector)
    options = selector.config["options"]
    assert sorted(options) == ["CINEMA 60", "Netflix", "TV"]


def test_a_configured_list_still_wins():
    flow = _Flow(configured={"HDMI 1": "KEY_HDMI1"}, published=["TV"])
    original = _with_entry_entities(["media_player.frame"])
    try:
        selector = flow._art_exit_source_selector()
    finally:
        _restore(original)

    assert selector.config["options"] == ["HDMI 1"]


def test_only_the_media_player_entity_is_read():
    # A switch or sensor of the same entry has no source_list to offer.
    flow = _Flow(configured={}, published=["TV"], entity="media_player.frame")
    original = _with_entry_entities(["switch.frame_art_mode", "media_player.frame"])
    try:
        selector = flow._art_exit_source_selector()
    finally:
        _restore(original)

    assert selector.config["options"] == ["TV"]


def test_a_free_text_field_remains_the_fallback():
    # Nothing configured and nothing published: a text box is still better
    # than an empty dropdown.
    flow = _Flow(configured={}, published=None)
    original = _with_entry_entities(["media_player.frame"])
    try:
        selector = flow._art_exit_source_selector()
    finally:
        _restore(original)

    assert isinstance(selector, cf.TextSelector)


def test_a_typed_value_is_still_allowed():
    # custom_value keeps an app or source the TV has not reported yet usable.
    flow = _Flow(configured={}, published=["TV"])
    original = _with_entry_entities(["media_player.frame"])
    try:
        selector = flow._art_exit_source_selector()
    finally:
        _restore(original)

    assert selector.config["custom_value"] is True


# --- the wiring ------------------------------------------------------------


def test_the_select_path_uses_the_helper_and_not_the_bare_message():
    # Without this the helper could be orphaned and every message test above
    # would still pass against a call site that says only "Unsupported source".
    source = pathlib.Path(mp.__file__).read_text()
    start = source.index("    async def _async_select_source")
    block = source[start : source.index("\n    def _log_unsupported_source", start)]

    assert "self._log_unsupported_source(source)" in block
    assert '"Unsupported source"' not in block
