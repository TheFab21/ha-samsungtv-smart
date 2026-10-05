"""The IP Control art-mode option must gate every art-mode write.

Our documentation tells users to keep "Switch Art Mode over IP Control" off because
that path "can break Art Mode entirely and may need a factory reset" on some
firmware. Until 8.7.7 the option gated only the artModeControl *getter*, so a
user following that advice still had every art-mode toggle sent to the TV over
JSON-RPC. These tests pin the fix: with the option off, no artModeControl
write is issued. (Since 8.10.0 the getter is read whatever the option;
see test_art_mode_getter_read_without_option.py.)
"""

from pathlib import Path
import unittest

ROOT = Path(__file__).parents[1] / "custom_components" / "samsungtv_smart"
SWITCH = (ROOT / "switch.py").read_text()
MEDIA_PLAYER = (ROOT / "media_player.py").read_text()


def _block(source: str, start: str, end: str) -> str:
    begin = source.index(start)
    return source[begin : source.index(end, begin)]


class SwitchWritePathTest(unittest.TestCase):
    """The Art Mode switch must respect the option before writing."""

    def setUp(self):
        self.block = _block(
            SWITCH, "    async def _set_artmode", "    @property\n    def device_info"
        )

    def test_the_client_is_only_obtained_when_the_option_is_on(self):
        self.assertIn(
            "self._get_ip_control() if self._ip_control_art_mode() else None",
            self.block,
        )

    def test_the_websocket_fallback_is_still_reachable(self):
        self.assertIn("await self._art_api.set_artmode(turn_on)", self.block)

    def test_the_option_defaults_to_off(self):
        helper = _block(
            SWITCH, "    def _ip_control_art_mode", "    async def _set_artmode"
        )
        self.assertIn("CONF_IP_CONTROL_ART_MODE, False", helper)


class MediaPlayerWritePathTest(unittest.TestCase):
    """Entering Art Mode from the media player must respect it too."""

    def setUp(self):
        self.block = _block(
            MEDIA_PLAYER,
            "        # Prefer the reliable IP Control path to enter Art Mode",
            "    async def async_art_upload",
        )

    def test_the_client_is_gated_on_the_option(self):
        self.assertIn("CONF_IP_CONTROL_ART_MODE, False", self.block)
        gate = self.block[: self.block.index("async_set_art_mode_on")]
        self.assertIn("else None", gate)


class NoUngatedSetterTest(unittest.TestCase):
    """No art-mode setter anywhere may escape the option."""

    def test_every_art_mode_write_site_is_gated(self):
        for name, source in (("switch.py", SWITCH), ("media_player.py", MEDIA_PLAYER)):
            for setter in ("async_set_art_mode_on", "async_set_art_mode_off"):
                for line_no, line in enumerate(source.splitlines(), 1):
                    if setter + "()" not in line:
                        continue
                    # The nearest 60 lines above the call must show the gate —
                    # the option itself, or the switch's helper that reads it.
                    context = "\n".join(
                        source.splitlines()[max(0, line_no - 60) : line_no]
                    )
                    self.assertTrue(
                        "CONF_IP_CONTROL_ART_MODE" in context
                        or "self._ip_control_art_mode()" in context,
                        f"{name}:{line_no} calls {setter} with no visible gate",
                    )


if __name__ == "__main__":
    unittest.main()
