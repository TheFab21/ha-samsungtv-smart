"""Diagnostics carry the raw SmartThings powerConsumptionReport.

Diagnosing a 0 W reading on a 2025 Frame needed the raw report (start, end,
deltaEnergy, power, energy); the diagnostics had only the sensors' derived
states, so the report had to be reconstructed from history instead.

diagnostics.py needs Home Assistant to import, so the helper is exec'd from
source.
"""

from pathlib import Path
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).parents[1] / "custom_components" / "samsungtv_smart"
SOURCE = (ROOT / "diagnostics.py").read_text()


def _load():
    start = SOURCE.index("@callback\ndef _async_power_report(")
    namespace = {
        "callback": lambda func: func,
        "DOMAIN": "samsungtv_smart",
        "DATA_ST_POWER": "st_power",
        "HomeAssistant": object,
        "ConfigEntry": object,
    }
    exec(compile(SOURCE[start:], "diagnostics.py", "exec"), namespace)
    return namespace["_async_power_report"]


report = _load()
ENTRY = SimpleNamespace(entry_id="abc")


class PowerReportTest(unittest.TestCase):
    def test_no_coordinator_no_section(self):
        self.assertIsNone(report(SimpleNamespace(data={}), ENTRY))
        hass = SimpleNamespace(data={"samsungtv_smart": {"abc": {}}})
        self.assertIsNone(report(hass, ENTRY))

    def test_raw_report_is_published_json_safe(self):
        coordinator = SimpleNamespace(
            last_update_success=True,
            data={
                "start": "2026-11-03T12:47:33Z",
                "deltaEnergy": 13.43,
                "power": 0,
                "_tv_off": False,
                "odd": object(),
            },
        )
        hass = SimpleNamespace(
            data={"samsungtv_smart": {"abc": {"st_power": coordinator}}}
        )
        out = report(hass, ENTRY)
        self.assertTrue(out["last_update_success"])
        self.assertEqual(out["report"]["deltaEnergy"], 13.43)
        self.assertEqual(out["report"]["start"], "2026-11-03T12:47:33Z")
        self.assertIsInstance(out["report"]["odd"], str)


if __name__ == "__main__":
    unittest.main()
