"""Log lines from the 8.10.0 overnight log that said the wrong thing, or nothing.

192.168.1.161 off the network after 06:19:57:

    06:20:13.503 IP Control art-mode read ... failed 3 times in a row ...;
                 clearing stale cached value (False)
    06:20:18.502 IP Control art-mode read ... failed (...); keeping last value
                 (failure 4/3)                      <- nothing left to keep
    06:20:06.500 Error retrieving device info on 192.168.1.161:   <- empty
    05:18:28.399 [api.upnp]                                       <- empty
"""

import asyncio
import logging
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

if "pysmartthings" not in sys.modules:
    pysmartthings = ModuleType("pysmartthings")

    class _Names:
        def __getattr__(self, name):
            return name

    pysmartthings.Attribute = _Names()
    pysmartthings.Capability = _Names()
    pysmartthings.SmartThings = object
    sys.modules["pysmartthings"] = pysmartthings

from custom_components.samsungtv_smart.api.ipcontrol import (  # noqa: E402
    SamsungIPControlError,
)
from custom_components.samsungtv_smart.api.upnp import SamsungUPnP  # noqa: E402
from custom_components.samsungtv_smart.media_player import (  # noqa: E402
    SamsungTVDevice,
)


async def test_failures_after_the_cache_is_cleared_do_not_claim_a_last_value():
    device = object.__new__(SamsungTVDevice)
    device._host = "192.168.1.161"
    device._log = MagicMock()
    device._ip_art_mode = False
    device._ip_art_mode_failures = 0
    device.async_write_ha_state = MagicMock()
    client = AsyncMock()
    client.async_get_power_state.side_effect = SamsungIPControlError(
        "transport failure: [Errno 111] Connection refused"
    )
    device._get_ip_control_client = MagicMock(return_value=client)

    for _ in range(5):
        await device._refresh_ip_art_mode()

    messages = [c.args[0] % c.args[1:] for c in device._log.debug.call_args_list]
    assert "keeping last value (failure 1/3)" in messages[0]
    assert "clearing stale cached value" in messages[2]
    assert "no cached value (4 failures in a row)" in messages[3]
    assert not any("failure 4/3" in m or "failure 5/3" in m for m in messages)
    assert device._ip_art_mode is None


async def test_a_device_info_timeout_is_named():
    device = object.__new__(SamsungTVDevice)
    device._host = "192.168.1.161"
    device._log = MagicMock()
    device._device_info = None
    device._rest_api = SimpleNamespace(
        async_rest_device_info=AsyncMock(side_effect=asyncio.TimeoutError())
    )

    assert await device._async_load_device_info(force=True) is None

    line = device._log.debug.call_args[0][0] % device._log.debug.call_args[0][1:]
    assert line == "Error retrieving device info on 192.168.1.161: TimeoutError()"


async def test_a_upnp_timeout_is_named_with_its_host(caplog):
    caplog.set_level(logging.DEBUG)
    session = MagicMock()
    session.post.side_effect = asyncio.TimeoutError()
    upnp = SamsungUPnP(host="192.168.1.161", session=session)

    assert await upnp._soap_request("GetVolume", "", "RenderingControl") is None

    assert "UPnP GetVolume on 192.168.1.161 failed: TimeoutError()" in caplog.text
