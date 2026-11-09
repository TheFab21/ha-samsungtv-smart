"""Art services: leave a TV that is not in Art Mode alone, report a silent TV.

Two things seen on a 2025 Frame (QE43LS03FAUXXH):

1. art_set_slideshow, art_change_matte, art_set_brightness and
   art_set_color_temperature power the TV on and switch it to Art Mode before
   they write. An automation re-applied the slideshow 30 s after power-on
   while an app was on screen: the call took 5 s, wrote artModeOn, and the TV
   left the app about 3 s after it returned. art_select_image already has
   ``wake: false`` for this; these four now take it too.

2. art_select_image, art_change_matte, art_set_brightness,
   art_set_color_temperature and art_set_photo_filter returned
   ``{"success": True}`` whatever the TV said. A portrait upload the TV could
   not display got no answer to select_image, and the service still reported
   success. They now answer like art_delete and art_set_favourite (#401).

media_player.py needs Home Assistant to import, so the methods are exec'd from
source against a fake self, as in test_art_select_image_wake.py.
"""

import asyncio
from pathlib import Path
import textwrap
import unittest

ROOT = Path(__file__).parents[1] / "custom_components" / "samsungtv_smart"
MEDIA_PLAYER = (ROOT / "media_player.py").read_text()
SERVICES = (ROOT / "services.yaml").read_text()


def _load(name):
    start = MEDIA_PLAYER.index(f"    async def {name}(")
    end = MEDIA_PLAYER.index("\n    async def ", start + 10)
    namespace = {"_parse_slideshow_duration": lambda duration: 15}
    exec(
        compile(textwrap.dedent(MEDIA_PLAYER[start:end]), "media_player.py", "exec"),
        namespace,
    )
    return namespace[name]


class _Log:
    def debug(self, *args):
        pass

    warning = error = info = debug


class _ArtApi:
    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def _call(self, name):
        async def method(*args, **kwargs):
            self.calls.append(name)
            return self.answers

        return method

    def __getattr__(self, name):
        return self._call(name)


class _Device:
    def __init__(self, art_on, answers=True):
        self._log = _Log()
        self._art_api = _ArtApi(answers)
        self._art_on = art_on
        self._answers = answers
        self.woken = 0
        self.slideshow_writes = 0

    async def _ensure_frame_tv_check(self):
        return True

    def _art_mode_is_on(self):
        return self._art_on

    async def _ensure_art_mode_ready(self):
        self.woken += 1
        return True

    async def _set_slideshow_via_active_api(self, *args):
        self.slideshow_writes += 1
        return self._answers

    async def _get_slideshow_via_active_api(self):
        return {"value": "15"}

    def _get_active_slideshow_api(self):
        return "slideshow"


# service method -> positional arguments for a normal call
FORCING = {
    "async_art_set_brightness": (50,),
    "async_art_set_color_temperature": (0,),
    "async_art_change_matte": ("MY_F0001", "modernthin_neutral"),
    "async_art_set_slideshow": ("15min",),
}
REPORTING = {
    "async_art_select_image": ("MY_F0001",),
    "async_art_set_brightness": (50,),
    "async_art_set_color_temperature": (0,),
    "async_art_change_matte": ("MY_F0001", "modernthin_neutral"),
    "async_art_set_photo_filter": ("MY_F0001", "ink"),
}


def _run(name, device, args, **kwargs):
    return asyncio.run(_load(name)(device, *args, **kwargs))


def _wrote(device):
    return bool(device._art_api.calls) or device.slideshow_writes > 0


class WakeTest(unittest.TestCase):
    def test_default_still_wakes_the_tv(self):
        for name, args in FORCING.items():
            with self.subTest(service=name):
                device = _Device(art_on=False)
                result = _run(name, device, args)
                self.assertTrue(result.get("success"), result)
                self.assertEqual(device.woken, 1)
                self.assertTrue(_wrote(device))

    def test_no_wake_leaves_a_tv_that_is_not_in_art_mode_alone(self):
        for name, args in FORCING.items():
            for state in (False, None):
                with self.subTest(service=name, art_mode=state):
                    device = _Device(art_on=state)
                    result = _run(name, device, args, wake=False)
                    self.assertEqual(
                        result, {"skipped": True, "reason": "art_mode_off"}
                    )
                    self.assertEqual(device.woken, 0)
                    self.assertFalse(_wrote(device))

    def test_no_wake_still_writes_while_art_mode_is_on_screen(self):
        for name, args in FORCING.items():
            with self.subTest(service=name):
                device = _Device(art_on=True)
                result = _run(name, device, args, wake=False)
                self.assertTrue(result.get("success"), result)
                self.assertTrue(_wrote(device))

    def test_auto_rotation_passes_wake_on_to_the_slideshow(self):
        seen = {}

        class Device:
            async def async_art_set_slideshow(self, *args):
                seen["args"] = args
                return {"success": True}

        asyncio.run(_load("async_art_set_auto_rotation")(Device(), "15min", wake=False))
        self.assertEqual(seen["args"], ("15min", True, 2, None, False))


class ResultTest(unittest.TestCase):
    def test_a_silent_tv_is_reported_not_claimed_as_success(self):
        for name, args in REPORTING.items():
            with self.subTest(service=name):
                device = _Device(art_on=True, answers=False)
                result = _run(name, device, args)
                self.assertEqual(result.get("error"), "no answer from the TV")
                self.assertNotIn("success", result)

    def test_an_answer_from_the_tv_is_still_success(self):
        for name, args in REPORTING.items():
            with self.subTest(service=name):
                device = _Device(art_on=True, answers=True)
                self.assertTrue(_run(name, device, args).get("success"))


class SchemaTest(unittest.TestCase):
    SERVICES_WITH_WAKE = (
        "art_set_brightness",
        "art_set_color_temperature",
        "art_change_matte",
        "art_set_slideshow",
        "art_set_auto_rotation",
    )

    def test_the_services_accept_wake_defaulting_to_true(self):
        for service in self.SERVICES_WITH_WAKE:
            with self.subTest(service=service):
                handler = f'"async_{service}"'
                end = MEDIA_PLAYER.index(handler)
                start = MEDIA_PLAYER.rindex("async_register_entity_service", 0, end)
                self.assertIn(
                    "vol.Optional(ATTR_WAKE, default=True): cv.boolean",
                    MEDIA_PLAYER[start:end],
                )

    def test_services_yaml_documents_wake(self):
        for service in self.SERVICES_WITH_WAKE:
            with self.subTest(service=service):
                start = SERVICES.index(f"\n{service}:\n")
                rest = SERVICES[start + 1 :]
                nxt = rest.find("\n\n")
                while nxt != -1 and rest[nxt + 2 : nxt + 3] in (" ", ""):
                    nxt = rest.find("\n\n", nxt + 2)
                block = rest if nxt == -1 else rest[:nxt]
                self.assertIn("    wake:\n", block)

    def test_personal_photo_examples_use_the_id_the_tv_reports(self):
        # The TV lists uploads as MY_F0001; MY-F0001 gets no answer.
        self.assertNotIn("MY-F0", SERVICES)
        self.assertIn("MY_F0001", SERVICES)


if __name__ == "__main__":
    unittest.main()
