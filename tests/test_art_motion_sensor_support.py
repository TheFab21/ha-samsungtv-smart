"""Art Mode motion/brightness controls follow the TV's support flags (#402).

A setup while the art channel is silent used to read three ``None`` answers as
"this model has no sensor" and create no Motion Sensitivity / Motion Timer /
Brightness Sensor controls, leaving them missing until a reboot + reload. The
loader now consults ``get_device_info``'s ``support_motion_sensor`` /
``support_brightness_sensor`` and retries a silent-or-supported channel instead
of writing the sensor off.
"""

import sys
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class _Names:
    def __getattr__(self, name):
        return name


pysmartthings = sys.modules.setdefault("pysmartthings", ModuleType("pysmartthings"))
for _name in ("Attribute", "Capability", "Command"):
    if not hasattr(pysmartthings, _name):
        setattr(pysmartthings, _name, _Names())
if not hasattr(pysmartthings, "SmartThings"):
    pysmartthings.SmartThings = object

from custom_components.samsungtv_smart import select as select_mod  # noqa: E402


def _item(name, value, extra=None):
    item = {"item": name, "value": value}
    if extra:
        item.update(extra)
    return item


async def _run_load(art_api, max_retries=2):
    added = []

    def add_entities(entities):
        added.extend(entities)

    entry = MagicMock()
    entry.entry_id = "entry"
    entry.title = "Frame"

    with (
        patch.object(select_mod, "_MAX_RETRIES", max_retries),
        patch.object(select_mod.asyncio, "sleep", AsyncMock()),
    ):
        await select_mod._load_motion_options(
            MagicMock(), art_api, "Frame", "uid", add_entities, entry
        )
    return added


def test_support_flag_reader_tells_true_false_and_unknown_apart():
    assert select_mod._art_supports({"support_motion_sensor": "TRUE"},
                                    "support_motion_sensor") is True
    assert select_mod._art_supports({"support_motion_sensor": "FALSE"},
                                    "support_motion_sensor") is False
    assert select_mod._art_supports({}, "support_motion_sensor") is None
    assert select_mod._art_supports(None, "support_motion_sensor") is None


async def test_a_silent_channel_at_setup_is_retried_not_written_off():
    # device_info and every setting come back None: a silent channel, not a
    # model without a sensor — so it retries the full budget.
    art_api = MagicMock()
    art_api.get_device_info = AsyncMock(return_value=None)
    art_api.get_artmode_settings = AsyncMock(return_value=None)

    added = await _run_load(art_api, max_retries=2)

    assert added == []
    assert art_api.get_device_info.await_count == 2


async def test_no_sensor_support_gives_up_at_once():
    # The TV says it has neither sensor: no controls, and no pointless retries.
    art_api = MagicMock()
    art_api.get_device_info = AsyncMock(
        return_value={
            "support_motion_sensor": "FALSE",
            "support_brightness_sensor": "FALSE",
        }
    )
    art_api.get_artmode_settings = AsyncMock(return_value=None)

    added = await _run_load(art_api, max_retries=3)

    assert added == []
    assert art_api.get_device_info.await_count == 1


async def test_a_supported_sensor_is_retried_until_the_channel_answers():
    # device_info says the sensor exists, but the settings are not readable on
    # the first pass and arrive on the second — the controls are then created.
    art_api = MagicMock()
    art_api.get_device_info = AsyncMock(
        return_value={
            "support_motion_sensor": "TRUE",
            "support_brightness_sensor": "TRUE",
        }
    )
    answers = [
        None,
        None,
        None,  # first attempt: nothing readable yet
        _item("motion_sensitivity", "5", {"min": 1, "max": 10}),
        _item("motion_timer", "off", {"valid_values": '["off","60","120"]'}),
        _item("brightness_sensor_setting", "on"),
    ]
    art_api.get_artmode_settings = AsyncMock(side_effect=answers)

    added = await _run_load(art_api, max_retries=3)

    assert len(added) == 3
    assert {e._setting_name for e in added} == {
        "motion_sensitivity",
        "motion_timer",
        "brightness_sensor_setting",
    }


async def test_controls_are_created_on_a_healthy_channel_without_retry():
    art_api = MagicMock()
    art_api.get_device_info = AsyncMock(
        return_value={"support_motion_sensor": "TRUE"}
    )
    art_api.get_artmode_settings = AsyncMock(
        side_effect=[
            _item("motion_sensitivity", "5", {"min": 1, "max": 10}),
            _item("motion_timer", "off", {"valid_values": '["off","60"]'}),
            _item("brightness_sensor_setting", "on"),
        ]
    )

    added = await _run_load(art_api, max_retries=3)

    assert len(added) == 3
    assert art_api.get_device_info.await_count == 1
