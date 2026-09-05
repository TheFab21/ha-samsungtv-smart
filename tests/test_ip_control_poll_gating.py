"""Independently-polling IP Control entities must not read a sleeping TV.

The picture sliders that go through IPControlVideoCoordinator never read a TV
that is off or in Art Mode: the coordinator returns early. The colour-tone
select and the backlight number poll on their own and had no such gate, so
they kept opening connections to sleeping TVs every 30 s — ~7.8 s each,
against 0.15 s awake — and, queued behind each other on the per-host lock,
crossed Home Assistant's 10 s per-entity threshold: 6 019 core warnings in
21.4 hours on a 13-TV install (#248).
"""

from pathlib import Path
import unittest

ROOT = Path(__file__).parents[1] / "custom_components" / "samsungtv_smart"
SELECT = (ROOT / "select.py").read_text()
NUMBER = (ROOT / "number.py").read_text()


def _block(source: str, start: str, end: str) -> str:
    begin = source.index(start)
    return source[begin : source.index(end, begin)]


def _update_body(source: str, class_decl: str) -> str:
    """The async_update body of the class beginning at class_decl."""
    cls = source[source.index(class_decl) :]
    body = cls[cls.index("    async def async_update") :]
    end = body.index("\n    def ", 1) if "\n    def " in body[1:] else len(body)
    return body[:end]


class ColorToneSelectTest(unittest.TestCase):
    """select.<tv>_color_tone — 4 392 of the warnings."""

    def setUp(self):
        self.body = _update_body(
            SELECT, "class SamsungTVIPControlColorToneSelect(SelectEntity):"
        )

    def test_the_gate_runs_before_any_client_is_obtained(self):
        gate = self.body.index("_tv_normal_viewing(self.hass, self._entry_id)")
        client = self.body.index("self._get_ip_control()")
        self.assertLess(gate, client)

    def test_a_gated_poll_marks_the_entity_unavailable(self):
        gate = self.body.index("_tv_normal_viewing")
        client = self.body.index("self._get_ip_control()")
        self.assertIn("self._mark_unavailable()", self.body[gate:client])

    def test_the_entity_still_polls(self):
        # The gate must not be achieved by simply disabling polling.
        cls = SELECT[SELECT.index("class SamsungTVIPControlColorToneSelect") :]
        self.assertIn("_attr_should_poll = True", cls[: cls.index("def __init__")])


class BacklightNumberTest(unittest.TestCase):
    """number.<tv>_backlight — the other 1 626."""

    def setUp(self):
        self.body = _update_body(
            NUMBER, "class SamsungTVIPControlBacklightNumber(NumberEntity):"
        )

    def test_the_gate_runs_before_any_client_is_obtained(self):
        gate = self.body.index("_tv_normal_viewing(self.hass, entry)")
        client = self.body.index("self._get_ip_control()")
        self.assertLess(gate, client)

    def test_a_missing_config_entry_is_treated_as_not_viewing(self):
        self.assertIn("entry is None or not _tv_normal_viewing", self.body)

    def test_a_gated_poll_marks_the_entity_unavailable(self):
        gate = self.body.index("_tv_normal_viewing")
        client = self.body.index("self._get_ip_control()")
        self.assertIn("self._mark_unavailable()", self.body[gate:client])


class SharedHelperTest(unittest.TestCase):
    """One definition of "normal viewing" per module, not three."""

    def test_select_defines_the_gate_once_at_module_level(self):
        self.assertIn(
            "def _tv_normal_viewing(hass: HomeAssistant, entry_id: str)", SELECT
        )
        # The speaker select had its own copy; it must delegate now.
        speaker_gate = _block(
            SELECT,
            "    def _tv_normal_viewing(self) -> bool:",
            "    def _rebuild_options",
        )
        self.assertIn(
            "return _tv_normal_viewing(self.hass, self._entry_id)", speaker_gate
        )
        self.assertNotIn("registry = er.async_get(self.hass)", speaker_gate)

    def test_the_gate_excludes_off_unavailable_unknown_and_art(self):
        helper = _block(
            SELECT,
            "def _tv_normal_viewing(hass: HomeAssistant, entry_id: str)",
            "class SamsungTVIPControlColorToneSelect",
        )
        for token in ("STATE_OFF", '"unavailable"', '"unknown"', "art_mode_status"):
            self.assertIn(token, helper)


class ArtModeAbortMessageTest(unittest.TestCase):
    """The deferral message must describe what actually happened."""

    def setUp(self):
        self.block = _block(
            (ROOT / "switch.py").read_text(),
            "            tv_was_off = True",
            "            # Additional delay for TV Art subsystem",
        )

    def test_it_no_longer_claims_nothing_was_sent(self):
        # The power-on WAS sent before this point; only art mode is deferred.
        self.assertNotIn("Art Mode ON aborted", self.block)
        self.assertIn("did not answer within 20s of", self.block)
        self.assertIn("art mode deferred", self.block)

    def test_repeats_drop_to_info_while_a_deferral_is_outstanding(self):
        self.assertIn(
            "log = self._log.info if was_pending else self._log.warning", self.block
        )

    def test_the_outstanding_deferral_is_captured_before_being_cleared(self):
        turn_on = _block(
            (ROOT / "switch.py").read_text(),
            "    async def async_turn_on(self, **kwargs",
            "        # Check if TV is on",
        )
        captured = turn_on.index("was_pending = self._pending_art_on")
        cleared = turn_on.index("self._pending_art_on = False")
        self.assertLess(captured, cleared)


if __name__ == "__main__":
    unittest.main()
