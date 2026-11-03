from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.samsungtv_smart.api.ipcontrol import (
    SamsungIPControlUnsupportedError,
)
from custom_components.samsungtv_smart.const import CONF_IP_CONTROL_FW_VERSION

from .test_st_source_key_falls_back_to_ws import _device


def device():
    entity = _device(st=None, input_source="HDMI4")
    entity._get_ip_control_state_coordinator = Mock(return_value=None)
    client = SimpleNamespace(
        async_set_input_source=AsyncMock(
            side_effect=SamsungIPControlUnsupportedError("unsupported")
        ),
        async_get_tv_states=AsyncMock(return_value={"inputSource": "HDMI4"}),
    )
    entity._get_ip_control_client = lambda: client
    return entity, client


async def test_missing_setter_is_probed_once_before_falling_back():
    entity, client = device()
    assert await entity._smartthings_keys("ST_HDMI4") is True
    assert await entity._smartthings_keys("ST_HDMI4") is True
    client.async_set_input_source.assert_awaited_once()
    assert entity.sent == ["KEY_HDMI4", "KEY_HDMI4"]


async def test_a_refusal_in_ambient_does_not_disable_the_method():
    entity, client = device()
    entity._panel_may_be_ambient.return_value = True
    await entity._async_ip_source_fallback("ST_HDMI4")
    await entity._async_ip_source_fallback("ST_HDMI4")
    assert client.async_set_input_source.await_count == 2
    assert entity._ip_input_source_supported is None


async def test_new_pairing_client_allows_another_probe():
    entity, client = device()
    await entity._async_ip_source_fallback("ST_HDMI4")
    replacement = SimpleNamespace(
        async_set_input_source=AsyncMock(
            side_effect=SamsungIPControlUnsupportedError("unsupported")
        )
    )
    entity._get_ip_control_client = lambda: replacement
    await entity._async_ip_source_fallback("ST_HDMI4")
    replacement.async_set_input_source.assert_awaited_once()


async def test_firmware_change_allows_another_probe():
    entity, client = device()
    entry = SimpleNamespace(data={CONF_IP_CONTROL_FW_VERSION: "synthetic-1"})
    entity._entry_id = "synthetic-entry"
    entity.hass = SimpleNamespace(
        config_entries=SimpleNamespace(async_get_entry=lambda _: entry)
    )
    await entity._async_ip_source_fallback("ST_HDMI4")
    await entity._async_ip_source_fallback("ST_HDMI4")
    assert client.async_set_input_source.await_count == 1
    entry.data[CONF_IP_CONTROL_FW_VERSION] = "synthetic-2"
    await entity._async_ip_source_fallback("ST_HDMI4")
    assert client.async_set_input_source.await_count == 2


async def test_unsupported_readback_does_not_mark_the_setter_unsupported():
    entity, client = device()
    client.async_set_input_source.side_effect = None
    client.async_set_input_source.return_value = "HDMI4"
    client.async_get_tv_states.side_effect = SamsungIPControlUnsupportedError(
        "getter unavailable"
    )
    await entity._async_ip_source_fallback("ST_HDMI4")
    assert entity._ip_input_source_supported is None
