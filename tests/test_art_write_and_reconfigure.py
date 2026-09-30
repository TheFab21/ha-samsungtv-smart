"""Three papercuts found on 8.9.6 by the #273 reporter.

1. An explicit art-mode write was refused by the recovery cooldown. The
   cooldown exists to stop the pollers refilling a socket that is recovering,
   but it applied to writes too. After 8.9.6 made recurring wedges escalate it
   (30 -> 60 -> 120 -> 240 s), turning Art Mode on from an input failed for
   about 10 min: "Failed to turn Art Mode ON after 3 attempts", twice, five
   minutes apart. The blocked write is the one that would have ended the loop.

2. Reconfigure -> Connection re-tested the connection without the stored
   token, so it always paired from scratch. A Frame in Art Mode has no screen
   to show the pairing prompt on, so the step failed in ~30 s on a TV that was
   working; on an HDMI input it took 4 s.

3. The SamsungPing thread outlives the event loop at shutdown and its next
   tick raised "Event loop is closed" as an uncaught thread exception, writing
   a traceback on every stop.

art.py needs aiohttp to import, so it is loaded with a stub; config_flow.py and
media_player.py need Home Assistant, so those two are checked on the source.
"""

import asyncio
import importlib.util
from pathlib import Path
import sys
import types
import unittest

ROOT = Path(__file__).parents[1] / "custom_components" / "samsungtv_smart"


def _load_art():
    pkg_name = "_write_bypass_api"
    pkg = types.ModuleType(pkg_name)
    pkg.__path__ = [str(ROOT / "api")]
    sys.modules[pkg_name] = pkg
    stubbed = False
    try:
        import aiohttp  # noqa: F401
    except ImportError:
        sys.modules["aiohttp"] = types.ModuleType("aiohttp")
        stubbed = True
    try:
        for name, path in (
            (f"{pkg_name}._image_prep", ROOT / "api" / "_image_prep.py"),
            (f"{pkg_name}.art", ROOT / "api" / "art.py"),
        ):
            spec = importlib.util.spec_from_file_location(name, path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
        return sys.modules[f"{pkg_name}.art"]
    finally:
        if stubbed:
            del sys.modules["aiohttp"]


art = _load_art()


class _Channel(art.SamsungTVAsyncArt):
    """The real request gate; the transport answers everything."""

    def __init__(self):
        self._log = _Log()
        self._request_lock = asyncio.Lock()
        self._request_cooldown_until = 0.0
        self.sent = []

    async def _send_art_request_locked(self, request_data, wait_for_event, timeout):
        self.sent.append(request_data["request"])
        return {"event": request_data["request"]}


class _Log:
    def __init__(self):
        self.lines = []

    def debug(self, msg, *args):
        self.lines.append(msg % args if args else msg)

    def info(self, msg, *args):
        self.debug(msg, *args)

    def warning(self, msg, *args):
        self.debug(msg, *args)


class CooldownBlocksPollsNotWritesTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.ch = _Channel()
        self.ch._request_cooldown_until = art.time.monotonic() + 240

    async def test_a_poll_is_still_suppressed_during_recovery(self):
        self.assertIsNone(await self.ch._send_art_request({"request": "get_current"}))
        self.assertEqual(self.ch.sent, [])

    async def test_an_explicit_write_goes_through(self):
        result = await self.ch._send_art_request(
            {"request": "set_artmode_status", "value": "on"}, bypass_cooldown=True
        )
        self.assertIsNotNone(result)
        self.assertEqual(self.ch.sent, ["set_artmode_status"])

    async def test_the_cooldown_is_not_cleared_by_the_write(self):
        await self.ch._send_art_request(
            {"request": "set_artmode_status"}, bypass_cooldown=True
        )
        self.assertIsNone(await self.ch._send_art_request({"request": "get_current"}))

    def test_set_artmode_is_the_only_caller_that_bypasses(self):
        source = (ROOT / "api" / "art.py").read_text()
        self.assertEqual(source.count("bypass_cooldown=True"), 1)
        start = source.index("    async def set_artmode(")
        block = source[start : source.index("\n    async def ", start + 10)]
        self.assertIn("bypass_cooldown=True", block)


class ReconfigureConnectionUsesTheTokenTest(unittest.TestCase):
    def test_the_connection_retest_presents_the_stored_token(self):
        source = (ROOT / "config_flow.py").read_text()
        start = source.index("    async def async_step_reconfigure_connection(")
        block = source[start : source.index("\n    async def ", start + 10)]
        call = block[block.index("result = await self._try_connect(") :]
        self.assertIn("token=entry.data.get(CONF_TOKEN)", call)

    def test_the_auth_step_still_re_pairs_without_one(self):
        # Re-pairing is what that step is for; it must not present a token.
        source = (ROOT / "config_flow.py").read_text()
        start = source.index("    async def async_step_reconfigure_auth(")
        block = source[start : source.index("\n    async def ", start + 10)]
        call = block[block.index("await self._try_connect(") :]
        self.assertNotIn("token=", call[: call.index(")")])


class PingThreadSurvivesShutdownTest(unittest.TestCase):
    def test_the_status_callback_tolerates_a_closed_loop(self):
        source = (ROOT / "media_player.py").read_text()
        start = source.index("        def update_status_callback():")
        block = source[start : source.index("self._ws.register_status_callback", start)]
        self.assertIn("except RuntimeError:", block)
        guarded = block.index("try:")
        call = block.index("run_callback_threadsafe(")
        self.assertLess(guarded, call)

    def test_it_reproduces(self):
        loop = asyncio.new_event_loop()
        loop.close()
        calls = []

        def update_status_callback():
            try:
                loop.call_soon_threadsafe(lambda: calls.append(1))
            except RuntimeError:
                pass

        update_status_callback()  # must not raise
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
