"""art_set_slideshow must be able to play an Art Store collection.

The Art app's ``set_slideshow_status`` takes ``category_id`` plus
``sub_category_id`` (notes/QN55LS03FAFXZA/ART_MODE_DECOMPILED.md, "Slideshow
Commands") — that is what the TV's own "Display All" sends for a collection.
The integration only ever built ``f"MY-C000{n}"`` and never sent a
sub-category, so an Art Store collection could not be started from Home
Assistant, and category 10+ became ``MY-C00010``.

The service also returned ``success: true`` whatever the TV answered: the
setter's boolean was dropped. It now reports an error when the TV does not
acknowledge the write, and returns the status the TV reports afterwards.

art.py and media_player.py need Home Assistant to import, so the functions are
exec'd from source.
"""

import asyncio
from pathlib import Path
import textwrap
import unittest

ROOT = Path(__file__).parents[1] / "custom_components" / "samsungtv_smart"
ART = (ROOT / "api" / "art.py").read_text()
MEDIA_PLAYER = (ROOT / "media_player.py").read_text()


def _slice(source, start_marker, end_marker):
    start = source.index(start_marker)
    end = source.index(end_marker, start + len(start_marker))
    return source[start:end]


def _load_art_helpers():
    source = _slice(ART, "def slideshow_category_id(", "\nclass ")
    namespace = {}
    exec(compile(source, "art.py", "exec"), namespace)
    return namespace


def _load_media_player_parts():
    parser = _slice(MEDIA_PLAYER, "_SLIDESHOW_DURATIONS = {", "def _get_default_app_info(")
    method = textwrap.dedent(
        _slice(
            MEDIA_PLAYER,
            "    async def async_art_set_slideshow(",
            "\n    async def async_art_set_auto_rotation(",
        )
    )
    namespace = {}
    exec(compile(parser + "\n" + method, "media_player.py", "exec"), namespace)
    return namespace


ART_NS = _load_art_helpers()
MP_NS = _load_media_player_parts()
category_id = ART_NS["slideshow_category_id"]
build = ART_NS["build_slideshow_request"]
parse_duration = MP_NS["_parse_slideshow_duration"]
set_slideshow = MP_NS["async_art_set_slideshow"]


class CategoryTest(unittest.TestCase):
    def test_my_collection_numbers_keep_their_old_ids(self):
        self.assertEqual(category_id(2), "MY-C0002")
        self.assertEqual(category_id(4), "MY-C0004")
        self.assertEqual(category_id("8"), "MY-C0008")

    def test_two_digit_numbers_are_padded_not_appended(self):
        self.assertEqual(category_id(12), "MY-C0012")

    def test_full_ids_pass_through(self):
        self.assertEqual(category_id("STORE22_C0013"), "STORE22_C0013")
        self.assertEqual(category_id(" MY-C0004 "), "MY-C0004")


class RequestTest(unittest.TestCase):
    def test_favourites_payload_is_unchanged(self):
        self.assertEqual(
            build("set_slideshow_status", 15, True, 4),
            {
                "request": "set_slideshow_status",
                "value": "15",
                "category_id": "MY-C0004",
                "type": "shuffleslideshow",
            },
        )

    def test_store_collection_sends_the_sub_category(self):
        payload = build(
            "set_slideshow_status", 60, False, "STORE22_C0013", "STORE22_C0013_SC_001"
        )
        self.assertEqual(payload["category_id"], "STORE22_C0013")
        self.assertEqual(payload["sub_category_id"], "STORE22_C0013_SC_001")
        self.assertEqual(payload["type"], "slideshow")

    def test_no_sub_category_key_unless_given(self):
        self.assertNotIn("sub_category_id", build("set_slideshow_status", 3, True, 2))
        self.assertNotIn("sub_category_id", build("set_slideshow_status", 3, True, 2, ""))

    def test_zero_means_off(self):
        self.assertEqual(build("set_auto_rotation_status", 0, True, 4)["value"], "off")


class DurationTest(unittest.TestCase):
    def test_presets_integers_and_off(self):
        self.assertEqual(parse_duration("15min"), 15)
        self.assertEqual(parse_duration("1d"), 1440)
        self.assertEqual(parse_duration("30"), 30)
        self.assertEqual(parse_duration("30min"), 30)
        self.assertEqual(parse_duration(180), 180)
        self.assertEqual(parse_duration("off"), 0)
        self.assertEqual(parse_duration("0"), 0)

    def test_garbage_is_rejected(self):
        self.assertIsNone(parse_duration("soon"))
        self.assertIsNone(parse_duration("-5"))


class _Log:
    def debug(self, *args):
        pass

    warning = error = info = debug


class _Device:
    def __init__(self, acknowledged=True, status=None):
        self._log = _Log()
        self._acknowledged = acknowledged
        self._status = status
        self.sent = []

    async def _ensure_frame_tv_check(self):
        return True

    async def _ensure_art_mode_ready(self):
        return True

    def _get_active_slideshow_api(self):
        return "slideshow"

    async def _set_slideshow_via_active_api(self, *args):
        self.sent.append(args)
        return self._acknowledged

    async def _get_slideshow_via_active_api(self):
        return self._status


def _run(device, *args, **kwargs):
    return asyncio.run(set_slideshow(device, *args, **kwargs))


class ServiceTest(unittest.TestCase):
    def test_unacknowledged_write_is_an_error(self):
        result = _run(_Device(acknowledged=False), "15min", True, 8)
        self.assertIn("error", result)
        self.assertNotIn("success", result)

    def test_read_back_status_is_returned(self):
        device = _Device(
            status={
                "value": "15",
                "category_id": "STORE22_C0013",
                "sub_category_id": "STORE22_C0013_SC_001",
                "type": "shuffleslideshow",
                "content_list": "[...]",
            }
        )
        result = _run(device, "15min", True, "STORE22_C0013", "STORE22_C0013_SC_001")
        self.assertTrue(result["success"])
        self.assertEqual(result["status"]["sub_category_id"], "STORE22_C0013_SC_001")
        self.assertNotIn("content_list", result["status"])
        self.assertEqual(
            device.sent, [(15, True, "STORE22_C0013", "STORE22_C0013_SC_001")]
        )

    def test_off_reaches_the_tv_as_zero(self):
        device = _Device()
        _run(device, "off")
        self.assertEqual(device.sent[0][0], 0)

    def test_bad_duration_never_reaches_the_tv(self):
        device = _Device()
        self.assertIn("error", _run(device, "soon"))
        self.assertEqual(device.sent, [])


if __name__ == "__main__":
    unittest.main()
