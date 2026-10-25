"""Tests for picture mode over IP Control (pictureModeControl).

Measured on a Frame 2024 (QE55LS03D) and a 2026 S95H: the getter answers the
OCF name, the setter echoes the name it applied, and a name the TV does not
offer is refused with -32002.
"""

import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest


def _load_ipcontrol_module():
    """Load ipcontrol.py with a minimal Home Assistant type stub."""
    homeassistant = types.ModuleType("homeassistant")
    homeassistant_core = types.ModuleType("homeassistant.core")

    class HomeAssistant:
        """Type stub used only while importing the module."""

    homeassistant_core.HomeAssistant = HomeAssistant
    sys.modules.setdefault("homeassistant", homeassistant)
    sys.modules.setdefault("homeassistant.core", homeassistant_core)

    module_path = (
        Path(__file__).parents[2]
        / "custom_components"
        / "samsungtv_smart"
        / "api"
        / "ipcontrol.py"
    )
    spec = importlib.util.spec_from_file_location("ipcontrol_under_test", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ipcontrol = _load_ipcontrol_module()


class _FakeHass:
    """Minimal executor bridge used by the async client."""

    async def async_add_executor_job(self, func, *args):
        """Run executor work inline for the unit test."""
        return func(*args)


ACCEPTED = {"Dynamic", "Standard", "Movie", "FilmmakerMode", "Eco"}


class PictureModeControlTest(unittest.IsolatedAsyncioTestCase):
    def _client(self, accepted=ACCEPTED, current="Eco"):
        ipcontrol._HOST_LOCKS.clear()
        client = ipcontrol.SamsungIPControl(_FakeHass(), "192.0.2.1", token="t")
        self.sent = []

        def fake_post(payload, timeout):
            request = json.loads(payload)
            self.sent.append(request["params"].get("pictureMode"))
            value = request["params"].get("pictureMode")
            if value is None:
                return json.dumps({"id": 1, "result": {"pictureMode": current}})
            if value not in accepted:
                return json.dumps(
                    {"id": 1, "error": {"code": -32002, "message": "Server error"}}
                )
            return json.dumps({"id": 1, "result": {"pictureMode": value}})

        client._sync_post = fake_post
        return client

    async def test_getter_returns_the_ocf_name(self):
        self.assertEqual(await self._client().async_get_picture_mode(), "Eco")

    async def test_setter_returns_the_echo(self):
        client = self._client()
        self.assertEqual(
            await client.async_set_picture_mode("FilmmakerMode"), "FilmmakerMode"
        )
        self.assertEqual(self.sent, ["FilmmakerMode"])

    async def test_a_refused_name_raises_without_fallback(self):
        client = self._client()
        with self.assertRaises(ipcontrol.SamsungIPControlModeLockedError):
            await client.async_set_picture_mode("Natural")
        self.assertEqual(self.sent, ["Natural"])

    async def test_the_hdr_variant_is_tried_once_when_the_name_is_refused(self):
        client = self._client(accepted={"MovieHDR"})
        self.assertEqual(
            await client.async_set_picture_mode("Movie", "MovieHDR"), "MovieHDR"
        )
        self.assertEqual(self.sent, ["Movie", "MovieHDR"])

    async def test_no_fallback_when_the_name_is_accepted(self):
        client = self._client()
        await client.async_set_picture_mode("Movie", "MovieHDR")
        self.assertEqual(self.sent, ["Movie"])


if __name__ == "__main__":
    unittest.main()
