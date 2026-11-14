"""media_player.turn_off must use IP Control before the remote's KEY_POWER (#437).

On a 2024+ Frame a KEY_POWER sent over the WebSocket - even held for 3 s - is
read as TV <-> Art Mode and never reaches standby. The power switch already
tried IP Control first, but media_player.turn_off (the media card's power
button, automations, voice assistants) went SmartThings -> WebSocket only, so
a Frame paired for IP Control and without SmartThings still just toggled Art
Mode. What is pinned here: IP Control powerOff leads and is trusted only once
the TV reports powerOff; an unconfirmed or failed call falls back unchanged;
and a Frame with neither IP Control nor SmartThings says why, once.
"""

import asyncio
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

if "pysmartthings" not in sys.modules:
    pysmartthings = ModuleType("pysmartthings")

    class _Names:
        def __getattr__(self, name):
            return name

    pysmartthings.Attribute = _Names()
    pysmartthings.Capability = _Names()
    pysmartthings.Command = _Names()
    pysmartthings.SmartThings = object
    sys.modules["pysmartthings"] = pysmartthings

from custom_components.samsungtv_smart.api.ipcontrol import (  # noqa: E402
    SamsungIPControlAuthError,
    SamsungIPControlError,
)
from custom_components.samsungtv_smart.media_player import (  # noqa: E402
    ArtModeSupport,
    SamsungTVDevice,
)


def _device(*, ip=True, power_after="powerOff", ip_error=None, st=None):
    device = object.__new__(SamsungTVDevice)
    device._log = MagicMock()
    device._st = st
    device._end_of_power_off = None
    device._ws = SimpleNamespace(set_power_off_request=MagicMock())
    device._async_switch_entity = AsyncMock()
    device.calls = []

    async def power_off():
        device.calls.append("ip_off")
        if ip_error:
            raise ip_error
        return "powerOff"

    client = SimpleNamespace(
        async_power_off=AsyncMock(side_effect=power_off),
        async_get_power_state=AsyncMock(return_value=power_after),
    )
    device._get_ip_control_client = MagicMock(return_value=client if ip else None)

    def ws_turn_off():
        device.calls.append("ws_key_power")
        return True

    device._turn_off = ws_turn_off

    async def executor(func, *args):
        return func(*args)

    device.hass = SimpleNamespace(async_add_executor_job=executor)
    return device, client


def _run(device, art=ArtModeSupport.FULL):
    with (
        patch.object(
            SamsungTVDevice,
            "support_art_mode",
            new_callable=PropertyMock,
            return_value=art,
        ),
        patch(
            "custom_components.samsungtv_smart.media_player.asyncio.sleep",
            new=AsyncMock(),
        ),
    ):
        asyncio.run(device.async_turn_off())


def test_a_paired_frame_is_powered_off_over_ip_control_not_the_remote():
    device, client = _device()
    _run(device)
    assert device.calls == ["ip_off"]  # no KEY_POWER toggle
    device._ws.set_power_off_request.assert_called_once()
    device._async_switch_entity.assert_awaited_once_with(False)
    assert device._end_of_power_off is not None


def test_an_unconfirmed_ip_power_off_falls_back_to_the_remote():
    # The TV accepted the call but still reads powerOn: not trusted.
    device, client = _device(power_after="powerOn")
    _run(device)
    assert device.calls == ["ip_off", "ws_key_power"]
    assert client.async_get_power_state.await_count == 2  # re-read once


@pytest.mark.parametrize(
    "error", [SamsungIPControlError("offline"), SamsungIPControlAuthError("rejected")]
)
def test_a_failed_ip_power_off_falls_back_to_the_remote(error):
    device, _ = _device(ip_error=error)
    _run(device)
    assert device.calls == ["ip_off", "ws_key_power"]


def test_a_power_off_already_in_progress_sends_nothing():
    device, client = _device()
    device._power_off_in_progress = lambda: True
    _run(device)
    assert device.calls == []
    client.async_power_off.assert_not_awaited()


def test_ip_control_also_leads_before_smartthings():
    st = SimpleNamespace(async_turn_off=AsyncMock())
    device, _ = _device(st=st)
    _run(device)
    assert device.calls == ["ip_off"]
    st.async_turn_off.assert_not_awaited()


def test_a_frame_with_neither_ip_control_nor_smartthings_warns_once():
    device, _ = _device(ip=False)
    _run(device)
    _run(device)
    assert device.calls == ["ws_key_power", "ws_key_power"]
    warnings = [
        c for c in device._log.warning.call_args_list if "KEY_POWER" in c.args[0]
    ]
    assert len(warnings) == 1
    assert "Pair IP Control" in warnings[0].args[0]


def test_a_non_frame_without_ip_control_does_not_warn():
    # Off is a real standby on a non-Frame: KEY_POWER is fine there.
    device, _ = _device(ip=False)
    _run(device, art=ArtModeSupport.UNSUPPORTED)
    assert device.calls == ["ws_key_power"]
    device._log.warning.assert_not_called()


def test_a_frame_whose_ip_call_failed_does_not_get_the_unpaired_warning():
    # Paired but the call failed: the advice to pair would be wrong.
    device, _ = _device(ip_error=SamsungIPControlError("offline"))
    _run(device)
    device._log.warning.assert_not_called()
