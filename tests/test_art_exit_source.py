"""Source after leaving Art Mode (CONF_ART_EXIT_SOURCE).

Turning Art Mode off makes the art app return the panel to its last source,
which on a Frame is often TV — Samsung TV Plus starting a channel. The option
switches to a chosen input or app instead, but only when Home Assistant turned
Art Mode off: an exit made with the remote is somebody choosing what to watch.

media_player.py needs Home Assistant to import, so the methods are exec'd from
source against a fake self.
"""

import asyncio
from enum import Enum
from pathlib import Path
import textwrap
import time
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).parents[1] / "custom_components" / "samsungtv_smart"
MEDIA_PLAYER = (ROOT / "media_player.py").read_text()
SWITCH = (ROOT / "switch.py").read_text()


class _State(str, Enum):
    ON = "on"
    OFF = "off"


def _method(name):
    start = MEDIA_PLAYER.index(f"    async def {name}(")
    ends = (
        MEDIA_PLAYER.find(marker, start + 10)
        for marker in ("\n    async def ", "\n    def ", "\n    @")
    )
    end = min(i for i in ends if i > 0)
    return textwrap.dedent(MEDIA_PLAYER[start:end])


def _load():
    namespace = {
        "asyncio": asyncio,
        "time": time,
        "ART_EXIT_SOURCE_DELAY": 0,
        "ART_EXIT_SOURCE_STANDBY_GRACE": 3.0,
        "CONF_ART_EXIT_SOURCE": "art_exit_source",
        "DEFAULT_APP": "TV/HDMI",
        "MediaPlayerState": _State,
        "dt_util": SimpleNamespace(utcnow=lambda: None),
    }
    source = (
        _method("_async_apply_art_exit_source") + "\n" + _method("_async_select_source")
    )
    exec(compile(source, "media_player.py", "exec"), namespace)
    return namespace["_async_apply_art_exit_source"], namespace["_async_select_source"]


_apply, _select = _load()


class _Log:
    def debug(self, *args):
        pass

    info = warning = error = debug


class _Device:
    def __init__(self, option=None, state=_State.OFF, standby_at=None):
        self._log = _Log()
        self._options = {"art_exit_source": option} if option is not None else {}
        # The art channel, which stamps go_to_standby. None stands for a TV
        # whose art API is not up: no announcement to weigh.
        self._art_api = SimpleNamespace(standby_last_broadcast=standby_at)
        self.state = state
        self._source_list = {"HDMI 1": "KEY_HDMI1"}
        self._app_list = {"Netflix": "3201907018807"}
        self._channel_list = {}
        self._st = None
        self.calls = []

    def _get_option(self, key, default=None):
        return self._options.get(key, default)

    async def _async_select_source(self, source, *, assume_on=False):
        self.calls.append(("select", source, assume_on))
        return await _select(self, source, assume_on=assume_on)

    async def _async_turn_on(self):
        self.calls.append(("turn_on",))
        return True

    async def _async_wake_for_source_switch(self, source, key):
        self.calls.append(("wake", key))

    async def _async_send_keys(self, key):
        self.calls.append(("key", key))
        return True

    def _note_source_switch(self):
        pass

    async def _async_launch_app(self, app_id):
        self.calls.append(("app", app_id))

    def _resolve_source_by_id(self, source):
        return None

    async def _update_media(self):
        pass

    def async_write_ha_state(self):
        pass


def _run(coro):
    return asyncio.run(coro)


class ApplyTest(unittest.TestCase):
    def test_no_option_keeps_the_tv_behaviour(self):
        for option in (None, "", "   "):
            with self.subTest(option=option):
                device = _Device(option)
                _run(_apply(device))
                self.assertEqual(device.calls, [])

    def test_a_source_is_selected_without_the_power_on_path(self):
        # The media player can still read OFF right after Art Mode (2025
        # Frames report OFF while art is on): the turn-on path would send a
        # power key to a panel that is already on.
        device = _Device("HDMI 1", state=_State.OFF)
        _run(_apply(device))
        self.assertEqual(
            device.calls,
            [("select", "HDMI 1", True), ("wake", "KEY_HDMI1"), ("key", "KEY_HDMI1")],
        )
        self.assertNotIn(("turn_on",), device.calls)

    def test_an_app_can_be_the_exit_source(self):
        device = _Device("Netflix")
        _run(_apply(device))
        self.assertIn(("app", "3201907018807"), device.calls)


class StandbyTest(unittest.TestCase):
    """On a Frame, Art Mode off IS the TV off, and then there is no source.

    Measured on a QE55LS03D, 31 October: art off at 11:11:00.4,
    art_mode_changed 'off' at :00.7, go_to_standby at :03.5, and the exit
    source sent KEY_HDMI3 at :04.8 — 0.3 s before both WebSocket threads
    terminated and the TV read standby.
    """

    def test_an_announcement_during_the_wait_stops_the_switch(self):
        device = _Device("HDMI 1", standby_at=time.monotonic())
        _run(_apply(device))
        self.assertEqual(device.calls, [])

    def test_an_announcement_just_before_the_exit_stops_it_too(self):
        # The TV chooses the order of its own events, so the window reaches
        # back as well as forward.
        device = _Device("HDMI 1", standby_at=time.monotonic() - 2.0)
        _run(_apply(device))
        self.assertEqual(device.calls, [])

    def test_an_older_announcement_does_not_stop_it(self):
        # A standby from an earlier power cycle says nothing about this exit.
        device = _Device("HDMI 1", standby_at=time.monotonic() - 60.0)
        _run(_apply(device))
        self.assertIn(("select", "HDMI 1", True), device.calls)

    def test_no_announcement_at_all_switches_as_before(self):
        device = _Device("HDMI 1", standby_at=None)
        _run(_apply(device))
        self.assertIn(("select", "HDMI 1", True), device.calls)

    def test_a_tv_without_an_art_channel_switches_as_before(self):
        device = _Device("HDMI 1")
        device._art_api = None
        _run(_apply(device))
        self.assertIn(("select", "HDMI 1", True), device.calls)


class SelectSourceTest(unittest.TestCase):
    def test_the_normal_path_still_turns_an_off_tv_on(self):
        device = _Device(state=_State.OFF)
        device._delayed_set_source = None
        _run(_select(device, "HDMI 1"))
        self.assertEqual(device.calls, [("turn_on",)])


class WiringTest(unittest.TestCase):
    def test_the_switch_reports_a_confirmed_exit(self):
        block = SWITCH[SWITCH.index("    async def async_turn_off(") :]
        block = block[: block.index("return  # Success, exit")]
        self.assertIn('f"{SIGNAL_ART_EXITED}_{self._entry.entry_id}"', block)

    def test_the_media_player_listens_for_it(self):
        self.assertIn('f"{SIGNAL_ART_EXITED}_{self._entry_id}"', MEDIA_PLAYER)
        self.assertIn("self._on_art_exited", MEDIA_PLAYER)

    def test_art_set_artmode_off_applies_it_too(self):
        block = MEDIA_PLAYER[
            MEDIA_PLAYER.index("    async def async_art_set_artmode(") :
        ]
        block = block[: block.index("\n    async def ", 10)]
        self.assertIn("if applied and not enabled:", block)
        self.assertIn("self._async_apply_art_exit_source()", block)


if __name__ == "__main__":
    unittest.main()
