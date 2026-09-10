"""A -32002 on a state read is transient until it repeats.

The TV answers -32002 ("refused in its current state") to an occasional
getTVStates. Our own message calls it "usually transient", and two maintainer
logs bear that out: 6 isolated occurrences in 67.8 h, then 5 in 25.6 h across
two TVs, each recovering on the next cycle. Raising UpdateFailed on the first
one logs an ERROR for a normal condition — the mistake #248 fixed for the
sleeping-TV overrun. A run of them is a different thing and still fails.

Structural, like the other coordinator tests: sensor.py cannot be imported
without Home Assistant.
"""

from pathlib import Path
import re
import unittest

ROOT = Path(__file__).parents[1] / "custom_components" / "samsungtv_smart"
SENSOR = (ROOT / "sensor.py").read_text()
IPCONTROL = (ROOT / "api" / "ipcontrol.py").read_text()


def _block(source: str, start: str, end: str) -> str:
    begin = source.index(start)
    return source[begin : source.index(end, begin)]


class ExceptionContractTest(unittest.TestCase):
    """The handler only works if -32002 really arrives as ModeLockedError."""

    def test_minus_32002_raises_mode_locked_for_reads_too(self):
        block = _block(
            IPCONTROL,
            "            if code == ERROR_SERVER:",
            "            if code == ERROR_METHOD_NOT_FOUND:",
        )
        # Both the picture-write branch and the fallback raise the same class;
        # only the message differs. If that ever changes, the coordinator's
        # tolerance silently stops applying to reads.
        self.assertEqual(block.count("raise SamsungIPControlModeLockedError("), 2)
        self.assertIn("usually transient", block)

    def test_the_coordinator_imports_that_exception(self):
        self.assertIn("SamsungIPControlModeLockedError", SENSOR.split("class ")[0])


class ToleranceTest(unittest.TestCase):
    """The state coordinator holds its snapshot for a few refusals."""

    def setUp(self):
        self.block = _block(
            SENSOR,
            "        except SamsungIPControlModeLockedError as ex:",
            "\n        clear_token_problem(",
        )

    def test_the_specific_handler_precedes_the_generic_one(self):
        specific = SENSOR.index("except SamsungIPControlModeLockedError as ex:")
        generic = SENSOR.index(
            'except SamsungIPControlError as ex:\n            raise UpdateFailed(f"IP Control state read failed'
        )
        self.assertLess(specific, generic)

    def test_a_tolerated_refusal_does_not_raise(self):
        head = self.block[: self.block.index("raise UpdateFailed")]
        self.assertIn("self._transient_read_failures += 1", head)
        self.assertIn("<= IP_CONTROL_READ_TRANSIENT_TOLERANCE", head)
        self.assertIn("self._log.debug(", head)
        self.assertNotIn("self._log.error", head)

    def test_it_keeps_the_previous_snapshot_when_there_is_one(self):
        self.assertIn("if self.data is not None:", self.block)
        self.assertIn("return self.data", self.block)

    def test_it_has_a_first_refresh_fallback(self):
        # self.data is None before the first successful update.
        self.assertIn(
            'return {"tv": {}, "channel": {}, "powered_off": False}', self.block
        )

    def test_a_run_of_refusals_still_fails_the_coordinator(self):
        tail = self.block[self.block.index("raise UpdateFailed") :]
        self.assertIn("times in a row", tail)

    def test_the_counter_resets_after_a_successful_read(self):
        after = SENSOR[
            SENSOR.index(
                '        except SamsungIPControlError as ex:\n            raise UpdateFailed(f"IP Control state read failed'
            ) :
        ]
        reset = after.index("self._transient_read_failures = 0")
        clear = after.index("clear_token_problem(")
        self.assertLess(reset, clear)

    def test_the_tolerance_is_small_and_declared_once(self):
        value = re.search(
            r"^IP_CONTROL_READ_TRANSIENT_TOLERANCE = (\d+)$", SENSOR, re.M
        )
        self.assertIsNotNone(value)
        self.assertLessEqual(int(value.group(1)), 5)
        self.assertEqual(SENSOR.count("IP_CONTROL_READ_TRANSIENT_TOLERANCE = "), 1)

    def test_the_counter_is_initialised(self):
        init = _block(
            SENSOR,
            "        self._channel_control_supported: bool | None = None",
            "\n    def ",
        )
        self.assertIn("self._transient_read_failures = 0", init)


if __name__ == "__main__":
    unittest.main()
