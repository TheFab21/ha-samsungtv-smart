"""A redundant Art Mode OFF is not a stale reading.

8.10.0 log, 192.168.1.161: our own source switch took the panel out of art at
05:18:26; the getter read False six times in a row and art_mode_status was
"off". At 05:18:55 a switch.turn_off arrived (caller unknown):

    05:18:55.124 WARNING Art Mode OFF requested for Samsung HACS but the panel
                 already shows it — the art-mode reading was stale; not writing

Nothing was stale: the request was redundant. async_turn_on returns early when
art_mode_status already reads "on"; async_turn_off has no such short-circuit,
on purpose (under #248 art_mode_status can stay "off" while art is shown), so
the panel check is where a redundant OFF lands. It now says so at DEBUG; the
WARNING stays for a reading that really disagreed with the panel.
"""

import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


class _Names:
    def __getattr__(self, name):
        return name


# switch.py also imports Command, which the stubs other test modules install
# (whichever is collected first) do not carry.
pysmartthings = sys.modules.setdefault("pysmartthings", ModuleType("pysmartthings"))
for _name in ("Attribute", "Capability", "Command"):
    if not hasattr(pysmartthings, _name):
        setattr(pysmartthings, _name, _Names())
if not hasattr(pysmartthings, "SmartThings"):
    pysmartthings.SmartThings = object

from custom_components.samsungtv_smart.art_mode_guard import guard_for  # noqa: E402
from custom_components.samsungtv_smart.const import DOMAIN  # noqa: E402
from custom_components.samsungtv_smart.switch import (  # noqa: E402
    FrameArtModeSwitch,
)

ENTRY = "entry-161"
MP = "media_player.samsung_hacs"


def _switch(*, published, panel):
    switch = object.__new__(FrameArtModeSwitch)
    store = {}
    states = {
        MP: SimpleNamespace(state="on", attributes={"art_mode_status": published})
    }
    switch._hass = SimpleNamespace(
        data={DOMAIN: {ENTRY: store}}, states=SimpleNamespace(get=states.get)
    )
    switch._entry = SimpleNamespace(entry_id=ENTRY)
    switch._media_player_entity_id = MP
    switch._device_name = "Samsung HACS"
    switch._log = MagicMock()
    switch._panel_shows_art = AsyncMock(return_value=panel)
    return switch, guard_for(store)


@pytest.mark.parametrize("turn_on", [False, True])
async def test_a_request_our_reading_already_shows_is_debug(turn_on):
    switch, guard = _switch(published="on" if turn_on else "off", panel=turn_on)

    assert await switch._set_artmode(turn_on) is True

    switch._log.warning.assert_not_called()
    message = switch._log.debug.call_args[0][0]
    assert "nothing to write" in message
    assert guard.pending(turn_on) is None


@pytest.mark.parametrize(
    ("turn_on", "published"), [(False, "on"), (True, "off"), (False, None)]
)
async def test_a_reading_that_disagreed_with_the_panel_still_warns(turn_on, published):
    switch, _ = _switch(published=published, panel=turn_on)

    assert await switch._set_artmode(turn_on) is True

    switch._log.warning.assert_called_once()
    assert "the art-mode reading was stale" in switch._log.warning.call_args[0][0]
