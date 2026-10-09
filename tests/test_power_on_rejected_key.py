"""A rejected KEY_POWER must not pass for a successful wake.

send_key() reports success as soon as the frame is written to the socket. A
Frame that is asleep can answer ms.channel.unauthorized, which arrives
asynchronously on the socket thread and never reaches that result, so
_async_power_on treated the key as sent and skipped the configured wake method
entirely — measured on 192.168.1.31: KEY_POWER -> unauthorized every 20 minutes
with zero SmartThings attempts all day, which made the "Power on method" option
dead for any reachable-but-rejecting TV.

auth_blocked does not catch this: it needs several consecutive rejections and
resets whenever the TV accepts a connection while awake. The channel is the
honest signal, so a rejection now clears _is_connected and the power-on treats a
key sent over a channel that is not up as not sent.

Both modules need Home Assistant to import, so this is pinned on the source.
"""

from pathlib import Path
import unittest

ROOT = Path(__file__).parents[1] / "custom_components" / "samsungtv_smart"
MEDIA_PLAYER = (ROOT / "media_player.py").read_text()
SAMSUNGWS = (ROOT / "api" / "samsungws.py").read_text()


def _power_on_block() -> str:
    start = MEDIA_PLAYER.index("    async def _async_power_on(")
    return MEDIA_PLAYER[start : MEDIA_PLAYER.index("\n    async def ", start + 1)]


class RejectionClearsTheChannelTest(unittest.TestCase):
    """A rejected channel reports itself as not connected."""

    def test_bump_auth_failure_clears_is_connected(self):
        start = SAMSUNGWS.index("    def _bump_auth_failure")
        block = SAMSUNGWS[start : SAMSUNGWS.index("\n    def ", start + 1)]
        self.assertIn("self._is_connected = False", block)
        # Before the counter, so every rejection path clears it.
        cleared = block.index("self._is_connected = False")
        counter = block.index("self._consecutive_new_tokens += 1")
        self.assertLess(cleared, counter)

    def test_the_connect_path_still_marks_the_channel_up(self):
        # ms.channel.connect sets it back to True after calling the bump, so a
        # new-token issuance is not mistaken for a dead channel.
        self.assertIn("self._is_connected = True", SAMSUNGWS)


class PowerOnDistrustsAnUnbackedKeyTest(unittest.TestCase):
    """KEY_POWER only counts when the channel is actually up."""

    def setUp(self):
        self.block = _power_on_block()

    def test_a_sent_key_is_discarded_when_the_channel_is_down(self):
        self.assertIn("if key_power_sent and not self._ws.is_connected:", self.block)
        gate = self.block.index("if key_power_sent and not self._ws.is_connected:")
        reset = self.block.index("key_power_sent = False", gate)
        fallback = self.block.index("if not key_power_sent:")
        self.assertLess(gate, reset)
        self.assertLess(reset, fallback)

    def test_a_discarded_key_still_reaches_the_wol_default(self):
        # WOL is the default wake and is gated on the key: a discarded key
        # (key_power_sent = False) must still fall through to it.
        gate = self.block.index("elif not key_power_sent:")
        wol = self.block.index("self._send_wol_packet", gate)
        self.assertLess(gate, wol)

    def test_the_configured_method_is_not_gated_behind_the_key(self):
        # #319: an explicitly configured SmartThings / IP Control wake runs on
        # every cold power-on, so it must NOT sit behind `if not
        # key_power_sent` — a "sent" key over a sleeping Frame cannot suppress
        # the method the user chose.
        key_gate = self.block.index("elif not key_power_sent:")
        for wake in ("self._st.async_turn_on()", "ip_client.async_power_on()"):
            self.assertLess(self.block.index(wake), key_gate, wake)

    def test_the_decision_reproduces(self):
        def wake_method_runs(auth_blocked, sent, connected) -> bool:
            key_power_sent = False
            if not auth_blocked:
                key_power_sent = sent
                if key_power_sent and not connected:
                    key_power_sent = False
            return not key_power_sent

        # 192.168.1.31: key written, TV rejected it -> channel down -> wake runs.
        self.assertTrue(wake_method_runs(False, True, False))
        # Healthy TV where KEY_POWER works -> no extra wake.
        self.assertFalse(wake_method_runs(False, True, True))
        # Channel provably unusable -> key skipped entirely, wake runs.
        self.assertTrue(wake_method_runs(True, False, False))
        # Send itself failed (TV off the network) -> wake runs, as before.
        self.assertTrue(wake_method_runs(False, False, False))


if __name__ == "__main__":
    unittest.main()
