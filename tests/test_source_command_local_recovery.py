import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from aiohttp import ClientResponseError
import pytest

from custom_components.samsungtv_smart.api.ipcontrol import SamsungIPControlError
from custom_components.samsungtv_smart.const import DATA_MOBILEBFF, DOMAIN
from custom_components.samsungtv_smart.media_player import STStatus

from .test_st_source_key_falls_back_to_ws import Device, _device


@pytest.mark.parametrize("key", ["ST_HDMI4", "IP_HDMI4", "KEY_HDMI4"])
async def test_quick_settings_input_does_not_repeat_the_command_over_ip_or_cloud(key):
    device = _device()
    client = SimpleNamespace(
        connected=True,
        find_source=MagicMock(
            return_value=SimpleNamespace(source_id="s/source.layer.hdmi@HDMI4")
        ),
        async_launch_source=AsyncMock(return_value=True),
    )
    device._entry_id = "synthetic-entry"
    device.hass = SimpleNamespace(
        data={DOMAIN: {device._entry_id: {DATA_MOBILEBFF: client}}}
    )
    device._source_list = {"HDMI input": key}
    device._async_wake_for_source_switch = AsyncMock()
    device._async_send_keys = AsyncMock()
    device._note_source_switch = MagicMock()
    device._update_media = AsyncMock()
    device.async_write_ha_state = MagicMock()

    await device._async_select_source("HDMI input", assume_on=True)

    client.async_launch_source.assert_awaited_once_with("s/source.layer.hdmi@HDMI4")
    device._async_send_keys.assert_not_awaited()


@pytest.mark.parametrize(
    ("key", "expected"),
    [("ST_TV", "IP_TV"), ("ST_HDMI1", "IP_HDMI1"), ("ST_HDMI4", "IP_HDMI4")],
)
async def test_verified_local_input_avoids_cloud_command(key, expected):
    device = _device(
        st=SimpleNamespace(
            state=STStatus.STATE_ON,
            async_select_source=AsyncMock(
                side_effect=AssertionError(
                    "A verified local input needs no cloud command"
                )
            ),
        )
    )
    device._async_ip_control_source = AsyncMock(return_value=True)
    assert await device._smartthings_keys(key) is True
    device._async_ip_control_source.assert_awaited_once_with(expected, verify=True)
    device._st.async_select_source.assert_not_awaited()
    assert not device.sent


@pytest.mark.parametrize("status", [409, 429, 500])
async def test_cloud_failure_after_local_refusal_retains_ws_fallback(status):
    device = _device(
        st=SimpleNamespace(
            state=STStatus.STATE_ON,
            async_select_source=AsyncMock(
                side_effect=ClientResponseError(MagicMock(), (), status=status)
            ),
        )
    )
    device._async_ip_control_source = AsyncMock(return_value=False)
    device._async_st_key_over_ws = AsyncMock(return_value=True)
    assert await device._smartthings_keys("ST_HDMI4") is True
    device._async_ip_control_source.assert_awaited_once_with("IP_HDMI4", verify=True)
    device._st.async_select_source.assert_awaited_once_with("HDMI4")
    device._async_st_key_over_ws.assert_awaited_once()


async def test_offline_cloud_still_uses_paired_input_control():
    device = _device(st=None)
    device._async_ip_control_source = AsyncMock(return_value=True)
    assert await device._smartthings_keys("ST_TV") is True
    device._async_ip_control_source.assert_awaited_once_with("IP_TV", verify=True)


async def test_a_local_echo_without_changed_input_is_not_success(monkeypatch):
    device = _device()
    client = SimpleNamespace(
        async_set_input_source=AsyncMock(return_value="HDMI4"),
        async_get_tv_states=AsyncMock(return_value={"inputSource": "HDMI2"}),
    )
    device._get_ip_control_client = lambda: client
    device._get_ip_control_state_coordinator = MagicMock()
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    assert await device._async_ip_control_source("IP_HDMI4", verify=True) is False
    device._get_ip_control_state_coordinator.assert_not_called()


async def test_confirmed_local_input_publishes_the_actual_input(monkeypatch):
    device = _device()
    client = SimpleNamespace(
        async_set_input_source=AsyncMock(return_value="HDMI4"),
        async_get_tv_states=AsyncMock(return_value={"inputSource": "HDMI4"}),
    )
    device._get_ip_control_client = lambda: client
    device._get_ip_control_state_coordinator = lambda: None
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    assert await device._async_ip_control_source("IP_HDMI4", verify=True) is True
    assert device._ip_input_source == "HDMI4"


async def test_verification_failure_does_not_publish_an_assumed_input(monkeypatch):
    device = _device()
    client = SimpleNamespace(
        async_set_input_source=AsyncMock(return_value="HDMI4"),
        async_get_tv_states=AsyncMock(side_effect=SamsungIPControlError("offline")),
    )
    device._get_ip_control_client = lambda: client
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    assert await device._async_ip_control_source("IP_HDMI4", verify=True) is False


async def test_chained_command_stops_after_failed_source():
    device = _device()
    device._smartthings_keys = AsyncMock(return_value=False)
    assert await Device._async_send_keys(device, "ST_HDMI4+KEY_ENTER") is False
    assert not device.sent


@pytest.mark.parametrize("first", ["KEY_RETURN", "ST_VOLUP"])
async def test_non_input_steps_keep_the_existing_sequence_behavior(first, monkeypatch):
    device = _device()
    device._smartthings_keys = AsyncMock(return_value=False)
    device.async_send_command = AsyncMock(return_value=False)
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    assert await device._async_send_keys(first + "+KEY_ENTER") is True
    assert device.async_send_command.await_args_list[-1].args == ("KEY_ENTER",)
    assert device.async_send_command.await_count == (2 if first == "KEY_RETURN" else 1)
