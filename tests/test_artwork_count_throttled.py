"""The artwork count must honour CONF_CONTENT_LIST_INTERVAL.

get_content_list returns the WHOLE library — ~19 KB for 61 pieces, measured on
a 2024 Frame — and the only thing published from it is its length. It ran on
every 5 s poll: 5 203 calls and ~99 MB of WebSocket payload in 18 h on one
Frame, for a number that changes only when art is added or removed.

CONF_CONTENT_LIST_INTERVAL already existed for exactly this: defined in const.py
with a 300 s default, offered in the Options screen, translated into six
languages — and read by nothing. The count is now cached for that interval, and
invalidated on any art-content broadcast and after a delete, so an added or
removed piece still shows up at once.

sensor.py needs Home Assistant to import, so the coordinator's methods are
exec'd from source against a fake art API that records every request.
"""

import asyncio
from pathlib import Path
import unittest

ROOT = Path(__file__).parents[1] / "custom_components" / "samsungtv_smart"
SENSOR = (ROOT / "sensor.py").read_text()
ART = (ROOT / "api" / "art.py").read_text()


class _UpdateFailed(Exception):
    """Stand-in for homeassistant.helpers.update_coordinator.UpdateFailed."""


class _Clock:
    """Monotonic stand-in for time.time() so the window can be stepped."""

    def __init__(self):
        self.now = 1000.0

    def time(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


CLOCK = _Clock()


def _load_methods():
    """Exec the coordinator methods we exercise, from source."""
    wanted = (
        "    async def _async_update_data",
        "    def _content_list_interval",
        "    def _artwork_count_is_fresh",
        "    def invalidate_artwork_count",
        "    async def _refresh_art_extras",
        "    def _publish_art_extras",
    )
    body = ""
    for start in wanted:
        begin = SENSOR.index(start)
        end = min(
            i
            for i in (
                SENSOR.find("\n    def ", begin + 1),
                SENSOR.find("\n    async def ", begin + 1),
            )
            if i > 0
        )
        body += SENSOR[begin:end] + "\n"
    namespace = {
        "asyncio": asyncio,
        "time": CLOCK,
        "UpdateFailed": _UpdateFailed,
        "CONF_SLIDESHOW_API": "slideshow_api",
        "CONF_CONTENT_LIST_INTERVAL": "content_list_interval",
        "DEFAULT_CONTENT_LIST_INTERVAL": 300,
        "Any": object,
    }
    exec(compile("class _Holder:\n" + body, "sensor.py", "exec"), namespace)
    return namespace["_Holder"]


_Holder = _load_methods()


class _Log:
    def debug(self, *args):
        pass

    info = warning = error = debug


class _ArtApi:
    def __init__(self):
        self.calls = []

    async def get_artmode(self):
        self.calls.append("get_artmode")
        return "on"

    async def get_current(self):
        self.calls.append("get_current_artwork")
        return {"content_id": "MY_F0006"}

    async def available(self, *a, **kw):
        self.calls.append("get_content_list")
        return [{"content_id": f"MY_F{i:04}"} for i in range(61)]

    async def get_slideshow_status(self):
        self.calls.append("get_slideshow_status")
        return {"value": "off"}

    async def detect_slideshow_api(self):
        self.calls.append("detect_slideshow_api")
        return "slideshow"

    async def get_device_info(self):
        self.calls.append("get_device_info")
        return {"support_motion_sensor": "TRUE"}

    async def get_current_rotation(self):
        self.calls.append("get_current_rotation")
        return "landscape"

    async def get_art_picture_mode(self):
        self.calls.append("get_art_picture_mode")
        return 1


class _Entry:
    entry_id = "abc"
    data = {"slideshow_api": "slideshow"}
    options: dict = {}


class _Coordinator(_Holder):
    def __init__(self, options=None):
        self._log = _Log()
        self._entry = _Entry()
        self._entry.options = options if options is not None else {}
        self._art_api = _ArtApi()
        self.data = None
        self._backoff_until = None
        self._connection_failures = 0
        self._max_connection_failures = 5
        self._thumbnail_failures = 0
        self._thumbnail_backoff_until = None
        self._thumbnail_fetch_enabled = True
        self._last_content_id = None
        self._store_retry_content_id = None
        self._store_retry_at = None
        self._artwork_count = None
        self._artwork_count_at = None
        self._art_device_info = None
        self._art_picture_mode = None
        self._art_rotation = None
        self._art_extras_at = None
        self._art_extras_misses = 0

    def _is_tv_powered_off(self):
        return False

    def _get_media_player_art_mode(self):
        return "on"

    def _has_current_thumbnail(self):
        return True

    def _has_thumbnail_for(self, content_id):
        return True

    def _confirm_content_id(self, raw):
        return raw

    @property
    def _hass(self):
        return self

    async def _fetch_and_save_thumbnail(self, content_id):
        return None

    def async_create_background_task(self, coro, name):
        coro.close()

    def poll(self):
        self.data = asyncio.run(self._async_update_data())
        return self.data


class ThrottleTest(unittest.TestCase):
    def setUp(self):
        CLOCK.now = 1000.0
        self.c = _Coordinator()

    def _content_list_calls(self):
        return self.c._art_api.calls.count("get_content_list")

    def test_the_first_poll_reads_the_library(self):
        self.c.poll()
        self.assertEqual(self._content_list_calls(), 1)
        self.assertEqual(self.c.data["artwork_count"], 61)

    def test_later_polls_within_the_window_reuse_the_count(self):
        self.c.poll()
        for _ in range(12):  # a minute of 5 s polls
            CLOCK.advance(5)
            self.c.poll()
        self.assertEqual(self._content_list_calls(), 1)
        self.assertEqual(self.c.data["artwork_count"], 61)

    def test_the_count_is_re_read_once_the_window_elapses(self):
        self.c.poll()
        CLOCK.advance(301)
        self.c.poll()
        self.assertEqual(self._content_list_calls(), 2)

    def test_the_configured_interval_is_honoured(self):
        c = _Coordinator({"content_list_interval": 30})
        c.poll()
        CLOCK.advance(31)
        c.poll()
        self.assertEqual(c._art_api.calls.count("get_content_list"), 2)

    def test_an_art_content_broadcast_forces_a_re_read(self):
        self.c.poll()
        CLOCK.advance(5)
        self.c.invalidate_artwork_count()
        self.c.poll()
        self.assertEqual(self._content_list_calls(), 2)

    def test_a_failed_read_does_not_poison_the_cache(self):
        async def boom(*a, **kw):
            self.c._art_api.calls.append("get_content_list")
            raise asyncio.TimeoutError

        self.c._art_api.available = boom
        self.c.poll()
        CLOCK.advance(5)
        self.c.poll()
        # Nothing was cached, so the next poll tries again instead of
        # publishing a count the TV never gave us.
        self.assertEqual(self._content_list_calls(), 2)

    def test_the_current_artwork_is_still_read_every_poll(self):
        # The throttle must apply to the heavy call only.
        self.c.poll()
        CLOCK.advance(5)
        self.c.poll()
        self.assertEqual(self.c._art_api.calls.count("get_current_artwork"), 2)


class ArtExtrasTest(unittest.TestCase):
    """Device info, rotation and picture mode ride the same throttle."""

    def setUp(self):
        CLOCK.now = 1000.0
        self.c = _Coordinator()

    def test_extras_are_published(self):
        self.c.poll()
        self.assertEqual(self.c.data["art_rotation"], "landscape")
        self.assertEqual(self.c.data["art_picture_mode"], 1)
        self.assertEqual(
            self.c.data["art_device_info"], {"support_motion_sensor": "TRUE"}
        )
        # The slideshow read after them still runs.
        self.assertIn("get_slideshow_status", self.c._art_api.calls)

    def test_extras_are_throttled_and_device_info_read_once(self):
        self.c.poll()
        CLOCK.advance(5)
        self.c.poll()
        calls = self.c._art_api.calls
        self.assertEqual(calls.count("get_current_rotation"), 1)
        CLOCK.advance(301)
        self.c.poll()
        self.assertEqual(calls.count("get_current_rotation"), 2)
        self.assertEqual(calls.count("get_device_info"), 1)
        self.assertEqual(self.c.data["art_rotation"], "landscape")

    def test_a_broadcast_forces_a_rotation_re_read(self):
        self.c.poll()
        CLOCK.advance(5)
        self.c.invalidate_artwork_count()
        self.c.poll()
        self.assertEqual(self.c._art_api.calls.count("get_current_rotation"), 2)


class ArtExtrasUnsupportedTest(unittest.TestCase):
    """Firmware that does not answer the extras is not asked forever."""

    def setUp(self):
        CLOCK.now = 1000.0
        self.c = _Coordinator()

        async def silent(*a, **kw):
            self.c._art_api.calls.append("silent")
            return None

        api = self.c._art_api
        api.get_device_info = api.get_current_rotation = silent
        api.get_art_picture_mode = silent

    def test_one_attempt_per_window_then_stop_after_two(self):
        self.c.poll()
        CLOCK.advance(5)
        self.c.poll()
        self.assertEqual(self.c._art_api.calls.count("silent"), 3)
        CLOCK.advance(301)
        self.c.poll()
        self.assertEqual(self.c._art_api.calls.count("silent"), 6)
        CLOCK.advance(301)
        self.c.poll()
        self.assertEqual(self.c._art_api.calls.count("silent"), 6)


class WiringTest(unittest.TestCase):
    """The invalidation has to actually be hooked up."""

    def test_the_content_callback_invalidates_the_count(self):
        block = SENSOR[SENSOR.index("def _on_art_content()") :][:600]
        self.assertIn("coordinator.invalidate_artwork_count()", block)

    def test_a_delete_notifies_the_listeners(self):
        block = ART[ART.index("async def delete_list") :]
        block = block[: block.index("return True")]
        self.assertIn("self._fire_art_content_event()", block)

    def test_the_option_is_no_longer_decorative(self):
        # It was defined, offered in the Options screen and translated into six
        # languages while no runtime code read it.
        self.assertIn("CONF_CONTENT_LIST_INTERVAL,", SENSOR)
        self.assertIn(
            "self._entry.options.get(\n            CONF_CONTENT_LIST_INTERVAL",
            SENSOR,
        )


if __name__ == "__main__":
    unittest.main()
