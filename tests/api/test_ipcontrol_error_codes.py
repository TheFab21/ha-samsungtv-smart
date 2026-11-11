"""IP Control server-error classification.

Decoded from the live mde IP Control library (T-PTMDDEUC / Tizen 9), the TV's
server-error family all carries message "Server error" with the real reason in
``data``:

    -32000 "Unknown"            -32001 "Not supported model"
    -32002 "Failed"             -32003 "Invalid operation"

-32001 is a permanent model-capability verdict, so it latches as Unsupported.
-32000/-32002/-32003 are state-dependent ("refused in the current state") and
map to ModeLocked so the caller retries once the state changes — in particular
directVolumeControl answers -32003 while audio is on an external speaker output
(the #409 case), which must not be mistaken for a transport error and hammered
every poll.
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


class ErrorCodeClassificationTest(unittest.IsolatedAsyncioTestCase):
    def _client(self, code, data):
        ipcontrol._HOST_LOCKS.clear()
        client = ipcontrol.SamsungIPControl(_FakeHass(), "192.0.2.1", token="t")

        def fake_post(payload, timeout):
            return json.dumps(
                {
                    "id": 1,
                    "error": {"code": code, "message": "Server error", "data": data},
                }
            )

        client._sync_post = fake_post
        return client

    async def test_invalid_operation_32003_is_mode_locked(self):
        # directVolumeControl -> -32003 on an external speaker output (#409).
        with self.assertRaises(ipcontrol.SamsungIPControlModeLockedError):
            await self._client(-32003, "Invalid operation").async_get_volume()

    async def test_unknown_32000_is_mode_locked(self):
        with self.assertRaises(ipcontrol.SamsungIPControlModeLockedError):
            await self._client(-32000, "Unknown").async_get_volume()

    async def test_failed_32002_stays_mode_locked(self):
        with self.assertRaises(ipcontrol.SamsungIPControlModeLockedError):
            await self._client(-32002, "Failed").async_get_volume()

    async def test_not_supported_model_32001_is_unsupported(self):
        with self.assertRaises(ipcontrol.SamsungIPControlUnsupportedError):
            await self._client(-32001, "Not supported model").async_get_volume()

    async def test_method_not_found_32601_is_unsupported(self):
        with self.assertRaises(ipcontrol.SamsungIPControlUnsupportedError):
            await self._client(-32601, "").async_get_volume()

    async def test_unauthorized_32010_is_auth(self):
        with self.assertRaises(ipcontrol.SamsungIPControlAuthError):
            await self._client(-32010, "").async_get_volume()


if __name__ == "__main__":
    unittest.main()
