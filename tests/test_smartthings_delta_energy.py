"""Samsung TVs report energy per window, not a power draw or a running meter.

Measured on a QE43LS03FAUXXH (2025 Frame) in Art Mode over six hours: the
SmartThings powerConsumptionReport carried ``power`` = 0 and ``energy`` = 0 on
every report, while ``deltaEnergy`` held ~13.4 Wh per ~15-minute window
(about 53 W). The Power sensor therefore read 0 W, and the Energy difference
sensor — declared TOTAL_INCREASING — saw a roughly constant value, so the
recorder added almost nothing and the Energy dashboard stayed near zero.

Power now falls back to the window's average draw, and Energy difference is a
TOTAL with last_reset = the window start, so each report is counted once.

sensor.py needs Home Assistant to import, so the helpers are exec'd from
source against a stand-in for homeassistant.util.dt.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest

ROOT = Path(__file__).parents[1] / "custom_components" / "samsungtv_smart"
SENSOR = (ROOT / "sensor.py").read_text()

NOW = datetime(2026, 11, 3, 13, 3, tzinfo=timezone.utc)


class _DtUtil:
    @staticmethod
    def parse_datetime(text):
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None

    @staticmethod
    def utcnow():
        return NOW


def _load():
    start = SENSOR.index('POWER_DATA_TV_OFF = "_tv_off"')
    end = SENSOR.index("class SmartThingsPowerCoordinator(")
    namespace = {"timedelta": timedelta, "dt_util": _DtUtil}
    exec(compile(SENSOR[start:end], "sensor.py", "exec"), namespace)
    return namespace


NS = _load()
derive = NS["derive_average_power"]
window = NS["power_report_window"]

# A real report shape: 15 min 11 s window, 13.43 Wh, power and energy at 0.
REPORT = {
    "start": "2026-11-03T12:47:33Z",
    "end": "2026-11-03T13:02:44Z",
    "deltaEnergy": 13.431665,
    "power": 0,
    "energy": 0,
    "_tv_off": False,
}


class DerivedPowerTest(unittest.TestCase):
    def test_power_comes_from_the_window(self):
        self.assertAlmostEqual(derive(REPORT, NOW), 53.1, delta=0.1)

    def test_nothing_derived_while_the_tv_is_off(self):
        self.assertIsNone(derive({**REPORT, "_tv_off": True}, NOW))

    def test_a_stale_report_is_not_the_present(self):
        self.assertIsNone(derive(REPORT, NOW + timedelta(hours=1)))

    def test_no_window_or_no_energy_derives_nothing(self):
        self.assertIsNone(derive({"deltaEnergy": 13.4, "power": 0}, NOW))
        self.assertIsNone(derive({**REPORT, "deltaEnergy": 0}, NOW))
        self.assertIsNone(derive({**REPORT, "deltaEnergy": None}, NOW))

    def test_backwards_window_is_rejected(self):
        self.assertIsNone(window({"start": REPORT["end"], "end": REPORT["start"]}))


class SensorDeclarationTest(unittest.TestCase):
    def test_delta_energy_is_a_total_with_last_reset(self):
        start = SENSOR.index('"deltaEnergy": (')
        block = SENSOR[start : SENSOR.index("),", start)]
        self.assertIn("SensorStateClass.TOTAL,", block)
        self.assertNotIn("TOTAL_INCREASING", block)
        self.assertIn("def last_reset(self)", SENSOR)

    def test_the_coordinator_marks_on_and_off(self):
        self.assertIn("data[POWER_DATA_TV_OFF] = True", SENSOR)
        self.assertIn("{**value, POWER_DATA_TV_OFF: False}", SENSOR)


if __name__ == "__main__":
    unittest.main()
