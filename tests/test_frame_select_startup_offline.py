"""Frame select discovery must not probe a sleeping TV during startup.

A powered-off Frame is a normal Home Assistant startup condition. Matte
selects used to do two synchronous get_current() reads from
async_added_to_hass (up to 5 s each), crossing HA's 10 s platform-setup
warning. The background matte loader then spent ten network attempts on the
same sleeping TV and ended with "Could not populate matte options".

Keep setup passive and defer both matte and motion-option discovery until the
media player's local state says the Frame is awake. Art Mode is the exception:
the media player is OFF while the panel is still active, so art_mode_status=on
must permit the Art API.
"""

from pathlib import Path
import unittest

ROOT = Path(__file__).parents[1] / "custom_components" / "samsungtv_smart"
SELECT = (ROOT / "select.py").read_text()


def _block(source: str, start: str, end: str) -> str:
    begin = source.index(start)
    return source[begin : source.index(end, begin)]


class MatteStartupOfflineTest(unittest.TestCase):
    """Sleeping Frames do not block select setup or consume retry attempts."""

    def test_matte_entities_do_not_read_the_tv_from_async_added_to_hass(self):
        type_select = _block(
            SELECT,
            "class SamsungTVMatteTypeSelect",
            "class SamsungTVMatteColorSelect",
        )
        color_select = _block(
            SELECT,
            "class SamsungTVMatteColorSelect",
            "# ══════════════════════════════════════════════════════════════════════════\n"
            "# Art Mode motion sensor selects",
        )

        self.assertNotIn("async def async_added_to_hass", type_select)
        self.assertNotIn("async def async_added_to_hass", color_select)

    def test_matte_discovery_defers_before_spending_a_retry_or_calling_tv(self):
        loader = _block(
            SELECT,
            "async def _load_matte_options(",
            "async def _load_picture_mode_options(",
        )
        gate = loader.index("if _frame_art_api_asleep")
        sleep = loader.index("await asyncio.sleep(_RETRY_INTERVAL)", gate)
        cont = loader.index("continue", sleep)
        attempt = loader.index("attempt += 1")
        request = loader.index("get_matte_list")

        self.assertLess(gate, sleep)
        self.assertLess(sleep, cont)
        self.assertLess(cont, attempt)
        self.assertLess(attempt, request)

    def test_motion_discovery_uses_the_same_sleeping_tv_gate(self):
        loader = _block(
            SELECT,
            "async def _load_motion_options(",
            "# ══════════════════════════════════════════════════════════════════════════\n"
            "# IP Control Color Tone",
        )
        gate = loader.index("if _frame_art_api_asleep")
        attempt = loader.index("attempt += 1")
        request = loader.index("get_artmode_settings")

        self.assertLess(gate, attempt)
        self.assertLess(attempt, request)

    def test_art_mode_outranks_media_player_off(self):
        helper = _block(
            SELECT,
            "def _frame_art_api_asleep(",
            "async def _load_matte_options(",
        )
        art = helper.index('state.attributes.get("art_mode_status") == "on"')
        power = helper.index("return state.state in")

        self.assertLess(art, power)
        self.assertIn("STATE_OFF", helper)
        self.assertIn('"unavailable"', helper)
        self.assertIn('"unknown"', helper)

    def test_deferred_discovery_tasks_are_owned_by_the_config_entry(self):
        setup = _block(
            SELECT,
            "    # ── Background tasks",
            "# ══════════════════════════════════════════════════════════════════════════\n"
            "# Background loaders",
        )
        self.assertGreaterEqual(setup.count("entry.async_create_background_task("), 2)


if __name__ == "__main__":
    unittest.main()
