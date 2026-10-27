"""Art mode must not be written while an input switch is still negotiating.

Selecting an input sends key presses over the WebSocket; the TV then runs an
HDMI-CEC (Anynet+) negotiation with whatever sits on that input, and a device
that announces itself as the active source during that window is deciding
what the panel shows at the same moment we are.

Reported on a QE55LS03D, every weekday morning. One automation selected the
AVR's input on the TV and started playback on the AVR with no gap between the
two steps:

    07:18:30  select_source: CINEMA 60   -> key presses, then CEC negotiation
    07:18:35  play_media on the AVR      -> AVR announces itself active source

A second automation then asked for Art Mode, and from there the TV refused it
for the rest of the morning:

    07:19:47  artModeOn accepted, panel never switched to Ambient
    07:25:30  same
    ...
    07:59, 08:00, 08:01  still failing, a minute apart

Those last three are the diagnosis. The asking automation is throttled to
300 s, so by then it was not the one asking -- and a TV that merely had a
busy input would have recovered the moment the input changed. The refusals
outlived their cause, which is a wedged TV, not a busy one. The TV's own
remote could not toggle Art Mode either.

So the integration no longer writes art mode into a window it opened itself.
It waits the negotiation out, because the write is still wanted -- "not yet",
not "no". CEC traffic between the TV and its own devices stays invisible from
here, so this covers only the half we cause.
"""

import asyncio
from pathlib import Path
import sys
from types import ModuleType
import unittest

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

from custom_components.samsungtv_smart.art_mode_guard import (  # noqa: E402
    SOURCE_SWITCH_SETTLE,
    ArtModeWriteGuard,
    settle_after_source_switch,
)

ROOT = Path(__file__).parents[1] / "custom_components" / "samsungtv_smart"
MEDIA_PLAYER = (ROOT / "media_player.py").read_text()
SWITCH = (ROOT / "switch.py").read_text()


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def _guard(clock=None):
    clock = clock or _Clock()
    return ArtModeWriteGuard(clock=clock), clock


# --- the guard's own arithmetic -------------------------------------------


class SettleWindowTest(unittest.TestCase):
    def test_nothing_to_wait_for_before_any_source_switch(self):
        guard, _ = _guard()
        self.assertEqual(guard.source_switch_settling(), 0.0)

    def test_the_full_window_is_owed_right_after_a_switch(self):
        guard, _ = _guard()
        guard.note_source_switch()
        self.assertEqual(guard.source_switch_settling(), SOURCE_SWITCH_SETTLE)

    def test_only_the_remainder_is_owed_part_way_through(self):
        guard, clock = _guard()
        guard.note_source_switch()
        clock.advance(SOURCE_SWITCH_SETTLE - 4)
        self.assertAlmostEqual(guard.source_switch_settling(), 4.0)

    def test_nothing_is_owed_once_the_window_has_passed(self):
        guard, clock = _guard()
        guard.note_source_switch()
        clock.advance(SOURCE_SWITCH_SETTLE + 1)
        self.assertEqual(guard.source_switch_settling(), 0.0)

    def test_a_later_switch_restarts_the_window(self):
        guard, clock = _guard()
        guard.note_source_switch()
        clock.advance(SOURCE_SWITCH_SETTLE - 1)
        guard.note_source_switch()
        self.assertEqual(guard.source_switch_settling(), SOURCE_SWITCH_SETTLE)

    def test_the_settle_is_separate_from_the_repeat_write_cooldown(self):
        # check() is "no" and raises; the settle is "not yet" and is waited
        # out. A source switch must not start refusing writes.
        guard, _ = _guard()
        guard.note_source_switch()
        guard.check(True)  # must not raise


# --- the helper waits the remainder, and only the remainder ----------------


class _Log:
    def __init__(self):
        self.debug_calls = []

    def debug(self, *args):
        self.debug_calls.append(args)


def _waited(guard):
    """Seconds the helper actually sleeps, without sleeping in real time."""
    slept = []

    async def _fake_sleep(seconds):
        slept.append(seconds)

    real_sleep = asyncio.sleep
    asyncio.sleep = _fake_sleep
    try:
        log = _Log()
        returned = asyncio.run(settle_after_source_switch(guard, log, "test"))
    finally:
        asyncio.sleep = real_sleep
    return slept, returned, log


def test_no_source_switch_means_no_wait_at_all():
    guard, _ = _guard()

    slept, returned, log = _waited(guard)

    assert slept == []
    assert returned == 0.0
    assert log.debug_calls == [], "a write that waits for nothing must stay quiet"


def test_a_fresh_switch_is_waited_out_in_full():
    guard, _ = _guard()
    guard.note_source_switch()

    slept, returned, log = _waited(guard)

    assert slept == [SOURCE_SWITCH_SETTLE]
    assert returned == SOURCE_SWITCH_SETTLE
    assert log.debug_calls, "a write held back must say so in the log"


def test_only_the_remainder_is_waited():
    guard, clock = _guard()
    guard.note_source_switch()
    clock.advance(SOURCE_SWITCH_SETTLE - 3)

    slept, returned, _ = _waited(guard)

    assert slept == [3.0]
    assert returned == 3.0


def test_waiting_twice_does_not_wait_twice():
    # The switch entity and _ensure_art_mode_ready can both call this on one
    # write. The second call must be free, or every art-mode write through
    # both paths would pay the window twice.
    guard, clock = _guard()
    guard.note_source_switch()

    def _advancing_sleep(seconds):
        clock.advance(seconds)

        async def _noop():
            return None

        return _noop()

    real_sleep = asyncio.sleep
    asyncio.sleep = _advancing_sleep
    try:

        async def _both():
            log = _Log()
            first = await settle_after_source_switch(guard, log, "switch")
            second = await settle_after_source_switch(guard, log, "media_player")
            return first, second

        first, second = asyncio.run(_both())
    finally:
        asyncio.sleep = real_sleep

    assert first == SOURCE_SWITCH_SETTLE
    assert second == 0.0


# --- the wiring ------------------------------------------------------------


class WiringTest(unittest.TestCase):
    def test_selecting_an_input_records_the_switch(self):
        start = MEDIA_PLAYER.index("    async def _async_select_source")
        block = MEDIA_PLAYER[start : MEDIA_PLAYER.index("\n    async def ", start + 1)]
        # Both input branches: the display name and the technical id.
        self.assertEqual(block.count("self._note_source_switch()"), 2)
        # Each one goes after the keys actually reached the TV.
        for branch in block.split("self._note_source_switch()")[:-1]:
            self.assertTrue(branch.rstrip().endswith("return"))

    def test_launching_an_app_does_not_record_a_switch(self):
        # An app launch runs no CEC negotiation, so delaying art mode after
        # one would be a cost with no cause.
        start = MEDIA_PLAYER.index("    async def _async_select_source")
        block = MEDIA_PLAYER[start : MEDIA_PLAYER.index("\n    async def ", start + 1)]
        app_branch = block[block.index("elif self._app_list and source in") :]
        self.assertNotIn("_note_source_switch", app_branch)

    def test_both_switch_directions_settle_before_their_retry_loop(self):
        self.assertEqual(SWITCH.count("await settle_after_source_switch("), 2)
        for intent in ('"Art Mode ON"', '"Art Mode OFF"'):
            start = SWITCH.index(intent)
            after = SWITCH[start : start + 400]
            self.assertIn("since = self._art_api.art_mode_broadcast_count", after)
            self.assertIn("for attempt in range(max_retries):", after)

    def test_the_media_player_art_paths_settle_too(self):
        self.assertIn(
            'await settle_after_source_switch(guard, self._log, "Frame Art")',
            MEDIA_PLAYER,
        )
        self.assertIn('self._log, "art_set_artmode"', MEDIA_PLAYER)

    def test_an_already_on_tv_is_not_delayed(self):
        # The fast path returns before the settle, so a no-op stays instant.
        start = MEDIA_PLAYER.index("    async def _ensure_art_mode_ready")
        block = MEDIA_PLAYER[start : MEDIA_PLAYER.index("\n    async def ", start + 1)]
        self.assertLess(
            block.index("already in Art Mode, ready"),
            block.index("settle_after_source_switch"),
        )


if __name__ == "__main__":
    unittest.main()
