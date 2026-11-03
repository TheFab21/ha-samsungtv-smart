from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.samsungtv_smart.api.ipcontrol import (
    SamsungIPControlAuthError,
    SamsungIPControlError,
)
from custom_components.samsungtv_smart.switch import SamsungTVPowerSwitch


def device(*, local=True, local_error=None, cloud=True, cloud_error=None):
    entity = object.__new__(SamsungTVPowerSwitch)
    entity._log = MagicMock()
    entity._device_id = "synthetic-device" if cloud else None
    entity._device_name = "Test TV"
    entity._set_optimistic = MagicMock()
    entity._get_media_player_entity_id = lambda: "media_player.test_tv"
    entity.hass = SimpleNamespace(services=SimpleNamespace(async_call=AsyncMock()))
    calls = []

    async def power_off():
        calls.append("local")
        if local_error:
            raise local_error
        return "powerOff"

    async def cloud_off(*_args):
        calls.append("cloud")
        if cloud_error:
            raise cloud_error

    local_client = SimpleNamespace(
        async_power_off=AsyncMock(side_effect=power_off),
        async_get_power_state=AsyncMock(return_value="powerOff"),
    )
    cloud_client = SimpleNamespace(
        execute_device_command=AsyncMock(side_effect=cloud_off)
    )
    entity._get_ip_control = MagicMock(return_value=local_client if local else None)
    entity._get_st_client = AsyncMock(return_value=cloud_client)
    return entity, calls


async def test_local_success_needs_no_cloud_client_or_remote_fallback():
    entity, calls = device()
    await entity.async_turn_off()
    assert calls == ["local"]
    entity._get_st_client.assert_not_awaited()
    entity.hass.services.async_call.assert_not_awaited()
    entity._set_optimistic.assert_called_once_with(False)


@pytest.mark.parametrize(
    "error", [SamsungIPControlError("offline"), SamsungIPControlAuthError("rejected")]
)
async def test_local_failure_keeps_cloud_standby(error):
    entity, calls = device(local_error=error)
    await entity.async_turn_off()
    assert calls == ["local", "cloud"]
    entity.hass.services.async_call.assert_not_awaited()


async def test_unpaired_tv_still_uses_cloud():
    entity, calls = device(local=False)
    await entity.async_turn_off()
    assert calls == ["cloud"]


async def test_both_transports_failing_retain_the_existing_media_player_path():
    entity, calls = device(
        local_error=SamsungIPControlError("offline"),
        cloud_error=RuntimeError("offline"),
    )
    await entity.async_turn_off()
    assert calls == ["local", "cloud"]
    entity.hass.services.async_call.assert_awaited_once_with(
        "media_player",
        "turn_off",
        {"entity_id": "media_player.test_tv"},
        blocking=True,
    )


async def test_an_acknowledged_but_ignored_power_off_uses_the_cloud():
    entity, calls = device()
    client = entity._get_ip_control()
    client.async_get_power_state.return_value = "powerOn"
    await entity.async_turn_off()
    assert calls == ["local", "cloud"]
    assert client.async_get_power_state.await_count == 2
    entity._set_optimistic.assert_called_once_with(False)


async def test_delayed_local_standby_is_confirmed_before_reporting_off():
    entity, calls = device()
    client = entity._get_ip_control()
    client.async_get_power_state.side_effect = ["powerOn", "powerOff"]
    await entity.async_turn_off()
    assert calls == ["local"]
    assert client.async_get_power_state.await_count == 2


@pytest.mark.parametrize("reply", ["unknown", None])
async def test_unconfirmed_power_state_keeps_the_cloud_fallback(reply):
    entity, calls = device()
    entity._get_ip_control().async_get_power_state.return_value = reply
    await entity.async_turn_off()
    assert calls == ["local", "cloud"]


async def test_failed_local_readback_keeps_the_cloud_fallback():
    entity, calls = device()
    entity._get_ip_control().async_get_power_state.side_effect = SamsungIPControlError(
        "offline"
    )
    await entity.async_turn_off()
    assert calls == ["local", "cloud"]
