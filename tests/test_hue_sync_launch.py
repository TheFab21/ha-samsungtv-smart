"""start_hue_sync must launch the app when no session is running (#266).

samsungvd.lightControl only steers an already-running Hue Sync session: while a
session exists the capability reports supportedModes / streamControl /
selectedAppId, and while none exists it is empty and setLightControlMode returns
COMPLETED without doing anything. Since ~2026-09 the session no longer persists
on its own, so start_hue_sync (which only set the mode) silently no-op'd.

Now: async_hue_sync_session_active reads that capability; start_hue_sync launches
com.lighting.HueSyncService when there is no session, and stop_hue_sync reports a
clear error instead of a silent success. The orchestration is checked
structurally here; the session detection and the error paths run on the real
code in tests/api/test_smartthings_hue_sync.py and
tests/test_hue_sync_refused_error.py.
"""

import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).parents[1] / "custom_components" / "samsungtv_smart"
MEDIA_PLAYER = (ROOT / "media_player.py").read_text()
SMARTTHINGS = (ROOT / "api" / "smartthings.py").read_text()


def _load_const():
    spec = importlib.util.spec_from_file_location(
        "samsungtv_const_hue", ROOT / "const.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


const = _load_const()


# Session detection (async_hue_sync_session_active) used to be reproduced here
# from source. It is now tested on the real method, including the placeholder
# shape of an idle Frame 2024 (#298): tests/api/test_smartthings_hue_sync.py.


class ConstTest(unittest.TestCase):
    def test_app_id(self):
        self.assertEqual(const.HUE_SYNC_APP_ID, "com.lighting.HueSyncService")


class OrchestrationTest(unittest.TestCase):
    """media_player wires session-check -> launch / clear error."""

    def setUp(self):
        start = MEDIA_PLAYER.index("async def _async_set_hue_sync")
        self.block = MEDIA_PLAYER[start : MEDIA_PLAYER.index("\n    async def ", start + 1)]

    def test_it_checks_the_session_first(self):
        self.assertIn("async_hue_sync_session_active()", self.block)

    def test_start_launches_the_app_when_no_session(self):
        self.assertIn("if active is False:", self.block)
        self.assertIn("_async_launch_hue_sync_app()", self.block)

    def test_stop_with_no_session_raises_instead_of_silent_success(self):
        # The stop branch must reach a raised error, not fall through to a no-op.
        self.assertIn("nothing to stop", self.block)

    def test_the_launcher_polls_for_the_session_and_uses_the_app_id(self):
        launcher_start = MEDIA_PLAYER.index("async def _async_launch_hue_sync_app")
        launcher = MEDIA_PLAYER[
            launcher_start : MEDIA_PLAYER.index("\n    async def ", launcher_start + 1)
        ]
        self.assertIn("async_rest_app_run(HUE_SYNC_APP_ID)", launcher)
        self.assertIn("async_hue_sync_session_active()", launcher)


if __name__ == "__main__":
    unittest.main()
