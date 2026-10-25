"""One picture-mode change must not write twice on two channels.

async_select_picture_mode used to send the SmartThings command AND the
WebSocket key on every change, even when the cloud write had been read back
as applied. The WS key is a workaround for HDMI content protection, so it
must still be sent whenever SmartThings cannot be confirmed — but not when it
demonstrably worked.
"""

from pathlib import Path
import re
import unittest

ROOT = Path(__file__).parents[1] / "custom_components" / "samsungtv_smart"
MEDIA_PLAYER = (ROOT / "media_player.py").read_text()
SMARTTHINGS = (ROOT / "api" / "smartthings.py").read_text()


def _block(source: str, start: str, end: str) -> str:
    begin = source.index(start)
    return source[begin : source.index(end, begin)]


class SmartThingsContractTest(unittest.TestCase):
    """The setter must report whether the panel applied the mode."""

    def setUp(self):
        self.block = _block(
            SMARTTHINGS,
            "    async def async_set_picture_mode",
            "    def _remember_picture_mode_capability",
        )

    def test_the_signature_announces_a_tri_state_result(self):
        self.assertIn(
            "async def async_set_picture_mode(self, mode: str) -> bool | None:",
            SMARTTHINGS,
        )

    def test_a_verified_apply_returns_true(self):
        verified = self.block[self.block.index("if applied:") :]
        self.assertIn("return True", verified[: verified.index("if applied is None")])

    def test_an_unverifiable_send_returns_none(self):
        self.assertIn("if applied is None:\n                return None", self.block)

    def test_every_failure_path_returns_false(self):
        # "not ON", "every attempt rejected", "accepted but never applied".
        self.assertEqual(len(re.findall(r"\n            return False", self.block)), 3)

    def test_the_nested_attempt_builder_still_returns_nothing(self):
        helper = _block(self.block, "        def _add(", "        for capability in")
        self.assertNotIn("return False", helper)


class CallerTest(unittest.TestCase):
    """The WS key is a fallback, not a companion."""

    def setUp(self):
        self.block = _block(
            MEDIA_PLAYER,
            "    async def async_select_picture_mode",
            "    async def _async_set_hue_sync",
        )

    def test_a_verified_apply_stops_before_the_ws_key(self):
        head = self.block[: self.block.index("ws_key = picture_mode_ws_key(")]
        self.assertIn("if applied:", head)
        self.assertIn("return", head)

    def test_ip_control_goes_first_and_stops_the_chain(self):
        # pictureModeControl echoes the applied mode: nothing to verify, so a
        # success returns before SmartThings and the WS key.
        ip_call = self.block.index("await self._async_ip_control_picture_mode(")
        self.assertLess(
            ip_call, self.block.index("async_set_picture_mode(picture_mode)")
        )
        self.assertIn("return", self.block[ip_call : ip_call + 120])

    def test_only_an_exact_true_counts_as_applied(self):
        # None (unverifiable) must NOT suppress the key.
        self.assertIn("async_set_picture_mode(picture_mode) is True", self.block)

    def test_an_exception_does_not_suppress_the_key(self):
        self.assertIn("except Exception:\n                applied = False", self.block)

    def test_the_ws_key_is_still_sent_on_the_fallback_path(self):
        self.assertIn("await self.async_send_command(ws_key)", self.block)

    def test_a_superseded_request_sends_no_ws_key(self):
        # Two quick selections overlap while SmartThings verifies the first;
        # the older one must not fire its key over the newer mode.
        guard = self.block.index("if seq != self._picture_mode_seq:")
        self.assertLess(self.block.index("self._picture_mode_seq += 1"), guard)
        self.assertLess(
            guard, self.block.index("await self.async_send_command(ws_key)")
        )


if __name__ == "__main__":
    unittest.main()
