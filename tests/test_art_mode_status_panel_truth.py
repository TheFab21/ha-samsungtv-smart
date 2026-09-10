"""art_mode_status must follow the panel, not freeze on a stale WS reading (#248).

Measured over 7 days on an 11-Frame fleet: with the art-mode option off (the
recommended default), the transition INTO art mode left art_mode_status frozen
at its pre-transition value for hours — the panel showed art, getTVStates
reported pictureMode 'Ambient', but the attribute stayed 'off' because
_art_mode_is_on() only consulted the WebSocket art channel, which never
received the change event.

_art_mode_is_on now reads the cached getTVStates.pictureMode (tri-state) after
the IP flag cache and before the WS/SmartThings fallbacks. media_player.py is
not importable without Home Assistant, so this is checked structurally and the
tri-state reader is checked by extraction.
"""

from pathlib import Path
import unittest

MEDIA_PLAYER = (
    Path(__file__).parents[1]
    / "custom_components"
    / "samsungtv_smart"
    / "media_player.py"
).read_text()


def _block(source: str, start: str, end: str) -> str:
    begin = source.index(start)
    return source[begin : source.index(end, begin)]


class TriStateReaderTest(unittest.TestCase):
    """_ip_control_panel_art_cached: True / False / None, cached only."""

    def setUp(self):
        self.block = _block(
            MEDIA_PLAYER,
            "    def _ip_control_panel_art_cached",
            "    def _ip_control_ambient_mode_active",
        )

    def test_it_reads_the_cached_coordinator_snapshot_not_a_live_call(self):
        self.assertIn(
            "coordinator = self._get_ip_control_state_coordinator()", self.block
        )
        self.assertIn('data.get("powered_off")', self.block)
        # Must not issue a request — it runs from a sync property.
        self.assertNotIn("await", self.block)
        self.assertNotIn("async_panel_shows_art", self.block)

    def test_unreadable_states_return_none_not_false(self):
        # No coordinator / no snapshot / powered off / no pictureMode -> None,
        # so _art_mode_is_on falls through instead of asserting "off".
        head = self.block[: self.block.index('return mode == "Ambient"')]
        self.assertEqual(head.count("return None"), 3)

    def test_ambient_is_the_only_true(self):
        self.assertIn('return mode == "Ambient"', self.block)

    def test_the_bool_helper_delegates_to_the_tristate(self):
        helper = _block(
            MEDIA_PLAYER,
            "    def _ip_control_ambient_mode_active",
            "    def _get_ip_control_input_source",
        )
        self.assertIn("return self._ip_control_panel_art_cached() is True", helper)
        # The coordinator-reading logic now lives in one place only.
        self.assertNotIn("coordinator", helper)


class ArtModeIsOnTest(unittest.TestCase):
    """The panel truth is consulted in the right order."""

    def setUp(self):
        self.block = _block(
            MEDIA_PLAYER,
            "    def _art_mode_is_on",
            "    @property\n    def extra_state_attributes",
        )

    def test_the_panel_is_consulted_after_the_ip_flag_cache(self):
        flag = self.block.index("if self._ip_art_mode is not None:")
        panel = self.block.index("panel_art = self._ip_control_panel_art_cached()")
        self.assertLess(flag, panel)

    def test_the_panel_is_consulted_before_the_websocket_and_smartthings(self):
        panel = self.block.index("panel_art = self._ip_control_panel_art_cached()")
        art_api = self.block.index("art_api.art_mode")
        ws = self.block.index("self._ws.artmode_status")
        self.assertLess(panel, art_api)
        self.assertLess(panel, ws)

    def test_only_a_readable_panel_short_circuits(self):
        seg = self.block[
            self.block.index("panel_art = self._ip_control_panel_art_cached()") :
        ]
        head = seg[: seg.index("PowerState")]
        self.assertIn("if panel_art is not None:", head)
        self.assertIn("return panel_art", head)

    def test_the_running_app_guard_still_leads(self):
        # A real foreground app must still win over any art signal.
        guard = self.block.index("self._running_app not in (None, DEFAULT_APP)")
        panel = self.block.index("panel_art = self._ip_control_panel_art_cached()")
        self.assertLess(guard, panel)


if __name__ == "__main__":
    unittest.main()
