"""A deferred art-on must not suppress the answer it is waiting for (#319).

A 2022 Frame that leaves the network in standby, woken out of band by an IR
blaster. The user asks for Art Mode while Home Assistant still believes the
TV is off, so the switch powers it on itself and waits:

    12:38:51.058  Power on: method=WOL, KEY_POWER not sent
    12:39:11.190  TV did not become ready within 20 seconds
    12:39:11.191  ... the TV did not answer within 20s — art mode deferred

That path set an optimistic False. The optimistic hold exists to protect an
explicit toggle from a reading that still lags behind it — but the toggle
here was ON, so holding False suppressed the truth instead. Nine seconds
later the TV was up and in Art Mode, and every source said so:

    12:39:20.444  IP Control art-mode changed: None -> True
    12:39:20.446  Ignoring stale media_player art_mode_status=on
                  (holding optimistic False)
    ... repeated at 12:39:21, :26, :31, :36, :41, :46, :51 ...
    12:39:56.462  Art Mode state updated: True   (the 45 s window expiring)

45 seconds of a switch reading off with artwork on the panel. The deferral
still shows the switch off — the panel is dark at that moment — but it no
longer refuses contradicting readings; _pending_art_on already records that
a retry is owed.
"""

from pathlib import Path
import sys
import time
from types import ModuleType
import unittest
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

from custom_components.samsungtv_smart.switch import FrameArtModeSwitch  # noqa: E402

SWITCH = (
    Path(__file__).parents[1] / "custom_components" / "samsungtv_smart" / "switch.py"
).read_text()


def _switch():
    """A switch with only the optimistic-hold machinery wired up."""
    sw = object.__new__(FrameArtModeSwitch)
    sw._log = MagicMock()
    sw._attr_is_on = None
    sw._optimistic_value = None
    sw._optimistic_until = 0.0
    sw._pending_art_on = False
    return sw


def _defer(sw):
    """What the timeout branch of async_turn_on now leaves behind."""
    sw._pending_art_on = True
    sw._attr_is_on = False
    sw._optimistic_value = None


# --- the deferral must not block the TV's answer --------------------------


class DeferralTest(unittest.TestCase):
    def test_a_true_reading_after_a_deferral_is_accepted(self):
        sw = _switch()
        _defer(sw)

        self.assertFalse(sw._optimistic_blocks(True))

    def test_the_switch_still_reads_off_while_deferred(self):
        # The panel is dark at that moment; showing it on would be a lie.
        sw = _switch()
        _defer(sw)

        self.assertIs(sw._attr_is_on, False)

    def test_the_retry_is_still_recorded(self):
        sw = _switch()
        _defer(sw)

        self.assertTrue(sw._pending_art_on)

    def test_a_false_reading_after_a_deferral_is_accepted_too(self):
        # Nothing is held either way: the deferral has no opinion to defend.
        sw = _switch()
        _defer(sw)

        self.assertFalse(sw._optimistic_blocks(False))

    def test_a_deferral_clears_a_hold_left_by_an_earlier_toggle(self):
        sw = _switch()
        sw._set_optimistic(True)
        _defer(sw)

        self.assertFalse(sw._optimistic_blocks(True))


# --- the hold itself still works where it belongs -------------------------


class ExplicitToggleStillHeldTest(unittest.TestCase):
    def test_an_explicit_off_still_outranks_a_lagging_on(self):
        sw = _switch()
        sw._set_optimistic(False)

        self.assertTrue(sw._optimistic_blocks(True))

    def test_an_explicit_on_still_outranks_a_lagging_off(self):
        sw = _switch()
        sw._set_optimistic(True)

        self.assertTrue(sw._optimistic_blocks(False))

    def test_a_matching_reading_clears_the_hold(self):
        sw = _switch()
        sw._set_optimistic(True)

        self.assertFalse(sw._optimistic_blocks(True))
        self.assertIsNone(sw._optimistic_value)

    def test_the_hold_expires_on_its_own(self):
        sw = _switch()
        sw._set_optimistic(False)
        sw._optimistic_until = time.monotonic() - 1

        self.assertFalse(sw._optimistic_blocks(True))


# --- pinned against the source --------------------------------------------


class SourceTest(unittest.TestCase):
    def test_the_deferral_branch_does_not_arm_the_hold(self):
        branch = SWITCH[SWITCH.index("art mode deferred until it is back online") :]
        branch = branch[: branch.index("self.async_write_ha_state()")]
        self.assertNotIn("_set_optimistic", branch)
        self.assertIn("self._optimistic_value = None", branch)
        self.assertIn("self._attr_is_on = False", branch)

    def test_an_explicit_toggle_still_arms_it(self):
        # The success paths of turn_on and turn_off must keep the hold.
        self.assertIn("self._set_optimistic(True)", SWITCH)
        self.assertIn("self._set_optimistic(False)", SWITCH)


# --- the WS fallback honours the hold as well (#456) ------------------------


class WsFallbackHoldTest(unittest.TestCase):
    """Right after a power-on the media_player has no art_mode_status yet.

    Home Assistant refreshes a polled entity after its turn_on service, so the
    switch read the art channel directly 5 ms after confirming Art Mode ON and
    took a stale 'off' straight into its state.
    """

    def _run(self, sw, answer):
        import asyncio

        sw._updating = False
        sw._available = False
        sw._is_tv_on = MagicMock(return_value=_coro(True))
        sw._get_media_player_entity_id = MagicMock(return_value=None)
        sw._hass = MagicMock()
        sw._art_api = MagicMock()
        sw._art_api.get_artmode = MagicMock(return_value=_coro(answer))
        asyncio.run(sw.async_update())

    def test_a_contradicting_ws_read_is_held_off(self):
        sw = _switch()
        sw._set_optimistic(True)
        self._run(sw, "off")
        self.assertIs(sw._attr_is_on, True)

    def test_without_a_hold_the_ws_read_is_the_state(self):
        sw = _switch()
        self._run(sw, "off")
        self.assertIs(sw._attr_is_on, False)


async def _value(value):
    return value


def _coro(value):
    return _value(value)
