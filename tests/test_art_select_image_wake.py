"""art_select_image must be able to leave a sleeping TV asleep.

A slideshow loop (the Frame_Art_Automations.md recipe) kept calling
art_select_image every interval after the TV had been turned off for the
night, and the service powers the TV on into Art Mode first: a Frame bedroom
TV lit up at 00:59 and 02:00. ``wake: false`` only changes the artwork while
Art Mode is already on screen.

media_player.py needs Home Assistant to import, so the method is exec'd from
source against a fake self.
"""

import asyncio
from pathlib import Path
import textwrap
import unittest

ROOT = Path(__file__).parents[1] / "custom_components" / "samsungtv_smart"
MEDIA_PLAYER = (ROOT / "media_player.py").read_text()


def _load_method():
    start = MEDIA_PLAYER.index("    async def async_art_select_image(")
    end = MEDIA_PLAYER.index("\n    async def ", start + 10)
    source = textwrap.dedent(MEDIA_PLAYER[start:end])
    namespace = {}
    exec(compile(source, "media_player.py", "exec"), namespace)
    return namespace["async_art_select_image"]


_select = _load_method()


class _Log:
    def debug(self, *args):
        pass

    warning = error = info = debug


class _ArtApi:
    def __init__(self):
        self.selected = []

    async def select_image(self, content_id, category_id, show):
        self.selected.append(content_id)


class _Device:
    def __init__(self, art_on):
        self._log = _Log()
        self._art_api = _ArtApi()
        self._art_on = art_on
        self.woken = 0

    async def _ensure_frame_tv_check(self):
        return True

    def _art_mode_is_on(self):
        return self._art_on

    async def _ensure_art_mode_ready(self):
        self.woken += 1
        return True


def _run(device, **kwargs):
    return asyncio.run(_select(device, "MY_F0001", **kwargs))


class WakeTest(unittest.TestCase):
    def test_default_still_wakes_the_tv(self):
        device = _Device(art_on=False)
        self.assertEqual(_run(device)["success"], True)
        self.assertEqual(device.woken, 1)
        self.assertEqual(device._art_api.selected, ["MY_F0001"])

    def test_no_wake_leaves_an_off_tv_alone(self):
        for state in (False, None):
            with self.subTest(art_mode=state):
                device = _Device(art_on=state)
                result = _run(device, wake=False)
                self.assertTrue(result["skipped"])
                self.assertEqual(device.woken, 0)
                self.assertEqual(device._art_api.selected, [])

    def test_no_wake_still_changes_the_artwork_in_art_mode(self):
        device = _Device(art_on=True)
        self.assertEqual(_run(device, wake=False)["success"], True)
        self.assertEqual(device._art_api.selected, ["MY_F0001"])


class SchemaTest(unittest.TestCase):
    def test_the_service_accepts_wake_defaulting_to_true(self):
        block = MEDIA_PLAYER[
            MEDIA_PLAYER.index("        SERVICE_ART_SELECT_IMAGE,\n        {") :
        ]
        block = block[: block.index('"async_art_select_image"')]
        self.assertIn("vol.Optional(ATTR_WAKE, default=True): cv.boolean", block)

    def test_the_cookbook_slideshow_does_not_wake_the_tv_after_the_first_pick(self):
        cookbook = (Path(__file__).parents[1] / "Frame_Art_Automations.md").read_text()
        self.assertIn("wake: '{{ repeat.first }}'", cookbook)


if __name__ == "__main__":
    unittest.main()
