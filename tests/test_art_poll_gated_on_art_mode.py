"""Art-only reads must not be polled while the TV is not showing art (#273).

A Frame sitting on an input (HDMI) answers get_artmode_status but stays silent
on get_current_artwork and on the slideshow reads. The Frame Art coordinator
polls every 5 s, so each cycle piled up two requests that could only time out
after 5 s each. Three consecutive timeouts trip the art channel's wedge breaker
(ART_WS_TIMEOUT_TRIP), which force-closes a socket that is demonstrably
answering — 14 reconnects in 70 min measured on a 13-TV fleet, none of which
changed anything, because nothing was broken. The recovery backoff added in
8.9.6 only spaced the pointless reconnects out; it did not stop them.

Neither the current artwork nor the slideshow state can change while art mode
is off, so those reads are skipped and the already-published values are kept.
get_artmode() is still polled every cycle: it is the request the TV does
answer, and it is what tells us when to resume.

sensor.py needs Home Assistant to import, so the coordinator's update method is
exec'd from source against a fake art API that records every request.
"""

import asyncio
from pathlib import Path
import unittest

ROOT = Path(__file__).parents[1] / "custom_components" / "samsungtv_smart"
SENSOR = (ROOT / "sensor.py").read_text()


class _UpdateFailed(Exception):
    """Stand-in for homeassistant.helpers.update_coordinator.UpdateFailed."""


def _load_update_method():
    """Exec _async_update_data from source and return the raw function."""
    start = SENSOR.index("    async def _async_update_data")
    end = SENSOR.index("\n    def _is_tv_powered_off", start)
    source = "class _Holder:\n" + SENSOR[start:end] + "\n"
    namespace = {
        "asyncio": asyncio,
        "time": __import__("time"),
        "UpdateFailed": _UpdateFailed,
        "CONF_SLIDESHOW_API": "slideshow_api",
        "Any": object,
    }
    exec(compile(source, "sensor.py:_async_update_data", "exec"), namespace)
    return namespace["_Holder"]._async_update_data


_update = _load_update_method()


class _Log:
    def debug(self, *args):
        pass

    info = warning = error = debug


class _Entry:
    entry_id = "abc"
    data: dict = {"slideshow_api": "slideshow"}


class _ArtApi:
    """Records every art request; answers only get_artmode, like a Frame on HDMI."""

    def __init__(self, art_mode="off"):
        self.calls: list[str] = []
        self._art_mode = art_mode

    async def get_artmode(self):
        self.calls.append("get_artmode")
        return self._art_mode

    async def get_current(self):
        self.calls.append("get_current_artwork")
        # The real TV never answers this on an input: it times out.
        raise asyncio.TimeoutError

    async def get_slideshow_status(self):
        self.calls.append("get_slideshow_status")
        raise asyncio.TimeoutError

    async def get_auto_rotation_status(self):
        self.calls.append("get_auto_rotation_status")
        raise asyncio.TimeoutError

    async def detect_slideshow_api(self):
        self.calls.append("detect_slideshow_api")
        raise asyncio.TimeoutError

    async def available(self, *args, **kwargs):
        self.calls.append("available")
        raise asyncio.TimeoutError


class _Coordinator:
    """The fake self the extracted method runs against."""

    def __init__(self, art_mode="off", previous=None):
        self._log = _Log()
        self._entry = _Entry()
        self._art_api = _ArtApi(art_mode)
        self._media_player_art_mode = art_mode
        self.data = previous
        self._backoff_until = None
        self._connection_failures = 0
        self._max_connection_failures = 5
        self._thumbnail_failures = 0
        self._thumbnail_backoff_until = None
        self._thumbnail_fetch_enabled = True
        self._last_content_id = None
        self._store_retry_content_id = None
        self._store_retry_at = None
        self.background_tasks: list[str] = []

    # --- collaborators the method calls on self ---
    def _is_tv_powered_off(self):
        return False

    def _get_media_player_art_mode(self):
        return self._media_player_art_mode

    def _has_current_thumbnail(self):
        return True

    def _has_thumbnail_for(self, content_id):
        return False

    def _confirm_content_id(self, raw):
        return raw

    @property
    def _hass(self):
        return self

    def async_create_background_task(self, coro, name):
        coro.close()
        self.background_tasks.append(name)


def _run(coordinator):
    return asyncio.run(_update(coordinator))


class ArtModeOffTest(unittest.TestCase):
    """The case that produced the loop: the TV is on, showing an input."""

    def setUp(self):
        self.previous = {
            "art_mode": "on",
            "current_artwork": {"content_id": "MY_F0042"},
            "artwork_count": 17,
            "slideshow_status": "off",
            "api_version": None,
            "current_thumbnail_url": None,
            "tv_powered_off": False,
        }
        self.coordinator = _Coordinator("off", previous=self.previous)
        self.data = _run(self.coordinator)

    def test_no_art_only_request_is_sent(self):
        for request in (
            "get_current_artwork",
            "get_slideshow_status",
            "get_auto_rotation_status",
            "available",
        ):
            self.assertNotIn(request, self.coordinator._art_api.calls)

    def test_the_cycle_sends_nothing_at_all_when_the_media_player_knows(self):
        # art_mode comes from the media_player here, so a gated cycle must be
        # free: this is what stops the 5 s poll feeding the wedge breaker.
        self.assertEqual(self.coordinator._art_api.calls, [])

    def test_the_already_published_values_are_kept(self):
        self.assertEqual(self.data["current_artwork"], self.previous["current_artwork"])
        self.assertEqual(self.data["artwork_count"], 17)
        self.assertEqual(self.data["slideshow_status"], "off")

    def test_no_thumbnail_fetch_is_triggered(self):
        self.assertEqual(self.coordinator.background_tasks, [])

    def test_the_slideshow_api_is_not_detected_on_a_silent_channel(self):
        # Detection on a TV that cannot answer either endpoint would latch a
        # wrong choice into entry.data, as the capability probes once did.
        coordinator = _Coordinator("off", previous=self.previous)
        coordinator._entry = type("E", (_Entry,), {"data": {}})()
        _run(coordinator)
        self.assertNotIn("detect_slideshow_api", coordinator._art_api.calls)


class ArtModeOnTest(unittest.TestCase):
    """Art mode on: every read still happens."""

    def setUp(self):
        self.coordinator = _Coordinator("on")
        _run(self.coordinator)

    def test_the_current_artwork_is_still_read(self):
        self.assertIn("get_current_artwork", self.coordinator._art_api.calls)


class ArtModeUnknownTest(unittest.TestCase):
    """An unknown reading must not gate: only a positive "off" skips."""

    def setUp(self):
        self.coordinator = _Coordinator("on")
        # media_player state not up yet -> the direct API fallback is used.
        self.coordinator._media_player_art_mode = None
        self.coordinator._art_api._art_mode = None
        _run(self.coordinator)

    def test_the_direct_api_is_asked(self):
        self.assertIn("get_artmode", self.coordinator._art_api.calls)

    def test_the_current_artwork_is_still_read(self):
        self.assertIn("get_current_artwork", self.coordinator._art_api.calls)


class SourceShapeTest(unittest.TestCase):
    """Guard the two properties the behavioural tests rely on."""

    def test_the_gate_is_a_positive_off_not_a_falsy_check(self):
        self.assertIn('art_mode_off = data["art_mode"] == "off"', SENSOR)

    def test_get_artmode_is_read_before_the_gate(self):
        poll = SENSOR.index("art_mode = await self._art_api.get_artmode()")
        gate = SENSOR.index('art_mode_off = data["art_mode"] == "off"')
        self.assertLess(poll, gate)


if __name__ == "__main__":
    unittest.main()
