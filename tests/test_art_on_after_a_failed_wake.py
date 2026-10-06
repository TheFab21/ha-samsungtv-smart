"""_ensure_art_mode_ready after a power-on that did not wake the TV.

8.10.0 log, 192.168.1.161 leaving the network as it went into standby, then
an art service call:

    06:20:06.395 Error in send_command() -> OSError      (KEY_POWER)
    06:20:16.396 Frame Art: TV should now be on
    06:20:16.398 ... TV unreachable
    06:20:16.399 Frame Art: Art Mode is OFF, activating it...
    06:20:16.400 ERROR Frame Art: Failed to activate Art Mode

Nothing had answered: the art state was unknown, not off, and no write left
Home Assistant — yet the write guard recorded one, refusing the next art-on
for 60 s. Now the TV must answer before anything else is tried, an unknown
state is said to be unknown, and only a write that may have reached the TV is
held against a retry.
"""

import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.media_player import MediaPlayerState

if "pysmartthings" not in sys.modules:
    pysmartthings = ModuleType("pysmartthings")

    class _Names:
        def __getattr__(self, name):
            return name

    pysmartthings.Attribute = _Names()
    pysmartthings.Capability = _Names()
    pysmartthings.SmartThings = object
    sys.modules["pysmartthings"] = pysmartthings

from custom_components.samsungtv_smart.api.samsungws import (  # noqa: E402
    ArtModeStatus,
)
from custom_components.samsungtv_smart.art_mode_guard import (  # noqa: E402
    ArtModeWriteSuppressed,
    guard_for,
)
from custom_components.samsungtv_smart.const import DOMAIN  # noqa: E402
from custom_components.samsungtv_smart.media_player import (  # noqa: E402
    SamsungTVDevice,
)

MP = "custom_components.samsungtv_smart.media_player"
ENTRY = "entry-161"


def _device(*, reachable=(True,), art_state=None, written=False, connected=False):
    device = object.__new__(SamsungTVDevice)
    device._entry_id = ENTRY
    device._host = "192.168.1.161"
    device._log = MagicMock()
    device.hass = SimpleNamespace(data={})
    device._ws = SimpleNamespace(artmode_status=ArtModeStatus.Off)
    device._st = None
    device._get_option = MagicMock(return_value=False)  # no IP Control writes
    device._panel_shows_art = AsyncMock(return_value=None)
    device.async_turn_on = AsyncMock()
    answers = [{"device": {"PowerState": "on"}} if r else None for r in reachable]
    device._async_load_device_info = AsyncMock(side_effect=answers)
    device._art_api = SimpleNamespace(
        get_artmode=AsyncMock(return_value=art_state),
        set_artmode=AsyncMock(return_value=written),
        connected=connected,
    )
    return device


async def _ensure(device):
    with (
        patch.object(SamsungTVDevice, "state", new=MediaPlayerState.OFF),
        patch.object(SamsungTVDevice, "extra_state_attributes", new={}),
        patch(f"{MP}.asyncio.sleep", new=AsyncMock()),
    ):
        return await device._ensure_art_mode_ready()


def _guard(device):
    return guard_for(device.hass.data.setdefault(DOMAIN, {}).setdefault(ENTRY, {}))


def _infos(device):
    return [c.args[0] for c in device._log.info.call_args_list]


async def test_a_tv_that_never_answers_is_not_sent_anything():
    device = _device(reachable=(False, False))

    assert await _ensure(device) is False

    assert device._async_load_device_info.await_count == 2  # one retry
    device._art_api.get_artmode.assert_not_awaited()
    device._art_api.set_artmode.assert_not_awaited()
    device._log.warning.assert_called_once()
    assert "did not answer after the power-on" in device._log.warning.call_args[0][0]
    device._log.error.assert_not_called()
    assert not any("should now be on" in m for m in _infos(device))
    _guard(device).check(True)  # nothing recorded: no exception


async def test_a_tv_slow_to_come_back_gets_one_more_try():
    device = _device(reachable=(False, True), art_state="on")
    assert await _ensure(device) is True
    assert "Frame Art: TV answers after the power-on" in _infos(device)


async def test_an_unknown_art_state_is_not_called_off():
    device = _device(art_state=None, written=False, connected=False)

    assert await _ensure(device) is False

    infos = _infos(device)
    assert any("Art Mode state unknown" in m for m in infos)
    assert not any("Art Mode is OFF" in m for m in infos)


async def test_a_write_that_never_left_is_not_held_against_a_retry():
    device = _device(art_state=None, written=False, connected=False)
    await _ensure(device)
    _guard(device).check(True)  # no ArtModeWriteSuppressed


async def test_a_write_sent_on_an_open_channel_is_still_held():
    device = _device(art_state="off", written=False, connected=True)
    await _ensure(device)
    assert any("Art Mode is OFF" in m for m in _infos(device))
    with pytest.raises(ArtModeWriteSuppressed):
        _guard(device).check(True)


async def test_a_tv_already_on_is_not_probed():
    device = _device(art_state="on")
    with (
        patch.object(SamsungTVDevice, "state", new=MediaPlayerState.ON),
        patch.object(SamsungTVDevice, "extra_state_attributes", new={}),
        patch(f"{MP}.asyncio.sleep", new=AsyncMock()),
    ):
        assert await device._ensure_art_mode_ready() is True
    device._async_load_device_info.assert_not_awaited()
    device.async_turn_on.assert_not_awaited()
