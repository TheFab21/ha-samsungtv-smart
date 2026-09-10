"""sw_version must reach the device registry as a string.

CONF_DEVICE_OS is stored verbatim from the TV's REST payload (`device.OS`),
so its type is whatever the firmware sent. Home Assistant's device registry
accepts only a string; anything else is deprecated and stops working in
2026.12.0:

    Detected code that passes a non-string value of type list as sw_version
    to the device registry. This will stop working in Home Assistant 2026.12.0

The entity is not importable without Home Assistant, so the coercion is
exercised by compiling that block on its own.
"""

from pathlib import Path
import re
import unittest

ROOT = Path(__file__).parents[1] / "custom_components" / "samsungtv_smart"
ENTITY = (ROOT / "entity.py").read_text()


def _coerce(value):
    """Run the module's own coercion on one value.

    Extracted from the source rather than reimplemented, so the test fails if
    the real code changes shape.
    """
    block = ENTITY[ENTITY.index("        if dev_os := config.get(CONF_DEVICE_OS):") :]
    block = block[: block.index("        if self._mac:")]
    body = "\n".join(line[8:] for line in block.split("\n"))
    body = body.replace(
        "if dev_os := config.get(CONF_DEVICE_OS):", "if dev_os := config.get('x'):"
    ).replace(
        "self._attr_device_info[ATTR_SW_VERSION] = dev_os", "result['sw'] = dev_os"
    )
    namespace = {"config": {"x": value}, "result": {}}
    exec(compile(body, "entity_sw_version", "exec"), namespace)
    return namespace["result"].get("sw")


class CoercionTest(unittest.TestCase):
    """Whatever the firmware sent, the registry gets a string or nothing."""

    def test_a_plain_string_is_unchanged(self):
        self.assertEqual(_coerce("Tizen"), "Tizen")

    def test_a_list_is_joined_not_dropped(self):
        # The reported case. Keep the information rather than discarding it.
        self.assertEqual(_coerce(["Tizen"]), "Tizen")
        self.assertEqual(_coerce(["Tizen", "9.0"]), "Tizen, 9.0")

    def test_a_tuple_is_joined_too(self):
        self.assertEqual(_coerce(("Tizen", "9.0")), "Tizen, 9.0")

    def test_a_number_becomes_a_string(self):
        self.assertEqual(_coerce(9), "9")

    def test_falsy_values_set_nothing(self):
        for value in (None, "", [], 0):
            self.assertIsNone(_coerce(value), value)

    def test_the_result_is_always_a_string_or_absent(self):
        for value in ("Tizen", ["a", "b"], ("a",), 9, 9.5, {"a"}):
            out = _coerce(value)
            self.assertIsInstance(out, str, value)


class SourceTest(unittest.TestCase):
    """The guard must stay in front of the only assignment."""

    def test_sw_version_is_assigned_exactly_once(self):
        self.assertEqual(
            len(re.findall(r"ATTR_SW_VERSION\]\s*=", ENTITY)),
            1,
            "a second assignment would bypass the coercion",
        )

    def test_the_assignment_is_guarded_by_the_type_checks(self):
        block = ENTITY[ENTITY.index("if dev_os := config.get(CONF_DEVICE_OS):") :]
        block = block[: block.index("if self._mac:")]
        coercion = block.index("isinstance(dev_os, (list, tuple, set))")
        assignment = block.index("ATTR_SW_VERSION")
        self.assertLess(coercion, assignment)


if __name__ == "__main__":
    unittest.main()
